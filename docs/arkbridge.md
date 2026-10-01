# arkbridge

A small, transparent **WAN failover** for an OpenWrt **side router** (旁路由).

It keeps traffic on a **primary gateway** (your normal router) and moves the
default route to a **backup uplink** (for example a cellular/USB gateway) when
the primary path stops working, then moves it back automatically.

Everything is configured through UCI. No site-specific value is hard coded.

## What is automatic and what is not (read first)

This is **not** a plug-and-play "insert the modem and you are online" product.

- **USB driver / interface detection**: provided by the companion `usb-uplink`
  package (its default allowlist matches the tested `12d1:14db`). That only
  means the device may be accepted and appear as a network interface; it does
  **not** guarantee the driver loads on every kernel or that the interface name
  is stable.
- **DHCP address on the backup device**: provided by the backup device itself
  (e.g. the mobile WiFi). Getting an address does **not** mean traffic can leave.
- **Default route / NAT / failover takeover**: provided by **this** package, and
  it is **disabled by default** (`enabled=0`). You must configure the primary
  and backup gateways and enable it. Until then, traffic keeps using the primary
  path only.

In short, you must (1) have a working backup device, (2) know its interface and
gateway, and (3) configure and enable this service.

## Topology and scope

- This service changes the **default route of the router that runs it**. On a
  side router, that affects the side router's own traffic and any LAN client
  whose gateway is the side router. Clients that use the main router directly
  are unaffected.
- It does **not** manage DHCP, DNS, or the main router. Run it only on a router
  that is already a working side router.

## Requirements

- OpenWrt / iStoreOS side router with `procd`, `uci`, `iptables`, `ip-full`, `curl`.
- A reachable primary gateway and a backup gateway (e.g. a USB/cellular
  interface already up with an address on its subnet).
- IPv4 only. IPv6 is not handled.

Firewall notes: the probe bypass and NAT use `iptables`. On `fw4`/nftables
systems `iptables` may be a compatibility layer (`iptables-nft`); verify the
rules actually appear in your active ruleset.

## Configuration

`/etc/config/arkbridge`:

| option | meaning |
|---|---|
| `enabled` | master switch (`0`/`1`); default `0` |
| `interval` | re-check interval in seconds (default `10`) |
| `primary_gateway` / `primary_device` | primary path |
| `backup_gateway` / `backup_device` | backup path |
| `backup_src_prefix` | only switch when the backup device has an address inside this CIDR |
| `probe_targets` | space-separated primary probe IPs |
| `backup_probe_targets` | optional targets to verify the backup reaches the **internet** |
| `probe_port` | probe TCP port (default `443`) |
| `probe_host` | optional SNI host used with `--resolve` |
| `probe_table` / `backup_probe_table` | policy tables for probing each path (default `250`/`251`) |
| `rule_pref` | ip-rule preference for probes (default `3000`) |
| `bypass_transparent_proxy` | exempt probes from a local transparent proxy |
| `masquerade_backup` | masquerade traffic leaving via the backup device |
| `failures_before_switch` | consecutive failures before switching to backup |
| `successes_before_failback` | consecutive successes before failing back |
| `failback_cooldown` | minimum seconds on backup before failing back |

> The default config uses **documentation addresses** (`192.0.2.1`,
> `198.51.100.1`, RFC 5737) which are **not usable**; replace them with your
> real gateways.

Example (replace with your real values):

```sh
uci set arkbridge.main.enabled=1
uci set arkbridge.main.primary_gateway=<your-main-gateway>
uci set arkbridge.main.primary_device=br-lan
uci set arkbridge.main.backup_gateway=<your-backup-gateway>
uci set arkbridge.main.backup_device=eth0
uci set arkbridge.main.backup_src_prefix=<your-backup-subnet-prefix>
uci commit arkbridge
/etc/init.d/arkbridge restart
```

Runtime state: `/var/run/arkbridge/state` (line 1 = `primary`/`backup`,
line 2 = switch timestamp), in a root-only (0700) directory. Log:
`/var/run/arkbridge/log`.

## How it works

1. For each probe target, install a `/32` route in `probe_table` via the primary
   gateway and an `ip rule` to that table. This forces the primary probe through
   the primary path **even while traffic is on the backup**.
2. HTTPS-probe the primary targets (certificate verification is disabled on
   purpose: this measures TCP/TLS reachability, not identity; any completed
   HTTPS transaction counts as reachable). An optional `nat OUTPUT RETURN`
   exempts the probe from a local transparent proxy so it measures the raw path.
