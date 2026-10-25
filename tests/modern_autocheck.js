'use strict';
// Execute the complete LuCI view. Only its DOM and read-only RPC are fixtures.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname,
  '../package/luci-app-arkbridge/htdocs/luci-static/resources/view/arkbridge/overview.js'), 'utf8');

async function invoke(payload) {
  const names = ['primary_gateway', 'primary_device', 'backup_gateway', 'backup_device',
    'backup_src_prefix', 'primary_gateway6', 'primary_device6', 'backup_gateway6', 'backup_device6'];
  const nodes = Object.fromEntries(names.map(name => [name, {
    id: name, name: 'cbid.arkbridge.main.' + name, value: 'preserve-' + name,
  }]));
  const options = {}, calls = [], notifications = [];
  class Map {
    section() {
      return {option(type, name) {
        return (options[name] = {value() {}});
      }};
    }
    render() { return Promise.resolve({appendChild() {}}); }
  }
  const context = {
    view: {extend(value) { return value; }}, form: {Map},
    fs: {exec(command) {
      calls.push(command);
      return Promise.resolve({code: 0, stdout: JSON.stringify(payload)});
    }},
    uci: {load() { return Promise.resolve(); }},
    ui: {addNotification(...args) { notifications.push(args); }},
    _: value => value, E: (tag, children) => ({tag, children}),
    document: {querySelectorAll() { return Object.values(nodes); }, createElement() { return {}; }},
  };
  vm.createContext(context);
  vm.runInContext('String.prototype.format = function() {' +
    'var a = arguments, i = 0; return this.replace(/%s/g, function() { return String(a[i++]); }); };', context);
  const view = vm.runInContext('(function(){\n' + source + '\n})()', context, {timeout: 1000});
  await view.render();
  await options._autocheck.onclick();
  assert.deepEqual(calls, ['/usr/libexec/arkbridge-detect']);
  assert.equal(notifications.length, 1);
  assert.notEqual(notifications[0][2], 'error', JSON.stringify(notifications));
  return {nodes, notifications};
}

let failed = 0;
async function test(name, check) {
  try { await check(); console.log('PASS ' + name); }
  catch (error) { failed++; console.error('FAIL ' + name + ': ' + error.message); }
}

(async function () {
  await test('guessed primary pair preserves both manual fields', async () => {
    const {nodes} = await invoke({primary: {gateway: '192.0.2.1', device: 'wan0', guessed: true}});
    assert.equal(nodes.primary_gateway.value, 'preserve-primary_gateway');
    assert.equal(nodes.primary_device.value, 'preserve-primary_device');
  });
  await test('primary without a gateway preserves the manual pair', async () => {
    const {nodes} = await invoke({primary: {device: 'wan0'}});
    assert.equal(nodes.primary_device.value, 'preserve-primary_device');
    assert.equal(nodes.primary_gateway.value, 'preserve-primary_gateway');
  });
  await test('primary without a device preserves the manual pair', async () => {
    const {nodes} = await invoke({primary: {gateway: '192.0.2.1', guessed: false}});
    assert.equal(nodes.primary_device.value, 'preserve-primary_device');
    assert.equal(nodes.primary_gateway.value, 'preserve-primary_gateway');
  });
  await test('confirmed primary pair fills both fields', async () => {
    const {nodes} = await invoke({primary: {gateway: '192.0.2.1', device: 'wan0', guessed: false}});
    assert.equal(nodes.primary_gateway.value, '192.0.2.1');
    assert.equal(nodes.primary_device.value, 'wan0');
  });
  await test('guessed IPv6 primary pair preserves both manual fields', async () => {
    const {nodes} = await invoke({primary: {gateway6: 'fe80::1', device6: 'wan0', guessed6: true}});
    assert.equal(nodes.primary_gateway6.value, 'preserve-primary_gateway6');
    assert.equal(nodes.primary_device6.value, 'preserve-primary_device6');
  });
  await test('incomplete IPv6 primary pair preserves both manual fields', async () => {
    const {nodes} = await invoke({primary: {device6: 'wan0'}});
    assert.equal(nodes.primary_gateway6.value, 'preserve-primary_gateway6');
    assert.equal(nodes.primary_device6.value, 'preserve-primary_device6');
  });
  await test('one confirmed backup fills its pair and CIDR after a guess', async () => {
    const {nodes} = await invoke({candidates: [
      {device: 'eth0', gateway: '192.0.2.1', guessed: true},
      {device: 'usb0', gateway: '198.51.100.1', cidr: '198.51.100.0/24', guessed: false},
    ]});
    assert.equal(nodes.backup_device.value, 'usb0');
    assert.equal(nodes.backup_gateway.value, '198.51.100.1');
    assert.equal(nodes.backup_src_prefix.value, '198.51.100.0/24');
  });
  await test('ambiguous backups preserve the manual pair', async () => {
    const {nodes} = await invoke({candidates: [
      {device: 'usb0', gateway: '198.51.100.1', guessed: false},
      {device: 'usb1', gateway: '192.0.2.1', guessed: false},
    ]});
    assert.equal(nodes.backup_device.value, 'preserve-backup_device');
    assert.equal(nodes.backup_gateway.value, 'preserve-backup_gateway');
  });
  await test('IPv6 backup fills independently of IPv4', async () => {
    const {nodes} = await invoke({candidates: [
      {device: 'usb0', gateway6: 'fe80::1', guessed6: false},
    ]});
    assert.equal(nodes.backup_device.value, 'preserve-backup_device');
    assert.equal(nodes.backup_device6.value, 'usb0');
    assert.equal(nodes.backup_gateway6.value, 'fe80::1');
  });
  process.exitCode = failed ? 1 : 0;
})();
