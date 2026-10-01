import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FILES = ROOT / "package" / "arkbridge-usb" / "files"
CHECKER = FILES / "usr" / "libexec" / "usb-uplink-client-path"
CLI = FILES / "usr" / "sbin" / "usb-uplink"


class ClientPathCheckerFixtureTests(unittest.TestCase):
    """Read-only S0-S3 gate checker, driven entirely by fixtures.

    These tests never require OpenWrt, uci, ubus, ip, or jsonfilter.
    """

    def make_fixture(
        self,
        *,
        mode="standby",
        failover_enabled="0",
        route_control_enabled="0",
        defaultroute="0",
        peerdns="0",
        interface="usb_uplink",
        netdev="eth7",
        include_uplink_state=True,
        dynamic="true",
        proto="dhcp",
        device="eth7",
        up="true",
        address="192.0.2.2",
        mask="24",
        valid="3600",
        include_route=True,
        route_device="br-wan",
        client_gateway="192.168.1.1",
        include_firewall=True,
        firewall_zone_netdev="eth7",
        masq="1",
        forwarding="lan",
        include_dhcp=True,
        dhcp_ignore="0",
        client_path_recovery="s0-hash",
        recovery_operation=None,
        pending_journal=False,
    ):
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)

        config_lines = [
            f"mode={mode}",
            f"failover_enabled={failover_enabled}",
            f"route_control_enabled={route_control_enabled}",
            f"defaultroute={defaultroute}",
            f"peerdns={peerdns}",
            f"interface_name={interface}",
            "primary_device=br-lan",
            "lan_bridge=br-lan",
            "usb_zone=usb_uplink",
            "lan_zone=lan",
        ]
        if client_path_recovery is not None:
            config_lines.append(f"client_path_recovery={client_path_recovery}")
        (root / "usb_uplink.conf").write_text("\n".join(config_lines) + "\n")

        runtime = root / "run"
        runtime.mkdir()
        if include_uplink_state:
            (root / "uplink-state").write_text(
                f"netdev={netdev}\ninterface={interface}\n"
                "usb_id=12d1:14db\ndriver=cdc_ether\n"
            )
        if recovery_operation is not None:
            (runtime / "cli-recovery").write_text(
                f"operation={recovery_operation}\ntimestamp=1\n"
            )

        status_parts = [
            f'"dynamic":{dynamic}',
            f'"proto":"{proto}"',
            f'"device":"{device}"',
            f'"up":{up}',
        ]
        if address is not None:
            addr = f'"address":"{address}"'
            if mask is not None:
                addr += f',"mask":{mask}'
            if valid is not None:
                addr += f',"valid":{valid}'
            status_parts.append(f'"ipv4-address":[{{{addr}}}]')

        (root / "uplink-status").write_text(
            "{" + ",".join(status_parts) + "}\n"
        )

        if include_route:
            (root / "ip-route").write_text(
                f"default via 198.51.100.1 dev {route_device} proto dhcp metric 0\n"
            )
        else:
            (root / "ip-route").write_text("")

        if pending_journal:
            (root / "failover-pending").write_text("operation=switch\n")

        if client_gateway is not None:
            (root / "client-gateway").write_text(f"{client_gateway}\n")

        if include_firewall:
            firewall = [
                "firewall.usb_zone=zone",
                "firewall.usb_zone.name=usb_uplink",
                f"firewall.usb_zone.network={firewall_zone_netdev}",
                f"firewall.usb_zone.masq={masq}",
                "firewall.lan=zone",
                "firewall.lan.name=lan",
                "firewall.lan.network=br-lan",
            ]
            if forwarding is not None:
                firewall.extend(
                    [
                        "firewall.fwd=forwarding",
                        f"firewall.fwd.src={forwarding}",
                        "firewall.fwd.dest=usb_uplink",
                    ]
                    if forwarding == "lan"
                    else [
                        "firewall.fwd=forwarding",
                        f"firewall.fwd.src={forwarding}",
                        "firewall.fwd.dest=usb_uplink",
                    ]
                )
            (root / "uci-show-firewall").write_text("\n".join(firewall) + "\n")

        if include_dhcp:
            (root / "uci-show-dhcp").write_text(
                "dhcp.lan=dhcp\n"
                "dhcp.lan.interface=lan\n"
                f"dhcp.lan.ignore={dhcp_ignore}\n"
            )

        env = os.environ.copy()
        env.update(
            {
                "USB_UPLINK_TEST_MODE": "1",
                "USB_UPLINK_CONFIG_FILE": str(root / "usb_uplink.conf"),
                "USB_UPLINK_RUNTIME_DIR": str(runtime),
                "USB_UPLINK_CLIENT_PATH_FIXTURE": str(root),
            }
        )
        return temp, root, runtime, env

    def run_checker(self, env, command=None):
        return subprocess.run(
            [str(CHECKER)] if command is None else command,
            capture_output=True,
            text=True,
            env=env,
        )

    def gate_output(self, stdout, gate):
        for line in stdout.splitlines():
            if line.startswith(f"gate={gate} "):
                return line
        return ""

    def test_passes_s0_through_s2_and_never_passes_s3(self):
        temp, root, runtime, env = self.make_fixture()
        self.addCleanup(temp.cleanup)

        result = self.run_checker(env)
        out = result.stdout

        self.assertEqual(self.gate_output(out, "S0").split()[1], "status=passed")
        self.assertEqual(self.gate_output(out, "S1").split()[1], "status=passed")
        self.assertEqual(self.gate_output(out, "S2").split()[1], "status=passed")
        s3 = self.gate_output(out, "S3")
        self.assertIn("status=blocked", s3)
        self.assertIn("cannot pass Path B", s3)
        self.assertIn("note=not client connectivity proof", out)
        self.assertEqual(result.returncode, 1)
        self.assertIn("result=blocked", out)

    def test_fails_closed_without_any_evidence_or_tools(self):
        # No fixture dir and no live tools: every gate must block or be unknown.
        env = {
            **os.environ,
            "USB_UPLINK_TEST_MODE": "0",
            "USB_UPLINK_RUNTIME_DIR": tempfile.mkdtemp(),
        }
        env.pop("USB_UPLINK_CLIENT_PATH_FIXTURE", None)

        result = subprocess.run(
            [str(CHECKER)], capture_output=True, text=True, env=env
        )

        self.assertNotEqual(result.returncode, 0)
        for gate in ("S0", "S1", "S2", "S3"):
            self.assertNotIn(
                f"gate={gate} status=passed",
                result.stdout,
                f"{gate} must not pass without evidence",
            )

    def test_non_standby_defaults_block_s1(self):
        temp, root, runtime, env = self.make_fixture(
            mode="failover", failover_enabled="1"
        )
        self.addCleanup(temp.cleanup)

        result = self.run_checker(env)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("status=blocked", self.gate_output(result.stdout, "S1"))
        self.assertNotIn("gate=S1 status=passed", result.stdout)

    def test_non_dynamic_interface_blocks_s1(self):
        temp, root, runtime, env = self.make_fixture(dynamic="false")
        self.addCleanup(temp.cleanup)

        result = self.run_checker(env)

        self.assertIn("status=blocked", self.gate_output(result.stdout, "S1"))

    def test_interface_not_up_blocks_s1(self):
        temp, root, runtime, env = self.make_fixture(up="false")
        self.addCleanup(temp.cleanup)

        result = self.run_checker(env)

        self.assertIn("is not up", self.gate_output(result.stdout, "S1"))

    def test_device_mismatch_blocks_s1(self):
        temp, root, runtime, env = self.make_fixture(device="eth9")
        self.addCleanup(temp.cleanup)

        result = self.run_checker(env)

        self.assertIn(
            "does not match ownership state",
            self.gate_output(result.stdout, "S1"),
        )

    def test_missing_lease_lifetime_blocks_s1(self):
        temp, root, runtime, env = self.make_fixture(valid=None)
        self.addCleanup(temp.cleanup)

        result = self.run_checker(env)

        self.assertIn("lifetime", self.gate_output(result.stdout, "S1"))

    def test_usb_as_primary_route_blocks_s2(self):
        temp, root, runtime, env = self.make_fixture(route_device="eth7")
        self.addCleanup(temp.cleanup)

        result = self.run_checker(env)

        self.assertIn(
            "acting as a primary default route",
            self.gate_output(result.stdout, "S2"),
        )

    def test_pending_journal_blocks_s2(self):
        temp, root, runtime, env = self.make_fixture(pending_journal=True)
        self.addCleanup(temp.cleanup)

        result = self.run_checker(env)

        self.assertIn(
            "pending failover transaction",
            self.gate_output(result.stdout, "S2"),
        )

    def test_recovery_evidence_blocks_s0(self):
        temp, root, runtime, env = self.make_fixture(
            recovery_operation="set-mode"
        )
        self.addCleanup(temp.cleanup)

        result = self.run_checker(env)

        self.assertIn("status=blocked", self.gate_output(result.stdout, "S0"))

    def test_missing_client_gateway_blocks_s3(self):
        temp, root, runtime, env = self.make_fixture(client_gateway=None)
        self.addCleanup(temp.cleanup)

        result = self.run_checker(env)

        self.assertIn("default-gateway ownership", self.gate_output(result.stdout, "S3"))

    def test_zone_without_netdev_blocks_s3(self):
        temp, root, runtime, env = self.make_fixture(
            firewall_zone_netdev="br-other"
        )
        self.addCleanup(temp.cleanup)

        result = self.run_checker(env)

        self.assertIn(
            "does not include the USB uplink netdev",
            self.gate_output(result.stdout, "S3"),
        )

    def test_missing_masquerade_blocks_s3(self):
        temp, root, runtime, env = self.make_fixture(masq="0")
        self.addCleanup(temp.cleanup)

        result = self.run_checker(env)

        self.assertIn("no masquerade rule", self.gate_output(result.stdout, "S3"))

    def test_missing_forwarding_blocks_s3(self):
        temp, root, runtime, env = self.make_fixture(forwarding="")
        self.addCleanup(temp.cleanup)

        result = self.run_checker(env)

        self.assertIn("no firewall forwarding", self.gate_output(result.stdout, "S3"))

    def test_disabled_lan_dhcp_blocks_s3(self):
        temp, root, runtime, env = self.make_fixture(dhcp_ignore="1")
        self.addCleanup(temp.cleanup)

        result = self.run_checker(env)

        self.assertIn("DHCP/DNS authority", self.gate_output(result.stdout, "S3"))

    def test_missing_client_recovery_evidence_blocks_s3(self):
        temp, root, runtime, env = self.make_fixture(client_path_recovery=None)
        self.addCleanup(temp.cleanup)

        result = self.run_checker(env)

        self.assertIn(
            "client-path rollback/recovery evidence",
            self.gate_output(result.stdout, "S3"),
        )

    def test_cli_subcommand_delegates_to_checker(self):
        temp, root, runtime, env = self.make_fixture()
        self.addCleanup(temp.cleanup)
        env["USB_UPLINK_CLIENT_PATH_CHECKER"] = str(CHECKER)

        result = subprocess.run(
            [str(CLI), "client-path-check"],
            capture_output=True,
            text=True,
            env=env,
        )

        self.assertIn("checker=usb-uplink-client-path", result.stdout)
        self.assertIn("result=", result.stdout)
        # S3 must never pass through the CLI either.
        self.assertNotIn("gate=S3 status=passed", result.stdout)

    def test_cli_reports_unavailable_checker_fail_closed(self):
        env = {**os.environ, "USB_UPLINK_CLIENT_PATH_CHECKER": "/nonexistent/helper"}

        result = subprocess.run(
            [str(CLI), "client-path-check"],
            capture_output=True,
            text=True,
            env=env,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("status=unknown", result.stdout)


if __name__ == "__main__":
    unittest.main()