3. Optionally probe the backup *internet* path through `backup_probe_table`.
4. Apply hysteresis: switch to backup only after `failures_before_switch`
   consecutive primary failures; fail back only after the primary is healthy and
   `failback_cooldown` seconds have elapsed.
5. Switch with `ip route replace` (atomic), verify the result, and only then
   persist state. If the switch fails, restore the previous default route(s).

## Start, stop, and rollback

- Start/enable: `/etc/init.d/arkbridge enable && /etc/init.d/arkbridge start`
- Stop: `/etc/init.d/arkbridge stop` — this runs `cleanup`, which
  removes the probe routes, `ip rule`s, the transparent-proxy bypass and the
  backup NAT.
- Disable + clean: `/etc/init.d/arkbridge disable`
- Manual cleanup: `/usr/libexec/arkbridge cleanup`
- Rollback of the routing change: set `enabled=0`, `/etc/init.d/arkbridge restart`,
  or simply delete the service. The default route returns to whatever netifd
  installs for the primary interface.

> Warning: while enabled, this service owns the default route. Stopping it does
> not automatically re-add a default route; make sure the primary interface
> provides one (most side routers do via their `gateway` option).

## Troubleshooting

- No switch happens: check `enabled=1`, that `primary_device`/`backup_device`
  exist, and that `probe_targets` are reachable from the primary path.
- Always switching to backup: the primary probe is failing. Test the same
  targets from the primary path manually; note that some paths block ICMP, so a
  failing `ping` is not conclusive — check the HTTPS probe.
- Switched but no internet on backup: set `backup_probe_targets` to confirm the
  backup actually reaches the internet, and check `masquerade_backup` and the
  backup gateway's own NAT.
- Interface name changed (e.g. `eth0` -> `eth1`): USB interface names are not
  guaranteed; update `backup_device`.

## LuCI panel

Install `luci-app-arkbridge` to get a panel under
**Services -> ArkBridge**. The page has a read-only **status area**
at the top and the **settings** below it.

**Status area** (read-only, refreshes every 15 s, plus a **Refresh** button):

| Field | Meaning |
|---|---|
| Current path | `primary` / `backup` / `unknown` (colour-coded), derived from the real kernel default route |
| Service | `enabled` / `disabled` |
| Preferred | preferred gateway + device |
| Backup | backup gateway + device, and readiness (`ready` / `device has address, gateway not via it` / `no address`) |
| Default route | current IPv4 default route |
| Last switch | most recent "switched to ..." line from the log |

**Settings**:

- **Mode** (main / side / aggregate), **Enable** toggle.
- **Auto-check**: fills the preferred gateway/device from the current default
  route, and the backup gateway/device/CIDR from the USB/mobile interface (the
  gateway is read from netifd's real DHCP route; the CIDR from the interface
  subnet). Guessed values are highlighted with a warning. Auto-check only lists
  and reads; it does not verify reachability.
- Manual fields for the preferred/backup gateway and device.
- **Save & Apply** to apply; **Reset** discards unsaved edits (cancel).
- **Probe targets (DNS/HTTPS)**: separate multiple IPs with a **comma**.

On older Lua-based LuCI builds the panel is registered with a Lua controller +
CBI; on modern builds with `menu.d`/`acl.d`/view.

## Device support

Only the **Huawei E6878 / E6878-370** has been validated here. The engine is
device-agnostic but a different CPE/mobile WiFi is untested and will only work
if it exposes a USB network interface (CDC-ECM/NCM/RNDIS), gets a DHCP lease
with a gateway, and can reach the internet. Modems that need a mode switch, or
that only support Wi-Fi tethering / proprietary Windows drivers, may not work.
Always verify the USB ID, driver, DHCP gateway and egress before relying on it.

## Tested hardware

Validated with a **Huawei E6878 / E6878-370 (5G Mobile WiFi)** as the backup
uplink over USB (`12d1:14db` -> `cdc_ether`). See the
[tested-device list](devices.md).

## Install (as an OpenWrt package)

Drop `package/arkbridge` into an OpenWrt buildroot `package/`
directory (or a feed) and build `arkbridge`. For a quick manual
install, copy the three files under `files/` to `/`, `chmod +x` the two scripts,
edit the UCI config, then enable and start the service.
