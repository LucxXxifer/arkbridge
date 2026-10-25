#!/usr/bin/env python3
"""Run the complete ArkBridge engine against hermetic external-tool fixtures.

No engine functions are mocked or rewritten. The engine owns its ordinary
runtime files; fake ip/curl/uci/iptables exchange state through one JSON file.
No real routing command is invoked. Run under Linux /bin/sh or BusyBox ash.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / 'package/arkbridge/files/usr/libexec/arkbridge'
SHELL = ['/bin/sh']

FIXTURE = r'''import json,os,pathlib,sys
p=pathlib.Path(os.environ['ARK_TEST_MODEL'])
s=json.loads(p.read_text()); a=sys.argv[1:]; tool=pathlib.Path(sys.argv[0]).name
s['calls'].append([tool,*a]); rc=0; out=''
def primary_addr():
 global rc
 s['primary_addr_calls']+=1
 k=s['scenario']; n=s['primary_addr_calls']
 if k=='query_failure': rc=42; return ''
 if k=='first_query_failed' and n%2: rc=42; return ''
 if k=='invalid_nonempty': return 'malformed-address-record\n'
 if k=='whitespace_nonempty': return ' \n'
 if k=='address_race':
  return '' if n%2 else '2: primary0    inet 198.18.1.2/24 scope global primary0\n'
 if k in ('empty','first_query_failed','backup_dead'): return ''
 return '2: primary0    inet 198.18.1.2/24 scope global primary0\n'
if tool=='uci':
 key=a[-1].removeprefix('arkbridge.main.')
 if key in s['config']: out=s['config'][key]+'\n'
 else: rc=1
elif tool=='date':
 out='1000\n' if a==['+%s'] else '2026-10-25 03:00:00\n'
elif tool=='curl':
 source=a[a.index('--interface')+1] if '--interface' in a else ''
 rc=0 if source=='192.168.8.153' and s['scenario']!='backup_dead' else (0 if s['scenario']=='healthy' else 28)
elif tool=='iptables':
 if a[:3]!=['-t','nat','-C'] and a[:3]!=['-t','nat','-A'] and a[:3]!=['-t','nat','-D']:
  rc=91
 else:
  op=a[2]
  if op=='-C': rc=0 if s['nat'] else 1
  elif op=='-A': s['nat']=True
  else:
   rc=0 if s['nat'] else 1; s['nat']=False
elif tool=='ip':
 family=a.pop(0)
 if family not in ('-4','-6'): rc=91
 elif family=='-6':
  if a[:3]!=['route','show','default']: rc=91
 elif a==['link','show','primary0']:
  out='2: primary0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 state UP\n'
 elif a==['-o','addr','show','primary0']: out=primary_addr()
 elif a in (['-o','addr','show','backup0'],['addr','show','backup0']):
  out='3: backup0    inet 192.168.8.153/24 scope global backup0\n'
 elif a==['route','show','default']:
  out=s['default']+'\n' if s['default'] else ''
 elif a[:3]==['route','replace','default']:
  s['default']='default '+' '.join(a[3:]); s['default_changes'].append(s['default'])
 elif a[:2]==['route','replace'] and 'table' in a:
  table=a[a.index('table')+1]
  if table=='250' and s['scenario']=='probe_route_failure': rc=43
  else: s['probe_routes'][table+':'+a[2]]=' '.join(a)
 elif a[:2]==['route','flush'] and 'table' in a:
  table=a[a.index('table')+1]; s['probe_routes'].pop(table+':'+a[2],None)
 elif a[:2]==['rule','add']:
  table=a[a.index('lookup')+1]
  if table=='250' and s['scenario']=='probe_rule_failure': rc=44
  else: s['rules'].append(a)
 elif a[:2]==['rule','del']:
  # Deletes of absent legacy tuples are deliberately allowed to fail.
  body=a[2:]; idx=next((i for i,r in enumerate(s['rules']) if r[2:]==body),None)
  if idx is None: rc=2
  else: s['rules'].pop(idx)
 else: rc=91
else: rc=91
if rc==91: s['unexpected'].append([tool,*sys.argv[1:]])
p.write_text(json.dumps(s)); sys.stdout.write(out); sys.exit(rc)
'''


class EngineBehavior(unittest.TestCase):
    def run_case(self, scenario, expected, expected_fail_count=None):
        with tempfile.TemporaryDirectory(prefix='arkbridge-unit-') as temp:
            root = Path(temp)
            fixture = root / 'fixture.py'
            fixture.write_text('#!' + sys.executable + '\n' + FIXTURE)
            fixture.chmod(0o700)
            for tool in ['uci', 'ip', 'iptables', 'curl', 'date']:
                (root / tool).symlink_to(fixture.name)
            model = root / 'model.json'
            config = {
                'enabled': '1', 'mode': 'main', 'primary_gateway': '198.18.1.1',
                'primary_device': 'primary0', 'backup_gateway': '192.168.8.1',
                'backup_device': 'backup0', 'backup_src_prefix': '192.168.8.0/24',
                'probe_targets': '223.5.5.5', 'backup_probe_targets': '223.5.5.5',
                'probe_port': '443', 'probe_table': '250', 'backup_probe_table': '251',
                'rule_pref': '3000', 'backup_source_rule_pref': '2998',
                'bypass_transparent_proxy': '0', 'masquerade_backup': '1',
                'failures_before_switch': '2', 'successes_before_failback': '2',
                'failback_cooldown': '30', 'backup_grace_seconds': '20',
                'backup_watchdog_seconds': '25', 'ipv6_enabled': '0',
                'post_switch_hook': '',
            }
            initial = {'scenario': scenario, 'config': config, 'primary_addr_calls': 0,
                       'default': 'default via 198.18.1.1 dev primary0 proto static',
                       'default_changes': [], 'probe_routes': {}, 'rules': [],
                       'calls': [], 'unexpected': [], 'nat': False}
            model.write_text(json.dumps(initial))
            env = dict(os.environ, ARK_TEST_MODEL=str(model),
                       ARKBRIDGE_RUNDIR=str(root / 'runtime'))
            for key, tool in [('UCI', 'uci'), ('IP', 'ip'), ('IPTABLES', 'iptables'),
                              ('IP6TABLES', 'iptables'), ('CURL', 'curl'), ('DATE', 'date')]:
                env['ARKBRIDGE_' + key + '_BIN'] = str(root / tool)
            invocations = []
            for _ in range(2):
                # Each engine invocation observes the same two-sample scenario.
                # In the race fixture, recovery occurs between queries within
                # one run, rather than accidentally becoming a new next-run state.
                current = json.loads(model.read_text())
                current['primary_addr_calls'] = 0
                model.write_text(json.dumps(current))
                command = SHELL + [str(ENGINE)]
                proc = subprocess.run(command, env=env, text=True, capture_output=True, timeout=20)
                current = json.loads(model.read_text())
                statepath = root / 'runtime/state'
                state = statepath.read_text().splitlines() if statepath.exists() else []
                invocations.append({'exit_code': proc.returncode, 'stdout': proc.stdout,
                                    'stderr': proc.stderr, 'route': current['default'], 'state': state})
            current = json.loads(model.read_text())
            logpath = root / 'runtime/log'
            log = logpath.read_text() if logpath.exists() else ''
            result = {'scenario': scenario, 'expected': expected, 'invocations': invocations,
                      'engine_log': log}
            self.assertFalse(current['unexpected'], current['unexpected'])
            self.assertEqual([p['exit_code'] for p in invocations], [0, 0], invocations)
            self.assertFalse(any(p['stderr'] for p in invocations), invocations)
            if expected == 'backup':
                self.assertIn('via 198.18.1.1 dev primary0', invocations[0]['route'], result)
                self.assertIn('via 192.168.8.1 dev backup0', invocations[1]['route'], result)
                self.assertEqual(invocations[0]['state'][2], '1', result)
                self.assertEqual(invocations[1]['state'][0], 'backup', result)
                self.assertTrue(current['nat'], result)
                self.assertEqual(len(current['default_changes']), 1, result)
            else:
                self.assertIn('via 198.18.1.1 dev primary0', current['default'], result)
                self.assertEqual(current['default_changes'], [], result)
                self.assertFalse(current['nat'], result)
                if expected == 'hold':
                    self.assertIn('probe invalid; holding state=primary', log, result)
                    self.assertTrue(all(not p['state'] or p['state'][2] == '0'
                                        for p in invocations), result)
                elif expected == 'primary':
                    self.assertNotIn('probe invalid;', log, result)
            if expected_fail_count is not None:
                self.assertEqual(invocations[-1]['state'][2], str(expected_fail_count), result)

    def test_empty_confirmed_address_uses_backup_after_threshold(self):
        self.run_case('empty', 'backup')

    def test_address_query_failure_remains_unknown(self):
        self.run_case('query_failure', 'hold')

    def test_nonempty_invalid_address_remains_unknown(self):
        self.run_case('invalid_nonempty', 'hold')

    def test_whitespace_address_output_remains_unknown(self):
        self.run_case('whitespace_nonempty', 'hold')

    def test_address_reappearing_between_queries_holds(self):
        self.run_case('address_race', 'hold')

    def test_second_successful_empty_query_is_sufficient(self):
        self.run_case('first_query_failed', 'backup')

    def test_primary_probe_route_failure_remains_unknown(self):
        self.run_case('probe_route_failure', 'hold')

    def test_primary_probe_rule_failure_remains_unknown(self):
        self.run_case('probe_rule_failure', 'hold')

    def test_existing_healthy_primary_is_preserved(self):
        self.run_case('healthy', 'primary', 0)

    def test_dead_backup_cannot_trigger_failover(self):
        self.run_case('backup_dead', 'no_switch')


if __name__ == '__main__':
    unittest.main()
