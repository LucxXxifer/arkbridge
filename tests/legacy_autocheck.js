'use strict';
// Execute the complete legacy script with a fixture DOM and an HTTP-200 XHR.
// No jsdom dependency, no fetch, no browser, no router access.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const file = path.join(__dirname, '../package/luci-app-arkbridge/root/usr/lib/lua/luci/view/arkbridge/autocheck.htm');
const html = fs.readFileSync(file, 'utf8');
const script = html.match(/<script[^>]*>([\s\S]*?)<\/script>/)[1];
const results = [];
function invoke(payload) {
  const keys = ['primary_gateway', 'primary_device', 'backup_device', 'backup_gateway',
    'backup_src_prefix', 'primary_gateway6', 'primary_device6', 'backup_device6', 'backup_gateway6'];
  const nodes = Object.fromEntries(keys.map(key => [key, {
    id: key, name: 'cbid.arkbridge.main.' + key, value: 'preserve-' + key,
    style: {}, title: '', changes: 0, dispatchEvent() { this.changes++; },
  }]));
  const output = {style: {}, textContent: '', innerHTML: ''};
  const calls = [];
  class XHR {
    open(method, url, async) { calls.push([method, url, async]); }
    send() {
      this.status = 200; this.readyState = 4; this.responseText = JSON.stringify(payload);
      this.onreadystatechange();
    }
  }
  const context = {
    document: {querySelectorAll() { return Object.values(nodes); },
      getElementById() { return output; }},
    window: {location: {pathname: '/cgi-bin/luci/admin/services/arkbridge'}},
    XMLHttpRequest: XHR, Event: class Event {},
  };
  vm.createContext(context);
  vm.runInContext(script, context, {timeout: 1000});
  assert.equal(context.srfAutocheck(), false);
  assert.deepEqual(calls, [['GET', '/cgi-bin/luci/admin/services/arkbridge/detect', true]]);
  return {nodes, output};
}
function test(name, check) {
  try { check(); results.push({name, passed: true}); console.log('PASS ' + name); }
  catch (e) { results.push({name, passed: false, error: e.message}); console.error('FAIL ' + name + ': ' + e.message); }
}
const primary = {gateway: '198.18.0.1', device: 'wan0', guessed: false};
test('guessed gateway is visible but does not fill backup fields', () => {
  const {nodes, output} = invoke({primary, candidates: [
    {device: 'eth2', gateway: '192.168.8.1', cidr: '192.168.8.0/24', guessed: true},
  ]});
  for (const key of ['backup_device', 'backup_gateway', 'backup_src_prefix'])
    assert.equal(nodes[key].value, 'preserve-' + key, key);
  assert.match(output.innerHTML, /192\.168\.8\.1/);
  assert.match(output.innerHTML, /guessed/i);
});
test('link-local-only candidate never fills IPv4 backup device', () => {
  const {nodes} = invoke({primary, candidates: [
    {device: 'eth0', address6: 'fe80::1234', gateway: '', gateway6: 'fe80::1', guessed6: true},
  ]});
  assert.equal(nodes.backup_device.value, 'preserve-backup_device');
  assert.equal(nodes.backup_device6.value, 'preserve-backup_device6');
});
test('exactly one confirmed IPv4 candidate is selected even after a guess', () => {
  const {nodes} = invoke({primary, candidates: [
    {device: 'eth0', gateway: '192.0.2.1', guessed: true},
    {device: 'eth2', gateway: '192.168.8.1', cidr: '192.168.8.0/24', guessed: false},
  ]});
  assert.equal(nodes.backup_device.value, 'eth2');
  assert.equal(nodes.backup_gateway.value, '192.168.8.1');
  assert.equal(nodes.backup_src_prefix.value, '192.168.8.0/24');
  assert.equal(nodes.primary_gateway.value, '198.18.0.1');
});
test('ambiguous confirmed candidates are listed but not auto-selected', () => {
  const {nodes} = invoke({primary, candidates: [
    {device: 'eth2', gateway: '192.168.8.1', guessed: false},
    {device: 'eth3', gateway: '192.0.2.1', guessed: false},
  ]});
  assert.equal(nodes.backup_device.value, 'preserve-backup_device');
  assert.equal(nodes.backup_gateway.value, 'preserve-backup_gateway');
});
test('guessed primary gateway remains display-only', () => {
  const {nodes} = invoke({primary: {device: 'wan0', gateway: '198.18.0.1', guessed: true}, candidates: []});
  assert.equal(nodes.primary_gateway.value, 'preserve-primary_gateway');
  assert.equal(nodes.primary_device.value, 'preserve-primary_device');
});
test('primary device without a gateway does not overwrite a manual pair', () => {
  const {nodes} = invoke({primary: {device: 'wan0'}, candidates: []});
  assert.equal(nodes.primary_gateway.value, 'preserve-primary_gateway');
  assert.equal(nodes.primary_device.value, 'preserve-primary_device');
});
test('guessed primary IPv6 pair remains display-only', () => {
  const {nodes} = invoke({primary: {device6: 'wwan0', gateway6: 'fe80::1', guessed6: true}, candidates: []});
  assert.equal(nodes.primary_gateway6.value, 'preserve-primary_gateway6');
  assert.equal(nodes.primary_device6.value, 'preserve-primary_device6');
});
test('primary IPv6 device without a gateway preserves a manual pair', () => {
  const {nodes} = invoke({primary: {device6: 'wwan0'}, candidates: []});
  assert.equal(nodes.primary_gateway6.value, 'preserve-primary_gateway6');
  assert.equal(nodes.primary_device6.value, 'preserve-primary_device6');
});
test('exactly one confirmed IPv6 candidate fills only IPv6 backup', () => {
  const {nodes} = invoke({primary, candidates: [
    {device: 'wwan0', address6: '2001:db8::2', gateway6: 'fe80::1', guessed6: false},
  ]});
  assert.equal(nodes.backup_device.value, 'preserve-backup_device');
  assert.equal(nodes.backup_device6.value, 'wwan0');
  assert.equal(nodes.backup_gateway6.value, 'fe80::1');
});
test('ambiguous confirmed IPv6 candidates are not auto-selected', () => {
  const {nodes} = invoke({primary, candidates: [
    {device: 'wwan0', gateway6: 'fe80::1', guessed6: false},
    {device: 'wwan1', gateway6: 'fe80::2', guessed6: false},
  ]});
  assert.equal(nodes.backup_device6.value, 'preserve-backup_device6');
});
test('no candidates preserves manually configured backup fields', () => {
  const {nodes, output} = invoke({primary, candidates: []});
  assert.equal(nodes.backup_device.value, 'preserve-backup_device');
  assert.equal(nodes.backup_gateway.value, 'preserve-backup_gateway');
  assert.match(output.innerHTML, /Candidates: none/);
});
test('candidate diagnostics escape HTML', () => {
  const {output} = invoke({primary, candidates: [
    {device: '<img src=x onerror=boom>', gateway: '<script>boom</script>', guessed: true},
  ]});
  assert.doesNotMatch(output.innerHTML, /<img|<script>/);
  assert.match(output.innerHTML, /&lt;img/);
});
const failed = results.filter(r => !r.passed).length;
process.exitCode = failed ? 1 : 0;
