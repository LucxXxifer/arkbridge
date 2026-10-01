import os
import shlex
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FILES = ROOT / "package" / "arkbridge-usb" / "files"
CONTROLLER = FILES / "usr" / "libexec" / "usb-uplink-failoverd"
INIT = FILES / "etc" / "init.d" / "usb-uplink"
CONFIG = FILES / "etc" / "config" / "usb_uplink"


class FailoverPackageContractTests(unittest.TestCase):
    def test_failover_controller_is_installed_and_shell_parses(self):
        self.assertTrue(CONTROLLER.is_file())
        result = subprocess.run(
            ["sh", "-n", str(CONTROLLER)], capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_init_supervises_both_uplink_and_failover_services(self):
        text = INIT.read_text()
        self.assertIn("usb-uplink-failoverd", text)
        self.assertGreaterEqual(text.count("procd_open_instance"), 2)

    def test_failover_policy_has_safe_defaults_and_explicit_thresholds(self):
        text = CONFIG.read_text()
        self.assertIn("option mode 'standby'", text)
        self.assertIn("option failover_enabled '0'", text)
        self.assertIn("option active_metric '10'", text)
        self.assertIn("option standby_metric '500'", text)
        self.assertIn("option failover_rounds '3'", text)
        self.assertIn("option candidate_seconds '60'", text)
        self.assertIn("option failback_seconds '300'", text)
        self.assertNotIn("option primary_gateway", text)
        self.assertNotIn("option backup_gateway", text)


class FailoverFixtureTests(unittest.TestCase):
    def make_fixture(
        self,
        *,
        health="primary=healthy,backup=healthy",
        seed_state=False,
        route_control_enabled="0",
        state_active="primary",
        backup_route_target="0.0.0.0",
        backup_route_mask="0",
        backup_route_metric="600",
        backup_source_mask="24",
        backup_valid="3600",
        backup_leasetime=None,
        backup_kernel_gateway="192.0.2.1",
        backup_kernel_source="192.0.2.2",
        backup_kernel_protocol="static",
        backup_route_layout="default_first",
        backup_default_routes=1,
        app_targets="https://example.invalid/health,https://example.invalid/ready",
        candidate_seconds="0",
        uplink_state=True,
        primary_standby_protocol="dhcp",
        enabled="1",
    ):
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        config = root / "usb_uplink.conf"
        config.write_text(
            "\n".join(
                (
                    f"enabled={enabled}",
                    "mode=failover",
                    "failover_enabled=1",
                    f"route_control_enabled={route_control_enabled}",
                    "primary_device=br-wan",
                    "active_metric=10",
                    "standby_metric=500",
                    "backup_metric=600",
                    "route_protocol=242",
                    "failover_rounds=3",
                    f"candidate_seconds={candidate_seconds}",
                    "failback_seconds=300",
                    "probe_interval=10",
                    "probe_targets=198.51.100.10,198.51.100.11,198.51.100.12",
                    f"app_targets={app_targets}",
                )
            )
            + "\n"
        )
        runtime = root / "run"
        runtime.mkdir()
        sysfs = root / "sys"
        device_root = sysfs / "devices" / "platform" / "usb1" / "1-1"
        device_interface = device_root / "1-1:1.0"
        driver_root = sysfs / "bus" / "usb" / "drivers" / "cdc_ether"
        driver_root.mkdir(parents=True)
        device_interface.mkdir(parents=True)
        (device_root / "idVendor").write_text("12d1\n")
        (device_root / "idProduct").write_text("14db\n")
        (device_interface / "driver").symlink_to(driver_root)
        netdev_root = sysfs / "class" / "net" / "eth7"
        netdev_root.mkdir(parents=True)
        (netdev_root / "device").symlink_to(device_interface)
        device_path = device_interface.resolve()
        if uplink_state:
            (runtime / "state").write_text(
                "created=1\n"
                "netdev=eth7\n"
                "interface=usb_uplink\n"
                "usb_id=12d1:14db\n"
                "driver=cdc_ether\n"
                f"device_path={device_path}\n"
            )
        if seed_state:
            (runtime / "failover-state").write_text(
                f"controller={state_active}\n"
                f"active={state_active}\n"
                "candidate_streak=0\n"
                "primary_healthy_since=-\n"
                "primary_gateway=198.51.100.1\n"
                "primary_device=br-wan\n"
                "primary_source=198.51.100.2\n"
                "primary_protocol=242\n"
                "primary_metric=10\n"
                "original_gateway=198.51.100.1\n"
                "original_device=br-wan\n"
                "original_source=198.51.100.2\n"
                "original_protocol=dhcp\n"
                "original_metric=0\n"
                + (
                    "backup_gateway=192.0.2.1\n"
                    "backup_device=eth7\n"
                    "backup_source=192.0.2.2\n"
                    "backup_protocol=242\n"
                    "backup_valid_lft=3600\n"
                    if state_active == "backup"
                    else "backup_gateway=\n"
                    "backup_device=\n"
                    "backup_source=-\n"
                    "backup_protocol=\n"
                    "backup_valid_lft=\n"
                )
            )
        bin_dir = root / "bin"
        bin_dir.mkdir()
        route_state = root / "route-state"
        if seed_state:
            route_state.write_text(
                "backup-active\n" if state_active == "backup" else "primary-standby\n"
            )
        else:
            route_state.write_text("primary-original\n")
        ip_log = root / "ip.log"
        ip = bin_dir / "ip"
        ip.write_text(
            "#!/bin/sh\n"
            f"printf '%s\\n' \"$*\" >> {ip_log}\n"
            f"state=$(cat {route_state})\n"
            "case \"$*\" in\n"
            "  *'route show default metric 10'*)\n"
            "    if [ \"$state\" = primary-active ] || [ \"$state\" = primary-standby ] || [ \"$state\" = primary-restoring ]; then\n"
            "      printf '%s\\n' 'default via 198.51.100.1 dev br-wan proto 242 src 198.51.100.2 metric 10'\n"
            "    elif [ \"$state\" = backup-active ] || [ \"$state\" = backup-restoring ]; then\n"
            "      printf '%s\\n' 'default via 192.0.2.1 dev eth7 proto 242 src 192.0.2.2 metric 10'\n"
            "    elif [ \"$state\" = unknown-active ]; then\n"
            "      printf '%s\\n' 'default via 203.0.113.1 dev br-other proto static src 203.0.113.2 metric 10'\n"
            "    fi\n"
            "    exit 0;;\n"
            "  *'route show default dev br-wan metric 10'*)\n"
            "    case \"$state\" in\n"
            "      primary-active|primary-standby|primary-restoring)\n"
            "        printf '%s\\n' 'default via 198.51.100.1 dev br-wan proto 242 src 198.51.100.2 metric 10'\n"
            "        ;;\n"
            "    esac\n"
            "    exit 0;;\n"
            "  *'route show default dev br-wan metric 500'*)\n"
            "    case \"$state\" in\n"
            "      primary-standby|backup-active|no-active)\n"
            f"        printf '%s\\n' 'default via 198.51.100.1 dev br-wan proto {primary_standby_protocol} src 198.51.100.2 metric 500'\n"
            "        ;;\n"
            "    esac\n"
            "    exit 0;;\n"
            "  *'route show default dev br-wan metric 0'*)\n"
            "    case \"$state\" in\n"
            "      primary-original|primary-active|primary-restoring|backup-restoring)\n"
            "        printf '%s\\n' 'default via 198.51.100.1 dev br-wan proto dhcp src 198.51.100.2 metric 0'\n"
            "        ;;\n"
            "    esac\n"
            "    exit 0;;\n"
            "  *'route show default dev br-wan'*)\n"
            "    case \"$state\" in\n"
            "      primary-original) printf '%s\\n' 'default via 198.51.100.1 dev br-wan proto dhcp src 198.51.100.2 metric 0' ;;\n"
            "      primary-active|primary-restoring)\n"
            "        printf '%s\\n' 'default via 198.51.100.1 dev br-wan proto dhcp src 198.51.100.2 metric 0'\n"
            "        printf '%s\\n' 'default via 198.51.100.1 dev br-wan proto 242 src 198.51.100.2 metric 10'\n"
            "        ;;\n"
            "      primary-standby)\n"
            "        printf '%s\\n' 'default via 198.51.100.1 dev br-wan proto dhcp src 198.51.100.2 metric 500'\n"
            "        printf '%s\\n' 'default via 198.51.100.1 dev br-wan proto 242 src 198.51.100.2 metric 10'\n"
            "        ;;\n"
            "      backup-active)\n"
            "        printf '%s\\n' 'default via 198.51.100.1 dev br-wan proto dhcp src 198.51.100.2 metric 500'\n"
            "        ;;\n"
            "      no-active)\n"
            "        printf '%s\\n' 'default via 198.51.100.1 dev br-wan proto dhcp src 198.51.100.2 metric 500'\n"
            "        ;;\n"
            "      backup-restoring)\n"
            "        printf '%s\\n' 'default via 198.51.100.1 dev br-wan proto dhcp src 198.51.100.2 metric 0'\n"
            "        ;;\n"
            "    esac\n"
            "    exit 0;;\n"
            "  *'route show default dev eth7 metric 10'*)\n"
            "    case \"$state\" in\n"
            "      backup-active|backup-restoring)\n"
            "        printf '%s\\n' 'default via 192.0.2.1 dev eth7 proto 242 src 192.0.2.2 metric 10'\n"
            "        ;;\n"
            "    esac\n"
            "    exit 0;;\n"
            "  *'route show default dev eth7 metric 600'*)\n"
            f"    printf '%s\\n' 'default via {backup_kernel_gateway} dev eth7 proto {backup_kernel_protocol} src {backup_kernel_source} metric 600'\n"
            "    exit 0;;\n"
            "  *'route add default via 198.51.100.1 dev br-wan'*'metric 10'*)\n"
            f"    state=$(cat {route_state}); case \"$state\" in no-active) printf '%s\\n' primary-standby > {route_state} ;; *) printf '%s\\n' primary-active > {route_state} ;; esac; exit 0;;\n"
            "  *'route replace default via 198.51.100.1 dev br-wan'*'metric 10'*)\n"
            f"    state=$(cat {route_state}); case \"$state\" in backup-active|backup-restoring) printf '%s\\n' primary-standby > {route_state} ;; *) printf '%s\\n' primary-active > {route_state} ;; esac; exit 0;;\n"
            "  *'route change default via 198.51.100.1 dev br-wan'*'metric 10'*)\n"
            f"    printf '%s\\n' primary-active > {route_state}; exit 0;;\n"
            "  *'route change default via 198.51.100.1 dev br-wan'*'metric 500'*)\n"
            f"    state=$(cat {route_state}); case \"$state\" in primary-active|primary-restoring) printf '%s\\n' primary-standby > {route_state} ;; esac; exit 0;;\n"
            "  *'route change default via 198.51.100.1 dev br-wan'*'metric 0'*)\n"
            f"    state=$(cat {route_state}); case \"$state\" in primary-standby) printf '%s\\n' primary-restoring > {route_state} ;; backup-active) printf '%s\\n' backup-restoring > {route_state} ;; no-active) printf '%s\\n' primary-original > {route_state} ;; esac; exit 0;;\n"
            "  *'route add default via 192.0.2.1 dev eth7'*'metric 10'*)\n"
            f"    printf '%s\\n' backup-active > {route_state}; exit 0;;\n"
            "  *'route replace default via 192.0.2.1 dev eth7'*'metric 10'*)\n"
            f"    printf '%s\\n' backup-active > {route_state}; exit 0;;\n"
            "  *'route del default via 192.0.2.1 dev eth7'*'metric 10'*)\n"
            f"    state=$(cat {route_state}); case \"$state\" in backup-active) printf '%s\\n' no-active > {route_state} ;; *) printf '%s\\n' primary-original > {route_state} ;; esac; exit 0;;\n"
            "  *'route del default via 198.51.100.1 dev br-wan'*'metric 10'*)\n"
            f"    state=$(cat {route_state}); case \"$state\" in primary-standby) printf '%s\\n' no-active > {route_state} ;; *) printf '%s\\n' primary-original > {route_state} ;; esac; exit 0;;\n"
            "  *) exit 0;;\n"
            "esac\n"
        )
        ip.chmod(0o755)
        ubus = bin_dir / "ubus"
        ubus.write_text(
            "#!/bin/sh\n"
            "case \"$*\" in\n"
            "  *'network.interface.usb_uplink status'*)\n"
            f"    printf '%s\\n' 'PLACEHOLDER'\n"
            "    ;;\n"
            "  *) printf '%s\\n' '{}' ;;\n"
            "esac\n"
        )
        status_json = (
            '{"up":true,"device":"eth7","ipv4-address":[{"address":"192.0.2.2",'
            f'"mask":{backup_source_mask}'
            + (f',"valid":{backup_valid}' if backup_valid is not None else "")
            + '],"route":['
        )
        if backup_route_layout == "host_first":
            status_json += (
                '{"target":"192.0.2.1","mask":32,"nexthop":"192.0.2.1",'
                '"metric":600},'
            )
        status_json += (
            f'{{"target":"{backup_route_target}","mask":{backup_route_mask},'
            f'"nexthop":"192.0.2.1","metric":{backup_route_metric}}}'
        )
        if backup_default_routes > 1:
            for _ in range(backup_default_routes - 1):
                status_json += (
                    f',{{"target":"0.0.0.0","mask":0,"nexthop":"192.0.2.1",'
                    f'"metric":{backup_route_metric}}}'
                )
        status_json += ']}'
        ubus.write_text(
            "#!/bin/sh\n"
            "case \"$*\" in\n"
            "  *'network.interface.usb_uplink status'*)\n"
            f"    printf '%s\\n' '{status_json}'\n"
            "    ;;\n"
            "  *) printf '%s\\n' '{}' ;;\n"
            "esac\n"
        )
        ubus.chmod(0o755)
        jsonfilter = bin_dir / "jsonfilter"
        default_values = {
            "target": "192.0.2.1",
            "mask": "32",
            "nexthop": "192.0.2.1",
            "metric": "600",
        }
        if backup_route_layout != "host_first":
            default_values = {
                "target": backup_route_target,
                "mask": backup_route_mask,
                "nexthop": "192.0.2.1",
                "metric": backup_route_metric,
            }
        jsonfilter_lines = [
            "#!/bin/sh",
            "expression=",
            "while [ $# -gt 0 ]; do",
            "  case \"$1\" in",
            "    -e) expression=$2; shift 2 ;;",
            "    *) shift ;;",
            "  esac",
            "done",
            "case \"$expression\" in",
            "  '@.up') printf true ;;",
            "  '@.dynamic') printf true ;;",
            "  '@.proto') printf dhcp ;;",
            "  '@.device') printf eth7 ;;",
            "  '@.ipv4-address[0].address') printf 192.0.2.2 ;;",
            f"  '@.ipv4-address[0].mask') printf '%s' '{backup_source_mask}' ;;",
        ]
        if backup_valid is None:
            jsonfilter_lines.append("  '@.ipv4-address[0].valid') exit 0 ;;")
            jsonfilter_lines.append("  '@.ipv4-address[0].valid_lft') exit 0 ;;")
        else:
            jsonfilter_lines.append(
                f"  '@.ipv4-address[0].valid') printf '%s' '{backup_valid}' ;;"
            )
            jsonfilter_lines.append(
                f"  '@.ipv4-address[0].valid_lft') printf '%s' '{backup_valid}' ;;"
            )
        if backup_leasetime is None:
            jsonfilter_lines.append("  '@.data.leasetime') exit 0 ;;")
        else:
            jsonfilter_lines.append(
                f"  '@.data.leasetime') printf '%s' '{backup_leasetime}' ;;"
            )
        default_output = "printf '%s\\n' '0.0.0.0'"
        if backup_default_routes > 1:
            default_output += "; printf '%s\\n' '0.0.0.0'"
        jsonfilter_lines.extend(
            [
                "  *route*target*0.0.0.0*target) "
                + default_output
                + " ;;",
                "  *route*target*0.0.0.0*mask) "
                + default_output.replace("0.0.0.0", "0")
                + " ;;",
                "  *route*target*0.0.0.0*nexthop) "
                + default_output.replace("0.0.0.0", "192.0.2.1")
                + " ;;",
                "  *route*target*0.0.0.0*metric) "
                + default_output.replace("0.0.0.0", backup_route_metric)
                + " ;;",
                f"  '@.route[0].target') printf '%s' '{default_values['target']}' ;;",
                f"  '@.route[0].mask') printf '%s' '{default_values['mask']}' ;;",
                f"  '@.route[0].nexthop') printf '%s' '{default_values['nexthop']}' ;;",
                f"  '@.route[0].metric') printf '%s' '{default_values['metric']}' ;;",
                "esac",
            ]
        )
        jsonfilter.write_text("\n".join(jsonfilter_lines) + "\n")
        jsonfilter.chmod(0o755)
        env = os.environ.copy()
        env.update(
            {
                "USB_UPLINK_TEST_MODE": "1",
                "USB_UPLINK_CONFIG_FILE": str(config),
                "USB_UPLINK_RUNTIME_DIR": str(runtime),
                "USB_UPLINK_SYSFS_ROOT": str(sysfs),
                "USB_UPLINK_IP_BIN": str(ip),
                "USB_UPLINK_UBUS_BIN": str(ubus),
                "USB_UPLINK_JSONFILTER_BIN": str(jsonfilter),
                "USB_UPLINK_TEST_HEALTH": health,
                "USB_UPLINK_TEST_OBSERVER_ID": "fixture-observer",
                "USB_UPLINK_TEST_NOW": "1000",
            }
        )
        return temp, root, runtime, env, ip_log

    def run_controller(self, env, command="--once"):
        return subprocess.run(
            [str(CONTROLLER), command], capture_output=True, text=True, env=env
        )

    def make_noop_route_mutator(self, root, env):
        original_ip = env["USB_UPLINK_IP_BIN"]
        wrapper = root / "bin" / "ip-noop-mutator"
        wrapper.write_text(
            "#!/bin/sh\n"
            "case \"$*\" in\n"
            "  *'route add '*|*'route replace '*|*'route change '*|*'route del '*) exit 0 ;;\n"
            f"  *) exec {original_ip} \"$@\" ;;\n"
            "esac\n"
        )
        wrapper.chmod(0o755)
        env["USB_UPLINK_IP_BIN"] = str(wrapper)

    def test_initialize_preserves_original_route_protocol_and_metric(self):
        temp, root, runtime, env, ip_log = self.make_fixture(
            route_control_enabled="1",
        )
        self.addCleanup(temp.cleanup)

        result = self.run_controller(env)

        self.assertEqual(result.returncode, 0, result.stderr)
        log = ip_log.read_text()
        self.assertIn("route change default via 198.51.100.1 dev br-wan src 198.51.100.2 proto dhcp metric 500", log)
        self.assertNotIn("route change default via 198.51.100.1 dev br-wan src 198.51.100.2 proto 242 metric 500", log)
        self.assertIn("original_protocol=dhcp", (runtime / "failover-state").read_text())

    def test_rollback_refuses_active_metric_route_without_ownership_state(self):
        temp, root, runtime, env, ip_log = self.make_fixture()
        self.addCleanup(temp.cleanup)
        (root / "route-state").write_text("unknown-active\n")

        result = self.run_controller(env, "--rollback")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "active route exists without ownership state",
            (runtime / "failover-status").read_text(),
        )
        self.assertNotIn("route del", ip_log.read_text())

    def test_backup_snapshot_requires_matching_kernel_default_route(self):
        temp, root, runtime, env, ip_log = self.make_fixture(
            health="primary=candidate,backup=healthy",
            seed_state=True,
            route_control_enabled="1",
            backup_kernel_gateway="192.0.2.254",
        )
        self.addCleanup(temp.cleanup)

        for _ in range(3):
            result = self.run_controller(env)

        self.assertEqual(result.returncode, 0, result.stderr)
        status = (runtime / "failover-status").read_text()
        self.assertIn("kernel route gateway does not match DHCP snapshot", status)
        self.assertIn("state=unavailable", status)
        self.assertNotIn("route replace default via 192.0.2.1", ip_log.read_text())

    def test_backup_snapshot_revalidates_usb_identity_after_status_snapshot(self):
        temp, root, runtime, env, ip_log = self.make_fixture(
            health="primary=candidate,backup=healthy",
            seed_state=True,
            route_control_enabled="1",
        )
        self.addCleanup(temp.cleanup)

        sysfs = Path(env["USB_UPLINK_SYSFS_ROOT"])
        replacement_root = sysfs / "devices" / "platform" / "usb1" / "2-1"
        replacement_interface = replacement_root / "2-1:1.0"
        replacement_interface.mkdir(parents=True)
        (replacement_root / "idVendor").write_text("12d1\n")
        (replacement_root / "idProduct").write_text("14db\n")
        (replacement_interface / "driver").symlink_to(
            sysfs / "bus" / "usb" / "drivers" / "cdc_ether"
        )
        netdev_device = sysfs / "class" / "net" / "eth7" / "device"
        original_ubus = env["USB_UPLINK_UBUS_BIN"]
        count_file = root / "ubus-status-count"
        race_ubus = root / "bin" / "ubus-status-race"
        race_ubus.write_text(
            "#!/bin/sh\n"
            f"count_file={shlex.quote(str(count_file))}\n"
            f"netdev_device={shlex.quote(str(netdev_device))}\n"
            f"replacement={shlex.quote(str(replacement_interface))}\n"
            f"original={shlex.quote(original_ubus)}\n"
            "case \"$*\" in\n"
            "  *'network.interface.usb_uplink status'*)\n"
            "    count=0\n"
            "    [ ! -e \"$count_file\" ] || count=$(cat \"$count_file\")\n"
            "    count=$((count + 1))\n"
            "    printf '%s\\n' \"$count\" > \"$count_file\"\n"
            "    if [ \"$count\" -eq 8 ]; then\n"
            "      rm -f \"$netdev_device\"\n"
            "      ln -s \"$replacement\" \"$netdev_device\"\n"
            "    fi\n"
            "    exec \"$original\" \"$@\"\n"
            "    ;;\n"
            "  *) exec \"$original\" \"$@\" ;;\n"
            "esac\n"
        )
        race_ubus.chmod(0o755)
        env["USB_UPLINK_UBUS_BIN"] = str(race_ubus)

        for _ in range(3):
            result = self.run_controller(env)

        self.assertEqual(result.returncode, 0, result.stderr)
        status = (runtime / "failover-status").read_text()
        self.assertIn("device path does not match state", status)
        self.assertIn("backup_health=hard_down", status)
        self.assertIn("state=unavailable", status)
        self.assertNotIn("route replace default via 192.0.2.1", ip_log.read_text())

    def test_switch_rechecks_active_route_immediately_before_replace(self):
        temp, root, runtime, env, ip_log = self.make_fixture(
            health="primary=candidate,backup=healthy",
            seed_state=True,
            route_control_enabled="1",
        )
        self.addCleanup(temp.cleanup)

        first = self.run_controller(env)
        second = self.run_controller(env)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)

        original_ip = env["USB_UPLINK_IP_BIN"]
        active_query_seen = root / "active-query-seen"
        race_ip = root / "bin" / "ip-race"
        race_ip.write_text(
            "#!/bin/sh\n"
            "case \"$*\" in\n"
            "  *'route show default metric 10'*)\n"
            f"    if [ -e {active_query_seen} ]; then\n"
            "      printf '%s\\n' 'default via 203.0.113.1 dev br-other proto static src 203.0.113.2 metric 10'\n"
            "      exit 0\n"
            "    fi\n"
            f"    touch {active_query_seen}\n"
            "    ;;\n"
            "esac\n"
            f"exec {original_ip} \"$@\"\n"
        )
        race_ip.chmod(0o755)
        env["USB_UPLINK_IP_BIN"] = str(race_ip)

        third = self.run_controller(env)

        self.assertNotEqual(third.returncode, 0)
        self.assertIn("state=fail_closed", (runtime / "failover-status").read_text())
        self.assertNotIn("route replace default via 192.0.2.1", ip_log.read_text())

    def test_initialized_ownership_survives_the_next_observation(self):
        temp, root, runtime, env, _ = self.make_fixture(
            route_control_enabled="1",
        )
        self.addCleanup(temp.cleanup)

        first = self.run_controller(env)
        self.assertEqual(first.returncode, 0, first.stderr)
        second = self.run_controller(env)

        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertNotIn("invalid failover ownership state", (runtime / "failover-status").read_text())

    def test_current_uplink_state_with_link_lifecycle_field_is_accepted(self):
        temp, root, runtime, env, ip_log = self.make_fixture(
            health="primary=candidate,backup=healthy",
            route_control_enabled="1",
        )
        self.addCleanup(temp.cleanup)
        uplink_state = runtime / "state"
        uplink_state.write_text(uplink_state.read_text() + "link_was_up=0\n")

        result = self.run_controller(env)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(
            "invalid USB uplink ownership state",
            (runtime / "failover-status").read_text(),
        )
        self.assertIn(
            "waiting for sustained failover candidate",
            (runtime / "failover-status").read_text(),
        )
        self.assertIn(
            "route change default via 198.51.100.1 dev br-wan src 198.51.100.2 proto dhcp metric 500",
            ip_log.read_text(),
        )

    def test_initialization_rolls_back_routes_when_state_persist_fails(self):
        temp, root, runtime, env, ip_log = self.make_fixture(
            route_control_enabled="1",
        )
        self.addCleanup(temp.cleanup)
        state_parent = root / "state-parent"
        state_parent.write_text("not a directory\n")
        env["USB_UPLINK_FAILOVER_STATE_FILE"] = str(state_parent / "failover-state")

        result = self.run_controller(env)

        self.assertNotEqual(result.returncode, 0)
        log = ip_log.read_text()
        self.assertIn("proto dhcp metric 500", log)
        self.assertIn("proto dhcp metric 0", log)
        self.assertIn("route del default via 198.51.100.1 dev br-wan src 198.51.100.2 proto 242 metric 10", log)
        self.assertIn("persist initial route ownership", (runtime / "failover-status").read_text())

    def test_failed_initialization_keeps_journal_for_explicit_rollback(self):
        temp, root, runtime, env, _ = self.make_fixture(
            route_control_enabled="1",
        )
        self.addCleanup(temp.cleanup)
        original_ip = env["USB_UPLINK_IP_BIN"]
        wrapper = root / "bin" / "ip-initialize-failure"
        wrapper.write_text(
            "#!/bin/sh\n"
            "case \"$*\" in\n"
            "  *'route change default via 198.51.100.1 dev br-wan'*'metric 500'*) exit 1 ;;\n"
            "  *'route del default via 198.51.100.1 dev br-wan'*'metric 10'*) exit 1 ;;\n"
            f"  *) exec {original_ip} \"$@\" ;;\n"
            "esac\n"
        )
        wrapper.chmod(0o755)
        env["USB_UPLINK_IP_BIN"] = str(wrapper)

        failed = self.run_controller(env)

        self.assertNotEqual(failed.returncode, 0)
        pending = runtime / "failover-pending"
        self.assertTrue(pending.exists())
        self.assertIn("operation=initialize", pending.read_text())
        self.assertFalse((runtime / "failover-state").exists())
        self.assertEqual((root / "route-state").read_text(), "primary-active\n")

        env["USB_UPLINK_IP_BIN"] = original_ip
        rollback = self.run_controller(env, "--rollback")

        self.assertEqual(rollback.returncode, 0, rollback.stderr)
        self.assertFalse(pending.exists())
        self.assertEqual((root / "route-state").read_text(), "primary-original\n")

    def test_route_add_requires_kernel_postcondition(self):
        temp, root, runtime, env, _ = self.make_fixture(
            route_control_enabled="1",
        )
        self.addCleanup(temp.cleanup)
        self.make_noop_route_mutator(root, env)

        result = self.run_controller(env)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("route post-condition", (runtime / "failover-status").read_text())
        self.assertFalse((runtime / "failover-state").exists())

    def test_route_change_requires_kernel_postcondition_before_rollback_success(self):
        temp, root, runtime, env, _ = self.make_fixture(
            seed_state=True,
            route_control_enabled="1",
        )
        self.addCleanup(temp.cleanup)
        self.make_noop_route_mutator(root, env)

        result = self.run_controller(env, "--rollback")

        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((runtime / "failover-state").exists())
        self.assertIn("route post-condition", (runtime / "failover-status").read_text())

    def test_primary_rollback_restores_original_protocol_and_metric(self):
        temp, root, runtime, env, ip_log = self.make_fixture(
            seed_state=True,
            route_control_enabled="1",
        )
        self.addCleanup(temp.cleanup)

        result = self.run_controller(env, "--rollback")

        self.assertEqual(result.returncode, 0, result.stderr)
        log = ip_log.read_text()
        self.assertIn("route change default via 198.51.100.1 dev br-wan src 198.51.100.2 proto dhcp metric 0", log)
        self.assertNotIn("proto 242 metric 0", log)
        self.assertFalse((runtime / "failover-state").exists())

    def test_backup_rollback_removes_backup_route_and_restores_primary(self):
        temp, root, runtime, env, ip_log = self.make_fixture(
            seed_state=True,
            state_active="backup",
            route_control_enabled="1",
        )
        self.addCleanup(temp.cleanup)
        (root / "route-state").write_text("backup-active\n")

        result = self.run_controller(env, "--rollback")

        self.assertEqual(result.returncode, 0, result.stderr)
        log = ip_log.read_text()
        self.assertIn("route del default via 192.0.2.1 dev eth7 src 192.0.2.2 proto 242 metric 10", log)
        self.assertIn("route change default via 198.51.100.1 dev br-wan src 198.51.100.2 proto dhcp metric 0", log)
        self.assertFalse((runtime / "failover-state").exists())

    def test_both_unhealthy_with_owned_active_route_withdraws_the_route(self):
        temp, root, runtime, env, ip_log = self.make_fixture(
            health="primary=hard_down,backup=hard_down",
            seed_state=True,
            state_active="backup",
            route_control_enabled="1",
        )
        self.addCleanup(temp.cleanup)

        result = self.run_controller(env)

        self.assertEqual(result.returncode, 0, result.stderr)
        state = (runtime / "failover-state").read_text()
        self.assertIn("active=unavailable", state)
        self.assertIn("previous_active=backup", state)
        self.assertIn("state=unavailable", (runtime / "failover-status").read_text())
        self.assertEqual((root / "route-state").read_text(), "no-active\n")
        self.assertIn(
            "route del default via 192.0.2.1 dev eth7 src 192.0.2.2 proto 242 metric 10",
            ip_log.read_text(),
        )

    def test_withdrawn_route_can_be_explicitly_rolled_back(self):
        temp, root, runtime, env, ip_log = self.make_fixture(
            health="primary=hard_down,backup=hard_down",
            seed_state=True,
            state_active="backup",
            route_control_enabled="1",
        )
        self.addCleanup(temp.cleanup)

        withdrawn = self.run_controller(env)
        self.assertEqual(withdrawn.returncode, 0, withdrawn.stderr)
        self.assertIn("active=unavailable", (runtime / "failover-state").read_text())

        rollback = self.run_controller(env, "--rollback")

        self.assertEqual(rollback.returncode, 0, rollback.stderr)
        self.assertFalse((runtime / "failover-state").exists())
        self.assertFalse((runtime / "failover-pending").exists())
        self.assertEqual((root / "route-state").read_text(), "primary-original\n")
        self.assertIn(
            "route change default via 198.51.100.1 dev br-wan src 198.51.100.2 proto dhcp metric 0",
            ip_log.read_text(),
        )

    def test_unavailable_state_reactivates_a_recovered_primary(self):
        temp, root, runtime, env, ip_log = self.make_fixture(
            health="primary=hard_down,backup=hard_down",
            seed_state=True,
            state_active="backup",
            route_control_enabled="1",
        )
        self.addCleanup(temp.cleanup)

        withdrawn = self.run_controller(env)
        self.assertEqual(withdrawn.returncode, 0, withdrawn.stderr)
        env["USB_UPLINK_TEST_HEALTH"] = "primary=healthy,backup=hard_down"

        recovered = self.run_controller(env)

        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        state = (runtime / "failover-state").read_text()
        self.assertIn("active=primary", state)
        self.assertIn("controller=primary", state)
        self.assertIn(
            "route add default via 198.51.100.1 dev br-wan src 198.51.100.2 proto 242 metric 10",
            ip_log.read_text(),
        )
        self.assertEqual((root / "route-state").read_text(), "primary-standby\n")

    def test_unavailable_state_refuses_unknown_active_route(self):
        temp, root, runtime, env, ip_log = self.make_fixture(
            health="primary=hard_down,backup=hard_down",
            seed_state=True,
            state_active="backup",
            route_control_enabled="1",
        )
        self.addCleanup(temp.cleanup)

        withdrawn = self.run_controller(env)
        self.assertEqual(withdrawn.returncode, 0, withdrawn.stderr)
        (root / "route-state").write_text("unknown-active\n")
        env["USB_UPLINK_TEST_HEALTH"] = "primary=healthy,backup=hard_down"

        recovered = self.run_controller(env)

        self.assertNotEqual(recovered.returncode, 0)
        status = (runtime / "failover-status").read_text()
        self.assertIn("active route exists while ownership state is unavailable", status)
        self.assertNotIn("route add default via 198.51.100.1", ip_log.read_text())
        self.assertIn("active=unavailable", (runtime / "failover-state").read_text())

    def test_backup_switch_requires_verified_primary_standby_route(self):
        temp, root, runtime, env, ip_log = self.make_fixture(
            health="primary=candidate,backup=healthy",
            seed_state=True,
            route_control_enabled="1",
            primary_standby_protocol="static",
        )
        self.addCleanup(temp.cleanup)

        for _ in range(3):
            result = self.run_controller(env)

        self.assertNotEqual(result.returncode, 0)
        status = (runtime / "failover-status").read_text()
        self.assertIn("standby protocol ownership conflict", status)
        self.assertIn("state=fail_closed", status)
        self.assertNotIn("route replace default via 192.0.2.1", ip_log.read_text())

    def test_invalid_backup_route_is_rejected_before_switch(self):
        temp, root, runtime, env, _ = self.make_fixture(
            health="primary=candidate,backup=healthy",
            seed_state=True,
            route_control_enabled="1",
            backup_route_metric="500",
        )
        self.addCleanup(temp.cleanup)

        for _ in range(3):
            result = self.run_controller(env)

        self.assertEqual(result.returncode, 0, result.stderr)
        status = (runtime / "failover-status").read_text()
        self.assertIn("default route metric", status)
        self.assertIn("state=unavailable", status)
        self.assertNotIn("route replace default via 192.0.2.1", (root / "ip.log").read_text())

    def test_backup_route_selection_ignores_non_default_route_order(self):
        temp, root, runtime, env, ip_log = self.make_fixture(
            health="primary=candidate,backup=healthy",
            seed_state=True,
            route_control_enabled="1",
            backup_route_layout="host_first",
        )
        self.addCleanup(temp.cleanup)

        for _ in range(3):
            result = self.run_controller(env)
            self.assertEqual(result.returncode, 0, result.stderr)

        self.assertIn("state=backup", (runtime / "failover-status").read_text())
        self.assertIn("route replace default via 192.0.2.1 dev eth7", ip_log.read_text())

    def test_ipv4_dhcp_without_valid_lifetime_is_rejected(self):
        temp, root, runtime, env, ip_log = self.make_fixture(
            health="primary=candidate,backup=healthy",
            seed_state=True,
            route_control_enabled="1",
            backup_valid=None,
        )
        self.addCleanup(temp.cleanup)

        for _ in range(3):
            result = self.run_controller(env)
            self.assertEqual(result.returncode, 0, result.stderr)

        status = (runtime / "failover-status").read_text()
        self.assertIn("lease lifetime is unavailable", status)
        self.assertIn("state=unavailable", status)
        self.assertNotIn("route replace default via 192.0.2.1", ip_log.read_text())

    def test_dhcp_data_leasetime_is_accepted_when_address_valid_is_absent(self):
        temp, root, runtime, env, ip_log = self.make_fixture(
            health="primary=candidate,backup=healthy",
            seed_state=True,
            route_control_enabled="1",
            backup_valid=None,
            backup_leasetime="3600",
        )
        self.addCleanup(temp.cleanup)

        for _ in range(3):
            result = self.run_controller(env)
            self.assertEqual(result.returncode, 0, result.stderr)

        self.assertIn("state=backup", (runtime / "failover-status").read_text())
        self.assertIn("route replace default via 192.0.2.1", ip_log.read_text())

    def test_route_control_requires_two_https_targets(self):
        temp, root, runtime, env, _ = self.make_fixture(
            health="primary=candidate,backup=healthy",
            seed_state=True,
            route_control_enabled="1",
            app_targets="",
        )
        self.addCleanup(temp.cleanup)

        result = self.run_controller(env)

        self.assertNotEqual(result.returncode, 0)
        status = (runtime / "failover-status").read_text()
        self.assertIn("requires at least two HTTPS application targets", status)

    def test_loss_between_five_and_twenty_percent_never_becomes_candidate(self):
        temp, root, runtime, env, _ = self.make_fixture(
            health="",
            seed_state=True,
            route_control_enabled="1",
        )
        self.addCleanup(temp.cleanup)
        ping = root / "bin" / "ping"
        ping.write_text(
            "#!/bin/sh\n"
            "case \"$2\" in\n"
            "  br-wan) printf '%s\\n' '3 packets transmitted, 3 packets received, 10.0% packet loss' ;;\n"
            "  eth7) printf '%s\\n' '3 packets transmitted, 3 packets received, 0.0% packet loss' ;;\n"
            "  *) exit 1 ;;\n"
            "esac\n"
        )
        ping.chmod(0o755)
        env["USB_UPLINK_PING_BIN"] = str(ping)
        curl = root / "bin" / "curl"
        curl.write_text("#!/bin/sh\nexit 0\n")
        curl.chmod(0o755)
        env["USB_UPLINK_CURL_BIN"] = str(curl)

        result = self.run_controller(env)

        self.assertEqual(result.returncode, 0, result.stderr)
        status = (runtime / "failover-status").read_text()
        self.assertIn("primary_health=degraded", status)
        self.assertIn("candidate_streak=0", status)
        self.assertIn("retaining primary", status)

    def test_candidate_requires_two_lossy_targets_and_two_https_failures(self):
        temp, root, runtime, env, _ = self.make_fixture(
            health="",
            seed_state=True,
            route_control_enabled="1",
        )
        self.addCleanup(temp.cleanup)
        ping = root / "bin" / "ping"
        ping.write_text(
            "#!/bin/sh\n"
            "case \"$7\" in\n"
            "  198.51.100.10) printf '%s\\n' '3 packets transmitted, 2 packets received, 30.0% packet loss' ;;\n"
            "  *) printf '%s\\n' '3 packets transmitted, 3 packets received, 0.0% packet loss' ;;\n"
            "esac\n"
        )
        ping.chmod(0o755)
        env["USB_UPLINK_PING_BIN"] = str(ping)
        curl = root / "bin" / "curl"
        curl.write_text("#!/bin/sh\nexit 1\n")
        curl.chmod(0o755)
        env["USB_UPLINK_CURL_BIN"] = str(curl)

        result = self.run_controller(env)

        self.assertEqual(result.returncode, 0, result.stderr)
        status = (runtime / "failover-status").read_text()
        self.assertIn("primary_health=degraded", status)
        self.assertIn("candidate_streak=0", status)

    def test_disabled_controller_does_not_initialize_or_mutate_routes(self):
        temp, root, runtime, env, ip_log = self.make_fixture(
            health="primary=candidate,backup=healthy",
            route_control_enabled="1",
            enabled="0",
        )
        self.addCleanup(temp.cleanup)

        result = self.run_controller(env)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("disabled", (runtime / "failover-status").read_text())
        self.assertNotIn("route add", ip_log.read_text())
        self.assertNotIn("route replace", ip_log.read_text())

    def test_hard_down_still_requires_consecutive_failover_rounds(self):
        temp, root, runtime, env, ip_log = self.make_fixture(
            health="primary=hard_down,backup=healthy",
            seed_state=True,
            route_control_enabled="1",
        )
        self.addCleanup(temp.cleanup)

        first = self.run_controller(env)
        second = self.run_controller(env)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("state=primary", (runtime / "failover-status").read_text())
        self.assertNotIn("route replace default via 192.0.2.1", ip_log.read_text())

        third = self.run_controller(env)
        self.assertEqual(third.returncode, 0, third.stderr)
        self.assertIn("state=backup", (runtime / "failover-status").read_text())
        self.assertIn("route replace default via 192.0.2.1", ip_log.read_text())

    def test_zero_optional_valid_lifetime_is_rejected(self):
        temp, root, runtime, env, ip_log = self.make_fixture(
            health="primary=candidate,backup=healthy",
            seed_state=True,
            route_control_enabled="1",
            backup_valid="0",
        )
        self.addCleanup(temp.cleanup)

        for _ in range(3):
            result = self.run_controller(env)

        self.assertEqual(result.returncode, 0, result.stderr)
        status = (runtime / "failover-status").read_text()
        self.assertIn("lease has expired", status)
        self.assertIn("state=unavailable", status)
        self.assertNotIn("route replace default via 192.0.2.1", ip_log.read_text())

    def test_multiple_default_routes_are_rejected_before_switch(self):
        temp, root, runtime, env, ip_log = self.make_fixture(
            health="primary=candidate,backup=healthy",
            seed_state=True,
            route_control_enabled="1",
            backup_default_routes=2,
        )
        self.addCleanup(temp.cleanup)

        for _ in range(3):
            result = self.run_controller(env)

        self.assertEqual(result.returncode, 0, result.stderr)
        status = (runtime / "failover-status").read_text()
        self.assertIn("multiple backup default routes", status)
        self.assertIn("state=unavailable", status)
        self.assertNotIn("route replace default via 192.0.2.1", ip_log.read_text())

    def test_malformed_state_writes_failover_status(self):
        temp, root, runtime, env, _ = self.make_fixture()
        self.addCleanup(temp.cleanup)
        (runtime / "failover-state").write_text("active=primary\n")

        result = self.run_controller(env)

        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((runtime / "failover-status").exists())
        self.assertIn("invalid failover ownership state", (runtime / "failover-status").read_text())

    def test_invalid_configuration_writes_failover_status(self):
        temp, root, runtime, env, _ = self.make_fixture()
        self.addCleanup(temp.cleanup)
        config = Path(env["USB_UPLINK_CONFIG_FILE"])
        config.write_text(config.read_text().replace("mode=failover", "mode=unsupported"))

        result = self.run_controller(env)

        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((runtime / "failover-status").exists())
        self.assertIn("unsupported mode", (runtime / "failover-status").read_text())

    def test_route_metrics_must_increase_from_active_to_backup(self):
        temp, root, runtime, env, _ = self.make_fixture()
        self.addCleanup(temp.cleanup)
        config = Path(env["USB_UPLINK_CONFIG_FILE"])
        config.write_text(config.read_text().replace("standby_metric=500", "standby_metric=5"))

        result = self.run_controller(env)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("metric order", (runtime / "failover-status").read_text())

    def test_missing_usb_uplink_state_writes_status_and_blocks_switch(self):
        temp, root, runtime, env, ip_log = self.make_fixture(
            health="primary=candidate,backup=healthy",
            seed_state=True,
            route_control_enabled="1",
            uplink_state=False,
        )
        self.addCleanup(temp.cleanup)

        result = self.run_controller(env)

        self.assertEqual(result.returncode, 0, result.stderr)
        status = (runtime / "failover-status").read_text()
        self.assertIn("USB uplink ownership state is unavailable", status)
        self.assertIn("state=unavailable", status)
        self.assertNotIn("route replace default via 192.0.2.1", ip_log.read_text())

    def test_mismatched_usb_uplink_state_writes_status_and_blocks_switch(self):
        temp, root, runtime, env, ip_log = self.make_fixture(
            health="primary=candidate,backup=healthy",
            seed_state=True,
            route_control_enabled="1",
        )
        self.addCleanup(temp.cleanup)
        eth8 = Path(env["USB_UPLINK_SYSFS_ROOT"]) / "class" / "net" / "eth8"
        eth8.mkdir(parents=True)
        eth8_device = eth8 / "device"
        eth8_device.symlink_to(
            Path(env["USB_UPLINK_SYSFS_ROOT"])
            / "devices"
            / "platform"
            / "usb1"
            / "1-1"
            / "1-1:1.0"
        )
        device_path = eth8_device.resolve()
        (runtime / "state").write_text(
            "created=1\n"
            "netdev=eth8\n"
            "interface=usb_uplink\n"
            "usb_id=12d1:14db\n"
            "driver=cdc_ether\n"
            f"device_path={device_path}\n"
        )

        result = self.run_controller(env)

        self.assertEqual(result.returncode, 0, result.stderr)
        status = (runtime / "failover-status").read_text()
        self.assertIn("USB uplink device ownership does not match state", status)
        self.assertIn("state=unavailable", status)
        self.assertNotIn("route replace default via 192.0.2.1", ip_log.read_text())

    def test_uplink_identity_fields_are_required_before_failover(self):
        temp, root, runtime, env, ip_log = self.make_fixture(
            health="primary=candidate,backup=healthy",
            seed_state=True,
            route_control_enabled="1",
        )
        self.addCleanup(temp.cleanup)
        (runtime / "state").write_text(
            "created=1\nnetdev=eth7\ninterface=usb_uplink\n"
        )

        result = self.run_controller(env)

        self.assertEqual(result.returncode, 0, result.stderr)
        status = (runtime / "failover-status").read_text()
        self.assertIn("invalid USB uplink ownership state", status)
        self.assertIn("state=unavailable", status)
        self.assertNotIn("route replace default via 192.0.2.1", ip_log.read_text())

    def test_standby_status_write_failure_is_not_reported_as_success(self):
        temp, root, runtime, env, _ = self.make_fixture()
        self.addCleanup(temp.cleanup)
        status_parent = root / "status-parent"
        status_parent.write_text("not a directory\n")
        env["USB_UPLINK_FAILOVER_STATUS_FILE"] = str(status_parent / "failover-status")

        result = self.run_controller(env)

        self.assertNotEqual(result.returncode, 0)

    def test_check_only_status_write_failure_is_not_reported_as_success(self):
        temp, root, runtime, env, _ = self.make_fixture()
        self.addCleanup(temp.cleanup)
        status_parent = root / "status-parent"
        status_parent.write_text("not a directory\n")
        env["USB_UPLINK_FAILOVER_STATUS_FILE"] = str(status_parent / "failover-status")

        result = self.run_controller(env, "--check-only")

        self.assertNotEqual(result.returncode, 0)

    def test_check_ready_verifies_prepared_backup_without_route_mutation(self):
        temp, root, runtime, env, ip_log = self.make_fixture(
            route_control_enabled="0",
        )
        self.addCleanup(temp.cleanup)

        result = self.run_controller(env, "--check-ready")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "route-control readiness check passed",
            (runtime / "failover-status").read_text(),
        )
        log = ip_log.read_text()
        self.assertIn("route show default dev eth7 metric 600", log)
        self.assertNotRegex(log, r"route (?:add|replace|change|del) ")

    def test_check_ready_accepts_sysfs_device_paths_with_at_signs(self):
        temp, root, runtime, env, _ = self.make_fixture(route_control_enabled="0")
        self.addCleanup(temp.cleanup)
        sysfs = Path(env["USB_UPLINK_SYSFS_ROOT"])
        original_device = sysfs / "devices" / "platform" / "usb1" / "1-1"
        nested_device = (
            sysfs
            / "devices"
            / "platform"
            / "soc@0"
            / "8af8800.usb"
            / "8a00000.usb"
            / "xhci-hcd.1.auto"
            / "usb1"
            / "1-1"
        )
        nested_device.parent.mkdir(parents=True)
        original_path = original_device / "1-1:1.0"
        original_state = (runtime / "state").read_text()
        shutil.move(str(original_device), str(nested_device))
        netdev_device = sysfs / "class" / "net" / "eth7" / "device"
        netdev_device.unlink()
        netdev_device.symlink_to(nested_device / "1-1:1.0")
        (runtime / "state").write_text(
            original_state.replace(str(original_path), str(nested_device / "1-1:1.0"))
        )

        result = self.run_controller(env, "--check-ready")

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_route_query_failure_is_not_treated_as_an_empty_route_set(self):
        temp, root, runtime, env, _ = self.make_fixture()
        self.addCleanup(temp.cleanup)
        failing_ip = root / "bin" / "ip-fails"
        failing_ip.write_text("#!/bin/sh\nexit 1\n")
        failing_ip.chmod(0o755)
        env["USB_UPLINK_IP_BIN"] = str(failing_ip)

        result = self.run_controller(env, "--check-only")

        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((runtime / "failover-status").exists())
        self.assertIn("route query failed", (runtime / "failover-status").read_text())

    def test_duplicate_state_key_fails_closed(self):
        temp, root, runtime, env, _ = self.make_fixture(seed_state=True)
        self.addCleanup(temp.cleanup)
        with (runtime / "failover-state").open("a") as state:
            state.write("active=backup\n")

        result = self.run_controller(env)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid failover ownership state", (runtime / "failover-status").read_text())

    def test_test_health_override_is_ignored_outside_test_mode(self):
        temp, root, runtime, env, _ = self.make_fixture(
            health="primary=candidate,backup=healthy",
            seed_state=True,
            route_control_enabled="1",
        )
        self.addCleanup(temp.cleanup)
        uci = root / "bin" / "uci-production"
        uci.write_text(
            "#!/bin/sh\n"
            "key=\"${3#usb_uplink.main.}\"\n"
            "case \"$key\" in\n"
            "  enabled) printf 1 ;; mode) printf failover ;; failover_enabled) printf 1 ;;\n"
            "  route_control_enabled) printf 1 ;; primary_device) printf br-wan ;;\n"
            "  active_metric) printf 10 ;; standby_metric) printf 500 ;; backup_metric) printf 600 ;;\n"
            "  route_protocol) printf 242 ;; failover_rounds) printf 3 ;; failback_seconds) printf 300 ;;\n"
            "  probe_interval) printf 10 ;; probe_count) printf 3 ;; probe_timeout) printf 1 ;;\n"
            "  probe_targets) printf 198.51.100.10,198.51.100.11,198.51.100.12 ;;\n"
            "  app_targets) printf '' ;; *) exit 1 ;;\n"
            "esac\n"
        )
        uci.chmod(0o755)
        env["USB_UPLINK_TEST_MODE"] = "0"
        env["USB_UPLINK_UCI_BIN"] = str(uci)
        env["USB_UPLINK_PING_BIN"] = str(root / "missing-ping")

        result = self.run_controller(env)

        self.assertNotEqual(result.returncode, 0)
        status = (runtime / "failover-status").read_text()
        self.assertIn("primary_health=unknown", status)
        self.assertNotIn("state=backup", status)

    def test_missing_curl_blocks_route_control(self):
        temp, root, runtime, env, ip_log = self.make_fixture(
            health="",
            seed_state=True,
            route_control_enabled="1",
            app_targets="https://example.invalid/health,https://example.invalid/ready",
        )
        self.addCleanup(temp.cleanup)
        ping = root / "bin" / "ping"
        ping.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' '3 packets transmitted, 3 packets received, 0.0% packet loss'\n"
        )
        ping.chmod(0o755)
        env["USB_UPLINK_PING_BIN"] = str(ping)
        env["USB_UPLINK_CURL_BIN"] = str(root / "missing-curl")

        result = self.run_controller(env)

        self.assertNotEqual(result.returncode, 0)
        status = (runtime / "failover-status").read_text()
        self.assertIn("route control requires curl", status)
        self.assertFalse(ip_log.exists())

    def test_ipv6_default_route_blocks_ipv4_route_control(self):
        temp, root, runtime, env, ip_log = self.make_fixture(
            route_control_enabled="1",
        )
        self.addCleanup(temp.cleanup)
        original_ip = env["USB_UPLINK_IP_BIN"]
        ipv6_ip = root / "bin" / "ip-with-ipv6-default"
        ipv6_ip.write_text(
            "#!/bin/sh\n"
            "case \"$*\" in\n"
            "  *'-6 route show default'*)\n"
            "    printf '%s\\n' 'default via 2001:db8::1 dev br-wan'\n"
            "    exit 0;;\n"
            f"  *) exec {shlex.quote(original_ip)} \"$@\";;\n"
            "esac\n"
        )
        ipv6_ip.chmod(0o755)
        env["USB_UPLINK_IP_BIN"] = str(ipv6_ip)

        result = self.run_controller(env)

        self.assertNotEqual(result.returncode, 0)
        status = (runtime / "failover-status").read_text()
        self.assertIn("IPv4-only route control is blocked", status)
        self.assertFalse(ip_log.exists())

    def test_first_failover_round_does_not_switch(self):
        temp, root, runtime, env, ip_log = self.make_fixture(
            health="primary=candidate,backup=healthy",
            seed_state=True,
            route_control_enabled="1",
        )
        self.addCleanup(temp.cleanup)
        result = self.run_controller(env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("state=primary", (runtime / "failover-status").read_text())
        self.assertNotIn("route add default", ip_log.read_text())

    def test_candidate_observation_resets_after_controller_restart(self):
        temp, root, runtime, env, ip_log = self.make_fixture(
            health="primary=candidate,backup=healthy",
            seed_state=True,
            route_control_enabled="1",
        )
        self.addCleanup(temp.cleanup)

        first = self.run_controller(env)
        second = self.run_controller(env)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("candidate_streak=2", (runtime / "failover-status").read_text())

        env["USB_UPLINK_TEST_OBSERVER_ID"] = "restarted-observer"
        third = self.run_controller(env)

        self.assertEqual(third.returncode, 0, third.stderr)
        status = (runtime / "failover-status").read_text()
        self.assertIn("candidate_streak=1", status)
        self.assertIn("state=primary", status)
        self.assertNotIn("route replace default via 192.0.2.1", ip_log.read_text())

    def test_unknown_existing_active_slot_is_rejected(self):
        temp, root, runtime, env, ip_log = self.make_fixture()
        self.addCleanup(temp.cleanup)
        (root / "route-state").write_text("unknown-active\n")
        result = self.run_controller(env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ownership", (runtime / "failover-status").read_text())

    def test_three_candidate_rounds_switch_using_service_route_protocol(self):
        temp, root, runtime, env, ip_log = self.make_fixture(
            health="primary=candidate,backup=healthy",
            seed_state=True,
            route_control_enabled="1",
        )
        self.addCleanup(temp.cleanup)

        for _ in range(3):
            result = self.run_controller(env)
            self.assertEqual(result.returncode, 0, result.stderr)

        status = (runtime / "failover-status").read_text()
        self.assertIn("state=backup", status)
        self.assertIn("route replace default via 192.0.2.1 dev eth7", ip_log.read_text())
        self.assertIn("proto 242", ip_log.read_text())

    def test_failed_switch_state_persistence_leaves_recovery_journal(self):
        temp, root, runtime, env, ip_log = self.make_fixture(
            health="primary=candidate,backup=healthy",
            seed_state=True,
            route_control_enabled="1",
        )
        self.addCleanup(temp.cleanup)
        state_dir = root / "state-dir"
        state_dir.mkdir()
        state_file = state_dir / "failover-state"
        (runtime / "failover-state").replace(state_file)
        env["USB_UPLINK_FAILOVER_STATE_FILE"] = str(state_file)

        first = self.run_controller(env)
        second = self.run_controller(env)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)

        state_dir.chmod(0o555)
        self.addCleanup(state_dir.chmod, 0o755)
        third = self.run_controller(env)

        self.assertNotEqual(third.returncode, 0)
        pending = runtime / "failover-pending"
        self.assertTrue(pending.exists())
        journal = pending.read_text()
        self.assertIn("operation=switch", journal)
        self.assertIn("from_active=primary", journal)
        self.assertIn("to_active=backup", journal)
        self.assertEqual((root / "route-state").read_text(), "backup-active\n")

        replace_count = ip_log.read_text().count(
            "route replace default via 192.0.2.1 dev eth7"
        )
        fourth = self.run_controller(env)
        self.assertNotEqual(fourth.returncode, 0)
        self.assertIn(
            "pending route transaction requires explicit rollback",
            (runtime / "failover-status").read_text(),
        )
        self.assertEqual(
            ip_log.read_text().count("route replace default via 192.0.2.1 dev eth7"),
            replace_count,
        )

    def test_rollback_uses_pending_journal_to_restore_original_primary(self):
        temp, root, runtime, env, _ = self.make_fixture(
            health="primary=candidate,backup=healthy",
            seed_state=True,
            route_control_enabled="1",
        )
        self.addCleanup(temp.cleanup)
        state_dir = root / "state-dir"
        state_dir.mkdir()
        state_file = state_dir / "failover-state"
        (runtime / "failover-state").replace(state_file)
        env["USB_UPLINK_FAILOVER_STATE_FILE"] = str(state_file)

        self.assertEqual(self.run_controller(env).returncode, 0)
        self.assertEqual(self.run_controller(env).returncode, 0)
        state_dir.chmod(0o555)
        failed = self.run_controller(env)
        self.assertNotEqual(failed.returncode, 0)
        self.assertTrue((runtime / "failover-pending").exists())

        state_dir.chmod(0o755)
        rollback = self.run_controller(env, "--rollback")

        self.assertEqual(rollback.returncode, 0, rollback.stderr)
        self.assertFalse(state_file.exists())
        self.assertFalse((runtime / "failover-pending").exists())
        self.assertEqual((root / "route-state").read_text(), "primary-original\n")

    def test_standby_does_not_leave_an_owned_active_route(self):
        temp, root, runtime, env, _ = self.make_fixture(
            seed_state=True,
            route_control_enabled="0",
        )
        self.addCleanup(temp.cleanup)

        result = self.run_controller(env)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("rollback", (runtime / "failover-status").read_text())

    def test_backup_route_protocol_mismatch_is_an_ownership_conflict(self):
        temp, root, runtime, env, _ = self.make_fixture(
            seed_state=True,
            state_active="backup",
            route_control_enabled="1",
        )
        self.addCleanup(temp.cleanup)
        (root / "route-state").write_text("backup-active\n")
        mismatch_ip = root / "bin" / "ip-mismatch"
        mismatch_ip.write_text(
            "#!/bin/sh\n"
            f"printf '%s\\n' \"$*\" >> {root / 'ip-mismatch.log'}\n"
            "case \"$*\" in\n"
            "  *'route show default metric 10'*) printf '%s\\n' 'default via 192.0.2.1 dev eth7 proto 99 src 192.0.2.2 metric 10' ;;\n"
            "  *) exec "
            + env["USB_UPLINK_IP_BIN"]
            + " \"$@\" ;;\n"
            "esac\n"
        )
        mismatch_ip.chmod(0o755)
        env["USB_UPLINK_IP_BIN"] = str(mismatch_ip)

        result = self.run_controller(env)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ownership", (runtime / "failover-status").read_text())

    def test_controller_state_is_preserved_when_state_removal_fails(self):
        temp, root, runtime, env, _ = self.make_fixture(
            seed_state=True,
            route_control_enabled="1",
        )
        self.addCleanup(temp.cleanup)
        rm = root / "bin" / "rm-fails"
        rm.write_text("#!/bin/sh\nexit 1\n")
        rm.chmod(0o755)
        env["USB_UPLINK_RM_BIN"] = str(rm)

        result = self.run_controller(env, "--rollback")

        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((runtime / "failover-state").exists())
        status = (runtime / "failover-status").read_text()
        self.assertIn("ownership state could not be removed", status)

    def test_rollback_retries_state_removal_after_routes_are_restored(self):
        temp, root, runtime, env, ip_log = self.make_fixture(
            seed_state=True,
            route_control_enabled="1",
        )
        self.addCleanup(temp.cleanup)
        marker = root / "rm-attempted"
        rm = root / "bin" / "rm-once-fails"
        rm.write_text(
            "#!/bin/sh\n"
            f"if [ ! -e {marker} ]; then touch {marker}; exit 1; fi\n"
            "exec /bin/rm \"$@\"\n"
        )
        rm.chmod(0o755)
        env["USB_UPLINK_RM_BIN"] = str(rm)

        first = self.run_controller(env, "--rollback")
        self.assertNotEqual(first.returncode, 0)
        self.assertTrue((runtime / "failover-state").exists())

        first_log = ip_log.read_text()
        second = self.run_controller(env, "--rollback")

        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertFalse((runtime / "failover-state").exists())
        self.assertEqual(ip_log.read_text().count("route change"), first_log.count("route change"))
