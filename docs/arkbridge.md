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
- Dual-stack is **opt-in**: the engine is IPv4 by default and can also fail over
  **IPv6** when `ipv6_enabled=1` (see below).

Firewall notes: the probe bypass and NAT use `iptables`. On `fw4`/nftables
systems `iptables` may be a compatibility layer (`iptables-nft`); verify the
rules actually appear in your active ruleset.

## IPv6 (dual-stack, optional)

IPv6 failover is **off by default** and must be enabled explicitly. It is
switched at the **routing level only — no NAT** (no NAT66). When enabled:

- The engine reads the IPv6 primary from the live IPv6 default route (or
  `primary_gateway6`/`primary_device6`), records the IPv6 backup from
  `backup_gateway6`/`backup_device6`, and probes the IPv6 primary with HTTPS over
  IPv6. Empty `probe_targets6` uses a built-in public set.
- A switch moves the IPv4 and IPv6 default routes **together**; fail-back moves
  both back. Primary health is the OR of the enabled families, and a backup is
  considered working if **either** IPv4 or IPv6 reaches the internet.
- If the backup has **no usable IPv6**, the engine switches **IPv4 only** and
  leaves the IPv6 default route untouched.

> **Proxy / leak warning.** A transparent proxy that only handles IPv4 (for
> example `shellcrash`) does not see IPv6 traffic. Enabling IPv6 can therefore
> let IPv6 traffic **leak around the proxy**. If you depend on a v4-only proxy,
> keep `ipv6_enabled=0` — or extend the proxy to IPv6 — before turning it on.

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
| `backup_source_rule_pref` | source-bound IPv4 backup probe preference (default `2998`) |
| `bypass_transparent_proxy` | exempt probes from a local transparent proxy |
| `masquerade_backup` | masquerade traffic leaving via the backup device |
| `failures_before_switch` | consecutive failures before switching to backup |
| `successes_before_failback` | consecutive successes before failing back |
| `failback_cooldown` | minimum seconds on backup before failing back |
| `backup_grace_seconds` | begin the source-bound watchdog check after switching (default `20`) |
| `backup_watchdog_seconds` | absolute initial backup recovery window (default `25`) |
| `post_switch_hook` | optional absolute executable for connection refresh, invoked with new and previous path |
| `ipv6_enabled` | enable IPv6 failover (dual-stack); default `0` |
| `primary_gateway6` / `primary_device6` | IPv6 primary path (empty = auto-detected) |
| `backup_gateway6` / `backup_device6` | IPv6 backup path (empty = auto-detected) |
| `probe_targets6` | IPv6 probe targets (empty = built-in public set) |
| `probe_port6` / `probe_host6` | IPv6 probe port / optional SNI host |
| `probe_table6` / `backup_probe_table6` | IPv6 policy tables (default `252`/`253`) |
| `rule_pref6` | ip-6 rule preference for IPv6 probes (default `3001`) |
| `backup_source_rule_pref6` | source-bound IPv6 backup probe preference (default `2998`) |
| `backup_probe_targets6` | optional IPv6 targets for real backup internet checks |
| `bypass_transparent_proxy6` | exempt IPv6 probes via `ip6tables` when available |

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
3. Probe the backup *internet* path through `backup_probe_table`, bound to the
   address acquired on the backup device. If `backup_probe_targets` is empty,
   built-in public HTTPS targets are used; gateway reachability alone is not
   treated as internet reachability.
4. Apply hysteresis: switch to backup only after `failures_before_switch`
   consecutive primary failures; fail back only after the primary is healthy and
   `failback_cooldown` seconds have elapsed.
5. Switch with `ip route replace` (atomic), verify the result, and only then
   persist state. A failed backup transaction immediately uses the same verified
   primary recovery as the watchdog. Ordinary failback remains family-aware.

## Start, stop, and rollback

- Start/enable: `/etc/init.d/arkbridge enable && /etc/init.d/arkbridge start`
- Stop: `/etc/init.d/arkbridge stop` — this runs `cleanup`, which restores the
  primary route **for each family this service actually moved**, then removes the
  probe routes, `ip rule`s, the transparent-proxy bypass and the backup NAT.
