'use strict';
'require view';
'require form';
'require fs';
'require uci';
'require ui';

var DETECT = '/usr/libexec/arkbridge-detect';

function detect() {
	return fs.exec(DETECT).then(function (res) {
		if (!res || res.code)
			throw new Error((res && res.stderr) || ('exit ' + (res && res.code)));
		return JSON.parse((res.stdout || '{}'));
	});
}

function setField(name, value) {
	var nodes = document.querySelectorAll('input,select,textarea');
	for (var i = 0; i < nodes.length; i++) {
		var n = nodes[i];
		if ((n.name && n.name.indexOf(name) >= 0) || (n.id && n.id.indexOf(name) >= 0)) {
			n.value = value;
			return true;
		}
	}
	return false;
}

return view.extend({
	load: function () {
		return uci.load('arkbridge');
	},

	render: function () {
		var m, s, o;

		m = new form.Map('arkbridge', _('ArkBridge'),
			_('Automatic WAN failover: keep the preferred path and move the default route to a backup uplink when it stops working. Disabled by default; it changes the default route and can take the network offline if misconfigured. Use the form Reset button to cancel unsaved edits.'));

		s = m.section(form.NamedSection, 'main', 'arkbridge', _('Settings'));
		s.anonymous = true;

		o = s.option(form.ListValue, 'mode', _('Mode'));
		o.value('main', _('Main router - internet via Huawei Mobile WiFi (主路由，經華為移動 WiFi 上網)'));
		o.value('side', _('Side router - fallback / backup (旁路由，保底模式)'));
		o.value('aggregate', _('Main router - aggregation, broadband + Huawei (主路由聚合模式)'));
		o.default = 'side';
		o.description = _('One failover engine, three roles. "aggregate" does NOT load-balance (see the warning below). When the preferred device is left empty the service applies the mode default (br-lan for side, wan otherwise).');
		o.onchange = function (ev, section_id, value) {
			var cur = document.querySelector('input[name$="primary_device"]');
			if (cur && !cur.value && value)
				cur.value = (value === 'side') ? 'br-lan' : 'wan';
		};

		o = s.option(form.Flag, 'enabled', _('Enable'));
		o.default = '0';
		o.rmempty = false;

		o = s.option(form.Value, 'primary_gateway', _('Preferred gateway'), _('Used while the preferred path is healthy (e.g. the main router or the broadband WAN gateway).'));
		o.datatype = 'or(ip4addr,"")';

		o = s.option(form.Value, 'primary_device', _('Preferred device'), _('e.g. br-lan (side) or wan (main). Default depends on the mode.'));
		o.datatype = 'string';

		o = s.option(form.Value, 'backup_gateway', _('Backup gateway'), _('The gateway of the backup uplink (e.g. the Huawei USB/cellular gateway).'));
		o.datatype = 'or(ip4addr,"")';

		o = s.option(form.Value, 'backup_device', _('Backup device'), _('e.g. eth0 / usb0 / wwan0.'));
		o.datatype = 'string';

		o = s.option(form.Value, 'backup_src_prefix', _('Backup address CIDR'), _('Optional. Only switch when the backup device has an address inside this CIDR (e.g. 192.0.2.0/24).'));
		o.datatype = 'string';

		o = s.option(form.Value, 'probe_targets', _('Probe targets'), _('Space-separated IPs probed with HTTPS to decide if the preferred path works.'));
		o.datatype = 'string';

		o = s.option(form.Value, 'interval', _('Check interval (s)'));
		o.datatype = 'uinteger';
		o.placeholder = '10';

		o = s.option(form.Flag, 'aggregation', _('I understand the aggregation latency risk'),
			_('WARNING: aggregating the broadband and the mobile link (load balancing) is NOT done by this plugin. If you add it separately (e.g. mwan3), expect higher and more variable latency: some flows go over the slower mobile link, per-flow path stickiness can break sessions, and mobile links change IP. Prefer failover for stability.'));
		o.default = '0';
		o.rmempty = false;

		o = s.option(form.Button, '_autocheck', ' ');
		o.inputtitle = _('Auto-check');
		o.inputstyle = 'apply';
		o.onclick = function () {
			return detect().then(function (d) {
				var pr = d.primary || {};
				var cands = d.candidates || [];
				var pOk = 0, bOk = 0;
				// Do not write a guessed value into the form; list and let the
				// user confirm it.
				if (pr.gateway && !pr.guessed && setField('primary_gateway', pr.gateway)) pOk++;
				if (pr.device && setField('primary_device', pr.device)) pOk++;
				// Auto-fill the backup only when there is exactly one usable
				// candidate (device+gateway read from the kernel, not guessed).
				// With several candidates, list them and let the user choose.
				var usable = cands.filter(function (x) {
					return x.device && x.gateway && !x.guessed;
				});
				if (usable.length === 1) {
					if (setField('backup_device', usable[0].device)) bOk++;
					if (setField('backup_gateway', usable[0].gateway)) bOk++;
				}
				var lines = cands.map(function (x) {
					return '%s (%s)%s'.format(x.device, x.gateway, x.guessed ? ' *' : '');
				}).join(', ');
				var needManual = (pOk < 2) || (bOk < 2) || usable.length !== 1 || pr.guessed;
				var msgs = [
					E('p', _('Detected preferred: %s (%s)').format(pr.gateway || '-', pr.device || '-')),
					E('p', _('Backup candidates: %s').format(lines || _('none'))),
					E('p', _('Auto-check only lists and guesses; it does not verify reachability. A candidate marked * has a guessed gateway and is NOT auto-filled.'))
				];
				if (needManual)
					msgs.push(E('p', _('Warning: verify and complete the fields manually before enabling; not everything could be detected reliably.')));
				ui.addNotification(null, E('div', msgs), needManual ? 'warning' : 'info');
			}).catch(function (e) {
				ui.addNotification(null, E('p', _('Auto-check failed: %s').format(e && e.message ? e.message : e)), 'error');
			});
		};

		return m.render();
	}
});
