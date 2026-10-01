import re
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "package" / "arkbridge-usb"
FILES = PACKAGE / "files"
CLI = FILES / "usr" / "sbin" / "usb-uplink"
CLIENT_PATH = FILES / "usr" / "libexec" / "usb-uplink-client-path"
README = ROOT / "README.md"


class PackageContractTests(unittest.TestCase):
    def test_package_layout_is_complete(self):
        required = (
            ROOT / "Makefile",
            PACKAGE / "Makefile",
            FILES / "etc" / "config" / "usb_uplink",
            FILES / "etc" / "init.d" / "usb-uplink",
            FILES / "etc" / "hotplug.d" / "net" / "90-usb-uplink",
            FILES / "etc" / "hotplug.d" / "usb" / "90-usb-uplink-mode-switch",
            FILES / "usr" / "sbin" / "usb-uplink",
            FILES / "usr" / "libexec" / "usb-uplinkd",
            FILES / "usr" / "libexec" / "usb-uplink-failoverd",
            FILES / "usr" / "libexec" / "usb-uplink-modeswitch",
            FILES / "usr" / "libexec" / "usb-uplink-client-path",
        )
        missing = [str(path.relative_to(ROOT)) for path in required if not path.is_file()]
        self.assertEqual(missing, [])

    def test_shell_files_parse(self):
        scripts = (
            FILES / "etc" / "init.d" / "usb-uplink",
            FILES / "etc" / "hotplug.d" / "net" / "90-usb-uplink",
            FILES / "etc" / "hotplug.d" / "usb" / "90-usb-uplink-mode-switch",
            FILES / "usr" / "sbin" / "usb-uplink",
            FILES / "usr" / "libexec" / "usb-uplinkd",
            FILES / "usr" / "libexec" / "usb-uplink-failoverd",
            FILES / "usr" / "libexec" / "usb-uplink-modeswitch",
            FILES / "usr" / "libexec" / "usb-uplink-client-path",
        )
        for script in scripts:
            with self.subTest(script=script):
                result = subprocess.run(
                    ["sh", "-n", str(script)],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_default_config_is_standby_and_not_lan_bridge(self):
        config = (FILES / "etc" / "config" / "usb_uplink").read_text()
        self.assertRegex(config, r"option mode ['\"]standby['\"]")
        self.assertRegex(config, r"option failover_enabled ['\"]0['\"]")
        self.assertRegex(config, r"option mode_switch_enabled ['\"]0['\"]")
        self.assertNotRegex(config, r"option (device|ifname) ['\"](?:eth0|usb0|br-lan)['\"]")
        self.assertNotIn("192.0.2.1", config)

    def test_default_huawei_identity_matches_observed_e6878(self):
        config = (FILES / "etc" / "config" / "usb_uplink").read_text()
        daemon = (FILES / "usr" / "libexec" / "usb-uplinkd").read_text()
        readme = README.read_text()
        self.assertIn("option allowed_ids '12d1:14db'", config)
        self.assertIn("ALLOWED_IDS=12d1:14db", daemon)
        self.assertIn("get_option allowed_ids 12d1:14db", daemon)
        self.assertIn("`12d1:14db`", readme)

    def test_package_declares_usb_mode_switch_dependency(self):
        makefile = (PACKAGE / "Makefile").read_text()
        self.assertIn("+usb-modeswitch", makefile)

    def test_arkbridge_declares_python_for_interface_detection(self):
        makefile = (ROOT / "package" / "arkbridge" / "Makefile").read_text()
        detector = (ROOT / "package" / "arkbridge" / "files" / "usr" / "libexec" / "arkbridge-detect").read_text()
        self.assertIn("+python3", makefile)
        self.assertIn("python3", detector)

    def test_static_packages_disable_source_build(self):
        for package_name in ("arkbridge", "arkbridge-usb"):
            makefile = (ROOT / "package" / package_name / "Makefile").read_text()
            with self.subTest(package=package_name):
                self.assertIn("define Build/Compile", makefile)
                self.assertIn("endef", makefile)

    def test_arkbridge_runtime_state_is_root_only_not_world_writable_tmp(self):
        script = (ROOT / "package" / "arkbridge" / "files" / "usr" / "libexec" / "arkbridge").read_text()
        # State, ownership marks, NAT marks and log must not live in /tmp,
        # where any local user could forge them to steer privileged cleanup.
        self.assertNotIn("/tmp/arkbridge.state", script)
        self.assertNotIn("/tmp/arkbridge.owned", script)
        self.assertNotIn("/tmp/arkbridge.natowned", script)
        self.assertIn("/var/run/arkbridge", script)

    def test_arkbridge_validates_nat_device_before_removing_masquerade(self):
        script = (ROOT / "package" / "arkbridge" / "files" / "usr" / "libexec" / "arkbridge").read_text()
        # The device read back from the NAT mark must be validated as a real
        # interface name before it is passed to iptables -D.
        self.assertIn("valid_ifname", script)

    def test_arkbridge_authenticates_runtime_directory_ownership(self):
        script = (ROOT / "package" / "arkbridge" / "files" / "usr" / "libexec" / "arkbridge").read_text()
        # The run dir on tmpfs may be pre-created by a local user; it must be
        # verified root-owned and not a symlink, and forced to root ownership.
        self.assertIn('-L "$RUNDIR"', script)
        self.assertIn('"$STAT_BIN" -c', script)
        self.assertIn('"$CHOWN_BIN" 0:0', script)
        # The tools must default to the real commands.
        self.assertIn("STAT_BIN=${ARKBRIDGE_STAT_BIN:-stat}", script)
        self.assertIn("CHOWN_BIN=${ARKBRIDGE_CHOWN_BIN:-chown}", script)

    def test_usb_uplink_daemons_authenticate_runtime_directory_ownership(self):
        for name in ("usb-uplinkd", "usb-uplink-failoverd"):
            script = (
                ROOT / "package" / "arkbridge-usb" / "files" / "usr" / "libexec" / name
            ).read_text()
            with self.subTest(daemon=name):
                # Same tmpfs-hardening contract as arkbridge: the shared runtime
                # dir must be verified root-owned / not a symlink before use.
                self.assertIn("ensure_runtime_dir", script)
                self.assertIn('stat -c', script)
                self.assertIn('chown 0:0', script)

    def test_mode_switch_hotplug_is_usb_only_and_non_routing(self):
        hotplug = (
            FILES / "etc" / "hotplug.d" / "usb" / "90-usb-uplink-mode-switch"
        ).read_text()
        self.assertIn('[ "$SUBSYSTEM" = usb ] || exit 0', hotplug)
        self.assertIn("usb-uplink-modeswitch", hotplug)
        self.assertNotIn("uci commit", hotplug)

    def test_daemon_uses_dynamic_netifd_and_rejects_bridge(self):
        daemon = (FILES / "usr" / "libexec" / "usb-uplinkd").read_text()
        self.assertIn("network add_dynamic", daemon)
        self.assertIn("br-lan", daemon)
        self.assertIn("/sys/class/net", daemon)
        self.assertIn("cdc_ether", daemon)
        self.assertIn("defaultroute", daemon)
        self.assertIn("metric", daemon)

    def test_daemon_revalidates_selected_usb_identity_before_attachment(self):
        daemon = (FILES / "usr" / "libexec" / "usb-uplinkd").read_text()
        self.assertNotIn(
            'inspect_netdev "$MATCH_NETDEV" >/dev/null 2>&1',
            daemon,
        )
        self.assertIn(
            'inspect_netdev "$MATCH_NETDEV" || return 1',
            daemon,
        )

    def test_init_stop_and_reload_attempt_owned_cleanup(self):
        init = (FILES / "etc" / "init.d" / "usb-uplink").read_text()
        before_kill = init.split("stop_service() {", 1)[1].split("\n}", 1)[0]
        self.assertIn("service_stopped() {", init)
        after_kill = init.split("service_stopped() {", 1)[1].split("\n}", 1)[0]
        self.assertNotIn("service_stop", before_kill)
        self.assertNotIn("--rollback", before_kill)
        self.assertIn('wait_for_worker_stop "$FAILOVER_PROG"', after_kill)
        self.assertIn('wait_for_worker_stop "$PROG"', after_kill)
        self.assertLess(
            after_kill.index('wait_for_worker_stop "$PROG"'),
            min(
                after_kill.index('"$FAILOVER_PROG" --rollback'),
                after_kill.index('"$PROG" --rollback'),
            ),
        )
        self.assertIn("stop || return 1", init)

    def test_init_waits_for_procd_kill_before_owned_cleanup(self):
        init = FILES / "etc" / "init.d" / "usb-uplink"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "operations.log"
            daemon = root / "usb-uplinkd"
            failover = root / "usb-uplink-failoverd"
            for worker, name in ((daemon, "daemon"), (failover, "failover")):
                worker.write_text(
                    "#!/bin/sh\n"
                    f"printf 'rollback:{name}\\n' >> {shlex.quote(str(log))}\n"
                )
                worker.chmod(0o755)
            pidof = root / "pidof"
            pidof.write_text("#!/bin/sh\nexit 1\n")
            pidof.chmod(0o755)

            harness = (
                "service_stop() {\n"
                f"    printf 'legacy-stop\\n' >> {shlex.quote(str(log))}\n"
                "    return 1\n"
                "}\n"
                "procd_kill() {\n"
                f"    printf 'procd-kill\\n' >> {shlex.quote(str(log))}\n"
                "}\n"
                "logger() { :; }\n"
                f"USB_UPLINK_PIDOF_BIN={shlex.quote(str(pidof))}\n"
                f". {shlex.quote(str(init))}\n"
                f"PROG={shlex.quote(str(daemon))}\n"
                f"FAILOVER_PROG={shlex.quote(str(failover))}\n"
                "stop_service\n"
                "procd_kill usb-uplink\n"
                "service_stopped\n"
            )
            result = subprocess.run(["sh", "-c", harness], capture_output=True, text=True)

            self.assertNotIn("not found", result.stderr)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                log.read_text().splitlines(),
                ["procd-kill", "rollback:failover", "rollback:daemon"],
            )

    def test_init_refuses_cleanup_when_worker_survives_procd_kill(self):
        init = FILES / "etc" / "init.d" / "usb-uplink"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "operations.log"
            pidof = root / "pidof"
            pidof.write_text("#!/bin/sh\n[ \"$1\" = usb-uplink-failoverd ]\n")
            pidof.chmod(0o755)
            sleeper = root / "sleep"
            sleeper.write_text("#!/bin/sh\nexit 0\n")
            sleeper.chmod(0o755)
            harness = (
                "service_stop() { :; }\n"
                "logger() { :; }\n"
                f"USB_UPLINK_PIDOF_BIN={shlex.quote(str(pidof))}\n"
                f"USB_UPLINK_SLEEP_BIN={shlex.quote(str(sleeper))}\n"
                "USB_UPLINK_STOP_WAIT_ATTEMPTS=1\n"
                f". {shlex.quote(str(init))}\n"
                f"PROG={shlex.quote(str(root / 'usb-uplinkd'))}\n"
                f"FAILOVER_PROG={shlex.quote(str(root / 'usb-uplink-failoverd'))}\n"
                "procd_kill() {\n"
                f"    printf 'procd-kill\\n' >> {shlex.quote(str(log))}\n"
                "}\n"
                "stop_service\n"
                "procd_kill usb-uplink\n"
                "service_stopped\n"
            )
            result = subprocess.run(["sh", "-c", harness], capture_output=True, text=True)

            self.assertNotIn("not found", result.stderr)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(log.read_text().splitlines(), ["procd-kill"])

    def test_reload_uses_procd_stop_and_start_wrappers(self):
        init = FILES / "etc" / "init.d" / "usb-uplink"
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "operations.log"
            harness = (
                "service_stop() { :; }\n"
                "logger() { :; }\n"
                f". {shlex.quote(str(init))}\n"
                "stop() {\n"
                f"    printf 'stop\\n' >> {shlex.quote(str(log))}\n"
                "    return 1\n"
                "}\n"
                "start() {\n"
                f"    printf 'start\\n' >> {shlex.quote(str(log))}\n"
                "}\n"
                "reload_service\n"
            )
            result = subprocess.run(["sh", "-c", harness], capture_output=True, text=True)

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(log.read_text().splitlines() if log.exists() else [], ["stop"])

    def test_restart_does_not_start_after_failed_stop(self):
        init = FILES / "etc" / "init.d" / "usb-uplink"
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "operations.log"
            harness = (
                f". {shlex.quote(str(init))}\n"
                "stop() {\n"
                f"    printf 'stop\\n' >> {shlex.quote(str(log))}\n"
                "    return 1\n"
                "}\n"
                "start() {\n"
                f"    printf 'start\\n' >> {shlex.quote(str(log))}\n"
                "}\n"
                "restart\n"
            )
            result = subprocess.run(["sh", "-c", harness], capture_output=True, text=True)

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(log.read_text().splitlines() if log.exists() else [], ["stop"])

    def test_init_skips_usb_rollback_when_failover_rollback_fails(self):
        init = FILES / "etc" / "init.d" / "usb-uplink"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "operations.log"
            daemon = root / "usb-uplinkd"
            failover = root / "usb-uplink-failoverd"
            for worker, name in ((daemon, "daemon"), (failover, "failover")):
                worker.write_text(
                    "#!/bin/sh\n"
                    f"printf 'rollback:{name}:%s\\n' \"$*\" >> {shlex.quote(str(log))}\n"
                    f"{'exit 1' if name == 'failover' else 'exit 0'}\n"
                )
                worker.chmod(0o755)
            pidof = root / "pidof"
            pidof.write_text("#!/bin/sh\nexit 1\n")
            pidof.chmod(0o755)

            harness = (
                "logger() { :; }\n"
                f"USB_UPLINK_PIDOF_BIN={shlex.quote(str(pidof))}\n"
                f". {shlex.quote(str(init))}\n"
                f"PROG={shlex.quote(str(daemon))}\n"
                f"FAILOVER_PROG={shlex.quote(str(failover))}\n"
                "stop_service\n"
                f"printf 'procd-kill\\n' >> {shlex.quote(str(log))}\n"
                "service_stopped\n"
            )
            result = subprocess.run(
                ["sh", "-c", harness],
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(
                log.read_text().splitlines(),
                ["procd-kill", "rollback:failover:--rollback"],
            )

    def test_init_skips_rollback_when_daemon_remains_running(self):
        init = FILES / "etc" / "init.d" / "usb-uplink"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "operations.log"
            daemon = root / "usb-uplinkd"
            failover = root / "usb-uplink-failoverd"
            pidof = root / "pidof"
            sleep = root / "sleep"
            for worker, name in ((daemon, "daemon"), (failover, "failover")):
                worker.write_text(
                    "#!/bin/sh\n"
                    f"printf 'rollback:{name}:%s\\n' \"$*\" >> {log}\n"
                )
                worker.chmod(0o755)
            pidof.write_text(
                "#!/bin/sh\n"
                "[ \"$1\" = usb-uplinkd ]\n"
            )
            pidof.chmod(0o755)
            sleep.write_text("#!/bin/sh\nexit 0\n")
            sleep.chmod(0o755)

            harness = (
                "logger() { :; }\n"
                f"USB_UPLINK_PIDOF_BIN={shlex.quote(str(pidof))}\n"
                f"USB_UPLINK_SLEEP_BIN={shlex.quote(str(sleep))}\n"
                "USB_UPLINK_STOP_WAIT_ATTEMPTS=1\n"
                f". {shlex.quote(str(init))}\n"
                f"PROG={shlex.quote(str(daemon))}\n"
                f"FAILOVER_PROG={shlex.quote(str(failover))}\n"
                "stop_service\n"
                f"printf 'procd-kill\\n' >> {shlex.quote(str(log))}\n"
                "service_stopped\n"
            )
            result = subprocess.run(
                ["sh", "-c", harness],
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(
                log.read_text().splitlines(),
                ["procd-kill"],
            )

    def test_usb_daemon_takes_rollback_lock_before_reading_state(self):
        daemon = (FILES / "usr" / "libexec" / "usb-uplinkd").read_text()
        rollback = daemon.split("\t--rollback)", 1)[1].split("\t\t;;", 1)[0]
        self.assertLess(
            rollback.index("acquire_lock"),
            rollback.index("load_state"),
        )

    def test_failover_daemon_takes_rollback_lock_before_reading_state(self):
        failover = (FILES / "usr" / "libexec" / "usb-uplink-failoverd").read_text()
        rollback = failover.split("\t\t--rollback)", 1)[1].split("\t\t;;", 1)[0]
        self.assertLess(
            rollback.index("acquire_lock"),
            rollback.index("load_state"),
        )

    def test_failover_cli_is_explicit_and_rolls_back_before_standby(self):
        cli = CLI.read_text()
        self.assertIn("set-mode failover --confirm", cli)
        self.assertIn("check-ready", cli)
        self.assertIn('"$FAILOVER_DAEMON" --check-ready', cli)
        self.assertIn('"$FAILOVER_DAEMON" --rollback', cli)
        self.assertIn('"$DAEMON" --rollback', cli)
        self.assertIn("set_config_value route_control_enabled 1", cli)
        self.assertIn("set_config_value route_control_enabled 0", cli)

    def test_cli_standby_rolls_back_both_owned_layers_before_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "operations.log"
            scripts = {}
            script_bodies = {
                "daemon": (
                    "#!/bin/sh\n"
                    f"printf '%s %s\\n' daemon \"$*\" >> {log}\n"
                    "exit 0\n"
                ),
                "failover": (
                    "#!/bin/sh\n"
                    f"printf '%s %s\\n' failover \"$*\" >> {log}\n"
                    "exit 0\n"
                ),
                "uci": (
                    "#!/bin/sh\n"
                    f"printf '%s %s\\n' uci \"$*\" >> {log}\n"
                    "exit 0\n"
                ),
                "init": (
                    "#!/bin/sh\n"
                    f"printf '%s %s\\n' init \"$*\" >> {log}\n"
                    "exit 0\n"
                ),
            }
            for name, body in script_bodies.items():
                path = root / name
                path.write_text(body)
                path.chmod(0o755)
                scripts[name] = path

            env = {
                **__import__("os").environ,
                "USB_UPLINK_DAEMON": str(scripts["daemon"]),
                "USB_UPLINK_FAILOVER_DAEMON": str(scripts["failover"]),
                "USB_UPLINK_CLI_UCI_BIN": str(scripts["uci"]),
                "USB_UPLINK_INIT_SCRIPT": str(scripts["init"]),
            }
            result = subprocess.run(
                [str(CLI), "set-mode", "standby"],
                capture_output=True,
                text=True,
                env=env,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            operations = log.read_text().splitlines()
            self.assertLess(
                operations.index("failover --rollback"),
                operations.index("uci set usb_uplink.main.mode=standby"),
            )
            self.assertLess(
                operations.index("daemon --rollback"),
                operations.index("uci set usb_uplink.main.mode=standby"),
            )
            self.assertIn("uci commit usb_uplink", operations)
            self.assertIn("init reload", operations)

    def test_cli_reports_reload_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "operations.log"
            scripts = {}
            script_bodies = {
                "daemon": "#!/bin/sh\nexit 0\n",
                "failover": "#!/bin/sh\nexit 0\n",
                "uci": f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> {log}\nexit 0\n",
                "init": "#!/bin/sh\nexit 1\n",
            }
            for name, body in script_bodies.items():
                path = root / name
                path.write_text(body)
                path.chmod(0o755)
                scripts[name] = path
            env = {
                **__import__("os").environ,
                "USB_UPLINK_DAEMON": str(scripts["daemon"]),
                "USB_UPLINK_FAILOVER_DAEMON": str(scripts["failover"]),
                "USB_UPLINK_CLI_UCI_BIN": str(scripts["uci"]),
                "USB_UPLINK_INIT_SCRIPT": str(scripts["init"]),
            }

            result = subprocess.run(
                [str(CLI), "set-mode", "standby"],
                capture_output=True,
                text=True,
                env=env,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("reload failed", result.stderr)

    def test_cli_reverts_staged_config_after_uci_set_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "operations.log"
            recovery = root / "cli-recovery"
            scripts = {}
            script_bodies = {
                "daemon": "#!/bin/sh\nexit 0\n",
                "failover": "#!/bin/sh\nexit 0\n",
                "init": "#!/bin/sh\nexit 0\n",
                "uci": (
                    "#!/bin/sh\n"
                    f"printf '%s\\n' \"$*\" >> {log}\n"
                    "case \"$*\" in\n"
                    "  *' changes usb_uplink'*) exit 0 ;;\n"
                    "  *' get usb_uplink.main.mode'*) printf failover ;;\n"
                    "  *' get usb_uplink.main.failover_enabled'*) printf 1 ;;\n"
                    "  *' get usb_uplink.main.route_control_enabled'*) printf 1 ;;\n"
                    "  *' get usb_uplink.main.defaultroute'*) printf 1 ;;\n"
                    "  *' get usb_uplink.main.peerdns'*) printf 0 ;;\n"
                    "  'set usb_uplink.main.route_control_enabled=0') exit 1 ;;\n"
                    "  'revert usb_uplink'*) touch "
                    + str(root / "reverted")
                    + " ; exit 0 ;;\n"
                    "  *) exit 0 ;;\n"
                    "esac\n"
                ),
            }
            for name, body in script_bodies.items():
                path = root / name
                path.write_text(body)
                path.chmod(0o755)
                scripts[name] = path
            env = {
                **__import__("os").environ,
                "USB_UPLINK_DAEMON": str(scripts["daemon"]),
                "USB_UPLINK_FAILOVER_DAEMON": str(scripts["failover"]),
                "USB_UPLINK_CLI_UCI_BIN": str(scripts["uci"]),
                "USB_UPLINK_INIT_SCRIPT": str(scripts["init"]),
                "USB_UPLINK_CLI_RECOVERY_FILE": str(recovery),
            }

            result = subprocess.run(
                [str(CLI), "set-mode", "standby"],
                capture_output=True,
                text=True,
                env=env,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertTrue((root / "reverted").exists())
            self.assertTrue(recovery.exists())
            self.assertIn("uci set failed", recovery.read_text())

    def test_cli_requires_confirmation_before_failover(self):
        result = subprocess.run(
            [str(CLI), "set-mode", "failover"], capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("--confirm", result.stderr)

    def test_cli_enables_route_control_only_after_prepared_readiness(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "operations.log"
            scripts = {}
            script_bodies = {
                "daemon": (
                    "#!/bin/sh\n"
                    f"printf 'daemon %s\\n' \"$*\" >> {log}\n"
                    "exit 0\n"
                ),
                "failover": (
                    "#!/bin/sh\n"
                    f"printf 'failover %s\\n' \"$*\" >> {log}\n"
                    "exit 0\n"
                ),
                "init": (
                    "#!/bin/sh\n"
                    f"printf 'init %s\\n' \"$*\" >> {log}\n"
                    "exit 0\n"
                ),
                "uci": (
                    "#!/bin/sh\n"
                    f"printf 'uci %s\\n' \"$*\" >> {log}\n"
                    "case \"$*\" in\n"
                    "  '-q changes usb_uplink') exit 0 ;;\n"
                    "  '-q get usb_uplink.main.mode') printf standby ;;\n"
                    "  '-q get usb_uplink.main.failover_enabled') printf 0 ;;\n"
                    "  '-q get usb_uplink.main.route_control_enabled') printf 0 ;;\n"
                    "  '-q get usb_uplink.main.defaultroute') printf 0 ;;\n"
                    "  '-q get usb_uplink.main.peerdns') printf 0 ;;\n"
                    "  *) exit 0 ;;\n"
                    "esac\n"
                ),
            }
            for name, body in script_bodies.items():
                path = root / name
                path.write_text(body)
                path.chmod(0o755)
                scripts[name] = path
            env = {
                **__import__("os").environ,
                "USB_UPLINK_DAEMON": str(scripts["daemon"]),
                "USB_UPLINK_FAILOVER_DAEMON": str(scripts["failover"]),
                "USB_UPLINK_CLI_UCI_BIN": str(scripts["uci"]),
                "USB_UPLINK_INIT_SCRIPT": str(scripts["init"]),
                "USB_UPLINK_CLI_RECOVERY_FILE": str(root / "recovery"),
            }

            result = subprocess.run(
                [str(CLI), "set-mode", "failover", "--confirm"],
                capture_output=True,
                text=True,
                env=env,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            operations = log.read_text().splitlines()
            readiness = operations.index("failover --check-ready")
            route_control = operations.index(
                "uci set usb_uplink.main.route_control_enabled=1"
            )
            first_reload = operations.index("init reload")
            self.assertLess(first_reload, readiness)
            self.assertLess(readiness, route_control)
            self.assertEqual(operations.count("init reload"), 2)
            self.assertIn(
                "uci set usb_uplink.main.route_control_enabled=0",
                operations[:readiness],
            )

    def test_failover_package_declares_userspace_route_and_probe_tools(self):
        makefile = (PACKAGE / "Makefile").read_text()
        failover = (FILES / "usr" / "libexec" / "usb-uplink-failoverd").read_text()
        self.assertIn("+ip-full", makefile)
        self.assertNotIn("+curl", makefile)
        self.assertIn("--ipv4", failover)

    def test_cli_exposes_read_only_client_path_check(self):
        cli = CLI.read_text()
        self.assertIn("client-path-check", cli)
        self.assertIn('"$CLIENT_PATH_CHECKER"', cli)
        self.assertIn("USB_UPLINK_CLIENT_PATH_CHECKER", cli)

    def test_client_path_checker_is_read_only_and_fail_closed(self):
        checker = CLIENT_PATH.read_text()
        # No live mutation verbs: it must never touch UCI, routes, or services.
        self.assertNotIn("uci set", checker)
        self.assertNotIn("uci commit", checker)
        self.assertNotIn("uci delete", checker)
        self.assertNotIn("route add default", checker)
        self.assertNotIn("route replace default", checker)
        self.assertNotIn("route del default", checker)
        self.assertNotIn("/etc/init.d", checker)
        self.assertIn("mode=read-only", checker)
        self.assertIn("not client connectivity proof", checker)

    def test_client_path_checker_covers_all_review_gates(self):
        checker = CLIENT_PATH.read_text()
        for gate in ("S0", "S1", "S2", "S3"):
            with self.subTest(gate=gate):
                self.assertIn(f"emit {gate} ", checker)
        for term in ("masquerade", "forwarding", "dhcp", "recovery"):
            with self.subTest(term=term):
                self.assertIn(term, checker.lower())

    def test_client_path_checker_supports_fixture_override(self):
        checker = CLIENT_PATH.read_text()
        self.assertIn("USB_UPLINK_CLIENT_PATH_FIXTURE", checker)
        self.assertIn("USB_UPLINK_TEST_MODE", checker)

    def test_package_installs_client_path_checker(self):
        makefile = (PACKAGE / "Makefile").read_text()
        self.assertIn("usb-uplink-client-path", makefile)
        self.assertIn("/usr/libexec", makefile)

    def test_readme_points_to_the_technical_docs(self):
        # The README is intentionally short (product positioning); the detailed
        # client-path / probe material lives in the docs.
        readme = README.read_text()
        self.assertIn("docs/arkbridge.md", readme)
        self.assertIn("docs/devices.md", readme)

    def test_readme_is_product_positioning_not_a_tech_dump(self):
        readme = README.read_text()
        # Positioning and features, bilingual, and links to the detailed docs.
        self.assertIn("ArkBridge", readme)
        self.assertIn("繁體中文", readme)
        self.assertIn("docs/arkbridge.md", readme)
        self.assertNotIn("192.168.", readme)

    def test_public_device_list_does_not_disclose_site_router_label(self):
        devices = (ROOT / "docs" / "devices.md").read_text()
        self.assertNotIn("ER01", devices)

    def test_public_tree_excludes_internal_deployment_plans(self):
        internal_paths = (
            ROOT / "docs" / "plan-c-wired-client-backup.md",
            ROOT / "docs" / "superpowers" / "plans" / "2026-09-23-plan-a-controller.md",
            ROOT / "docs" / "superpowers" / "plans" / "2026-09-23-plan-a-audit-remediation.md",
        )
        self.assertEqual([str(path.relative_to(ROOT)) for path in internal_paths if path.exists()], [])

    def test_public_tree_excludes_legacy_package_paths_and_site_hardware_note(self):
        legacy_paths = (
            ROOT / "package" / "luci-app-side-router-failover",
            ROOT / "package" / "side-router-failover",
            ROOT / "docs" / "tested-hardware-huawei-e6878.md",
        )
        self.assertEqual([str(path.relative_to(ROOT)) for path in legacy_paths if path.exists()], [])

    def test_public_source_has_no_site_product_names(self):
        forbidden = ("ER01", "BE7000", "plan-d-er01", "tested-hardware-huawei")
        findings = []
        for path in ROOT.rglob("*"):
            if (
                not path.is_file()
                or ".git" in path.parts
                or "__pycache__" in path.parts
                or "tests" in path.parts
            ):
                continue
            try:
                text = path.read_text()
            except UnicodeDecodeError:
                continue
            if any(term in text for term in forbidden):
                findings.append(str(path.relative_to(ROOT)))
        self.assertEqual(findings, [])

    def test_release_workflow_copies_packages_into_sdk_package_directories(self):
        workflow = (ROOT / ".github" / "workflows" / "build.yml").read_text()
        self.assertIn('cp -r "$GITHUB_WORKSPACE/package/arkbridge/." package/arkbridge/', workflow)
        self.assertIn(
            'cp -r "$GITHUB_WORKSPACE/package/luci-app-arkbridge/." package/luci-app-arkbridge/',
            workflow,
        )
        self.assertIn(
            'cp -r "$GITHUB_WORKSPACE/package/arkbridge-usb/." package/arkbridge-usb/',
            workflow,
        )
        self.assertNotIn('cp -r "$GITHUB_WORKSPACE/package/arkbridge" ./package/', workflow)
        self.assertIn("CONFIG_PACKAGE_usb-uplink=m", workflow)
        self.assertNotIn("CONFIG_PACKAGE_arkbridge-usb=m", workflow)
        self.assertIn("-o -name 'usb-uplink*.ipk'", workflow)
        self.assertIn("mkdir -p sdk", workflow)
        self.assertIn("find . -maxdepth 1 -type d -name 'openwrt-sdk-*'", workflow)
        self.assertIn('mv "$SDK_ROOT"/* sdk/', workflow)
        self.assertIn('rmdir "$SDK_ROOT"', workflow)
        # The SDK toolchain must be integrity-checked against the signed
        # sha256sums published in the same directory before it is extracted.
        self.assertIn("sha256sums", workflow)
        self.assertIn("sha256sum -c", workflow)
        # Build only the three ArkBridge packages, never the whole SDK world.
        self.assertIn("package/arkbridge/compile", workflow)
        self.assertIn("package/luci-app-arkbridge/compile", workflow)
        self.assertIn("package/arkbridge-usb/compile", workflow)
        self.assertNotIn('make -j"$(nproc)" V=s 2>&1', workflow)
        self.assertGreaterEqual(workflow.count("set -euo pipefail"), 3)
        self.assertIn('for package in arkbridge luci-app-arkbridge usb-uplink; do', workflow)
        # Per-package checks must match the arch-prefixed artifact names.
        self.assertIn('find out -name "*_${package}_*.ipk"', workflow)
        self.assertIn('find release -name "*_${package}_*.ipk"', workflow)
        self.assertIn('needs: [build, tests]', workflow)
        self.assertIn('test -n "$(find release -name "*_${package}_*.ipk" -print -quit)"', workflow)
        self.assertIn("publish:", workflow)
        self.assertIn("if: ${{ github.event_name != 'workflow_dispatch' || inputs.publish }}", workflow)

    def test_release_artifacts_are_arch_scoped_to_avoid_collisions(self):
        workflow = (ROOT / ".github" / "workflows" / "build.yml").read_text()
        # The LuCI panel is arch=all, so all three SDK builds emit an
        # identically-named ipk. Flattening them into one directory would
        # silently overwrite and ship only one copy. Namespace by architecture.
        # Every uploaded ipk name is prefixed with its architecture.
        self.assertIn('cp "$f" "out/${{ matrix.sdk_arch }}_$(basename "$f")"', workflow)
        # The LuCI panel is arch=all; without the prefix all three SDK builds
        # would produce the same filename and overwrite each other.
        self.assertNotIn("luci-app-arkbridge_all.ipk' -exec cp {} release/", workflow)

    def test_release_write_permission_is_scoped_to_the_publish_job(self):
        workflow = (ROOT / ".github" / "workflows" / "build.yml").read_text()
        # Workflow default is read-only; only the publish job elevates.
        self.assertIn("permissions:\n  contents: read\n", workflow)
        publish_block = workflow.split("\n  publish:")[-1]
        self.assertIn("permissions:\n      contents: write\n", publish_block)

    def test_release_actions_are_pinned_to_commit_shas(self):
        workflow = (ROOT / ".github" / "workflows" / "build.yml").read_text()
        for action in (
            "actions/checkout",
            "actions/setup-python",
            "actions/upload-artifact",
            "actions/download-artifact",
            "softprops/action-gh-release",
        ):
            with self.subTest(action=action):
                self.assertRegex(workflow, rf"{re.escape(action)}@[0-9a-f]{{40}}")

    def test_publish_tag_is_validated_and_does_not_move_existing_tags(self):
        workflow = (ROOT / ".github" / "workflows" / "build.yml").read_text()
        publish = workflow.split("\n  publish:")[-1]
        # Reject tags that are not of the form vX.Y.Z.
        self.assertIn("^v[0-9]+\\.[0-9]+\\.[0-9]+$", publish)
        # Do not clobber an existing release/tag.
        self.assertIn("fail_on_unmatched_files: true", publish)

    def test_publish_binds_the_tag_to_the_built_commit(self):
        workflow = (ROOT / ".github" / "workflows" / "build.yml").read_text()
        publish = workflow.split("\n  publish:")[-1]
        # The published tag must point at the commit that produced the ipk,
        # never at whatever the default branch happens to be at publish time.
        self.assertIn("actions/checkout@", publish)
        self.assertIn("target_commitish:", publish)
        self.assertIn("github.sha", publish)
        # The tag-push trigger must not be self-blocked by the exists-guard:
        # the guard may only run for manual dispatch.
        self.assertIn("EVENT_NAME: ${{ github.event_name }}", publish)
        self.assertIn('[ "$EVENT_NAME" = "workflow_dispatch" ]', publish)

    def test_publish_is_bound_to_the_trusted_branch_and_sha(self):
        workflow = (ROOT / ".github" / "workflows" / "build.yml").read_text()
        publish = workflow.split("\n  publish:")[-1]
        # Manual publish must not be runnable from an arbitrary ref: it must
        # assert the run came from the protected default branch, and refuse to
        # publish anything other than the exact built commit.
        self.assertIn('github.ref', publish)
        self.assertIn('github.sha', publish)
        self.assertIn("refs/heads/arkbridge-main", publish)

    def test_no_sensitive_or_fixed_site_values_in_publishable_tree(self):
        patterns = (
            re.compile(r"192\.168\.\d+\.\d+"),
            re.compile(r"10\.04:[0-9a-f:]{11,}", re.I),
            re.compile(r"(?:password|passwd|authorization|bearer|api[_-]?key)", re.I),
            re.compile(r"BEGIN (?:RSA|OPENSSH|EC|DSA) PRIVATE KEY"),
        )
        candidates = [
            path
            for path in ROOT.rglob("*")
            if path.is_file()
            and ".git" not in path.parts
            and "__pycache__" not in path.parts
            and "tests" not in path.parts
        ]
        findings = []
        for path in candidates:
            try:
                text = path.read_text()
            except UnicodeDecodeError:
                continue
            for pattern in patterns:
                if pattern.search(text):
                    findings.append(str(path.relative_to(ROOT)))
                    break
        self.assertEqual(findings, [])


if __name__ == "__main__":
    unittest.main()
