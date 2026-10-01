import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DAEMON = ROOT / "package" / "arkbridge-usb" / "files" / "usr" / "libexec" / "usb-uplinkd"


class DaemonFixtureTests(unittest.TestCase):
    def make_fixture(
        self,
        *,
        devices=("usb9",),
        driver="cdc_ether",
        bridge=False,
        vendor="12d1",
        product="14db",
        allowed_ids="12d1:14db",
        allowed_drivers="cdc_ether",
        peerdns="0",
        poll_interval="1",
        mode="standby",
        failover_enabled="0",
        route_control_enabled="0",
        backup_metric="600",
    ):
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        sysfs = root / "sys"
        device_root = sysfs / "devices" / "platform" / "usb1"
        net_root = sysfs / "class" / "net"
        driver_root = sysfs / "bus" / "usb" / "drivers" / driver
        driver_root.mkdir(parents=True)

        for index, netdev in enumerate(devices, start=1):
            usb_device = device_root / f"1-{index}"
            usb_interface = usb_device / f"1-{index}:1.0"
            usb_interface.mkdir(parents=True)
            (usb_device / "idVendor").write_text(f"{vendor}\n")
            (usb_device / "idProduct").write_text(f"{product}\n")
            (usb_interface / "driver").symlink_to(driver_root)

            netdev_root = net_root / netdev
            netdev_root.mkdir(parents=True)
            (netdev_root / "device").symlink_to(usb_interface)
            (netdev_root / "flags").write_text("0x1002\n")
            if bridge:
                bridge_root = net_root / "br-lan"
                bridge_root.mkdir(parents=True, exist_ok=True)
                (netdev_root / "master").symlink_to(bridge_root)

        config = root / "usb_uplink.conf"
        config.write_text(
            "\n".join(
                (
                    "enabled=1",
                    f"mode={mode}",
                    f"failover_enabled={failover_enabled}",
                    f"route_control_enabled={route_control_enabled}",
                    "interface_name=usb_uplink",
                    "metric=600",
                    "defaultroute=0",
                    f"backup_metric={backup_metric}",
                    f"peerdns={peerdns}",
                    f"poll_interval={poll_interval}",
                    "lan_bridge=br-lan",
                    f"allowed_ids={allowed_ids}",
                    f"allowed_drivers={allowed_drivers}",
                )
            )
            + "\n"
        )
        return temp, root, sysfs, config

    def run_daemon(
        self,
        root,
        sysfs,
        config,
        *,
        check_only=True,
        fake_ubus=False,
        ubus_path=None,
        command=None,
        state_file=None,
        rm_path=None,
        ip_path=None,
        extra_env=None,
    ):
        runtime = root / "run"
        runtime.mkdir(exist_ok=True)
        env = os.environ.copy()
        env.update(
            {
                "USB_UPLINK_TEST_MODE": "1",
                "USB_UPLINK_SYSFS_ROOT": str(sysfs),
                "USB_UPLINK_CONFIG_FILE": str(config),
                "USB_UPLINK_RUNTIME_DIR": str(runtime),
            }
        )
        if fake_ubus and ubus_path is None:
            bin_dir = root / "bin"
            bin_dir.mkdir(exist_ok=True)
            ubus_log = root / "ubus.log"
            ubus_path = bin_dir / "ubus"
            ubus_path.write_text(
                "#!/bin/sh\n"
                f"removed={root / 'dynamic-removed'}\n"
                f"created={root / 'dynamic-created'}\n"
                f"printf '%s\\n' \"$*\" >> {ubus_log}\n"
                "case \"$*\" in\n"
                "  *'network add_dynamic'*) touch \"$created\"; printf '%s\\n' '{\"section\":\"usb_uplink\"}' ;;\n"
                "  *'network.interface.usb_uplink remove {'*) touch \"$removed\" ;;\n"
                "  list)\n"
                "    if [ -e \"$created\" ] && [ ! -e \"$removed\" ]; then\n"
                "      printf '%s\\n' 'network.interface.usb_uplink'\n"
                "    fi\n"
                "    exit 0 ;;\n"
                "  *' status '*|*' status {'*)\n"
                "    [ -e \"$removed\" ] && exit 1\n"
                "    printf '%s\\n' '{\"dynamic\":true,\"proto\":\"dhcp\",\"device\":\"usb9\"}' ;;\n"
                "  *) printf '%s\\n' '{}' ;;\n"
                "esac\n"
            )
            ubus_path.chmod(0o755)
            jsonfilter_path = bin_dir / "jsonfilter"
            jsonfilter_path.write_text(
                "#!/bin/sh\n"
                "json=\n"
                "expression=\n"
                "while [ $# -gt 0 ]; do\n"
                "  case \"$1\" in\n"
                "    -s) json=$2; shift 2 ;;\n"
                "    -e) expression=$2; shift 2 ;;\n"
                "    *) shift ;;\n"
                "  esac\n"
                "done\n"
                "case \"$expression\" in\n"
                "  @.dynamic) case \"$json\" in *'\"dynamic\":true'*|*'\"dynamic\": true'*) printf true ;; *) printf false ;; esac ;;\n"
                "  @.proto) case \"$json\" in *'\"proto\":\"dhcp\"'*|*'\"proto\": \"dhcp\"'*) printf dhcp ;; esac ;;\n"
                "  @.device) case \"$json\" in *'\"device\":\"usb9\"'*|*'\"device\": \"usb9\"'*) printf usb9 ;; esac ;;\n"
                "esac\n"
            )
            jsonfilter_path.chmod(0o755)
            env["USB_UPLINK_JSONFILTER_BIN"] = str(jsonfilter_path)
        if ubus_path is not None:
            env["USB_UPLINK_UBUS_BIN"] = str(ubus_path)
            jsonfilter_path = root / "bin" / "jsonfilter"
            if jsonfilter_path.is_file():
                env["USB_UPLINK_JSONFILTER_BIN"] = str(jsonfilter_path)
        if ip_path is None:
            bin_dir = root / "bin"
            bin_dir.mkdir(exist_ok=True)
            ip_path = bin_dir / "ip"
            ip_path.write_text(
                "#!/bin/sh\n"
                f"net_root={sysfs / 'class' / 'net'}\n"
                "case \"$1:$2:$3:$5\" in\n"
                "  link:set:dev:up) printf '0x1003\\n' > \"$net_root/$4/flags\" ;;\n"
                "  link:set:dev:down) printf '0x1002\\n' > \"$net_root/$4/flags\" ;;\n"
                "esac\n"
            )
            ip_path.chmod(0o755)
        env["USB_UPLINK_IP_BIN"] = str(ip_path)
        if state_file is not None:
            env["USB_UPLINK_STATE_FILE"] = str(state_file)
        if rm_path is not None:
            env["USB_UPLINK_RM_BIN"] = str(rm_path)
        if extra_env:
            env.update(extra_env)
        daemon_command = [str(DAEMON), command or ("--check-only" if check_only else "--once")]
        result = subprocess.run(daemon_command, env=env, capture_output=True, text=True)
        return result, runtime, root / "ubus.log"

    def ownership_state(self, sysfs):
        device_path = (
            sysfs / "devices" / "platform" / "usb1" / "1-1" / "1-1:1.0"
        ).resolve()
        return (
            "created=1\n"
            "netdev=usb9\n"
            "interface=usb_uplink\n"
            "usb_id=12d1:14db\n"
            "driver=cdc_ether\n"
            f"device_path={device_path}\n"
        )

    def test_honors_custom_allowlists(self):
        temp, root, sysfs, config = self.make_fixture(
            driver="rndis_host",
            allowed_drivers="rndis_host",
        )
        self.addCleanup(temp.cleanup)

        result, runtime, _ = self.run_daemon(root, sysfs, config)

        self.assertEqual(result.returncode, 0, result.stderr)
        status = (runtime / "status").read_text()
        self.assertIn("detected=1", status)
        self.assertIn("driver=rndis_host", status)

    def test_rejects_zero_poll_interval(self):
        temp, root, sysfs, config = self.make_fixture(poll_interval="0")
        self.addCleanup(temp.cleanup)

        result, runtime, _ = self.run_daemon(root, sysfs, config)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid poll interval", (runtime / "status").read_text())

    def test_standby_forces_peer_dns_off(self):
        temp, root, sysfs, config = self.make_fixture(peerdns="1")
        self.addCleanup(temp.cleanup)

        result, _, log_path = self.run_daemon(
            root, sysfs, config, check_only=False, fake_ubus=True
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"peerdns":false', log_path.read_text())

    def test_admin_down_usb_netdev_is_restored_after_owned_rollback(self):
        temp, root, sysfs, config = self.make_fixture()
        self.addCleanup(temp.cleanup)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        ip_log = root / "ip.log"
        ip = bin_dir / "ip"
        ip.write_text(
            "#!/bin/sh\n"
            f"net_root={sysfs / 'class' / 'net'}\n"
            f"printf '%s\\n' \"$*\" >> {ip_log}\n"
            "case \"$1:$2:$3:$5\" in\n"
            "  link:set:dev:up) printf '0x1003\\n' > \"$net_root/$4/flags\" ;;\n"
            "  link:set:dev:down) printf '0x1002\\n' > \"$net_root/$4/flags\" ;;\n"
            "esac\n"
        )
        ip.chmod(0o755)

        first, runtime, _ = self.run_daemon(
            root, sysfs, config, check_only=False, fake_ubus=True, ip_path=ip
        )

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("link_was_up=0", (runtime / "state").read_text())
        self.assertEqual(
            (sysfs / "class" / "net" / "usb9" / "flags").read_text(), "0x1003\n"
        )
        self.assertIn("link set dev usb9 up", ip_log.read_text())

        rollback, _, _ = self.run_daemon(
            root,
            sysfs,
            config,
            check_only=False,
            ubus_path=root / "bin" / "ubus",
            ip_path=ip,
            command="--rollback",
        )

        self.assertEqual(rollback.returncode, 0, rollback.stderr)
        self.assertFalse((runtime / "state").exists())
        self.assertEqual(
            (sysfs / "class" / "net" / "usb9" / "flags").read_text(), "0x1002\n"
        )
        self.assertIn("link set dev usb9 down", ip_log.read_text())

    def test_admin_up_usb_netdev_is_not_changed_by_owned_rollback(self):
        temp, root, sysfs, config = self.make_fixture()
        self.addCleanup(temp.cleanup)
        (sysfs / "class" / "net" / "usb9" / "flags").write_text("0x1003\n")
        bin_dir = root / "bin"
        bin_dir.mkdir()
        ip_log = root / "ip.log"
        ip = bin_dir / "ip"
        ip.write_text(
            "#!/bin/sh\n"
            f"printf '%s\\n' \"$*\" >> {ip_log}\n"
            "exit 0\n"
        )
        ip.chmod(0o755)

        first, runtime, _ = self.run_daemon(
            root, sysfs, config, check_only=False, fake_ubus=True, ip_path=ip
        )

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("link_was_up=1", (runtime / "state").read_text())

        rollback, _, _ = self.run_daemon(
            root,
            sysfs,
            config,
            check_only=False,
            ubus_path=root / "bin" / "ubus",
            ip_path=ip,
            command="--rollback",
        )

        self.assertEqual(rollback.returncode, 0, rollback.stderr)
        self.assertFalse(ip_log.exists())

    def test_owned_rollback_accepts_sysfs_device_paths_with_at_signs(self):
        temp, root, sysfs, config = self.make_fixture()
        self.addCleanup(temp.cleanup)
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
        shutil.move(str(original_device), str(nested_device))
        netdev_device = sysfs / "class" / "net" / "usb9" / "device"
        netdev_device.unlink()
        netdev_device.symlink_to(nested_device / "1-1:1.0")

        attached, runtime, _ = self.run_daemon(
            root, sysfs, config, check_only=False, fake_ubus=True
        )
        self.assertEqual(attached.returncode, 0, attached.stderr)

        rollback, _, _ = self.run_daemon(
            root,
            sysfs,
            config,
            check_only=False,
            ubus_path=root / "bin" / "ubus",
            command="--rollback",
        )

        self.assertEqual(rollback.returncode, 0, rollback.stderr)
        self.assertFalse((runtime / "state").exists())

    def test_link_restore_failure_keeps_owned_dynamic_interface_for_retry(self):
        temp, root, sysfs, config = self.make_fixture()
        self.addCleanup(temp.cleanup)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        ip = bin_dir / "ip"
        ip.write_text(
            "#!/bin/sh\n"
            f"net_root={sysfs / 'class' / 'net'}\n"
            "case \"$1:$2:$3:$5\" in\n"
            "  link:set:dev:up) printf '0x1003\\n' > \"$net_root/$4/flags\" ;;\n"
            "  link:set:dev:down) exit 1 ;;\n"
            "esac\n"
        )
        ip.chmod(0o755)

        first, runtime, log_path = self.run_daemon(
            root, sysfs, config, check_only=False, fake_ubus=True, ip_path=ip
        )
        self.assertEqual(first.returncode, 0, first.stderr)

        rollback, _, _ = self.run_daemon(
            root,
            sysfs,
            config,
            check_only=False,
            ubus_path=root / "bin" / "ubus",
            ip_path=ip,
            command="--rollback",
        )

        self.assertNotEqual(rollback.returncode, 0)
        self.assertTrue((runtime / "state").exists())
        self.assertIn("remove_device", log_path.read_text())
        self.assertNotIn(
            "network.interface.usb_uplink remove {}",
            log_path.read_text(),
        )

    def test_remove_device_failure_keeps_dynamic_interface_for_retry(self):
        temp, root, sysfs, config = self.make_fixture()
        self.addCleanup(temp.cleanup)

        first, runtime, _ = self.run_daemon(
            root, sysfs, config, check_only=False, fake_ubus=True
        )
        self.assertEqual(first.returncode, 0, first.stderr)

        original_ubus = root / "bin" / "ubus"
        marker = root / "remove-device-failed-once"
        retrying_ubus = root / "bin" / "ubus-remove-device-fails-once"
        retrying_ubus.write_text(
            "#!/bin/sh\n"
            "case \"$*\" in\n"
            "  *'remove_device'*)\n"
            f"    if [ ! -e {marker} ]; then touch {marker}; exit 1; fi\n"
            "    ;;\n"
            "esac\n"
            f"exec {original_ubus} \"$@\"\n"
        )
        retrying_ubus.chmod(0o755)

        failed, _, _ = self.run_daemon(
            root,
            sysfs,
            config,
            check_only=False,
            ubus_path=retrying_ubus,
            command="--rollback",
        )

        self.assertNotEqual(failed.returncode, 0)
        self.assertTrue((runtime / "state").exists())
        self.assertFalse((root / "dynamic-removed").exists())

        retried, _, _ = self.run_daemon(
            root,
            sysfs,
            config,
            check_only=False,
            ubus_path=retrying_ubus,
            command="--rollback",
        )

        self.assertEqual(retried.returncode, 0, retried.stderr)
        self.assertFalse((runtime / "state").exists())
        self.assertTrue((root / "dynamic-removed").exists())

    def test_owned_rollback_uses_recorded_identity_after_allowlist_edit(self):
        temp, root, sysfs, config = self.make_fixture()
        self.addCleanup(temp.cleanup)

        first, runtime, _ = self.run_daemon(
            root, sysfs, config, check_only=False, fake_ubus=True
        )
        self.assertEqual(first.returncode, 0, first.stderr)

        config.write_text(
            config.read_text().replace(
                "allowed_drivers=cdc_ether", "allowed_drivers=rndis_host"
            )
        )
        rollback, _, _ = self.run_daemon(
            root,
            sysfs,
            config,
            check_only=False,
            ubus_path=root / "bin" / "ubus",
            command="--rollback",
        )

        self.assertEqual(rollback.returncode, 0, rollback.stderr)
        self.assertFalse((runtime / "state").exists())

    def test_explicit_failover_uses_high_metric_huawei_default_route(self):
        temp, root, sysfs, config = self.make_fixture(
            mode="failover",
            failover_enabled="1",
            route_control_enabled="1",
        )
        self.addCleanup(temp.cleanup)

        result, _, log_path = self.run_daemon(
            root, sysfs, config, check_only=False, fake_ubus=True
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        log = log_path.read_text()
        self.assertIn('"defaultroute":true', log)
        self.assertIn('"metric":600', log)

    def test_failover_preparation_uses_high_metric_route_without_route_control(self):
        temp, root, sysfs, config = self.make_fixture(
            mode="failover",
            failover_enabled="1",
            route_control_enabled="0",
        )
        self.addCleanup(temp.cleanup)

        result, _, log_path = self.run_daemon(
            root, sysfs, config, check_only=False, fake_ubus=True
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        log = log_path.read_text()
        self.assertIn('"defaultroute":true', log)
        self.assertIn('"metric":600', log)

    def test_rejects_usb_identity_outside_custom_allowlist(self):
        temp, root, sysfs, config = self.make_fixture(
            vendor="19d2",
            product="0031",
            allowed_ids="12d1:14db",
        )
        self.addCleanup(temp.cleanup)

        result, runtime, _ = self.run_daemon(root, sysfs, config)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("detected=0", (runtime / "status").read_text())
        self.assertIn("USB identity is not allowlisted", (runtime / "status").read_text())

    def test_discovers_dynamic_usb_netdev_without_fixed_name(self):
        temp, root, sysfs, config = self.make_fixture()
        self.addCleanup(temp.cleanup)

        result, runtime, _ = self.run_daemon(root, sysfs, config)

        self.assertEqual(result.returncode, 0, result.stderr)
        status = (runtime / "status").read_text()
        self.assertIn("detected=1", status)
        self.assertIn("netdev=usb9", status)
        self.assertIn("driver=cdc_ether", status)
        self.assertIn("mode=standby", status)

    def test_rejects_driver_mismatch(self):
        temp, root, sysfs, config = self.make_fixture(driver="rndis_host")
        self.addCleanup(temp.cleanup)

        result, runtime, _ = self.run_daemon(root, sysfs, config)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("detected=0", (runtime / "status").read_text())
        self.assertIn("driver is not allowlisted", (runtime / "status").read_text())

    def test_rejects_bridge_member(self):
        temp, root, sysfs, config = self.make_fixture(bridge=True)
        self.addCleanup(temp.cleanup)

        result, runtime, _ = self.run_daemon(root, sysfs, config)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("detected=0", (runtime / "status").read_text())
        self.assertIn("bridge", (runtime / "status").read_text())

    def test_rejects_ambiguous_matching_devices(self):
        temp, root, sysfs, config = self.make_fixture(devices=("usb9", "usb10"))
        self.addCleanup(temp.cleanup)

        result, runtime, _ = self.run_daemon(root, sysfs, config)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("detected=0", (runtime / "status").read_text())
        self.assertIn("ambiguous", (runtime / "status").read_text())

    def test_remove_reconcile_removes_only_owned_dynamic_interface(self):
        temp, root, sysfs, config = self.make_fixture()
        self.addCleanup(temp.cleanup)

        first, runtime, log_path = self.run_daemon(
            root, sysfs, config, check_only=False, fake_ubus=True
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("add_dynamic", log_path.read_text())

        shutil.rmtree(sysfs / "class" / "net" / "usb9")
        ubus = root / "bin" / "ubus"
        second, _, _ = self.run_daemon(
            root, sysfs, config, check_only=False, ubus_path=ubus
        )
        self.assertNotEqual(second.returncode, 0)
        log = log_path.read_text()
        self.assertIn("remove_device", log)
        self.assertIn("remove {}", log)

    def test_same_netdev_with_different_usb_identity_is_not_reused(self):
        temp, root, sysfs, config = self.make_fixture()
        self.addCleanup(temp.cleanup)

        first, runtime, _ = self.run_daemon(
            root, sysfs, config, check_only=False, fake_ubus=True
        )
        self.assertEqual(first.returncode, 0, first.stderr)

        device_root = sysfs / "devices" / "platform" / "usb1" / "2-1"
        device_interface = device_root / "2-1:1.0"
        device_interface.mkdir(parents=True)
        (device_root / "idVendor").write_text("12d1\n")
        (device_root / "idProduct").write_text("14db\n")
        (device_interface / "driver").symlink_to(
            sysfs / "bus" / "usb" / "drivers" / "cdc_ether"
        )
        netdev_device = sysfs / "class" / "net" / "usb9" / "device"
        netdev_device.unlink()
        netdev_device.symlink_to(device_interface)

        second, _, _ = self.run_daemon(
            root, sysfs, config, check_only=False, ubus_path=root / "bin" / "ubus"
        )

        self.assertNotEqual(second.returncode, 0)
        status = (runtime / "status").read_text()
        self.assertIn("identity", status)
        self.assertTrue((runtime / "state").exists())

    def test_cleanup_revalidates_identity_immediately_before_detach(self):
        temp, root, sysfs, config = self.make_fixture()
        self.addCleanup(temp.cleanup)
        first, runtime, _ = self.run_daemon(
            root, sysfs, config, check_only=False, fake_ubus=True
        )
        self.assertEqual(first.returncode, 0, first.stderr)

        replacement_root = sysfs / "devices" / "platform" / "usb1" / "2-1"
        replacement_interface = replacement_root / "2-1:1.0"
        replacement_interface.mkdir(parents=True)
        (replacement_root / "idVendor").write_text("12d1\n")
        (replacement_root / "idProduct").write_text("14db\n")
        (replacement_interface / "driver").symlink_to(
            sysfs / "bus" / "usb" / "drivers" / "cdc_ether"
        )
        netdev_device = sysfs / "class" / "net" / "usb9" / "device"
        log = root / "identity-race.log"
        ubus = root / "bin" / "ubus-identity-race"
        ubus.write_text(
            "#!/bin/sh\n"
            f"printf '%s\\n' \"$*\" >> {log}\n"
            "case \"$1:$3\" in\n"
            "  list:) printf '%s\\n' 'network.interface.usb_uplink' ;;\n"
            "  call:status)\n"
            f"    rm -f {netdev_device}\n"
            f"    ln -s {replacement_interface} {netdev_device}\n"
            "    printf '%s\\n' '{\"dynamic\":true,\"proto\":\"dhcp\",\"device\":\"usb9\"}'\n"
            "    ;;\n"
            "  *) exit 0 ;;\n"
            "esac\n"
        )
        ubus.chmod(0o755)

        result, _, _ = self.run_daemon(
            root,
            sysfs,
            config,
            check_only=False,
            ubus_path=ubus,
            command="--rollback",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((runtime / "state").exists())
        self.assertNotIn("remove_device", log.read_text())
        self.assertIn("identity", (runtime / "status").read_text())

    def test_partial_attach_failure_cleans_owned_dynamic_interface(self):
        temp, root, sysfs, config = self.make_fixture()
        self.addCleanup(temp.cleanup)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        ubus = bin_dir / "ubus"
        log_path = root / "partial-attach.log"
        ubus.write_text(
            "#!/bin/sh\n"
            f"removed={root / 'dynamic-removed'}\n"
            f"printf '%s\\n' \"$*\" >> {log_path}\n"
            "case \"$3\" in\n"
            "  add_device)\n"
            f"    if [ ! -e {root / 'add-device-seen'} ]; then touch {root / 'add-device-seen'}; exit 1; fi ;;\n"
            "  remove) touch \"$removed\" ;;\n"
            "  status)\n"
            "    [ -e \"$removed\" ] && exit 1\n"
            "    printf '%s\\n' '{\"dynamic\":true,\"proto\":\"dhcp\",\"device\":\"usb9\"}' ;;\n"
            "  *) printf '%s\\n' '{}' ;;\n"
            "esac\n"
        )
        ubus.chmod(0o755)
        jsonfilter = bin_dir / "jsonfilter"
        jsonfilter.write_text(
            "#!/bin/sh\n"
            "case \"$*\" in\n"
            "  *'@.dynamic'*) printf true ;;\n"
            "  *'@.proto'*) printf dhcp ;;\n"
            "  *'@.device'*) printf usb9 ;;\n"
            "esac\n"
        )
        jsonfilter.chmod(0o755)

        result, runtime, _ = self.run_daemon(
            root, sysfs, config, check_only=False, ubus_path=ubus
        )

        self.assertNotEqual(result.returncode, 0)
        log = log_path.read_text()
        self.assertIn("add_device", log)
        self.assertIn("remove_device", log)
        self.assertIn("remove {}", log)
        self.assertFalse((runtime / "state").exists())

    def test_existing_interface_name_fails_closed_before_dynamic_creation(self):
        temp, root, sysfs, config = self.make_fixture()
        self.addCleanup(temp.cleanup)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        ubus = bin_dir / "ubus"
        marker = root / "add-dynamic-called"
        ubus.write_text(
            "#!/bin/sh\n"
            "case \"$*\" in\n"
            "  list) printf '%s\\n' 'network.interface.usb_uplink' ;;\n"
            f"  *'network add_dynamic'*) touch {marker}; exit 0 ;;\n"
            "  *) printf '%s\\n' '{}' ;;\n"
            "esac\n"
        )
        ubus.chmod(0o755)

        result, runtime, _ = self.run_daemon(
            root,
            sysfs,
            config,
            check_only=False,
            ubus_path=ubus,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(marker.exists())
        self.assertIn("interface name is already in use", (runtime / "status").read_text())

    def test_partial_attach_cleanup_failure_preserves_ownership_state(self):
        temp, root, sysfs, config = self.make_fixture()
        self.addCleanup(temp.cleanup)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        ubus = bin_dir / "ubus"
        log_path = root / "partial-attach-failure.log"
        ubus.write_text(
            "#!/bin/sh\n"
            f"printf '%s\\n' \"$*\" >> {log_path}\n"
            "case \"$3\" in\n"
            f"  add_device) if [ ! -e {root / 'add-device-seen'} ]; then touch {root / 'add-device-seen'}; exit 1; fi ;;\n"
            "  remove_device) exit 1 ;;\n"
            "  *) printf '%s\\n' '{}' ;;\n"
            "esac\n"
        )
        ubus.chmod(0o755)
        jsonfilter = bin_dir / "jsonfilter"
        jsonfilter.write_text(
            "#!/bin/sh\n"
            "case \"$*\" in\n"
            "  *'@.dynamic'*) printf true ;;\n"
            "  *'@.proto'*) printf dhcp ;;\n"
            "  *'@.device'*) printf usb9 ;;\n"
            "esac\n"
        )
        jsonfilter.chmod(0o755)

        result, runtime, _ = self.run_daemon(
            root, sysfs, config, check_only=False, ubus_path=ubus
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((runtime / "state").exists())
        self.assertIn("cleanup", (runtime / "status").read_text())

    def test_state_save_failure_cleans_dynamic_interface(self):
        temp, root, sysfs, config = self.make_fixture()
        self.addCleanup(temp.cleanup)
        state_parent = root / "state-parent"
        state_parent.write_text("not a directory\n")
        state_file = state_parent / "state"
        result, runtime, log_path = self.run_daemon(
            root,
            sysfs,
            config,
            check_only=False,
            fake_ubus=True,
            state_file=state_file,
        )

        self.assertNotEqual(result.returncode, 0)
        log = log_path.read_text()
        self.assertIn("add_dynamic", log)
        self.assertIn("remove {}", log)
        self.assertIn("persist ownership state", (runtime / "status").read_text())

    def test_partial_dynamic_interface_without_device_is_owned_for_cleanup(self):
        temp, root, sysfs, config = self.make_fixture()
        self.addCleanup(temp.cleanup)
        runtime = root / "run"
        runtime.mkdir()
        (runtime / "state").write_text(self.ownership_state(sysfs))
        bin_dir = root / "bin"
        bin_dir.mkdir()
        ubus = bin_dir / "ubus"
        log_path = root / "partial-cleanup.log"
        ubus.write_text(
            "#!/bin/sh\n"
            f"removed={root / 'dynamic-removed'}\n"
            f"printf '%s\\n' \"$*\" >> {log_path}\n"
            "case \"$3\" in\n"
            "  remove) touch \"$removed\" ;;\n"
            "  status)\n"
            "    [ -e \"$removed\" ] && exit 1\n"
            "    printf '%s\\n' '{\"dynamic\":true,\"proto\":\"dhcp\"}' ;;\n"
            "  *) printf '%s\\n' '{}' ;;\n"
            "esac\n"
        )
        ubus.chmod(0o755)
        jsonfilter = bin_dir / "jsonfilter"
        jsonfilter.write_text(
            "#!/bin/sh\n"
            "case \"$*\" in\n"
            "  *'@.dynamic'*) printf true ;;\n"
            "  *'@.proto'*) printf dhcp ;;\n"
            "esac\n"
        )
        jsonfilter.chmod(0o755)

        result, _, _ = self.run_daemon(
            root,
            sysfs,
            config,
            check_only=False,
            ubus_path=ubus,
            command="--rollback",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        log = log_path.read_text()
        self.assertNotIn("remove_device", log)
        self.assertIn("remove {}", log)
        self.assertFalse((runtime / "state").exists())

    def test_partial_cleanup_does_not_require_remove_device_when_unattached(self):
        temp, root, sysfs, config = self.make_fixture()
        self.addCleanup(temp.cleanup)
        runtime = root / "run"
        runtime.mkdir()
        (runtime / "state").write_text(self.ownership_state(sysfs))
        bin_dir = root / "bin"
        bin_dir.mkdir()
        ubus = bin_dir / "ubus"
        log_path = root / "partial-unattached.log"
        ubus.write_text(
            "#!/bin/sh\n"
            f"removed={root / 'dynamic-removed'}\n"
            f"printf '%s\\n' \"$*\" >> {log_path}\n"
            "case \"$3\" in\n"
            "  remove_device) exit 1 ;;\n"
            "  remove) touch \"$removed\" ;;\n"
            "  status)\n"
            "    [ -e \"$removed\" ] && exit 1\n"
            "    printf '%s\\n' '{\"dynamic\":true,\"proto\":\"dhcp\"}' ;;\n"
            "  *) printf '%s\\n' '{}' ;;\n"
            "esac\n"
        )
        ubus.chmod(0o755)
        jsonfilter = bin_dir / "jsonfilter"
        jsonfilter.write_text(
            "#!/bin/sh\n"
            "case \"$*\" in\n"
            "  *'@.dynamic'*) printf true ;;\n"
            "  *'@.proto'*) printf dhcp ;;\n"
            "  *'@.device'*) printf '' ;;\n"
            "esac\n"
        )
        jsonfilter.chmod(0o755)

        result, _, _ = self.run_daemon(
            root,
            sysfs,
            config,
            check_only=False,
            ubus_path=ubus,
            command="--rollback",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        log = log_path.read_text()
        self.assertNotIn("remove_device", log)
        self.assertIn("remove {}", log)

    def test_cleanup_failure_preserves_ownership_state(self):
        temp, root, sysfs, config = self.make_fixture()
        self.addCleanup(temp.cleanup)

        first, runtime, log_path = self.run_daemon(
            root, sysfs, config, check_only=False, fake_ubus=True
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        shutil.rmtree(sysfs / "class" / "net" / "usb9")

        ubus = root / "bin" / "ubus"
        ubus.write_text(
            "#!/bin/sh\n"
            f"printf '%s\\n' \"$*\" >> {root / 'ubus-failure.log'}\n"
            "case \"$3\" in\n"
            "  remove_device) exit 1 ;;\n"
            "  *) exit 0 ;;\n"
            "esac\n"
        )
        ubus.chmod(0o755)

        second, _, _ = self.run_daemon(
            root, sysfs, config, check_only=False, ubus_path=ubus
        )

        self.assertNotEqual(second.returncode, 0)
        self.assertIn("created=1", (runtime / "state").read_text())
        self.assertIn("cleanup failed", (runtime / "status").read_text())

    def test_async_dynamic_interface_removal_must_complete_before_state_clear(self):
        temp, root, sysfs, config = self.make_fixture()
        self.addCleanup(temp.cleanup)
        runtime = root / "run"
        runtime.mkdir()
        (runtime / "state").write_text(self.ownership_state(sysfs))
        bin_dir = root / "bin"
        bin_dir.mkdir()
        ubus = bin_dir / "ubus"
        ubus.write_text(
            "#!/bin/sh\n"
            "case \"$1:$3\" in\n"
            "  list:) printf '%s\\n' 'network.interface.usb_uplink' ;;\n"
            "  call:status) printf '%s\\n' '{\"dynamic\":true,\"proto\":\"dhcp\",\"device\":\"usb9\"}' ;;\n"
            "  *) exit 0 ;;\n"
            "esac\n"
        )
        ubus.chmod(0o755)
        jsonfilter = bin_dir / "jsonfilter"
        jsonfilter.write_text(
            "#!/bin/sh\n"
            "case \"$*\" in\n"
            "  *'@.dynamic'*) printf true ;;\n"
            "  *'@.proto'*) printf dhcp ;;\n"
            "  *'@.device'*) printf usb9 ;;\n"
            "esac\n"
        )
        jsonfilter.chmod(0o755)

        result, _, _ = self.run_daemon(
            root,
            sysfs,
            config,
            check_only=False,
            ubus_path=ubus,
            command="--rollback",
            extra_env={
                "USB_UPLINK_REMOVE_WAIT_ATTEMPTS": "1",
                "USB_UPLINK_REMOVE_POLL_SECONDS": "0",
            },
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((runtime / "state").exists())
        self.assertIn(
            "dynamic interface removal did not complete",
            (runtime / "status").read_text(),
        )

    def test_ubus_failure_during_async_removal_preserves_ownership_state(self):
        temp, root, sysfs, config = self.make_fixture()
        self.addCleanup(temp.cleanup)
        runtime = root / "run"
        runtime.mkdir()
        (runtime / "state").write_text(self.ownership_state(sysfs))
        bin_dir = root / "bin"
        bin_dir.mkdir()
        ubus = bin_dir / "ubus"
        ubus.write_text(
            "#!/bin/sh\n"
            "case \"$1:$3\" in\n"
            "  list:) exit 1 ;;\n"
            "  call:status) printf '%s\\n' '{\"dynamic\":true,\"proto\":\"dhcp\",\"device\":\"usb9\"}' ;;\n"
            "  *) exit 0 ;;\n"
            "esac\n"
        )
        ubus.chmod(0o755)
        jsonfilter = bin_dir / "jsonfilter"
        jsonfilter.write_text(
            "#!/bin/sh\n"
            "case \"$*\" in\n"
            "  *'@.dynamic'*) printf true ;;\n"
            "  *'@.proto'*) printf dhcp ;;\n"
            "  *'@.device'*) printf usb9 ;;\n"
            "esac\n"
        )
        jsonfilter.chmod(0o755)

        result, _, _ = self.run_daemon(
            root,
            sysfs,
            config,
            check_only=False,
            ubus_path=ubus,
            command="--rollback",
            extra_env={
                "USB_UPLINK_REMOVE_WAIT_ATTEMPTS": "1",
                "USB_UPLINK_REMOVE_POLL_SECONDS": "0",
            },
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((runtime / "state").exists())
        self.assertIn(
            "unable to verify dynamic interface removal",
            (runtime / "status").read_text(),
        )

    def test_state_delete_failure_preserves_ownership_state(self):
        temp, root, sysfs, config = self.make_fixture()
        self.addCleanup(temp.cleanup)
        runtime = root / "run"
        runtime.mkdir()
        state_dir = root / "readonly-state"
        state_dir.mkdir()
        state_file = state_dir / "state"
        state_file.write_text(self.ownership_state(sysfs))
        bin_dir = root / "bin"
        bin_dir.mkdir()
        ubus = bin_dir / "ubus"
        ubus.write_text(
            "#!/bin/sh\n"
            "case \"$3\" in\n"
            "  status) printf '%s\\n' '{\"dynamic\":true,\"proto\":\"dhcp\",\"device\":\"usb9\"}' ;;\n"
            "  *) printf '%s\\n' '{}' ;;\n"
            "esac\n"
        )
        ubus.chmod(0o755)
        jsonfilter = bin_dir / "jsonfilter"
        jsonfilter.write_text(
            "#!/bin/sh\n"
            "case \"$*\" in\n"
            "  *'@.dynamic'*) printf true ;;\n"
            "  *'@.proto'*) printf dhcp ;;\n"
            "  *'@.device'*) printf usb9 ;;\n"
            "esac\n"
        )
        jsonfilter.chmod(0o755)
        rm_path = bin_dir / "rm"
        rm_path.write_text(
            "#!/bin/sh\n"
            f"case \"$2\" in {state_file}) exit 1 ;; esac\n"
            "exec /bin/rm \"$@\"\n"
        )
        rm_path.chmod(0o755)

        result, _, _ = self.run_daemon(
            root,
            sysfs,
            config,
            check_only=False,
            ubus_path=ubus,
            command="--rollback",
            state_file=state_file,
            rm_path=rm_path,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(state_file.exists())
        self.assertIn("rollback failed", (runtime / "status").read_text())

    def test_state_delete_failure_can_retry_after_interface_is_removed(self):
        temp, root, sysfs, config = self.make_fixture()
        self.addCleanup(temp.cleanup)
        runtime = root / "run"
        runtime.mkdir()
        state_file = runtime / "state"
        state_file.write_text(self.ownership_state(sysfs))
        bin_dir = root / "bin"
        bin_dir.mkdir()
        removed = root / "dynamic-removed"
        ubus = bin_dir / "ubus"
        ubus.write_text(
            "#!/bin/sh\n"
            f"removed={removed}\n"
            "case \"$1:$3\" in\n"
            "  call:status)\n"
            "    [ ! -e \"$removed\" ] || exit 1\n"
            "    printf '%s\\n' '{\"dynamic\":true,\"proto\":\"dhcp\",\"device\":\"usb9\"}'\n"
            "    ;;\n"
            "  call:remove)\n"
            "    touch \"$removed\"\n"
            "    ;;\n"
            "  list:)\n"
            "    [ -e \"$removed\" ] || printf '%s\\n' 'network.interface.usb_uplink'\n"
            "    ;;\n"
            "  *) printf '%s\\n' '{}' ;;\n"
            "esac\n"
        )
        ubus.chmod(0o755)
        jsonfilter = bin_dir / "jsonfilter"
        jsonfilter.write_text(
            "#!/bin/sh\n"
            "case \"$*\" in\n"
            "  *'@.dynamic'*) printf true ;;\n"
            "  *'@.proto'*) printf dhcp ;;\n"
            "  *'@.device'*) printf usb9 ;;\n"
            "esac\n"
        )
        jsonfilter.chmod(0o755)
        marker = root / "state-rm-attempted"
        rm_path = bin_dir / "rm-once-fails"
        rm_path.write_text(
            "#!/bin/sh\n"
            f"case \"$2\" in {state_file})\n"
            f"  if [ ! -e {marker} ]; then touch {marker}; exit 1; fi\n"
            "  ;;\n"
            "esac\n"
            "exec /bin/rm \"$@\"\n"
        )
        rm_path.chmod(0o755)

        first, _, _ = self.run_daemon(
            root,
            sysfs,
            config,
            check_only=False,
            ubus_path=ubus,
            command="--rollback",
            state_file=state_file,
            rm_path=rm_path,
        )

        self.assertNotEqual(first.returncode, 0)
        self.assertTrue((runtime / "state").exists())
        self.assertTrue(removed.exists())

        second, _, _ = self.run_daemon(
            root,
            sysfs,
            config,
            check_only=False,
            ubus_path=ubus,
            command="--rollback",
            state_file=state_file,
            rm_path=rm_path,
        )

        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertFalse(state_file.exists())

    def test_empty_netdev_state_fails_closed(self):
        temp, root, sysfs, config = self.make_fixture()
        self.addCleanup(temp.cleanup)
        runtime = root / "run"
        runtime.mkdir()
        state_file = runtime / "state"
        state_file.write_text("created=1\nnetdev=\ninterface=usb_uplink\n")
        bin_dir = root / "bin"
        bin_dir.mkdir()
        rm_path = bin_dir / "rm"
        rm_path.write_text(
            "#!/bin/sh\n"
            f"case \"$2\" in {state_file}) exit 1 ;; esac\n"
            "exec /bin/rm \"$@\"\n"
        )
        rm_path.chmod(0o755)

        result, _, _ = self.run_daemon(
            root,
            sysfs,
            config,
            check_only=False,
            command="--rollback",
            state_file=state_file,
            rm_path=rm_path,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(state_file.exists())
        self.assertIn("invalid ownership state", (runtime / "status").read_text())

    def test_non_regular_state_path_fails_closed(self):
        temp, root, sysfs, config = self.make_fixture()
        self.addCleanup(temp.cleanup)
        runtime = root / "run"
        runtime.mkdir()
        state_file = runtime / "state"
        state_file.mkdir()

        result, _, _ = self.run_daemon(
            root,
            sysfs,
            config,
            check_only=False,
            command="--rollback",
            state_file=state_file,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "ownership state is not a regular file",
            (runtime / "status").read_text(),
        )

    def test_rollback_cli_refuses_non_dynamic_same_name(self):
        temp, root, sysfs, config = self.make_fixture()
        self.addCleanup(temp.cleanup)

        first, runtime, _ = self.run_daemon(
            root, sysfs, config, check_only=False, fake_ubus=True
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        shutil.rmtree(sysfs / "class" / "net" / "usb9")
        ubus = root / "bin" / "ubus"
        ubus.write_text(
            "#!/bin/sh\n"
            f"printf '%s\\n' \"$*\" >> {root / 'ownership.log'}\n"
            "case \"$3\" in\n"
            "  status) printf '%s\\n' '{\"dynamic\":false,\"proto\":\"dhcp\",\"device\":\"usb9\"}' ;;\n"
            "  *) exit 0 ;;\n"
            "esac\n"
        )
        ubus.chmod(0o755)

        result, _, _ = self.run_daemon(
            root,
            sysfs,
            config,
            check_only=False,
            ubus_path=ubus,
            command="--rollback",
        )

        self.assertNotEqual(result.returncode, 0)
        log = (root / "ownership.log").read_text()
        self.assertIn("status", log)
        self.assertNotIn("remove_device", log)
        self.assertNotIn(" down ", log)
        self.assertNotIn(" remove ", log)
        self.assertIn("ownership", (runtime / "status").read_text())

    def test_invalid_ownership_state_fails_closed(self):
        temp, root, sysfs, config = self.make_fixture()
        self.addCleanup(temp.cleanup)
        runtime = root / "run"
        runtime.mkdir()
        (runtime / "state").write_text(
            "created=1\nnetdev=usb9;bad\ninterface=usb_uplink\n"
        )

        result, _, _ = self.run_daemon(root, sysfs, config, check_only=False)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid ownership state", (runtime / "status").read_text())

    def test_stale_lock_is_recovered(self):
        temp, root, sysfs, config = self.make_fixture()
        self.addCleanup(temp.cleanup)
        runtime = root / "run"
        runtime.mkdir()
        lock = runtime / "lock"
        lock.mkdir()
        (lock / "pid").write_text("99999999\n")

        result, _, _ = self.run_daemon(
            root, sysfs, config, check_only=False, fake_ubus=True
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("detected=1", (runtime / "status").read_text())


if __name__ == "__main__":
    unittest.main()
