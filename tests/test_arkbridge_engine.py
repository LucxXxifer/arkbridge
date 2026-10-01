import json
import os
import re
import subprocess
import tempfile
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
          printf 'default via %s dev %s proto %s\n' "$via" "$dev" "${proto:-static}" > "$FX_DIR/default$fam"
        fi
        ;;
      flush) : ;;
    esac
    ;;
  rule) : ;;
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
    [ -f "$FX_DIR/link_$dev" ]
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


class ArkBridgeEngineTests(unittest.TestCase):
    def make_fixture(self, **overrides):
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        (root / "bin").mkdir()
        (root / "run").mkdir()
        (root / "state").mkdir()

        stubs = {
            "ip": STUB_IP, "curl": STUB_CURL, "ping": STUB_PING,
            "uci": STUB_UCI, "date": STUB_DATE, "stat": STUB_STAT,
            "chown": STUB_TRUE, "ip6tables": STUB_TRUE, "iptables": STUB_TRUE,
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
            "default via 192.0.2.1 dev br-lan proto static\n"
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
            "inet 192.0.2.2/24 br-lan\n" if overrides.get("primary_has_v4", True) else ""
        )
        (root / "state" / "addr4_eth0").write_text(
            "inet 198.51.100.2/24 eth0\n" if overrides.get("backup_has_v4", False) else ""
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
        healthy.extend(overrides.get("extra_healthy", []))
        (root / "state" / "healthy").write_text("\n".join(healthy) + "\n")

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


if __name__ == "__main__":
    unittest.main()
