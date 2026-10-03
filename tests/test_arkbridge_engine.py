import json
import os
import re
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "package" / "arkbridge" / "files" / "usr" / "libexec" / "arkbridge"


STUB_IP = r'''#!/bin/sh
echo "ip $*" >> "$FX_LOG"
fam=4
if [ "$1" = "-4" ]; then fam=4; shift
elif [ "$1" = "-6" ]; then fam=6; shift; fi
if [ "$1" = "-o" ]; then shift; fi
cmd=$1; shift
case "$cmd" in
  route)
    sub=$1; shift
    case "$sub" in
      show)
        if [ "$1" = "default" ]; then
          if [ "$2" = "dev" ] && [ -f "$FX_DIR/default${fam}_dev_$3" ]; then
            cat "$FX_DIR/default${fam}_dev_$3"
          else
            cat "$FX_DIR/default$fam" 2>/dev/null
          fi
        elif [ "$1" = "dev" ]; then
          cat "$FX_DIR/route_dev_$2" 2>/dev/null
        fi
        ;;
      get) cat "$FX_DIR/route_get_$1" 2>/dev/null ;;
      replace)
        if [ -f "$FX_DIR/fail_prefix" ] && [ "$1" = "$(cat "$FX_DIR/fail_prefix")" ]; then exit 2; fi
        if [ "$1" = "default" ]; then
          via=; dev=; proto=
          while [ $# -gt 0 ]; do
            case "$1" in
              via) via=$2; shift 2 ;;
              dev) dev=$2; shift 2 ;;
              proto) proto=$2; shift 2 ;;
              *) shift ;;
            esac
          done
          if [ "$via" = "192.0.2.1" ] && [ -f "$FX_DIR/noop_primary4" ]; then exit 0; fi
          if [ "$via" = "192.0.2.1" ] && [ -f "$FX_DIR/fail_primary4" ]; then exit 2; fi
          if [ "$via" = "2001:db8::ff" ] && [ -f "$FX_DIR/fail_primary6" ]; then exit 2; fi
          if [ "$via" = "2001:db8::1" ] && [ -f "$FX_DIR/fail_backup6" ]; then exit 2; fi
          printf 'default via %s dev %s proto %s\n' "$via" "$dev" "${proto:-static}" > "$FX_DIR/default$fam"
        fi
        ;;
      flush)
        # record flushed tables/targets so tests can detect orphan routes
        pfx=
        for a in "$@"; do [ -n "$pfx" ] || pfx=$a; done
        tbl=
        for a in "$@"; do case "$a" in [0-9]*) tbl=$a ;; esac; done
        echo "flush $fam $pfx $tbl" >> "$FX_DIR/rule_state" ;;
    esac
    ;;
  rule)
    sub=$1; shift
    case "$sub" in
      add)
        if [ -f "$FX_DIR/fail_rule_add" ]; then exit 2; fi
        pref=; to=; lookup=
        while [ $# -gt 0 ]; do
          case "$1" in
            pref) pref=$2; shift 2 ;;
            to) to=$2; shift 2 ;;
            lookup) lookup=$2; shift 2 ;;
            *) shift ;;
          esac
        done
        echo "add $fam $pref $to $lookup" >> "$FX_DIR/rule_state" ;;
      del)
        pref=; to=; lookup=
        while [ $# -gt 0 ]; do
          case "$1" in
            pref) pref=$2; shift 2 ;;
            to) to=$2; shift 2 ;;
            lookup) lookup=$2; shift 2 ;;
            *) shift ;;
          esac
        done
        echo "del $fam $pref $to $lookup" >> "$FX_DIR/rule_state" ;;
    esac
    ;;
  addr)
    # addr show DEV  (after optional -o stripped above, subcommand is show)
    sub=$1; shift
    dev=$1
    if [ "$fam" = "6" ]; then cat "$FX_DIR/addr6_$dev" 2>/dev/null
    else cat "$FX_DIR/addr4_$dev" 2>/dev/null; fi
    ;;
  link)
    sub=$1; shift
    dev=$1
    [ -f "$FX_DIR/link_$dev" ] || exit 1
    ;;
esac
exit 0
'''

STUB_CURL = r'''#!/bin/sh
echo "curl $*" >> "$FX_LOG"
url=
for a in "$@"; do url=$a; done
t=${url#https://}
t=${t%%/*}
t=${t%:*}
t=${t#[}
t=${t%]}
source=
prev=
timeout=4
for a in "$@"; do
  [ "$prev" = "--interface" ] && source=$a
  [ "$prev" = "-m" ] && timeout=$a
  prev=$a
done
if [ -f "$FX_DIR/slow_curl" ]; then
  now=$(cat "$FX_DIR/now")
  echo $((now + timeout)) > "$FX_DIR/now"
  exit 28
fi
# Watchdog: a forced backup-internet result overrides everything.
if [ -f "$FX_DIR/backup_internet" ]; then
  if [ "$source" = "198.51.100.2" ] || [ "$source" = "2001:db8::2" ]; then
    case "$(cat "$FX_DIR/backup_internet")" in
      ok) exit 0 ;;
      fail) exit 7 ;;
    esac
  fi
fi
if [ "$source" = "2001:db8::2" ] && grep -qx "$t" "$FX_DIR/backup6_healthy" 2>/dev/null; then exit 0; fi
if grep -qx "$t" "$FX_DIR/healthy" 2>/dev/null; then exit 0; fi
exit 7
'''

STUB_PING = r'''#!/bin/sh
echo "ping $*" >> "$FX_LOG"
last=
for a in "$@"; do last=$a; done
if grep -qx "$last" "$FX_DIR/healthy" 2>/dev/null; then exit 0; fi
exit 1
'''

STUB_UCI = r'''#!/bin/sh
echo "uci $*" >> "$FX_LOG"
opt=$3
key=${opt##*.}
grep "^$key=" "$FX_DIR/config" 2>/dev/null | head -n1 | cut -d= -f2-
'''

STUB_DATE = r'''#!/bin/sh
echo "date $*" >> "$FX_LOG"
if [ "$1" = "+%s" ]; then cat "$FX_DIR/now" 2>/dev/null || echo 1700000000
else echo "2026-10-01 00:00:00"; fi
'''

STUB_STAT = r'''#!/bin/sh
echo "stat $*" >> "$FX_LOG"
echo 0
'''

STUB_TRUE = r'''#!/bin/sh
echo "$(basename "$0") $*" >> "$FX_LOG"
[ -f "$FX_DIR/fail_$(basename "$0")" ] && exit 1
exit 0
'''

STUB_IPTABLES = r'''#!/bin/sh
echo "iptables $*" >> "$FX_LOG"
[ -f "$FX_DIR/fail_iptables" ] && exit 1
case "$*" in
  "-t nat -I OUTPUT 1 -d "*"-p tcp --dport "*" -j RETURN")
    [ -f "$FX_DIR/fail_bypass_insert" ] && exit 1 ;;
  "-t nat -D OUTPUT -d "*"-p tcp --dport "*" -j RETURN")
    [ -f "$FX_DIR/fail_bypass_insert" ] && { echo "iptables $*" >> "$FX_DIR/bypass_delete"; exit 1; } ;;
esac
case "$*" in
  "-t nat -C POSTROUTING -o eth0 -j MASQUERADE") [ -f "$FX_DIR/nat_eth0" ]; exit $? ;;
  "-t nat -A POSTROUTING -o eth0 -j MASQUERADE") touch "$FX_DIR/nat_eth0" ;;
  "-t nat -D POSTROUTING -o eth0 -j MASQUERADE")
    [ -f "$FX_DIR/fail_nat_delete" ] && exit 1
    [ -f "$FX_DIR/nat_eth0" ] || exit 1
    rm "$FX_DIR/nat_eth0" ;;
  "-t nat -S POSTROUTING")
    [ ! -f "$FX_DIR/nat_eth0" ] || echo '-A POSTROUTING -o eth0 -j MASQUERADE' ;;
esac
exit 0
'''


