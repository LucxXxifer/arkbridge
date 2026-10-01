local m, s, o, d

m = Map("arkbridge", translate("ArkBridge"))

s = m:section(NamedSection, "main", "arkbridge", translate("Status"))
s.anonymous = true

st = s:option(DummyValue, "_status")
st.template = "arkbridge/status"

s = m:section(NamedSection, "main", "arkbridge", translate("Settings"))
s.anonymous = true

o = s:option(ListValue, "mode", translate("Mode"))
o:value("main", translate("Main router - internet via Huawei Mobile WiFi (主路由，經華為移動 WiFi 上網)"))
o:value("side", translate("Side router - fallback / backup (旁路由，保底模式)"))
o:value("aggregate", translate("Main router - aggregation, broadband + Huawei (主路由聚合模式)"))
o.default = "side"
o.description = translate("One failover engine, three roles. 'aggregate' does NOT load-balance. When the preferred device is empty the service uses the mode default (br-lan for side, wan otherwise).")

o = s:option(Flag, "enabled", translate("Enable"))
o.default = "0"
o.rmempty = false

o = s:option(Value, "primary_gateway", translate("Preferred gateway"))
o.datatype = "ip4addr"
o.description = translate("Used while the preferred path is healthy (main router or broadband WAN gateway).")

o = s:option(Value, "primary_device", translate("Preferred device"))
o.description = translate("e.g. br-lan (side) or wan (main).")

o = s:option(Value, "backup_gateway", translate("Backup gateway"))
o.datatype = "ip4addr"
o.description = translate("The backup uplink gateway (e.g. the Huawei USB/cellular gateway).")

o = s:option(Value, "backup_device", translate("Backup device"))
o.description = translate("e.g. eth0 / usb0 / wwan0.")

o = s:option(Value, "backup_src_prefix", translate("Backup address CIDR"))
o.description = translate("Optional. Only switch when the backup device has an address inside this CIDR (e.g. 192.0.2.0/24).")

o = s:option(Value, "probe_targets", translate("Probe targets (DNS/HTTPS)"))
o.description = translate("IPs probed with HTTPS to decide if the preferred path works; separate multiple with a comma (e.g. 223.5.5.5, 119.29.29.29).")

o = s:option(Value, "interval", translate("Check interval (s)"))
o.datatype = "uinteger"
o.placeholder = "10"

o = s:option(Flag, "ipv6_enabled", translate("Enable IPv6 failover (dual-stack)"))
o.default = "0"
o.rmempty = false
o.description = translate("Off by default. If you run a transparent proxy that only handles IPv4 (e.g. shellcrash), enabling IPv6 can let IPv6 traffic bypass the proxy and leak. Turn this on only if you want IPv6 fallback.")

o = s:option(Value, "primary_gateway6", translate("IPv6 preferred gateway"))
o.datatype = "ip6addr"
o.description = translate("Empty = auto-detected from the current IPv6 default route.")

o = s:option(Value, "primary_device6", translate("IPv6 preferred device"))
o.description = translate("Empty = same as the IPv4 preferred device.")

o = s:option(Value, "backup_gateway6", translate("IPv6 backup gateway"))
o.datatype = "ip6addr"
o.description = translate("The IPv6 gateway of the backup uplink (empty = auto-detected).")

o = s:option(Value, "backup_device6", translate("IPv6 backup device"))
o.description = translate("Empty = same as the IPv4 backup device.")

o = s:option(Value, "probe_targets6", translate("IPv6 probe targets"))
o.description = translate("Space-separated IPv6 addresses. Empty = a built-in public set.")

o = s:option(DummyValue, "aggregation", translate("Aggregation note"))
o.rawhtml = true
o.value = translate("Note (aggregate mode): this plugin does NOT do aggregation / load balancing. It only warns of the latency risk. If you add load balancing separately (e.g. mwan3), expect higher and more variable latency: some flows go over the slower mobile link, per-flow path stickiness can break sessions, and the mobile link changes IP. Prefer failover for stability.")

d = s:option(DummyValue, "_autocheck", translate("Auto-check"))
d.template = "arkbridge/autocheck"

return m
