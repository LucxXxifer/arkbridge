'use strict';
'require view';
'require form';
'require fs';
'require uci';
'require ui';

var DETECT = '/usr/libexec/arkbridge-detect';

// Renders the read-only status area into #ark-status. IPv4 and IPv6 are shown
// on separate rows and DNS is shown per family.
var ARK_STATUS_JS = '(function(){' +
	'function esc(s){return String(s==null?"":s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");}' +
	'function get(u){return new Promise(function(r){var x=new XMLHttpRequest();x.open("GET",u,true);' +
	'x.onreadystatechange=function(){if(x.readyState!==4){return;}if(x.status!==200){r(null);return;}' +
	'try{r(JSON.parse(x.responseText));}catch(e){r(null);}};x.send();});}' +
	'function dns(l,f){var v=(l&&l.length)?l:(f||[]);return v.length?esc(v.join(", ")):"-";}' +
	'function row(a,b){return "<tr><td style=\\"white-space:nowrap\\">"+a+"</td><td>"+b+"</td></tr>";}' +
	'function render(){var el=document.getElementById("ark-status");if(!el){return;}' +
	'var base=window.location.pathname.replace(/\\/+$/,"");' +
	'Promise.all([get(base+"/status"),get(base+"/detect")]).then(function(r){' +
	'var d=r[0],det=r[1]||{};if(!d){el.style.color="#c00";el.textContent="Status failed.";return;}' +
	'var p=det.primary||{},c0=(det.candidates&&det.candidates[0])||{};' +
	'var color=d.current==="primary"?"#2e7d32":(d.current==="backup"?"#e65100":"#666");' +
	'var bk=d.backup||{},bk6=d.backup6||{};' +
	'var bkv=bk.state==="ready"?"<span style=\\"color:#2e7d32\\">ready</span>":(bk.state==="addr-only"?"device has address, gateway not via it":"no address");' +
	'var h="<div style=\\"font-size:1.05em;font-weight:bold;color:"+color+"\\">Current path: "+esc(d.current)+" ("+esc(d.state)+")</div>";' +
	'h+="<table class=\\"table\\" style=\\"margin-top:6px\\"><tbody>";' +
	'h+=row("Service","<b>IPv4</b>: "+(d.enabled==="1"?"enabled":"disabled"));' +
	'h+=row("IPv4 preferred",esc((d.primary&&d.primary.gateway)||"-")+" ("+esc((d.primary&&d.primary.device)||"-")+")");' +
	'h+=row("IPv4 backup",esc(bk.gateway||"-")+" ("+esc(bk.device||"-")+") - "+bkv);' +
	'h+=row("IPv4 route","<code>"+esc(d.route||"-")+"</code>");' +
	'h+=row("DNS (IPv4)",dns(p.dns4,c0.dns4));' +
	'if(d.ipv6_enabled==="1"){' +
	'h+=row("IPv6 preferred",esc((d.primary6&&d.primary6.gateway)||"-")+" ("+esc((d.primary6&&d.primary6.device)||"-")+")");' +
	'h+=row("IPv6 backup",esc(bk6.gateway||"-")+" ("+esc(bk6.device||"-")+")");' +
	'h+=row("IPv6 route","<code>"+esc(d.route6||"-")+"</code>");' +
	'h+=row("DNS (IPv6)",dns(p.dns6,c0.dns6));' +
	'}else{h+=row("IPv6","<span style=\\"color:#666\\">disabled</span>");}' +
	'h+=row("Last switch",esc(d.last_switch||"-"));' +
	'h+="</tbody></table>";el.style.color="#000";el.innerHTML=h;' +
	'});}' +
	'render();setInterval(render,15000);' +
	'})();';

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
		var re = new RegExp('[.]' + name + '$');
		if ((n.name && re.test(n.name)) || n.id === name) {
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

		s = m.section(form.NamedSection, 'main', 'arkbridge', _('Status'));
		s.anonymous = true;
		o = s.option(form.DummyValue, '_status', _('Status'));
		o.rawhtml = true;
		o.cfgvalue = function () {
			return '<div id="ark-status" style="color:#666">Loading ...</div>';
		};

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

		// --- IPv6 (dual-stack), opt-in. Separate fields, never merged with IPv4.
		o = s.option(form.Flag, 'ipv6_enabled', _('Enable IPv6 failover (dual-stack)'));
		o.default = '0';
		o.rmempty = false;
		o.description = _('Off by default. If you run a transparent proxy that only handles IPv4 (e.g. shellcrash), enabling IPv6 can let IPv6 traffic bypass the proxy and leak. Turn this on only if you want IPv6 fallback (e.g. a network that only has IPv6).');

		o = s.option(form.Value, 'primary_gateway6', _('IPv6 preferred gateway'));
		o.datatype = 'or(ip6addr,"")';
		o.description = _('Empty = auto-detected from the current IPv6 default route.');

		o = s.option(form.Value, 'primary_device6', _('IPv6 preferred device'));
		o.datatype = 'string';
		o.description = _('Empty = same as the IPv4 preferred device.');

		o = s.option(form.Value, 'backup_gateway6', _('IPv6 backup gateway'));
		o.datatype = 'or(ip6addr,"")';
		o.description = _('The IPv6 gateway of the backup uplink (empty = auto-detected).');

		o = s.option(form.Value, 'backup_device6', _('IPv6 backup device'));
		o.datatype = 'string';
		o.description = _('Empty = same as the IPv4 backup device.');

		o = s.option(form.Value, 'probe_targets6', _('IPv6 probe targets'));
		o.datatype = 'string';
		o.description = _('Space-separated IPv6 addresses. Empty = a built-in public set.');

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
				// IPv6 fields are filled into their own fields (never merged).
				if (pr.gateway6 && setField('primary_gateway6', pr.gateway6)) {}
				if (pr.device6 && setField('primary_device6', pr.device6)) {}
				var usable6 = cands.filter(function (x) {
					return x.device && x.gateway6 && !x.guessed6;
				});
				if (usable6.length === 1) {
					if (setField('backup_device6', usable6[0].device)) {}
					if (setField('backup_gateway6', usable6[0].gateway6)) {}
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

		return m.render().then(function (node) {
			var script = document.createElement('script');
			script.textContent = ARK_STATUS_JS;
			node.appendChild(script);
			return node;
		});
	}
});