DEFAULTS = {
    "enabled": "1",
    "mode": "side",
    "primary_gateway": "192.0.2.1",
    "primary_device": "br-lan",
    "backup_gateway": "198.51.100.1",
    "backup_device": "eth0",
    "backup_src_prefix": "",
    "probe_targets": "203.0.113.10",
    "probe_port": "443",
    "probe_host": "",
    "probe_table": "250",
    "backup_probe_table": "251",
    "rule_pref": "3000",
    "backup_rule_pref": "3001",
    "backup_source_rule_pref": "2998",
    "backup_grace_seconds": "20",
    "backup_watchdog_seconds": "25",
    "post_switch_hook": "",
    "backup_probe_targets": "",
    "bypass_transparent_proxy": "0",
    "masquerade_backup": "0",
    "failures_before_switch": "1",
    "successes_before_failback": "1",
    "failback_cooldown": "0",
    "interval": "10",
    "ipv6_enabled": "0",
    "primary_gateway6": "",
    "primary_device6": "",
    "backup_gateway6": "",
    "backup_device6": "",
    "probe_targets6": "",
    "probe_port6": "443",
    "probe_host6": "",
    "probe_table6": "252",
    "backup_probe_table6": "253",
    "rule_pref6": "3001",
    "bypass_transparent_proxy6": "0",
}


class Fixture:
    def __init__(self, root, run, env):
        self.root = root
        self.run = run
        self.env = env
        self.log = root / "cmd.log"

    @property
    def cmd_log(self):
        return self.log.read_text() if self.log.exists() else ""

    def run_engine(self, *args):
        return subprocess.run(
            ["sh", str(ENGINE), *args],
            env=self.env, capture_output=True, text=True,
        )

    def state(self):
        f = self.root / "run" / "state"
        return f.read_text().splitlines() if f.exists() else []

    def status(self):
        return self.run_engine("status").stdout

    def set_config(self, **overrides):
        cfg = {}
        for line in (self.root / "state" / "config").read_text().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                cfg[k] = v
        cfg.update({k: v for k, v in overrides.items() if k in DEFAULTS})
        (self.root / "state" / "config").write_text(
            "\n".join(f"{k}={v}" for k, v in cfg.items()) + "\n"
        )

    def set_healthy(self, values):
        (self.root / "state" / "healthy").write_text("\n".join(values) + "\n")
        targets = "2400:3200::1\n2400:3200:baba::1\n2402:4e00::\n" if "2001:db8::1" in values else ""
        (self.root / "state" / "backup6_healthy").write_text(targets)

    def set_now(self, epoch):
        (self.root / "state" / "now").write_text(str(int(epoch)))

    def backup_dead(self):
        return (self.root / "run" / "backup_dead").exists()

    def set_backup_internet(self, ok):
        # Controls the stub curl result for the backup-internet probe targets.
        mode = "ok" if ok else "fail"
        (self.root / "state" / "backup_internet").write_text(mode + "\n")

    def fail_primary_probe(self, target):
        (self.root / "state" / "fail_prefix").write_text(target + "/32")

    def default6(self):
        f = self.root / "state" / "default6"
        return f.read_text().strip() if f.exists() else ""

    def reset_log(self):
        self.log.write_text("")

    def engine_log(self):
        f = self.run / "log"
        return f.read_text() if f.exists() else ""

    def default4(self):
        f = self.root / "state" / "default4"
        return f.read_text().strip() if f.exists() else ""

    def fail_tool(self, name):
        (self.root / "state" / f"fail_{name}").write_text("")

    def rule_state(self):
        f = self.root / "state" / "rule_state"
        return f.read_text() if f.exists() else ""

    def fail_rule_add(self):
        (self.root / "state" / "fail_rule_add").write_text("")

    def fail_bypass_insert(self):
        (self.root / "state" / "fail_bypass_insert").write_text("")


