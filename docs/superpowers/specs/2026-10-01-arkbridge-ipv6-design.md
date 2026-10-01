# ArkBridge — Dual-Stack (IPv4 + IPv6) Failover Design

Date: 2026-10-01
Status: Draft for review
Component: `package/arkbridge` (the WAN failover engine)

## Objective

Make the ArkBridge engine fail over **IPv6** in sync with IPv4. Today the engine
is IPv4-only: it reads `ip -4 route show default`, probes IPv4 targets, and uses
`iptables`. After this change, when IPv6 is enabled and a usable IPv6 backup
exists, a switch moves **both** the IPv4 and IPv6 default routes to the backup
together; fail-back restores both to the primary.

## Rationale

The original IPv4-only scope was a deliberate choice for a small set of users:
with a v4-only transparent proxy (e.g. shellcrash) the proxy never sees IPv6
traffic, so untunnelled IPv6 can **leak** around it. Other users cannot use a
v4-only design at all — for example a network without a public IPv4 address
that only has IPv6 (or needs IPv6 for inbound traversal). IPv6 failover is
therefore added **opt-in** so those users get a working fallback, while users
who depend on a v4-only proxy keep `ipv6_enabled=0` and are unaffected.

This leak consideration MUST be documented: enabling IPv6 while running a
v4-only transparent proxy can expose IPv6 traffic outside the proxy unless the
proxy is extended to IPv6 or IPv6 egress is otherwise constrained.

## Non-goals

- No NAT for IPv6 (no NAT66). IPv6 is switched at the routing level only.
- No change to `package/arkbridge-usb` (`usb-uplink` / `usb-uplink-failoverd`)
  or its own `ipv6_guard` option. The two packages are independent.
- No DHCPv6/RA/DNS server management; no load balancing; no MPTCP.
- IPv6 remains opt-in and **off by default**; with it off the engine behaves
  exactly as today (IPv4-only).

## Requirements (agreed)

1. IPv6 failover is **synced** with IPv4 — one shared decision, both families
   applied from it.
2. IPv6 gateways/devices/targets are **auto-detected** with optional UCI
   override, mirroring the existing IPv4 Auto-check.
3. IPv6 is **route-only**; never masqueraded.
4. If the backup uplink has **no usable IPv6**, switch IPv4 only and **leave
   IPv6 unchanged**, recording a status/log note.
5. **Backup success = the backup device can reach the internet over IPv4 _or_
   IPv6.** Either family working is a success; both are not required.
6. The panel shows IPv4 and IPv6 as **two separate rows** (never merged
  /overlapping) for both Status and Settings, and **DNS is shown separately
   per family** (an IPv4 DNS row and an IPv6 DNS row).

## Configuration (new options in `/etc/config/arkbridge`)

All default conservative; existing installs are unaffected.

| option | default | meaning |
|---|---|---|
| `ipv6_enabled` | `0` | master switch for IPv6 failover |
| `primary_gateway6` | `''` | IPv6 primary gateway (auto-detected when empty) |
| `primary_device6` | `''` | IPv6 primary device (falls back to `primary_device`) |
| `backup_gateway6` | `''` | IPv6 backup gateway (auto-detected when empty) |
| `backup_device6` | `''` | IPv6 backup device (falls back to `backup_device`) |
| `probe_targets6` | `''` | IPv6 probe literals; when empty a built-in public set is used |
| `probe_port6` | `443` | IPv6 probe TCP port |
| `probe_host6` | `''` | optional SNI host for the IPv6 probe |
| `probe_table6` | `252` | policy-routing table for IPv6 probes |
| `rule_pref6` | `3001` | ip-6 rule preference for IPv6 probes |
| `bypass_transparent_proxy6` | `0` | exempt IPv6 probes via `ip6tables -t nat OUTPUT` when available |

Shared IPv4 options (`failures_before_switch`, `successes_before_failback`,
`failback_cooldown`, `enabled`, `mode`) apply to both families. `masquerade_backup`
is IPv4-only.

### Built-in IPv6 probe default

Used only when `ipv6_enabled=1` and `probe_targets6` is empty — public anycast
DNS resolvers, not site-specific: `2400:3200::1`, `2400:3200:baba::1`
(AliDNS), `2402:4e00::` (DNSPod).

## Architecture

Keep **one state machine**. Refactor the family-specific operations into helpers
that take an address family (`4`/`6`), then drive v4 and v6 from the single
`run_once` decision.