- Disable: `/etc/init.d/arkbridge disable` (does not itself run cleanup; stop the
  service to roll back).
- Manual cleanup: `/usr/libexec/arkbridge cleanup`
- Explicit rollback: `/usr/libexec/arkbridge rollback`. It uses the same
  verified force-primary transaction as the watchdog, removing backup NAT only
  after the primary route is confirmed. Failed recovery retains ownership and
  retries on the next service loop.
- Rollback of the routing change: set `enabled=0`, `/etc/init.d/arkbridge restart`,
  or simply stop the service.

### Backup watchdog

After switching to the backup, the engine checks its real HTTPS reachability at
`backup_grace_seconds` (default 20). All targets share a budget ending before
`backup_watchdog_seconds` (default 25), reserving time for route recovery. A
failed check immediately attempts primary recovery even if the primary probe
is still unhealthy. A late tick with no post-switch proof starts recovery
without another HTTPS wait. `rollback_pending` preserves recovery intent and
retries before health probes; `backup_dead` suppresses backup flapping until a
source-bound backup probe succeeds. The deadline assumes the router is running
and kernel route operations complete; it cannot recover a stalled OS.

### Existing connections across WAN changes

Existing conntrack NAT mappings and transparent-proxy sockets can retain the old
WAN source even after the default route changes. This can affect remote-control
signals while new HTTPS requests work. Confirm the post-NAT source on the backup
interface and reconnect only the affected sessions; avoid flushing conntrack or
all proxy connections.

`post_switch_hook` supports a site-specific refresh after a verified switch or
rollback. It is disabled by default, receives `<new-path> <previous-path>`, runs
asynchronously with the engine lock descriptor closed, and requires
`coreutils-timeout` to enforce a five-second limit. A ShellCrash integration can
use its local controller API to close only connections matching an opted-in
client and remote-control domain, letting the client establish a new socket on
the selected WAN. Changing the default route alone does not rebuild those
sessions. This hook does not change proxy selectors or DHCP/DNS settings.

> While enabled, this service owns the default route. On stop it rolls back to
> the primary for the families it moved; `cleanup` only touches a family whose
> live default route is still this service's backup route, so it will not
> clobber a route it never owned. Make sure the primary interface provides a
> working gateway (most side routers do via their `gateway` option).

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

IPv4 and IPv6 are shown on **separate rows** — they are never merged — and DNS
is shown per family.

| Field | Meaning |
|---|---|
| Current path | `primary` / `backup` / `unknown` (colour-coded), derived from the real kernel default route |
| Service | `enabled` / `disabled` |
| IPv4 | preferred gateway + device; backup gateway + device and readiness (`ready` / `device has address, gateway not via it` / `no address`); IPv4 default route |
| DNS (IPv4) | the IPv4 DNS servers currently acquired by the interfaces |
| IPv6 | (only when enabled) preferred/backup IPv6 gateway + device and readiness; IPv6 default route. Shown as `disabled` otherwise |
| DNS (IPv6) | the IPv6 DNS servers currently acquired by the interfaces |
| Last switch | most recent "switched to ..." line from the log |

**Settings**:

- **Mode** (main / side / aggregate), **Enable** toggle.
- **Enable IPv6 failover (dual-stack)** toggle — off by default, with the
  proxy-leak warning. This is the explicit opt-in for IPv6.
- **Auto-check**: fills the preferred gateway/device from the current default
  route, and the backup gateway/device/CIDR from the USB/mobile interface (the
  gateway is read from netifd's real DHCP route; the CIDR from the interface
  subnet). It fills the IPv6 fields from the detected IPv6 data. Guessed values
  are highlighted with a warning. Auto-check only lists and reads; it does not
  verify reachability.
- Manual fields for the preferred/backup gateway and device, separately for
  IPv4 and IPv6.
- **Save & Apply** to apply; **Reset** discards unsaved edits (cancel).
- **Probe targets (DNS/HTTPS)**: separate multiple IPs with a **comma**; IPv6
  targets have their own field.

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
