import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SWITCH = ROOT / "package" / "arkbridge-usb" / "files" / "usr" / "libexec" / "usb-uplink-modeswitch"


class ModeSwitchTests(unittest.TestCase):
    def run_switch(self, config, *, product, tool_body="exit 0"):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tool = root / "usbmode"
            log = root / "tool.log"
            tool.write_text(
                "#!/bin/sh\n"
                f"printf '%s\\n' \"$*\" >> {log}\n"
                f"{tool_body}\n"
            )
            tool.chmod(0o755)
            config_path = root / "usb_uplink.conf"
            config_path.write_text(config)
            env = os.environ.copy()
            env.update(
                {
                    "USB_UPLINK_TEST_MODE": "1",
                    "USB_UPLINK_CONFIG_FILE": str(config_path),
                    "USB_UPLINK_MODE_SWITCH_PRODUCT": product,
                    "USB_UPLINK_MODE_SWITCH_TOOL": str(tool),
                    "USB_UPLINK_MODE_SWITCH_LOCK_DIR": str(root / "lock"),
                    "USB_UPLINK_MODE_SWITCH_RUNTIME_DIR": str(root / "run"),
                    "USB_UPLINK_MODE_SWITCH_SLEEP_BIN": "/bin/true",
                }
            )
            result = subprocess.run(
                [str(SWITCH)],
                env=env,
                capture_output=True,
                text=True,
            )
            calls = log.read_text().splitlines() if log.exists() else []
            return result, calls

    def test_disabled_mode_never_invokes_switch_tool(self):
        result, calls = self.run_switch(
            "mode_switch_enabled=0\n"
            "mode_switch_ids=12d1:1f01\n"
            "mode_switch_config=/etc/usb-mode.json\n",
            product="12d1/1f01/0100",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, [])

    def test_matching_storage_id_invokes_usbmode_with_config(self):
        result, calls = self.run_switch(
            "mode_switch_enabled=1\n"
            "mode_switch_ids=12d1:1f01,12d1:1506\n"
            "mode_switch_config=/etc/usb-uplink/e6878-usb-mode.json\n",
            product="12D1/1F01/0100",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            calls,
            ["-s -c /etc/usb-uplink/e6878-usb-mode.json"],
        )

    def test_non_matching_id_does_not_invoke_switch_tool(self):
        result, calls = self.run_switch(
            "mode_switch_enabled=1\n"
            "mode_switch_ids=12d1:1f01\n"
            "mode_switch_config=/etc/usb-mode.json\n",
            product="12d1/14db/0100",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, [])

    def test_switch_failure_is_reported(self):
        result, calls = self.run_switch(
            "mode_switch_enabled=1\n"
            "mode_switch_ids=12d1:1f01\n"
            "mode_switch_config=/etc/usb-mode.json\n",
            product="12d1/1f01/0100",
            tool_body="exit 7",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, ["-s -c /etc/usb-mode.json"])


if __name__ == "__main__":
    unittest.main()
