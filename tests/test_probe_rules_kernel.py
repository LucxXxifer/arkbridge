"""Opt-in Linux policy-rule tests. Run inside an isolated network namespace.

sudo unshare --net env ARKBRIDGE_KERNEL_TEST=1 python3 -m unittest discover \
    -s tests -p test_probe_rules_kernel.py -v
"""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ENGINE = Path(__file__).resolve().parents[1] / "package/arkbridge/files/usr/libexec/arkbridge"


@unittest.skipUnless(os.environ.get("ARKBRIDGE_KERNEL_TEST") == "1", "requires isolated Linux network namespace")
class ProbeRulesKernelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="arkbridge-kernel-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ip = shutil.which("ip")
        self.assertIsNotNone(self.ip)
        self.call_ip("link", "add", "abprobe0", "type", "dummy")
        self.addCleanup(self.call_ip, "link", "del", "abprobe0")
        self.call_ip("link", "set", "abprobe0", "up")
        self.call_ip("addr", "add", "192.0.2.2/24", "dev", "abprobe0")
        self.call_ip("-6", "addr", "add", "2001:db8:0:1::2/64", "dev", "abprobe0", "nodad")
        self.functions = self.root / "functions.sh"
        self.functions.write_text(ENGINE.read_text().rsplit('case "${1:-}" in', 1)[0])
        wrapper = self.root / "ip"
        wrapper.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$AB_WORK/calls"\n'
                           'case "$*" in *"rule show"*) [ ! -f "$AB_WORK/query_fail" ] || exit 2;; esac\n'
                           'exec "$AB_REAL_IP" "$@"\n')
        wrapper.chmod(0o700)
        self.env = dict(os.environ, ARKBRIDGE_RUNDIR=str(self.root / "run"),
                        ARKBRIDGE_IP_BIN=str(wrapper), AB_REAL_IP=self.ip, AB_WORK=str(self.root))
        self.addCleanup(self.clear_rules)

    def call_ip(self, *args):
        return subprocess.run([self.ip, *args], text=True, capture_output=True, check=True).stdout

    def clear_rules(self):
        for family in ("-4", "-6"):
            for pref in ("3000", "3002"):
                for _ in range(10):
                    result = subprocess.run([self.ip, family, "rule", "del", "pref", pref], capture_output=True)
                    if result.returncode:
                        break
            subprocess.run([self.ip, family, "route", "flush", "table", "250"], capture_output=True)

    def install(self, family="4", source=None, pref="3000", repeat=1):
        source = source or ("192.0.2.2" if family == "4" else "2001:0db8:0:1::2")
        target = "203.0.113.10" if family == "4" else "2001:0db8:0:2::1"
        gw = "192.0.2.1" if family == "4" else "2001:db8:0:1::1"
        script = '. "$1"; BYPASS=0; BYPASS6=0; n=0; while [ "$n" -lt "$7" ]; do install_probe "$2" "$3" "$4" abprobe0 250 "$5" 1 "$6" || exit 1; n=$((n+1)); done'
        return subprocess.run([os.environ.get("ARKBRIDGE_TEST_SHELL", "/bin/sh"), "-c", script,
                               "test", str(self.functions), family, target, gw, pref, source, str(repeat)],
                              env=self.env, capture_output=True, text=True)

    def assert_installed(self, **kwargs):
        result = self.install(**kwargs)
        self.assertEqual(result.returncode, 0, result.stderr + (self.root / "run/log").read_text() if (self.root / "run/log").exists() else result.stderr)

    def reset_calls(self):
        (self.root / "calls").write_text("")

    def assert_no_rule_mutations(self):
        self.assertIsNone(re.search(r"rule (?:add|del) ", (self.root / "calls").read_text()))

    def test_repeated_ipv4_and_ipv6_probes_do_not_mutate_rules(self):
        for family in ("4", "6"):
            self.assert_installed(family=family)
            self.reset_calls()
            self.assert_installed(family=family, repeat=30)
            self.assert_no_rule_mutations()
            self.assertEqual(len(self.call_ip("-" + family, "rule", "show", "pref", "3000").splitlines()), 1)
        self.assertEqual(len((self.root / "run/probe_rules").read_text().splitlines()), 2)

    def test_missing_rule_and_missing_ownership_recover(self):
        self.assert_installed()
        self.call_ip("-4", "rule", "del", "pref", "3000")
        self.assert_installed()
        (self.root / "run/probe_rules").unlink()
        self.reset_calls()
        self.assert_installed()
        self.assert_no_rule_mutations()
        self.assertEqual(len(self.call_ip("-4", "rule", "show", "pref", "3000").splitlines()), 1)

    def test_source_priority_and_ipv6_spelling_changes(self):
        self.assert_installed()
        self.assert_installed(source="192.0.2.7", pref="3002")
        self.assertEqual(self.call_ip("-4", "rule", "show", "pref", "3000"), "")
        self.assertIn("from 192.0.2.7", self.call_ip("-4", "rule", "show", "pref", "3002"))
        self.assert_installed(family="6")
        self.assert_installed(family="6", source="2001:db8:0:1::2")
        self.assertEqual(len(self.call_ip("-6", "rule", "show", "pref", "3000").splitlines()), 1)
        self.reset_calls()
        self.assert_installed(family="6", source="2001:0db8:0:1::2")
        self.assert_no_rule_mutations()

    def test_broader_prefix_is_not_treated_as_exact_probe_rule(self):
        self.call_ip("-4", "rule", "add", "pref", "3000", "from", "192.0.2.0/24",
                     "to", "203.0.113.10", "lookup", "250")
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        rules = self.call_ip("-4", "rule", "show", "pref", "3000")
        self.assertEqual(len(rules.splitlines()), 1)
        self.assertIn("from 192.0.2.0/24", rules)

    def test_stale_owned_tuple_does_not_delete_foreign_marked_rule(self):
        self.assert_installed()
        self.call_ip("-4", "rule", "del", "pref", "3000", "from", "192.0.2.2",
                     "to", "203.0.113.10", "lookup", "250")
        self.call_ip("-4", "rule", "add", "pref", "3000", "from", "192.0.2.2",
                     "to", "203.0.113.10", "fwmark", "1", "lookup", "250")
        before = self.call_ip("-4", "rule", "show", "pref", "3000")
        (self.root / "run/probe_rules").write_text(
            "4 3000 192.0.2.2/32 203.0.113.10/32 250\n")
        result = self.install(source="192.0.2.7")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.call_ip("-4", "rule", "show", "pref", "3000"), before)

    def test_unrelated_source_rule_at_same_priority_is_preserved(self):
        self.call_ip("-4", "rule", "add", "pref", "3000", "from", "192.0.2.9",
                     "to", "203.0.113.10", "lookup", "250")
        self.assert_installed()
        rules = self.call_ip("-4", "rule", "show", "pref", "3000")
        self.assertEqual(len(rules.splitlines()), 2)
        self.assertIn("from 192.0.2.9", rules)
        self.assertIn("from 192.0.2.2", rules)

    def test_noncovering_source_prefix_at_same_priority_is_preserved(self):
        cases = (
            ("4", "198.51.100.0/24", "203.0.113.10"),
            ("6", "2001:db8:0:2::/64", "2001:db8:0:2::1"),
        )
        for family, source, target in cases:
            with self.subTest(family=family):
                self.call_ip("-" + family, "rule", "add", "pref", "3000", "from", source,
                             "to", target, "lookup", "250")
                result = self.install(family=family)
                self.assertEqual(result.returncode, 0,
                                 result.stderr + (self.root / "run/log").read_text()
                                 if (self.root / "run/log").exists() else result.stderr)
                rules = self.call_ip("-" + family, "rule", "show", "pref", "3000")
                self.assertEqual(len(rules.splitlines()), 2)
                self.assertIn("from " + source, rules)

    def test_legacy_destination_rule_is_migrated_once(self):
        self.call_ip("-4", "rule", "add", "pref", "3000", "to", "203.0.113.10", "lookup", "250")
        self.assert_installed()
        self.reset_calls()
        self.assert_installed()
        self.assert_no_rule_mutations()
        rules = self.call_ip("-4", "rule", "show", "pref", "3000")
        self.assertEqual(len(rules.splitlines()), 1)
        self.assertIn("from 192.0.2.2", rules)

    def test_marked_rule_is_preserved_and_not_accepted_as_probe(self):
        self.call_ip("-4", "rule", "add", "pref", "3000", "from", "192.0.2.2", "to", "203.0.113.10", "fwmark", "1", "lookup", "250")
        self.assertNotEqual(self.install().returncode, 0)
        rules = self.call_ip("-4", "rule", "show", "pref", "3000")
        self.assertEqual(len(rules.splitlines()), 1)
        self.assertIn("fwmark 0x1", rules)

    def test_query_failure_leaves_kernel_untouched(self):
        self.assert_installed()
        rules = self.call_ip("-4", "rule", "show", "pref", "3000")
        (self.root / "query_fail").touch()
        self.reset_calls()
        self.assertNotEqual(self.install().returncode, 0)
        self.assert_no_rule_mutations()
        self.assertEqual(self.call_ip("-4", "rule", "show", "pref", "3000"), rules)

    def test_ambiguous_legacy_rule_is_preserved(self):
        self.call_ip("-4", "rule", "add", "pref", "3000", "to", "203.0.113.10", "lookup", "250")
        self.call_ip("-4", "rule", "add", "pref", "3000", "from", "192.0.2.9", "to", "203.0.113.10", "lookup", "250")
        rules = self.call_ip("-4", "rule", "show", "pref", "3000")
        self.assertNotEqual(self.install().returncode, 0)
        self.assertEqual(self.call_ip("-4", "rule", "show", "pref", "3000"), rules)


if __name__ == "__main__":
    unittest.main()