Helpers (names indicative; exact shape set in the plan):

- `family_default F` — print default routes for family F.
- `family_route_replace F gw dev [proto]` — replace the family default route.
- `family_probe_install F target gw dev table [onlink]` — probe policy route+rule.
- `family_probe_cleanup F ...` — remove probe rules/routes/rules.
- `family_https_ok F target port host` — HTTPS reachability for the family.
- `family_has_addr F dev` — does the device carry a family address.
- `family_masq F dev` — add/remove masquerade (IPv6: no-op).

Each v4 call must be behavior-identical to today (same `ip -4`/`iptables`
arguments, same ordering).

## Health

- IPv4 primary health: as today (any configured `probe_targets` succeeds).
- IPv6 primary health (only when `ipv6_enabled=1`): any `probe_targets6`
  succeeds.
- `primary_ok = v4_ok OR v6_ok` (v6 term present only when enabled).
  A primary is unhealthy only when every enabled family's probe fails.
- Per-family probe-routing validity is tracked separately; a family whose probe
  routes/rules cannot be installed is **invalid** and contributes no success
  (fail-closed), matching current behavior.

## Backup readiness

- IPv4 backup ready: as today (backup gateway/target reachable, device has a
  v4 address, `backup_src_prefix` guard satisfied).
- IPv6 backup ready (only when enabled): an IPv6 backup gateway or target is
  reachable **and** the backup device carries an IPv6 address. Empty
  `backup_gateway6`/`probe_targets6` with nothing auto-detectable ⇒ not ready.
- **Overall backup is ready if either family's backup is ready.** A backup that
  provides only IPv4, or only IPv6, is still a success; both are not required.
  This is the agreed "backup success" rule.

## Decision and switching

1. Compute the shared `target` (`primary`/`backup`) using the existing
   hysteresis on `primary_ok` and overall backup readiness.
2. Apply, transactionally:
   - **IPv4**: when `target=backup` and v4 backup ready, replace the v4 default
     route with the backup (and apply masquerade as configured). When returning
     to primary, restore the v4 default route and remove owned masquerade.
   - **IPv6** (only when `ipv6_enabled=1`):
     - when `target=backup` and v6 backup ready, replace the v6 default route
       with the backup;
     - when `target=backup` but v6 backup **not** ready, **leave the v6 default
       route unchanged** and log `ipv6 backup unavailable; v6 left on primary`;
     - when returning to primary, restore the v6 default route to the primary
       (only if the service had moved it).
3. Verify after applying; on mismatch, roll back the families that were changed
   using per-family snapshots taken before the change.
4. Persist state only on success.

### Snapshots and rollback

Before any change, capture the current v4 default route(s) and (when enabled) the
v6 default route(s). Rollback restores exactly those captured routes per family,
including the "no default route" case, using the existing `restore_old` approach
generalized by family.

### State

The state file keeps the shared `state`/timestamp/fail/success counters and adds
a marker recording whether the service currently owns the v6 backup default
route (so fail-back/cleanup restores v6 only when it changed v6).

## Firewall

- IPv4: unchanged (`iptables` NAT + optional `nat OUTPUT RETURN` bypass).
- IPv6: no NAT. If `bypass_transparent_proxy6=1`, use `ip6tables -t nat OUTPUT
  ... -j RETURN` **only when** the `ip6tables` nat table is available; otherwise
  skip with a one-time log note. Never fail closed on a missing optional proxy
  bypass.

## Guard interaction

`arkbridge` currently has no IPv6 guard; it simply ignores IPv6. With this
change it owns v6 when enabled, so no guard is needed. `usb-uplink-failoverd`'s
separate `ipv6_guard` is unchanged — the two packages are not meant to run
together.

## Auto-detect data (`arkbridge-detect`)

Extend the detector JSON so the panel can render per-family rows. Each candidate
carries family-tagged fields instead of a single flat set:

- v4: `address`, `cidr`, `gateway`, `guessed` (existing) and `dns4[]`.
- v6: `address6`, `cidr6`, `gateway6`, `guessed6` and `dns6[]`.
- `primary` gains the same v6 keys.

DNS is read from netifd's `dns-server` entries and split by family. The detector
stays read-only.

## CLI / status

`arkbridge status` JSON gains a `family` field and per-family entries
(`primary`/`backup` gateway+device, v6 readiness, current v4/v6 default route,
and per-family DNS `dns4`/`dns6`). Existing fields keep their meaning.