class ArkBridgeEngineTests(unittest.TestCase):
    def make_fixture(self, **overrides):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        (root / "bin").mkdir()
        (root / "run").mkdir()
        (root / "state").mkdir()

        stubs = {
            "ip": STUB_IP, "curl": STUB_CURL, "ping": STUB_PING,
            "uci": STUB_UCI, "date": STUB_DATE, "stat": STUB_STAT,
            "chown": STUB_TRUE, "ip6tables": STUB_TRUE, "iptables": STUB_IPTABLES,
        }
        for name, body in stubs.items():
            path = root / "bin" / name
            path.write_text(body)
            path.chmod(0o755)

        cfg = dict(DEFAULTS)
        cfg.update({k: v for k, v in overrides.items() if k in DEFAULTS})
        (root / "state" / "config").write_text(
            "\n".join(f"{k}={v}" for k, v in cfg.items()) + "\n"
        )
        (root / "state" / "default4").write_text(
            "" if overrides.get("default4_empty")
            else "default via 192.0.2.1 dev br-lan proto static\n"
        )
        (root / "state" / "default6").write_text(
            overrides.get("default6", "")
        )
        if overrides.get("backup_default6"):
            (root / "state" / "default6_dev_eth0").write_text(
                overrides["backup_default6"]
            )
            m = re.search(r"via (\S+)", overrides["backup_default6"])
            if m:
                (root / "state" / f"route_get_{m.group(1)}").write_text(
                    f"{m.group(1)} dev eth0\n"
                )
        (root / "state" / "now").write_text(str(overrides.get("now", 1700000000)))
        (root / "state" / "link_br-lan").write_text("")
        (root / "state" / "link_eth0").write_text("")
        (root / "state" / "addr4_br-lan").write_text(
            "2: br-lan    inet 192.0.2.2/24 brd 192.0.2.255 scope global br-lan\n"
            if overrides.get("primary_has_v4", True) else ""
        )
        (root / "state" / "addr4_eth0").write_text(
            "2: eth0    inet 198.51.100.2/24 brd 198.51.100.255 scope global eth0\n"
            if overrides.get("backup_has_v4", False) else ""
        )
        (root / "state" / "addr6_eth0").write_text(
            "inet6 2001:db8::2/64 eth0\n" if overrides.get("backup_has_v6", False) else ""
        )
        (root / "state" / "addr6_br-lan").write_text(
            "inet6 2001:db8:1::2/64 br-lan\n" if overrides.get("primary_has_v6", False) else ""
        )
        (root / "state" / "route_get_198.51.100.1").write_text(
            "198.51.100.1 dev eth0\n"
        )
        v6gw = cfg.get("backup_gateway6") or ""
        if v6gw:
            (root / "state" / f"route_get_{v6gw}").write_text(
                f"{v6gw} dev {cfg.get('backup_device6') or 'eth0'}\n"
            )

        healthy = []
        if overrides.get("primary_ok", True):
            healthy.append("203.0.113.10")
        if overrides.get("backup_ok", False):
            healthy.append("198.51.100.1")
            healthy.extend(("223.5.5.5", "119.29.29.29"))
        healthy.extend(overrides.get("extra_healthy", []))
        (root / "state" / "healthy").write_text("\n".join(healthy) + "\n")
        if "2001:db8::1" in healthy:
            (root / "state" / "backup6_healthy").write_text(
                "2400:3200::1\n2400:3200:baba::1\n2402:4e00::\n"
            )
        if "backup_internet" in overrides:
            (root / "state" / "backup_internet").write_text(
                str(overrides["backup_internet"]) + "\n"
            )

        env = dict(os.environ)
        env.update({
            "PATH": f"{root / 'bin'}:{env['PATH']}",
            "FX_LOG": str(root / "cmd.log"),
            "FX_DIR": str(root / "state"),
            "ARKBRIDGE_RUNDIR": str(root / "run"),
            "ARKBRIDGE_LOCK": str(root / "arkbridge.lock"),
            "ARKBRIDGE_IP_BIN": str(root / "bin" / "ip"),
            "ARKBRIDGE_IPTABLES_BIN": str(root / "bin" / "iptables"),
            "ARKBRIDGE_IP6TABLES_BIN": str(root / "bin" / "ip6tables"),
            "ARKBRIDGE_CURL_BIN": str(root / "bin" / "curl"),
            "ARKBRIDGE_PING_BIN": str(root / "bin" / "ping"),
            "ARKBRIDGE_UCI_BIN": str(root / "bin" / "uci"),
            "ARKBRIDGE_DATE_BIN": str(root / "bin" / "date"),
            "ARKBRIDGE_STAT_BIN": str(root / "bin" / "stat"),
            "ARKBRIDGE_CHOWN_BIN": str(root / "bin" / "chown"),
        })
        fixture = Fixture(root, root / "run", env)
        fixture._temp = temp
        return fixture

    def test_v4_only_switch_is_unchanged(self):
        fx = self.make_fixture(primary_ok=False, backup_ok=True, backup_has_v4=True)
        fx.run_engine()
        log = fx.cmd_log
        self.assertIn("ip -4 route show default", log)
        self.assertIn(
            "ip -4 route replace default via 198.51.100.1 dev eth0", log
        )
        self.assertEqual(fx.state()[0], "backup")
        self.assertNotIn("ip -6", log)

    def test_healthy_primary_does_not_switch(self):
        fx = self.make_fixture(primary_ok=True, backup_ok=True, backup_has_v4=True)
        fx.run_engine()
        self.assertNotIn("ip -4 route replace default via 198.51.100.1", fx.cmd_log)
        self.assertEqual(fx.state()[0], "primary")


    def test_primary_health_is_or_of_enabled_families(self):
        fx = self.make_fixture(
            ipv6_enabled="1", primary_ok=False, backup_ok=False,
            primary_gateway6="2001:db8::ff", primary_has_v6=True,
            probe_targets6="2001:db8:100::1",
            extra_healthy=["2001:db8:100::1"], backup_has_v4=True,
        )
        fx.run_engine()
        # IPv6 primary is healthy even though IPv4 is not -> stay on primary.
        self.assertEqual(fx.state()[0], "primary")
        self.assertNotIn("ip -4 route replace default via 198.51.100.1", fx.cmd_log)

    def test_backup_ready_via_v6_only(self):
        fx = self.make_fixture(
            ipv6_enabled="1", primary_ok=False, backup_ok=False,
            backup_has_v4=False, backup_has_v6=True,
            backup_gateway6="2001:db8::1",
            primary_gateway6="2001:db8::ff", primary_has_v6=True,
            probe_targets6="2001:db8:100::1",
            extra_healthy=["2001:db8::1"],
        )
        fx.run_engine()
        log = fx.cmd_log
        self.assertEqual(fx.state()[0], "backup")
        # v6 moved; v4 left untouched because its backup is not ready.
        self.assertIn("ip -6 route replace default via 2001:db8::1 dev eth0", log)
        self.assertNotIn("ip -4 route replace default via 198.51.100.1", log)

    def test_ipv6_probe_defaults_used_when_empty(self):
        fx = self.make_fixture(
            ipv6_enabled="1", primary_gateway6="2001:db8::ff", primary_has_v6=True,
        )
        fx.run_engine()
        self.assertIn("2400:3200::1/128", fx.cmd_log)

    def test_switch_moves_both_families_when_both_backups_ready(self):
        fx = self.make_fixture(
            ipv6_enabled="1", primary_ok=False, backup_ok=True, backup_has_v4=True,
            backup_has_v6=True, backup_gateway6="2001:db8::1",
            primary_gateway6="2001:db8::ff", primary_has_v6=True,
            probe_targets6="2001:db8:100::1", extra_healthy=["2001:db8::1"],
        )
        fx.run_engine()
        log = fx.cmd_log
        self.assertIn("ip -4 route replace default via 198.51.100.1 dev eth0", log)
        self.assertIn("ip -6 route replace default via 2001:db8::1 dev eth0", log)
        self.assertEqual(fx.state()[0], "backup")
        self.assertEqual(fx.state()[4], "1")  # v6_active

    def test_backup_without_v6_leaves_v6_untouched(self):
        fx = self.make_fixture(
            ipv6_enabled="1", primary_ok=False, backup_ok=True, backup_has_v4=True,
            backup_has_v6=False, primary_gateway6="2001:db8::ff", primary_has_v6=True,
        )
        fx.run_engine()
        log = fx.cmd_log
        self.assertIn("ip -4 route replace default via 198.51.100.1 dev eth0", log)
        self.assertNotIn("ip -6 route replace default", log)

    def test_detect_reports_per_family_address_gateway_and_dns(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        bindir = root / "bin"; bindir.mkdir()
        netdir = root / "net"; (netdir / "eth0").mkdir(parents=True)
        (netdir / "eth0" / "operstate").write_text("up\n")
        (netdir / "eth0" / "carrier").write_text("1\n")
        (bindir / "ip").write_text(r'''#!/bin/sh
echo "ip $*" >> "$FX_LOG"
case "$*" in
  "-4 route show default dev br-lan") echo "default via 192.0.2.1 dev br-lan proto static" ;;
  "-4 route show default") echo "default via 192.0.2.1 dev br-lan proto static" ;;
  "-6 route show default") echo "default via 2001:db8::1 dev br-lan proto ra" ;;
  "-4 route show dev br-lan") echo "192.0.2.0/24 dev br-lan scope link" ;;
  "-6 route show dev br-lan") echo "2001:db8::/64 dev br-lan scope link" ;;
  "-4 route show dev eth0") echo "198.51.100.0/24 dev eth0 scope link" ;;
  "-6 route show dev eth0") echo "2001:db8:2::/64 dev eth0 scope link" ;;
  "-4 -o addr show eth0") echo "2: eth0    inet 198.51.100.2/24 brd 198.51.100.255 scope global eth0" ;;
  "-6 -o addr show eth0") echo "2: eth0    inet6 2001:db8:2::2/64 scope global" ;;
esac
exit 0
''')
        (bindir / "ip").chmod(0o755)
        dump = {
            "interface": [{
                "device": "eth0",
                "dns-server": ["198.51.100.1", "2001:db8:2::1"],
                "route": [
                    {"target": "0.0.0.0", "nexthop": "198.51.100.1"},
                    {"target": "::/0", "nexthop": "2001:db8:2::1"},
                ],
            }]
        }
        (bindir / "ubus").write_text(
            "#!/bin/sh\ncat <<'JSON'\n" + json.dumps(dump) + "\nJSON\n"
        )
        (bindir / "ubus").chmod(0o755)

        env = dict(os.environ)
        env.update({
            "FX_LOG": str(root / "cmd.log"),
            "ARKBRIDGE_NET_CLASS_ROOT": str(netdir),
            "ARKBRIDGE_IP_BIN": str(bindir / "ip"),
            "ARKBRIDGE_UBUS_BIN": str(bindir / "ubus"),
            "ARKBRIDGE_PYTHON_BIN": "python3",
        })
        detect = ROOT / "package" / "arkbridge" / "files" / "usr" / "libexec" / "arkbridge-detect"
        res = subprocess.run(["sh", str(detect)], env=env, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        data = json.loads(res.stdout)
        self.assertEqual(data["primary"]["device"], "br-lan")
        self.assertEqual(data["primary"]["gateway6"], "2001:db8::1")
        cand = data["candidates"][0]
        self.assertEqual(cand["device"], "eth0")
        self.assertEqual(cand["gateway"], "198.51.100.1")
        self.assertEqual(cand["gateway6"], "2001:db8:2::1")
        self.assertEqual(cand["dns4"], ["198.51.100.1"])
        self.assertEqual(cand["dns6"], ["2001:db8:2::1"])

    def test_status_reports_separate_family_rows(self):
        fx = self.make_fixture(ipv6_enabled="1", primary_gateway6="2001:db8::ff")
        s = json.loads(fx.status())
        self.assertEqual(s["ipv6_enabled"], "1")
        self.assertEqual(s["family"], "dual")
        for key in ("route6", "primary6", "backup6"):
            self.assertIn(key, s)
        self.assertEqual(s["primary6"]["gateway"], "2001:db8::ff")
        self.assertIn("has_ipv6", s["backup6"])


    def test_invalid_primary_probe_holds_when_ipv6_disabled(self):
        # C1: a bad primary probe route must hold, never switch, with v6 off.
        fx = self.make_fixture(primary_ok=False, backup_ok=True, backup_has_v4=True,
                               ipv6_enabled="0")
        fx.fail_primary_probe("203.0.113.10")
        fx.run_engine()
        self.assertNotIn("ip -4 route replace default via 198.51.100.1", fx.cmd_log)
        self.assertIn("probe invalid", fx.engine_log())

    def test_autodetected_v6_primary_not_overwritten_by_backup(self):
        # C2: after a v6 switch, fail-back must use the captured primary, not
        # the backup gateway currently in the default route.
        fx = self.make_fixture(
            ipv6_enabled="1", primary_ok=False, backup_ok=True,
            backup_has_v4=True, backup_has_v6=True, backup_gateway6="2001:db8::1",
            primary_has_v6=True, probe_targets6="2001:db8:100::1",
            extra_healthy=["2001:db8::1"],
            default6="default via 2001:db8::ff dev br-lan proto ra",
        )
        fx.run_engine()
        self.assertEqual(fx.state()[0], "backup")
        self.assertIn("via 2001:db8::1 dev eth0", fx.default6())
        fx.reset_log()
        fx.set_healthy(["203.0.113.10", "2001:db8:100::1"])
        fx.run_engine()
        self.assertIn("ip -6 route replace default via 2001:db8::ff dev br-lan proto static", fx.cmd_log)
        self.assertNotIn("via 2001:db8::1 dev br-lan", fx.cmd_log)

    def test_v6_only_backup_switch_fails_back(self):
        # C3: a v6-only switch must still be tracked and failed back.
        fx = self.make_fixture(
            ipv6_enabled="1", primary_ok=False, backup_ok=False,
            backup_has_v4=False, backup_has_v6=True, backup_gateway6="2001:db8::1",
            primary_gateway6="2001:db8::ff", primary_has_v6=True,
            probe_targets6="2001:db8:100::1", extra_healthy=["2001:db8::1"],
        )
        fx.run_engine()
        self.assertEqual(fx.state()[0], "backup")
        self.assertEqual(fx.state()[4], "1")
        fx.reset_log()
        fx.set_healthy(["203.0.113.10", "2001:db8:100::1"])
        fx.run_engine()
        self.assertIn("ip -6 route replace default via 2001:db8::ff dev br-lan proto static", fx.cmd_log)

    def test_disabling_ipv6_restores_v6_from_backup(self):
        # C4: disabling IPv6 while it is on the backup must restore it.
        fx = self.make_fixture(
            ipv6_enabled="1", primary_ok=False, backup_ok=False,
            backup_has_v4=False, backup_has_v6=True, backup_gateway6="2001:db8::1",
            primary_gateway6="2001:db8::ff", primary_has_v6=True,
            probe_targets6="2001:db8:100::1", extra_healthy=["2001:db8::1"],
        )
        fx.run_engine()
        self.assertEqual(fx.state()[4], "1")
        fx.reset_log()
        fx.set_config(ipv6_enabled="0")
        fx.set_healthy(["203.0.113.10"])
        fx.run_engine()
        self.assertIn("ip -6 route replace default via 2001:db8::ff dev br-lan proto static", fx.cmd_log)
        self.assertEqual(fx.state()[4], "0")


    def test_cleanup_restores_primary_when_on_backup(self):
        # C1: stop/cleanup must not leave traffic on the backup with NAT gone.
        fx = self.make_fixture(primary_ok=False, backup_ok=True, backup_has_v4=True)
        fx.run_engine()
        self.assertEqual(fx.state()[0], "backup")
        fx.reset_log()
        fx.run_engine("cleanup")
        self.assertIn("ip -4 route replace default via 192.0.2.1 dev br-lan proto static", fx.cmd_log)
        self.assertIn("via 192.0.2.1 dev br-lan", fx.default4())

    def test_cleanup_restores_v6_when_on_backup(self):
        # I2: cleanup must restore IPv6 too, from config or the captured value.
        fx = self.make_fixture(
            ipv6_enabled="1", primary_ok=False, backup_ok=True, backup_has_v4=True,
            backup_has_v6=True, backup_gateway6="2001:db8::1",
            primary_gateway6="2001:db8::ff", primary_has_v6=True,
            probe_targets6="2001:db8:100::1", extra_healthy=["2001:db8::1"],
        )
        fx.run_engine()
        self.assertEqual(fx.state()[4], "1")
        fx.reset_log()
        fx.run_engine("cleanup")
        self.assertIn("ip -6 route replace default via 2001:db8::ff dev br-lan proto static", fx.cmd_log)

    def test_v6_bypass_failure_does_not_fail_closed(self):
        # I1: a missing ip6tables nat table must degrade, not mark v6 down.
        fx = self.make_fixture(
            ipv6_enabled="1", primary_ok=False, backup_ok=False,
            primary_gateway6="2001:db8::ff", primary_has_v6=True,
            probe_targets6="2001:db8:100::1", extra_healthy=["2001:db8:100::1"],
            bypass_transparent_proxy6="1",
        )
        fx.fail_tool("ip6tables")
        fx.run_engine()
        # v6 primary is healthy -> primary_ok -> no switch to backup.
        self.assertNotIn("ip -4 route replace default via 198.51.100.1", fx.cmd_log)
        self.assertIn("proxy-bypass skipped", fx.engine_log())

    def test_failback_keeps_v6_on_backup_when_v6_primary_down(self):
        # I3: do not move v6 to a primary that is still unreachable.
        fx = self.make_fixture(
            ipv6_enabled="1", primary_ok=False, backup_ok=True, backup_has_v4=True,
            backup_has_v6=True, backup_gateway6="2001:db8::1",
            primary_gateway6="2001:db8::ff", primary_has_v6=True,
            probe_targets6="2001:db8:100::1", extra_healthy=["2001:db8::1"],
        )
        fx.run_engine()  # both families to backup
        self.assertEqual(fx.state()[0], "backup")
        fx.reset_log()
        # v4 primary recovers; v6 primary target stays unreachable, backup v6 healthy.
        fx.set_healthy(["203.0.113.10", "2001:db8::1"])
        fx.run_engine()
        self.assertNotIn("ip -6 route replace default via 2001:db8::ff dev br-lan", fx.cmd_log)
        # Overall stays on backup because v6 is still on the backup.
        self.assertEqual(fx.state()[0], "backup")


    def test_cleanup_does_not_touch_family_it_did_not_move(self):
        # C-1: a v4-only failover must not clobber the working v6 route on stop.
        fx = self.make_fixture(
            ipv6_enabled="1", primary_ok=False, backup_ok=True, backup_has_v4=True,
            backup_has_v6=False, primary_gateway6="2001:db8::ff", primary_has_v6=True,
            default6="default via 2001:db8:99::1 dev br-lan proto ra",
        )
        fx.run_engine()
        self.assertEqual(fx.state()[0], "backup")
        self.assertEqual(fx.state()[4], "0")   # v6 was never moved
        fx.reset_log()
        fx.run_engine("cleanup")
        self.assertNotIn("ip -6 route replace default", fx.cmd_log)
        self.assertIn("via 2001:db8:99::1 dev br-lan", fx.default6())

    def test_backup_v6_gateway_is_autodetected(self):
        # I-2: empty backup_gateway6 must still allow IPv6 failover.
        fx = self.make_fixture(
            ipv6_enabled="1", primary_ok=False, backup_ok=True, backup_has_v4=True,
            backup_has_v6=True, backup_gateway6="",
            primary_gateway6="2001:db8::ff", primary_has_v6=True,
            probe_targets6="2001:db8:100::1", extra_healthy=["2001:db8::1"],
            backup_default6="default via 2001:db8::1 dev eth0 proto ra",
        )
        fx.run_engine()
        self.assertIn("ip -6 route replace default via 2001:db8::1 dev eth0", fx.cmd_log)


    def test_cleanup_restores_v6_after_ipv6_disabled(self):
        # C-2: disabling IPv6 then stopping must still roll the v6 route back.
        fx = self.make_fixture(
            ipv6_enabled="1", primary_ok=False, backup_ok=True, backup_has_v4=True,
            backup_has_v6=True, backup_gateway6="2001:db8::1",
            primary_gateway6="2001:db8::ff", primary_has_v6=True,
            probe_targets6="2001:db8:100::1", extra_healthy=["2001:db8::1"],
        )
        fx.run_engine()
        self.assertEqual(fx.state()[4], "1")
        fx.set_config(ipv6_enabled="0")
        fx.reset_log()
        fx.run_engine("cleanup")
        self.assertIn("ip -6 route replace default via 2001:db8::ff dev br-lan proto static", fx.cmd_log)

    def test_status_reports_backup_for_v6_only_with_autodetected_gateway(self):
        # I-4: status must auto-detect the backup v6 gateway for the panel.
        fx = self.make_fixture(
            ipv6_enabled="1", primary_ok=False, backup_ok=False,
            backup_has_v4=False, backup_has_v6=True, backup_gateway6="",
            primary_gateway6="2001:db8::ff", primary_has_v6=True,
            probe_targets6="2001:db8:100::1", extra_healthy=["2001:db8::1"],
            backup_default6="default via 2001:db8::1 dev eth0 proto ra",
        )
        fx.run_engine()
        s = json.loads(fx.status())
        self.assertEqual(s["current"], "backup")
        self.assertEqual(s["backup6"]["gateway"], "2001:db8::1")


    def test_unknown_path_recovery_installs_primary_route(self):
        # C-3: with no default route, a healthy primary must be installed.
        fx = self.make_fixture(primary_ok=True, backup_ok=True, backup_has_v4=True,
                               default4_empty=True)
        fx.run_engine()
        self.assertIn("ip -4 route replace default via 192.0.2.1 dev br-lan proto static", fx.cmd_log)
        self.assertEqual(fx.state()[0], "primary")

    def test_disable_keeps_v6_owned_when_primary6_unknown(self):
        # I-2: if the v6 primary is unknown, disabling must not claim success.
        fx = self.make_fixture(
            ipv6_enabled="1", primary_ok=False, backup_ok=True, backup_has_v4=True,
            backup_has_v6=True, backup_gateway6="2001:db8::1",
            primary_gateway6="", primary_has_v6=False, default6="",
            probe_targets6="2001:db8:100::1", extra_healthy=["2001:db8::1"],
        )
        fx.run_engine()
        self.assertEqual(fx.state()[4], "1")
        fx.set_config(ipv6_enabled="0")
        fx.run_engine()
        self.assertEqual(fx.state()[4], "1")   # v6 still owned; not silently dropped
        self.assertEqual(fx.state()[0], "backup")   # recorded path reflects it
        self.assertIn("v6 primary unknown", fx.engine_log())

    def test_cleanup_does_not_touch_v6_never_enabled(self):
        # I-3: cleanup must not clobber v6 when IPv6 was never enabled.
        fx = self.make_fixture(
            ipv6_enabled="0", primary_ok=False, backup_ok=True, backup_has_v4=True,
            primary_gateway6="2001:db8::ff",
            default6="default via 2001:db8::1 dev eth0 proto ra",
        )
        fx.run_engine()
        fx.reset_log()
        fx.run_engine("cleanup")
        self.assertNotIn("ip -6 route replace default", fx.cmd_log)
        self.assertIn("via 2001:db8::1 dev eth0", fx.default6())


    def test_cleanup_keeps_v6_ownership_when_restore_fails(self):
        # C2: a failed v6 restore must not discard ownership.
        fx = self.make_fixture(
            ipv6_enabled="1", primary_ok=False, backup_ok=True, backup_has_v4=True,
            backup_has_v6=True, backup_gateway6="2001:db8::1",
            primary_gateway6="", primary_has_v6=False, default6="",
            probe_targets6="2001:db8:100::1", extra_healthy=["2001:db8::1"],
        )
        fx.run_engine()
        fx.set_config(ipv6_enabled="0")
        fx.run_engine("cleanup")
        self.assertEqual(fx.state()[4], "1")
        self.assertEqual(fx.state()[0], "backup")
        self.assertIn("via 2001:db8::1 dev eth0", fx.default6())

    def test_cleanup_restores_v4_after_backup_config_change(self):
        # I1: v4 ownership survives a config edit while on the backup.
        fx = self.make_fixture(primary_ok=False, backup_ok=True, backup_has_v4=True)
        fx.run_engine()
        self.assertEqual(fx.state()[0], "backup")
        fx.set_config(backup_gateway="198.51.100.99")
        fx.reset_log()
        fx.run_engine("cleanup")
        self.assertIn("ip -4 route replace default via 192.0.2.1 dev br-lan proto static", fx.cmd_log)

    def test_v4_marker_survives_unknown_config_edit(self):
        # Critical: an intervening loop run after the edit must not clear the
        # marker (fam_current is unknown, not primary).
        fx = self.make_fixture(primary_ok=False, backup_ok=True, backup_has_v4=True)
        fx.run_engine()
        fx.set_config(backup_gateway="198.51.100.99")
        fx.run_engine()   # intervening cycle: cur becomes unknown
        fx.reset_log()
        fx.run_engine("cleanup")
        self.assertIn("ip -4 route replace default via 192.0.2.1 dev br-lan proto static", fx.cmd_log)


    def test_late_ready_family_is_moved(self):
        # Critical: if v6 backup becomes ready after v4 already moved, it must
        # still be moved without a full failback/re-switch.
        fx = self.make_fixture(
            ipv6_enabled="1", primary_ok=False, backup_ok=True,
            backup_has_v4=True, backup_has_v6=False, backup_gateway6="2001:db8::1",
            primary_gateway6="2001:db8::ff", primary_has_v6=True,
            probe_targets6="2001:db8:100::1",
        )
        fx.run_engine()
        self.assertEqual(fx.state()[0], "backup")
        self.assertEqual(fx.state()[4], "0")
        # The backup uplink now provides IPv6.
        (fx.root / "state" / "addr6_eth0").write_text("inet6 2001:db8::2/64 eth0\n")
        (fx.root / "state" / "route_get_2001:db8::1").write_text("2001:db8::1 dev eth0\n")
        fx.set_healthy(["198.51.100.1", "2001:db8::1"])
        fx.reset_log()
        fx.run_engine()
        self.assertIn("ip -6 route replace default via 2001:db8::1 dev eth0", fx.cmd_log)
        self.assertEqual(fx.state()[4], "1")


    def test_disabling_ipv6_does_not_drag_healthy_v4_to_backup(self):
        # Critical regression: with the v4 primary healthy, disabling a stranded
        # IPv6 must not move IPv4 onto the backup.
        fx = self.make_fixture(
            ipv6_enabled="1", primary_ok=False, backup_ok=False,
            backup_has_v4=True, backup_has_v6=True, backup_gateway6="2001:db8::1",
            primary_gateway6="", primary_has_v6=False, default6="",
            probe_targets6="2001:db8:100::1", extra_healthy=["2001:db8::1"],
            successes_before_failback="2", failback_cooldown="30",
        )
        fx.run_engine()   # v6-only to backup (v4 backup not yet reachable)
        self.assertEqual(fx.state()[0], "backup")
        # Primary v4 recovers; disable IPv6 (v6 primary unresolvable).
        fx.set_config(ipv6_enabled="0")
        fx.set_healthy(["203.0.113.10", "198.51.100.1"])
        fx.reset_log()
        fx.run_engine()
        self.assertNotIn("ip -4 route replace default via 198.51.100.1", fx.cmd_log)


    def test_v6_probe_url_is_bracketed(self):
        # M1: IPv6 literals must be bracketed in the probe URL.
        fx = self.make_fixture(
            ipv6_enabled="1", primary_gateway6="2001:db8::ff", primary_has_v6=True,
        )
        fx.run_engine()
        self.assertIn("https://[2400:3200::1]:443/", fx.cmd_log)


    def test_missing_primary_device_still_allows_backup_failover(self):
        # I2: an absent primary device must not abort before the backup leg.
        fx = self.make_fixture(primary_ok=False, backup_ok=True, backup_has_v4=True)
        (fx.root / "state" / "link_br-lan").unlink()
        fx.run_engine()
        self.assertIn("ip -4 route replace default via 198.51.100.1 dev eth0", fx.cmd_log)
        self.assertEqual(fx.state()[0], "backup")


    def test_install_probe_removes_orphan_route_when_rule_add_fails(self):
        # A failed rule add must not leave the probe route behind in the table.
        fx = self.make_fixture(primary_ok=True, backup_ok=True, backup_has_v4=True)
        fx.fail_rule_add()
        fx.run_engine()
        state = fx.rule_state()
        # route was installed, rule add failed, route must be flushed back out.
        self.assertNotIn("add 4 3000", state)
        self.assertIn("flush 4 203.0.113.10/32 250", state)

    def test_install_probe_removes_orphan_route_and_rule_on_bypass_failure(self):
        # A failed proxy-bypass insert must also roll back route and rule.
        fx = self.make_fixture(primary_ok=True, backup_ok=True, backup_has_v4=True,
                               bypass_transparent_proxy="1")
        fx.fail_bypass_insert()
        fx.run_engine()
        state = fx.rule_state()
        # The primary probe attempted to install then had to fully unwind:
        # the rule is deleted and the route flushed, leaving no orphan.
        self.assertIn("add 4 3000 203.0.113.10/32 250", state)
        self.assertIn("del 4 3000 203.0.113.10/32 250", state)
        self.assertIn("flush 4 203.0.113.10/32 250", state)

    def test_install_probe_rejects_missing_source_before_installing_route(self):
        # No source means no probe install at all: never a route without a rule.
        fx = self.make_fixture(primary_ok=True, backup_ok=True, backup_has_v4=True)
        # Remove the primary interface address so family_source finds nothing.
        (fx.root / "state" / "addr4_br-lan").write_text("")
        fx.run_engine()
        self.assertNotIn("flush 4 203.0.113.10/32 250", fx.rule_state())
        self.assertNotIn("add 4 3000 203.0.113.10/32 250", fx.rule_state())

    def test_backup_probe_uses_distinct_rule_pref(self):
        # C1: primary and backup probe rules must not share a preference.
        fx = self.make_fixture(
            primary_ok=False, backup_ok=True, backup_has_v4=True,
            backup_probe_targets="198.51.100.88",
            extra_healthy=["198.51.100.88"],
        )
        fx.run_engine()
        self.assertIn(
            "ip -4 route replace 198.51.100.88/32 via 198.51.100.1 dev eth0 onlink table 251",
            fx.cmd_log,
        )
        self.assertIn(
            "ip -4 rule add pref 2998 from 198.51.100.2/32 to 198.51.100.88/32 lookup 251",
            fx.cmd_log,
        )
        self.assertIn(
            "curl -4 -s -m 4 -o /dev/null -k --noproxy * --interface 198.51.100.2 https://198.51.100.88:443/",
            fx.cmd_log,
        )

    def test_watchdog_probe_is_bound_to_backup_source_policy(self):
        # The watchdog must not accidentally succeed through the primary route.
        fx = self.make_fixture(
            primary_ok=False, backup_ok=True, backup_has_v4=True,
            backup_internet="ok", backup_grace_seconds="20",
            backup_watchdog_seconds="25",
        )
        fx.run_engine()
        self.assertEqual(fx.state()[0], "backup")
        fx.set_backup_internet(False)
        fx.set_now(1700000000 + 20)
        fx.reset_log()
        fx.run_engine()
        self.assertIn(
            "ip -4 rule add pref 2998 from 198.51.100.2/32 to 223.5.5.5/32 lookup 251",
            fx.cmd_log,
        )
        self.assertIn(
            "curl -4 -s -m 1 -o /dev/null -k --noproxy * --interface 198.51.100.2 https://223.5.5.5:443/",
            fx.cmd_log,
        )

    def test_ipv6_only_operation_without_ipv4_gateways(self):
        # I1: IPv6-only must work with empty IPv4 gateways.
        fx = self.make_fixture(
            ipv6_enabled="1", primary_gateway="", backup_gateway="",
            primary_ok=False, backup_ok=False, backup_has_v6=True,
            backup_gateway6="2001:db8::1", primary_gateway6="2001:db8::ff",
            primary_has_v6=True, probe_targets6="2001:db8:100::1",
            extra_healthy=["2001:db8::1"],
        )
        fx.run_engine()
        self.assertIn("ip -6 route replace default via 2001:db8::1 dev eth0", fx.cmd_log)

    def test_backup_nat_follows_masquerade_off(self):
        # I2: masquerade_backup=0 must not add NAT while on the backup.
        fx = self.make_fixture(primary_ok=False, backup_ok=True, backup_has_v4=True,
                               masquerade_backup="0")
        fx.run_engine()
        fx.run_engine()   # second cycle exercises the steady-state branch
        self.assertNotIn("-A POSTROUTING", fx.cmd_log)


    def test_failed_failback_keeps_v4_owned_and_nat_consistent(self):
        # Red-team: a failed failback must not clear V4MARK / drop NAT while v4
        # is still on the backup.
        fx = self.make_fixture(primary_ok=False, backup_ok=True, backup_has_v4=True,
                               masquerade_backup="1")
        fx.run_engine()                       # -> backup, NAT on, V4MARK set
        self.assertEqual(fx.state()[0], "backup")
        # Primary recovers (probe healthy) but the primary route replace fails.
        (fx.root / "state" / "fail_prefix").write_text("default")
        fx.set_healthy(["203.0.113.10"])
        fx.reset_log()
        fx.run_engine()
        self.assertIn("rollback", fx.engine_log().lower())
        # v4 restored to old (backup) route -> still owned, NAT NOT removed.
        self.assertTrue((fx.root / "run" / "v4moved").exists())
        self.assertNotIn("-D POSTROUTING", fx.cmd_log)


    def test_backup_watchdog_reverts_to_primary_when_backup_internet_dead(self):
        # Watchdog: within backup_grace the switch stands; once grace elapses
        # with no backup internet, revert to primary and mark backup dead.
        fx = self.make_fixture(
            primary_ok=False, backup_ok=True, backup_has_v4=True,
            backup_internet="ok", backup_grace_seconds="20",
            backup_watchdog_seconds="25",
        )
        fx.run_engine()  # switch to backup
        self.assertEqual(fx.state()[0], "backup")
        self.assertFalse(fx.backup_dead())
        # 26s later the grace period has elapsed -> watchdog must revert.
        fx.set_backup_internet(False)
        fx.set_now(1700000000 + 26)
        fx.reset_log()
        fx.run_engine()
        self.assertIn("watchdog", fx.engine_log().lower())
        self.assertIn("ip -4 route replace default via 192.0.2.1 dev br-lan proto static", fx.cmd_log)
        self.assertTrue(fx.backup_dead())

    def test_watchdog_holds_within_grace_window(self):
        fx = self.make_fixture(
            primary_ok=False, backup_ok=True, backup_has_v4=True,
            backup_internet="ok", backup_grace_seconds="20",
            backup_watchdog_seconds="25",
        )
        fx.run_engine()
        self.assertEqual(fx.state()[0], "backup")
        fx.set_backup_internet(False)
        fx.set_now(1700000000 + 10)  # still within grace
        fx.reset_log()
        fx.run_engine()
        self.assertNotIn("route replace default via 192.0.2.1", fx.cmd_log)
        self.assertFalse(fx.backup_dead())

    def test_dead_backup_is_not_reused_until_it_recovers(self):
        fx = self.make_fixture(
            primary_ok=False, backup_ok=True, backup_has_v4=True,
            backup_internet="ok", backup_grace_seconds="20",
            backup_watchdog_seconds="25",
        )
        fx.run_engine()
        fx.set_backup_internet(False)
        fx.set_now(1700000000 + 26)
        fx.run_engine()  # reverts + marks dead
        self.assertTrue(fx.backup_dead())
        # Primary still down, backup not recovered -> must NOT switch again.
        fx.set_now(1700000000 + 60)
        fx.reset_log()
        fx.run_engine()
        self.assertNotIn("ip -4 route replace default via 198.51.100.1", fx.cmd_log)
        # Backup recovers -> marker cleared, and it may be used again.
        fx.set_backup_internet(True)
        fx.reset_log()
        fx.run_engine()
        self.assertFalse(fx.backup_dead())

    def test_explicit_rollback_restores_primary_without_healthy_primary(self):
        fx = self.make_fixture(primary_ok=False, backup_ok=True, backup_has_v4=True,
                               masquerade_backup="1")
        fx.run_engine()
        fx.reset_log()
        result = fx.run_engine("rollback")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(fx.state()[0], "primary")
        self.assertIn("via 192.0.2.1 dev br-lan", fx.default4())
        self.assertFalse((fx.run / "v4moved").exists())
        self.assertFalse((fx.root / "state" / "nat_eth0").exists())
        self.assertNotIn("curl ", fx.cmd_log)

    def test_failed_explicit_rollback_retains_backup_nat_and_retries(self):
        fx = self.make_fixture(primary_ok=False, backup_ok=True, backup_has_v4=True,
                               masquerade_backup="1")
        fx.run_engine()
        (fx.root / "state" / "fail_primary4").touch()
        fx.reset_log()
        result = fx.run_engine("rollback")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(fx.state()[0], "backup")
        self.assertTrue((fx.run / "v4moved").exists())
        self.assertTrue((fx.run / "natowned").exists())
        self.assertTrue((fx.root / "state" / "nat_eth0").exists())
        self.assertNotIn("-D POSTROUTING", fx.cmd_log)
        (fx.root / "state" / "fail_primary4").unlink()
        # The outstanding rollback intent must retry even with primary health down.
        fx.run_engine()
        self.assertEqual(fx.state()[0], "primary")
        self.assertFalse((fx.root / "state" / "nat_eth0").exists())

    def test_cleanup_rejects_unverified_restore_and_preserves_backup_nat(self):
        fx = self.make_fixture(primary_ok=False, backup_ok=True, backup_has_v4=True,
                               masquerade_backup="1")
        fx.run_engine()
        (fx.root / "state" / "noop_primary4").touch()
        result = fx.run_engine("cleanup")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(fx.state()[0], "backup")
        self.assertTrue((fx.run / "v4moved").exists())
        self.assertTrue((fx.run / "natowned").exists())
        self.assertTrue((fx.root / "state" / "nat_eth0").exists())

    def test_failed_backup_transaction_immediately_returns_to_primary(self):
        fx = self.make_fixture(
            default4_empty=True, primary_ok=False, backup_ok=True, backup_has_v4=True,
            masquerade_backup="1", ipv6_enabled="1", backup_has_v6=True,
            backup_gateway6="2001:db8::1", primary_gateway6="2001:db8::ff",
            primary_has_v6=True, extra_healthy=["2001:db8::1"],
        )
        (fx.root / "state" / "fail_backup6").touch()
        result = fx.run_engine()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(fx.state()[0], "primary")
        self.assertIn("via 192.0.2.1 dev br-lan", fx.default4())
        self.assertFalse((fx.root / "state" / "nat_eth0").exists())
        self.assertTrue(fx.backup_dead())

    def test_watchdog_rolls_back_even_if_primary_probe_routing_is_invalid(self):
        fx = self.make_fixture(primary_ok=False, backup_ok=True, backup_has_v4=True,
                               masquerade_backup="1")
        fx.run_engine()
        fx.fail_primary_probe("203.0.113.10")
        fx.set_backup_internet(False)
        fx.set_now(1700000025)
        fx.run_engine()
        self.assertEqual(fx.state()[0], "primary")
        self.assertFalse((fx.root / "state" / "nat_eth0").exists())
        self.assertTrue(fx.backup_dead())

    def test_dead_backup_is_not_cleared_by_shared_target_on_healthy_primary(self):
        fx = self.make_fixture(primary_ok=True, backup_ok=False, backup_has_v4=True,
                               backup_probe_targets="203.0.113.10", backup_internet="fail")
        (fx.run / "backup_dead").touch()
        fx.run_engine()
        self.assertTrue(fx.backup_dead())
        self.assertEqual(fx.state()[0], "primary")

    def test_watchdog_budget_rolls_back_by_25_even_with_slow_https(self):
        fx = self.make_fixture(primary_ok=False, backup_ok=True, backup_has_v4=True,
                               masquerade_backup="1", probe_host="probe.example")
        fx.set_config(probe_host="")
        fx.run_engine()
        fx.set_config(probe_host="probe.example")
        fx.set_now(1700000020)
        (fx.root / "state" / "slow_curl").touch()
        result = fx.run_engine()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(fx.state()[0], "primary")
        self.assertLessEqual(int((fx.root / "state" / "now").read_text()), 1700000025)
        self.assertFalse((fx.root / "state" / "nat_eth0").exists())
        self.assertTrue(fx.backup_dead())

    def test_watchdog_does_not_force_healthy_ipv6_only_backup_to_primary(self):
        fx = self.make_fixture(
            ipv6_enabled="1", primary_ok=False, backup_ok=False, backup_has_v4=False,
            backup_has_v6=True, backup_gateway6="2001:db8::1",
            primary_gateway6="2001:db8::ff", primary_has_v6=True,
            probe_targets6="2001:db8:100::1", extra_healthy=["2001:db8::1"],
        )
        fx.run_engine()
        fx.set_now(1700000020)
        fx.run_engine()
        fx.set_now(1700000025)
        fx.run_engine()
        self.assertEqual(fx.state()[0], "backup")
        self.assertFalse(fx.backup_dead())

    def test_nat_delete_failure_retains_ownership_and_retries(self):
        fx = self.make_fixture(primary_ok=False, backup_ok=True, backup_has_v4=True,
                               masquerade_backup="1")
        fx.run_engine()
        (fx.root / "state" / "fail_nat_delete").touch()
        result = fx.run_engine("rollback")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(fx.state()[0], "primary")
        self.assertTrue((fx.run / "natowned").exists())
        self.assertTrue((fx.root / "state" / "nat_eth0").exists())
        (fx.root / "state" / "fail_nat_delete").unlink()
        self.assertEqual(fx.run_engine("rollback").returncode, 0)
        self.assertFalse((fx.run / "natowned").exists())
        self.assertFalse((fx.root / "state" / "nat_eth0").exists())

    def test_probe_shared_target_binds_primary_to_its_own_source(self):
        fx = self.make_fixture(primary_ok=True, backup_ok=True, backup_has_v4=True,
                               backup_probe_targets="203.0.113.10")
        fx.run_engine()
        calls = [line for line in fx.cmd_log.splitlines() if line.startswith("curl ")]
        self.assertTrue(any("--interface 192.0.2.2" in line for line in calls), calls)
        self.assertTrue(any("--interface 198.51.100.2" in line for line in calls), calls)

    def test_nat_query_error_keeps_cleanup_ownership(self):
        fx = self.make_fixture(primary_ok=False, backup_ok=True, backup_has_v4=True,
                               masquerade_backup="1")
        fx.run_engine()
        fx.fail_tool("iptables")
        result = fx.run_engine("rollback")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((fx.run / "natowned").exists())
        self.assertTrue((fx.run / "rollback_pending").exists())

    def test_failed_watchdog_rollback_retries_before_reprobing(self):
        fx = self.make_fixture(primary_ok=False, backup_ok=True, backup_has_v4=True,
                               masquerade_backup="1")
        fx.run_engine()
        fx.set_now(1700000020)
        fx.set_backup_internet(False)
        (fx.root / "state" / "fail_primary4").touch()
        result = fx.run_engine()
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((fx.run / "rollback_pending").exists())
        self.assertTrue((fx.root / "state" / "nat_eth0").exists())
        (fx.root / "state" / "fail_primary4").unlink()
        fx.reset_log()
        self.assertEqual(fx.run_engine().returncode, 0)
        self.assertEqual(fx.state()[0], "primary")
        self.assertNotIn("curl ", fx.cmd_log)
        self.assertTrue(fx.backup_dead())

    def test_probe_source_change_cleans_the_old_source_rule(self):
        fx = self.make_fixture(primary_ok=True, backup_ok=True, backup_has_v4=True)
        fx.run_engine()
        (fx.root / "state" / "addr4_eth0").write_text(
            "2: eth0 inet 198.51.100.7/24 brd 198.51.100.255 scope global eth0\n"
        )
        fx.reset_log()
        fx.run_engine()
        self.assertIn("rule del pref 2998 from 198.51.100.2/32 to 223.5.5.5/32 lookup 251", fx.cmd_log)
        self.assertIn("rule add pref 2998 from 198.51.100.7/32 to 223.5.5.5/32 lookup 251", fx.cmd_log)

    def test_default_route_device_substring_does_not_verify_wrong_device(self):
        fx = self.make_fixture()
        (fx.root / "state" / "default4").write_text(
            "default via 192.0.2.1 dev br-lan-extra proto static\n"
        )
        status = json.loads(fx.status())
        self.assertEqual(status["current"], "unknown")

    def test_watchdog_late_tick_uses_no_additional_https_budget(self):
        fx = self.make_fixture(primary_ok=False, backup_ok=True, backup_has_v4=True,
                               masquerade_backup="1")
        fx.run_engine()
        fx.set_now(1700000025)
        (fx.root / "state" / "slow_curl").touch()
        fx.reset_log()
        self.assertEqual(fx.run_engine().returncode, 0)
        self.assertEqual(fx.state()[0], "primary")
        self.assertEqual(int((fx.root / "state" / "now").read_text()), 1700000025)
        self.assertNotIn("curl ", fx.cmd_log)

    def test_primary_default_selected_before_high_metric_backup(self):
        fx = self.make_fixture()
        (fx.root / "state" / "default4").write_text(
            "default via 198.51.100.1 dev eth0 metric 600\n"
            "default via 192.0.2.1 dev br-lan metric 10\n"
        )
        self.assertEqual(json.loads(fx.status())["current"], "primary")

    def test_verified_switch_runs_optional_hook_without_holding_loop(self):
        baseline = self.make_fixture(primary_ok=False, backup_ok=True, backup_has_v4=True)
        baseline_start = time.monotonic()
        self.assertEqual(baseline.run_engine().returncode, 0)
        baseline_elapsed = time.monotonic() - baseline_start

        fx = self.make_fixture(primary_ok=False, backup_ok=True, backup_has_v4=True)
        hook = fx.root / "bin" / "refresh"
        events = fx.root / "hook-events"
        hook.write_text(f'#!/bin/sh\nprintf "%s %s\\n" "$1" "$2" >> "{events}"\nsleep 1\n')
        hook.chmod(0o755)
        fx.set_config(post_switch_hook=str(hook))
        start = time.monotonic()
        result = fx.run_engine()
        self.assertEqual(result.returncode, 0, result.stderr)
        # Compare with the same fixture's shell/startup cost. The hook's one
        # second sleep must not be added to the foreground engine invocation.
        self.assertLess(time.monotonic() - start, baseline_elapsed + 1)
        for _ in range(500):
            if events.exists():
                break
            time.sleep(0.01)
        self.assertTrue(events.exists(), "verified transition did not invoke the hook")
        self.assertEqual(events.read_text().strip(), "backup primary")
        fx.run_engine()
        self.assertEqual(events.read_text().splitlines(), ["backup primary"])

    def test_failed_switch_does_not_run_success_hook(self):
        fx = self.make_fixture(primary_ok=False, backup_ok=True, backup_has_v4=True)
        events = fx.root / "hook-events"
        hook = fx.root / "bin" / "refresh"
        hook.write_text(f'#!/bin/sh\nprintf called > "{events}"\n')
        hook.chmod(0o755)
        fx.set_config(post_switch_hook=str(hook))
        (fx.root / "state" / "fail_prefix").write_text("default")
        self.assertNotEqual(fx.run_engine().returncode, 0)
        self.assertFalse(events.exists())


if __name__ == "__main__":
    unittest.main()
