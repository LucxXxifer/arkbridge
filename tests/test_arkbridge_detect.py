#!/usr/bin/env python3
"""Execute the unmodified detector logic with hermetic ip/ubus/sysfs inputs.

Only the external read-only tools and sysfs data are fixtures. Unknown commands
are recorded and rejected; no production function is extracted or mocked.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
DETECTOR = ROOT / 'package/arkbridge/files/usr/libexec/arkbridge-detect'
SHELL = ['/bin/sh']

TOOL = r'''
import json, os, pathlib, sys
p = pathlib.Path(os.environ['ARK_DETECT_FIXTURE'])
m = json.loads(p.read_text())
tool = pathlib.Path(sys.argv[0]).name
a = sys.argv[1:]
m['calls'].append([tool, *a])
out, rc = '', 0
if tool == 'ubus' and a == ['call', 'network.interface', 'dump']:
    out = json.dumps(m['netifd'])
elif tool == 'ip' and a and a[0] in ('-4', '-6'):
    family, a = a[0], a[1:]
    if a[:3] == ['route', 'show', 'default'] and (len(a) == 3 or
            (len(a) == 5 and a[3] == 'dev')):
        dev = a[4] if len(a) == 5 else None
        rows = m['routes'].get(family, [])
        out = ''.join(r['line'] + '\n' for r in rows
                      if r.get('default') and (dev is None or dev == r['device']))
    elif a[:3] == ['route', 'show', 'dev'] and len(a) == 4:
        out = ''.join(r['line'] + '\n' for r in m['routes'].get(family, [])
                      if r['device'] == a[3])
    elif a[:3] == ['-o', 'addr', 'show'] and len(a) in (4, 6):
        dev = a[3]
        scope = None
        if len(a) == 6:
            if a[4] != 'scope': rc = 91
            scope = a[5]
        family_name = 'inet' if family == '-4' else 'inet6'
        for i, row in enumerate(m['devices'].get(dev, {}).get('addresses', [])):
            if row['family'] == family and (scope is None or scope == row['scope']):
                out += f"{i+2}: {dev}    {family_name} {row['cidr']} scope {row['scope']} {dev}\n"
    else:
        rc = 91
else:
    rc = 91
if rc == 91: m['unexpected'].append([tool, *sys.argv[1:]])
p.write_text(json.dumps(m))
sys.stdout.write(out)
sys.exit(rc)
'''


def addr(cidr, family='-4', scope='global'):
    return {'family': family, 'cidr': cidr, 'scope': scope}


def device(*addresses, operstate='up', carrier='1', master=None):
    return dict(addresses=list(addresses), operstate=operstate,
                carrier=carrier, master=master)


class DetectorTests(unittest.TestCase):
    def detect(self, devices, netifd=None):
        devices = dict(devices)
        devices['wan0'] = device(addr('198.18.0.2/24'))
        routes = {'-4': [
            {'device': 'wan0', 'default': True,
             'line': 'default via 198.18.0.1 dev wan0 proto static'},
            {'device': 'wan0', 'line': '198.18.0.0/24 dev wan0 scope link'},
        ], '-6': []}
        for name, item in devices.items():
            if name == 'wan0': continue
            for a in item['addresses']:
                routes[a['family']].append({
                    'device': name, 'line': f"{a['cidr']} dev {name} scope link"})
        initial = {'devices': devices, 'routes': routes,
                   'netifd': {'interface': netifd or []}, 'calls': [], 'unexpected': []}
        with tempfile.TemporaryDirectory(prefix='ark-detect-test-') as temporary:
            root = Path(temporary)
            model = root / 'fixture.json'
            model.write_text(json.dumps(initial))
            executable = root / 'fixture-tool'
            executable.write_text('#!' + sys.executable + '\n' + TOOL)
            executable.chmod(0o700)
            for name in ('ip', 'ubus'):
                (root / name).symlink_to(executable.name)
            net = root / 'net'
            net.mkdir()
            for name, item in devices.items():
                p = net / name
                p.mkdir()
                (p / 'operstate').write_text(item['operstate'] + '\n')
                (p / 'carrier').write_text(item['carrier'] + '\n')
            for name, item in devices.items():
                if item['master']:
                    master = net / item['master']
                    master.mkdir(exist_ok=True)
                    (master / 'bridge').mkdir(exist_ok=True)
                    (net / name / 'master').symlink_to(master)
                    (net / name / 'brport').mkdir()
            env = dict(os.environ, ARK_DETECT_FIXTURE=str(model),
                       ARKBRIDGE_IP_BIN=str(root / 'ip'),
                       ARKBRIDGE_UBUS_BIN=str(root / 'ubus'),
                       ARKBRIDGE_PYTHON_BIN=sys.executable,
                       ARKBRIDGE_NET_CLASS_ROOT=str(net))
            result = subprocess.run(SHELL + [str(DETECTOR)], env=env,
                                    text=True, capture_output=True, timeout=20)
            state = json.loads(model.read_text())
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, '')
            self.assertEqual(state['unexpected'], [], state['unexpected'])
            output = json.loads(result.stdout)
            return output

    def test_link_local_only_dsa_base_is_not_a_backup_candidate(self):
        d = self.detect({'eth0': device(addr('fe80::1234/64', '-6', 'link'))})
        self.assertEqual(d['candidates'], [], 'IPv6 link-local alone is not an uplink')

    def test_ipv4_global_candidate_keeps_confirmed_gateway_and_dns(self):
        d = self.detect({'eth2': device(addr('192.168.8.153/24'),
                                      addr('fe80::2345/64', '-6', 'link'))}, [
            {'device': 'eth2', 'route': [{'target': '0.0.0.0', 'nexthop': '192.168.8.1'}],
             'dns-server': ['192.168.8.1']},
        ])
        self.assertEqual(len(d['candidates']), 1)
        c = d['candidates'][0]
        self.assertEqual((c['device'], c['address'], c['gateway'], c['guessed']),
                         ('eth2', '192.168.8.153', '192.168.8.1', False))
        self.assertEqual(c['dns4'], ['192.168.8.1'])
        self.assertEqual(c['address6'], '')

    def test_ipv6_global_candidate_keeps_link_local_gateway(self):
        d = self.detect({'wwan0': device(addr('fe80::2345/64', '-6', 'link'),
                                       addr('2001:db8:2::42/64', '-6'))}, [
            {'device': 'wwan0', 'route': [{'target': '::', 'nexthop': 'fe80::1'}],
             'dns-server': ['2001:db8:2::53']},
        ])
        c = d['candidates'][0]
        self.assertEqual((c['address6'], c['gateway6'], c['guessed6']),
                         ('2001:db8:2::42', 'fe80::1', False))
        self.assertEqual(c['dns6'], ['2001:db8:2::53'])

    def test_bridge_slave_is_not_candidate_even_with_a_global_address(self):
        d = self.detect({'lan5': device(addr('192.0.2.5/24'), master='br-lan')})
        self.assertEqual(d['candidates'], [])

    def test_netifd_l3_device_has_gateway_and_dns_instead_of_a_guess(self):
        d = self.detect({'eth2.7': device(addr('192.168.8.153/24'))}, [
            {'device': 'eth2', 'l3_device': 'eth2.7',
             'route': [{'target': '0.0.0.0', 'nexthop': '192.168.8.1'}],
             'dns-server': ['192.168.8.1']},
        ])
        c = d['candidates'][0]
        self.assertFalse(c['guessed'], 'netifd l3_device must match the actual L3 device')
        self.assertEqual(c['gateway'], '192.168.8.1')
        self.assertEqual(c['dns4'], ['192.168.8.1'])

    def test_address_without_route_is_listed_as_guessed_not_confirmed(self):
        d = self.detect({'eth2': device(addr('192.168.8.153/24'))})
        c = d['candidates'][0]
        self.assertEqual((c['gateway'], c['guessed']), ('192.168.8.1', True))

    def test_down_or_addressless_devices_are_not_fabricated_as_candidates(self):
        d = self.detect({'eth2': device(operstate='down', carrier='0'),
                         'eth3': device(addr('192.0.2.5/24'), operstate='down')})
        self.assertEqual(d['candidates'], [])

    def test_unknown_state_with_carrier_and_global_address_is_preserved(self):
        d = self.detect({'wwan0': device(addr('192.0.2.5/24'), operstate='unknown')})
        self.assertEqual([c['device'] for c in d['candidates']], ['wwan0'])

    def test_link_local_ipv4_is_not_a_backup_candidate(self):
        d = self.detect({'eth0': device(addr('169.254.1.2/16', scope='link'))})
        self.assertEqual(d['candidates'], [])


if __name__ == '__main__':
    unittest.main()