## LuCI panel

Two explicit layout rules (agreed):

1. **IPv4 and IPv6 never overlap — they are separate rows.** No family's value
   is shown in the same row/field as the other. This applies to both the Status
   area and the Settings form.
2. **DNS is shown separately per family** — an IPv4 DNS row and an IPv6 DNS row.

Status area rows (read-only, polled):

| row | content |
|---|---|
| Current path | shared `primary`/`backup` + service state (one row) |
| IPv4 | preferred gw/dev; backup gw/dev + readiness; current v4 default route; **v4 DNS** |
| IPv6 | preferred gw/dev; backup gw/dev + readiness; current v6 default route; **v6 DNS** (or "disabled") |
| Last switch | most recent switch line |

Settings form is grouped into two labelled fieldsets — **IPv4** and **IPv6** —
so their fields render on separate rows:

- IPv4 fieldset: preferred gateway/device, backup gateway/device, backup CIDR,
  probe targets (existing fields, unchanged).
- IPv6 fieldset: **an explicit on/off enable toggle**, preferred gateway/device,
  backup gateway/device, probe targets (new fields).
- **Auto-check** fills the IPv4 fields as today and the IPv6 fields from
  detected IPv6 data, writing each into its own field (never a shared field).

### IPv6 enable toggle (explicit opt-in)

The IPv6 fieldset leads with a dedicated **Enable IPv6 failover** toggle
(`ipv6_enabled`, default **off**). This is the operator's deliberate switch: the
engine does nothing with IPv6 unless the operator turns it on, so enabling IPv6
can never happen as a silent side effect.

- The toggle carries a description warning: *"IPv6 is off by default. If you run
  a transparent proxy that only handles IPv4 (e.g. shellcrash), enabling IPv6 can
  let IPv6 traffic bypass the proxy and leak. Turn this on only if you want IPv6
  fallback (e.g. a network with no public IPv4 that only has IPv6)."*
- When off: the engine ignores IPv6 entirely; the Status IPv6 row shows
  `disabled` and the IPv6 fields are not applied.
- When on: IPv6 participates in detection, health, and switching per this spec.

### DNS source

The panel displays the DNS servers **currently acquired by the router's
interfaces**, split by family (v4 addresses in the IPv4 row, v6 addresses in the
IPv6 row), read from netifd's interface dump (`dns-server` entries) and/or
`/etc/resolv.conf` as a fallback. This is display-only; the engine does not
manage DNS.

## Documentation

- `README.md`: replace "IPv4-only" framing; state that IPv6 failover is
  optional, opt-in, and synced with IPv4.
- `docs/arkbridge.md`: add the IPv6 options table, an IPv6 behavior section,
  remove "IPv4 only. IPv6 is not handled.", and document:
  - route-only / no-NAT for IPv6;
  - backup-without-v6 leaves IPv6 unchanged;
  - **the proxy-leak note**: a v4-only transparent proxy does not cover IPv6, so
    enabling IPv6 can leak traffic around the proxy; users relying on a v4-only
    proxy should keep `ipv6_enabled=0` (or extend the proxy to IPv6).

## Testing

Fixture-driven (private temp dirs, no live networking), all hermetic:

- v6 probe install/cleanup and policy table/rule handling.
- Switch moves both v4 and v6 defaults when both backups ready.
- Backup with no v6: v4 switches, v6 default route is byte-for-byte unchanged.
- **Backup success via either family**: a v4-only backup and a v6-only backup
  each count as a working backup.
- Fail-back restores both families to primary.
- Per-family rollback on post-switch verify failure.
- `ipv6_enabled=0`: byte-for-byte IPv4-only behavior (regression guard).
- Config parsing/defaults and auto-detect precedence (explicit beats detected).
- Status JSON includes per-family fields.

Contract tests: config ships the new options defaulted safe/off; engine gates all
v6 work on `ipv6_enabled=1`; no NAT call is made for v6; the panel renders IPv4
and IPv6 in **separate rows** and renders **DNS per family** (no shared row).

## Compatibility / migration

- Default config adds the new options with `ipv6_enabled=0`; existing behavior is
  unchanged and no migration action is required.
- IPv6 requires a kernel/`ip6tables` capable image; absence degrades to IPv4-only
  with a log note, never a hard failure (except when v6 is explicitly enabled and
  its probe routing is invalid, which stays fail-closed).

## Open questions

None outstanding; resolved in the questions above.
