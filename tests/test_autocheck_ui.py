"""Run the complete LuCI JavaScript against synthetic DOM/tool inputs."""
import shutil
import subprocess
import unittest
from pathlib import Path


class AutocheckUITests(unittest.TestCase):
    def run_ui(self, name):
        node = shutil.which("node")
        self.assertIsNotNone(node, "Node.js is required for the LuCI regression tests")
        result = subprocess.run(
            [node, str(Path(__file__).with_name(name))],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_legacy_autocheck(self):
        self.run_ui("legacy_autocheck.js")

    def test_modern_autocheck(self):
        self.run_ui("modern_autocheck.js")
