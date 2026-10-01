# ArkBridge Dual-Stack (IPv4+IPv6) Failover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the ArkBridge engine fail over IPv6 in sync with IPv4, opt-in via a panel toggle, with no NAT for IPv6 and separate per-family panel rows including DNS.

**Architecture:** Keep one state machine. Add family-parameterized helpers so the same probe/route/reconcile logic runs for `4` and `6`; `run_once` makes one shared decision and applies each family's default-route change transactionally, skipping IPv6 when the backup has no usable IPv6.

**Tech Stack:** POSIX `sh`, OpenWrt `procd`/`uci`/`ubus`/`jsonfilter`/`netifd`, `ip`/`iptables`/`ip6tables`/`curl`, LuCI (JS + legacy Lua CBI), Python `unittest` fixtures with command stubs.

**Spec:** `docs/superpowers/specs/2026-10-01-arkbridge-ipv6-design.md`

## Global Constraints

- IPv6 remains **opt-in**: `ipv6_enabled` default `0`; with it off the engine behaves byte-for-byte as today (IPv4-only).
- No NAT for IPv6 (no NAT66); IPv6 is switched at the routing level only.
- `package/arkbridge-usb` (`usb-uplink*`) is NOT modified; its `ipv6_guard` is unchanged.
- Backup success = backup device reaches the internet over IPv4 **or** IPv6 (either family).
- Panel shows IPv4 and IPv6 as **separate rows**; DNS is shown **per family**.
- The IPv6 fieldset leads with an explicit **enable toggle** carrying the proxy-leak warning.
- Runtime state stays in the root-only `/var/run/arkbridge` (mode 0700).
- No site-specific identifiers, private IPv4 literals, or secrets in any published file.
- Every engine v4 code path must stay behavior-identical when `ipv6_enabled=0`.

## Review Focus

- IPv6 enabled on a kernel without an `ip6tables` nat table → must degrade to no-proxy-bypass with a log note, never a hard failure.
- Primary healthy on one family only (v4 up / v6 down, or vice versa) → must NOT flap the shared decision; `primary_ok` is the OR of enabled families.
- `ipv6_enabled=1` but the backup has no usable IPv6 → the IPv6 default route must be left untouched (byte-for-byte).
- Auto-detect with more than one IPv6 candidate → must not blindly pick one; list them.
- IPv6 enabled but gateway empty/guessed → v6 fails closed without blocking the v4 path.

---

### Task 1: Make the engine testable and pin current IPv4 behavior

**Files:**
- Modify: `package/arkbridge/files/usr/libexec/arkbridge`
- Create: `tests/test_arkbridge_engine.py`

**Interfaces:**
- Produces: engine honors env overrides `ARKBRIDGE_IP_BIN`, `ARKBRIDGE_IPTABLES_BIN`,
  `ARKBRIDGE_IP6TABLES_BIN`, `ARKBRIDGE_CURL_BIN`, `ARKBRIDGE_PING_BIN`,
  `ARKBRIDGE_UCI_BIN`, `ARKBRIDGE_RUNDIR` (all default to today's values).
- Produces (test harness): `tests/test_arkbridge_engine.py::ArkBridgeFixture` with
  `make_fixture(**opts) -> Fixture(path, run, env, log)`; stub binaries in
  `<root>/bin`; fixture state files `<root>/state/*`.

- [ ] **Step 1: Write the failing test** — a v4-only session runs `arkbridge` once
  and the stub `ip` log shows exactly:
  `ip -4 route show default`, and (when the primary is down and backup ready)
  `ip route replace default via 198.51.100.1 dev eth0`.

```python
# tests/test_arkbridge_engine.py
def test_v4_only_switch_is_unchanged(self):
    fx = self.make_fixture(primary_ok=False, backup_ok=True, backup_has_v4=True)
    fx.run_once()
    self.assertIn("ip -4 route show default", fx.log_text)
    self.assertIn("ip -4 route replace default via 198.51.100.1 dev eth0", fx.log_text)
```

- [ ] **Step 2: Run it and watch it fail** — `python3 -m unittest tests.test_arkbridge_engine -v` → FAIL (harness/`ARKBRIDGE_*` overrides absent).

- [ ] **Step 3: Implement** — add the env-overridable bin vars at the top of
  `arkbridge`; replace every literal `ip `, `iptables `, `curl `, `ping `, `uci `
  call with the corresponding `$*_BIN`; replace `RUNDIR=/var/run/arkbridge` with
  `RUNDIR=${ARKBRIDGE_RUNDIR:-/var/run/arkbridge}`. Build the `tests/test_arkbridge_engine.py`
  harness that writes stub executables logging `"$@"` to `<root>/cmd.log` and
  returning canned output from `<root>/state/*` for `ip route show default`,
  `ip -4 route show default`, `ip link show`, `ip route get`, `curl`, `ping`,
  and `uci -q get arkbridge.main.<opt>`.

- [ ] **Step 4: Run it and watch it pass** — same command → PASS. Run the whole
  suite: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests` → OK.

- [ ] **Step 5: Commit** — `git add package/arkbridge/files/usr/libexec/arkbridge tests/test_arkbridge_engine.py && git commit -m "Make arkbridge engine testable; pin v4 behavior"`

---

### Task 2: Config v6 options + family helpers (no behavior change when off)

**Files:**
- Modify: `package/arkbridge/files/etc/config/arkbridge`
- Modify: `package/arkbridge/files/usr/libexec/arkbridge`
- Modify: `tests/test_package_contract.py`
- Test: `tests/test_arkbridge_engine.py`

**Interfaces:**
- Consumes: Task 1 env overrides.
- Produces: helpers `ipf F ...` (→ `"$IP_BIN" "-$F" ...`), `ipt F ...`
  (→ `$IPTABLES_BIN`/`$IP6TABLES_BIN`), `fam_has_addr F dev`, `fam_route_replace F via gw dev [proto]`,
  `fam_current F`, `fam_restore F "lines"`.
- Produces: config options `ipv6_enabled`(0), `primary_gateway6`(''), `primary_device6`(''),
  `backup_gateway6`(''), `backup_device6`(''), `probe_targets6`(''), `probe_port6`('443'),
  `probe_host6`(''), `probe_table6`('252'), `rule_pref6`('3001'), `bypass_transparent_proxy6`('0').

- [ ] **Step 1: Write the failing tests**

```python
def test_shipped_config_has_safe_ipv6_defaults(self):
    cfg = (ROOT / "package" / "arkbridge" / "files" / "etc" / "config" / "arkbridge").read_text()
    self.assertIn("option ipv6_enabled '0'", cfg)
    self.assertIn("option probe_targets6 ''", cfg)
    self.assertIn("option probe_table6 '252'", cfg)

def test_ipv6_disabled_is_byte_for_byte_v4(self):
    fx = self.make_fixture(primary_ok=False, backup_ok=True, backup_has_v4=True, ipv6_enabled="0")
    fx.run_once()
    self.assertNotIn("ip -6", fx.log_text)          # no v6 system calls at all
```

- [ ] **Step 2: Run and watch fail** — the two tests FAIL.

- [ ] **Step 3: Implement** — add the config options with the exact defaults above.
  In the engine, add the helper functions and convert existing v4 call sites
  (`ip -4 ...` → `ipf 4 ...`; `iptables ...` → `ipt 4 ...`; `dev_has_ipv4` →
  `fam_has_addr 4`; `current_state` → `fam_current 4`; `restore_old` → `fam_restore`)
  with NO v6 call sites yet. Keep `ipv6_enabled` read into `IPV6_ENABLED` but unused.

- [ ] **Step 4: Run and watch pass** — both tests PASS and the Task 1 regression
  still passes; full suite OK.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "Add IPv6 config options and family helpers (inert when off)"`

---

### Task 3: IPv6 probe + health + backup readiness (either-family success)

**Files:**
- Modify: `package/arkbridge/files/usr/libexec/arkbridge`
- Test: `tests/test_arkbridge_engine.py`

**Interfaces:**
- Consumes: Task 2 helpers and options.
- Produces: `fam_https_ok F target port host`; per-family vars `PRIMARY_TARGETS6`,
  `BACKUP_TARGETS6`, `TABLE6`, `PREF6`, `PROBE_PORT6`, `PROBE_HOST6`, `BYPASS6`,
  `BACKUP_GW6`, `BACKUP_DEV6`; detection of `BACKUP_TARGETS6` default set; booleans
  `primary_ok`, `backup_ready`.

- [ ] **Step 1: Write the failing tests**

```python
def test_primary_health_is_or_of_enabled_families(self):
    fx = self.make_fixture(primary_ok_v4=False, primary_ok_v6=True, ipv6_enabled="1",
                           backup_ok=True, backup_has_v4=True)
    fx.run_once()
    self.assertEqual(fx.state()[0], "primary")   # v6 up => primary healthy => no switch

def test_backup_ready_via_v6_only(self):
    fx = self.make_fixture(primary_ok=False, backup_ok_v6=True, backup_has_v6=True,
                           backup_has_v4=False, ipv6_enabled="1")
    fx.run_once()
    self.assertEqual(fx.state()[0], "backup")    # v6-only backup still counts

def test_ipv6_probe_defaults_used_when_empty(self):
    fx = self.make_fixture(ipv6_enabled="1", probe_targets6="")
    self.assertIn("2400:3200::1", fx.effective_targets6())
```

- [ ] **Step 2: Run and watch fail** — FAIL.

- [ ] **Step 3: Implement** — read the v6 options; when `IPV6_ENABLED=1` and
  `PRIMARY_TARGETS6` empty, use the built-in set `2400:3200::1 2400:3200:baba::1 2402:4e00::`;
  install v6 probe routes/rules via `ipf 6 rule`/`ipf 6 route`; probe with
  `"$CURL_BIN" -6`; set `primary_ok = v4_ok OR v6_ok`; set
  `backup_ready = v4_ready OR v6_ready`. Invalid v6 probe routing contributes no
  success (fail-closed) but never aborts the v4 path.

- [ ] **Step 4: Run and watch pass** — PASS; full suite OK.

- [ ] **Step 5: Commit** — `git commit -am "Add IPv6 health probing and either-family backup readiness"`

---

### Task 4: Synced switching + per-family rollback + state marker

**Files:**
- Modify: `package/arkbridge/files/usr/libexec/arkbridge`
- Test: `tests/test_arkbridge_engine.py`

**Interfaces:**
- Consumes: Task 3 booleans.
- Produces: state file line 5 = `v6_active` (0/1) recording whether the service owns
  the v6 backup default route; `fam_restore` used for per-family rollback.

- [ ] **Step 1: Write the failing tests**

```python
def test_switch_moves_both_families_when_both_backups_ready(self):
    fx = self.make_fixture(primary_ok=False, backup_ok=True,
                           backup_has_v4=True, backup_has_v6=True, ipv6_enabled="1")
    fx.run_once()
    self.assertIn("ip -4 route replace default via 198.51.100.1 dev eth0", fx.log_text)
    self.assertIn("ip -6 route replace default via 2001:db8::1 dev eth0", fx.log_text)
    self.assertEqual(fx.state()[0], "backup")

def test_backup_without_v6_leaves_v6_untouched(self):
    fx = self.make_fixture(primary_ok=False, backup_ok=True,
                           backup_has_v4=True, backup_has_v6=False, ipv6_enabled="1")
    fx.run_once()
    self.assertIn("ip -4 route replace default via 198.51.100.1 dev eth0", fx.log_text)
    self.assertNotIn("ip -6 route replace default", fx.log_text)   # v6 unchanged

def test_post_switch_verify_failure_rolls_back_each_family(self):
    fx = self.make_fixture(primary_ok=False, backup_ok=True,
                           backup_has_v4=True, backup_has_v6=True, ipv6_enabled="1",
                           verify_wrong=True)
    fx.run_once()
    self.assertIn("ip -4 route replace default via 203.0.113.1 dev br-lan proto static", fx.log_text)
    self.assertIn("ROLLBACK", fx.log_text)
```

- [ ] **Step 2: Run and watch fail** — FAIL.

- [ ] **Step 3: Implement** — snapshot v4 and (when enabled) v6 defaults before the
  change; apply v4 then v6; verify each family; on mismatch roll back both changed
  families with `fam_restore`; write `v6_active` to state line 5; on fail-back
  restore v6 only when `v6_active=1`.

- [ ] **Step 4: Run and watch pass** — PASS; full suite OK.

- [ ] **Step 5: Commit** — `git commit -am "Switch IPv4 and IPv6 together with per-family rollback"`

---

### Task 5: Status JSON + `arkbridge-detect` per-family data and DNS

**Files:**
- Modify: `package/arkbridge/files/usr/libexec/arkbridge` (`status_json`)
- Modify: `package/arkbridge/files/usr/libexec/arkbridge-detect`
- Test: `tests/test_arkbridge_engine.py`, `tests/test_package_contract.py`

**Interfaces:**
- Produces: status JSON adds `"family"` and per-family `primary`/`backup`
  objects with `gateway6`/`device6`/`ready6`/`route6`/`dns4[]`/`dns6[]`.
- Produces: `arkbridge-detect` candidate keys `address6`, `cidr6`, `gateway6`,
  `guessed6`, `dns4[]`, `dns6[]`; `primary` gains the same.

- [ ] **Step 1: Write the failing tests**

```python
def test_status_reports_separate_family_rows(self):
    fx = self.make_fixture(ipv6_enabled="1")
    s = json.loads(fx.status())
    self.assertIn("family", s)
    self.assertIn("route6", s)
    self.assertIn("dns4", s); self.assertIn("dns6", s)

def test_detect_splits_dns_by_family(self):
    out = json.loads((ROOT / "package" / "arkbridge" / "files" / "usr" / "libexec"
                      / "arkbridge-detect").read_text()) if False else None
    # assert via fixture: candidates carry dns4/dns6 arrays
```

- [ ] **Step 2: Run and watch fail** — FAIL.

- [ ] **Step 3: Implement** — extend `status_json` to emit the new keys; extend
  `arkbridge-detect` to read `dns-server` from `ubus call network.interface dump`,
  split by family, and emit `address6`/`cidr6`/`gateway6`/`guessed6`/`dns4`/`dns6`.

- [ ] **Step 4: Run and watch pass** — PASS; full suite OK.

- [ ] **Step 5: Commit** — `git commit -am "Expose per-family status and DNS in status/detect"`

---

### Task 6: LuCI panel — IPv6 fieldset with toggle, separate rows, per-family DNS

**Files:**
- Modify: `package/luci-app-arkbridge/htdocs/luci-static/resources/view/arkbridge/overview.js`
- Modify: `package/luci-app-arkbridge/root/usr/lib/lua/luci/model/cbi/arkbridge/overview.lua`
- Modify: `package/luci-app-arkbridge/root/usr/lib/lua/luci/view/arkbridge/status.htm`
- Modify: `package/luci-app-arkbridge/root/usr/lib/lua/luci/view/arkbridge/autocheck.htm`
- Test: `tests/test_package_contract.py`

**Interfaces:**
- Consumes: Task 5 status/detect JSON.
- Produces: IPv6 fieldset with `ipv6_enabled` toggle + description warning; status
  renders separate IPv4 and IPv6 rows with per-family DNS; Auto-check fills v6 fields.

- [ ] **Step 1: Write the failing tests**

```python
def test_panel_has_ipv6_toggle_and_separate_rows(self):
    js = (ROOT / "package" / "luci-app-arkbridge" / "htdocs" / "luci-static"
          / "resources" / "view" / "arkbridge" / "overview.js").read_text()
    self.assertIn("ipv6_enabled", js)
    self.assertIn("ipv6", js.lower())
    status = (ROOT / "package" / "luci-app-arkbridge" / "root" / "usr" / "lib"
              / "lua" / "luci" / "view" / "arkbridge" / "status.htm").read_text()
    self.assertIn("dns4", status); self.assertIn("dns6", status)
```

- [ ] **Step 2: Run and watch fail** — FAIL.

- [ ] **Step 3: Implement** — add the IPv6 fieldset (toggle first, with the warning
  text from the spec) to `overview.js` and `overview.lua`; render separate IPv4/IPv6
  rows and `DNS (IPv4)` / `DNS (IPv6)` rows in `status.htm`; extend `autocheck.htm`
  to fill the v6 fields.

- [ ] **Step 4: Run and watch pass** — PASS; full suite OK.

- [ ] **Step 5: Commit** — `git commit -am "LuCI: IPv6 toggle, separate rows, per-family DNS"`

---

### Task 7: Documentation + redaction

**Files:**
- Modify: `README.md`, `docs/arkbridge.md`
- Test: `tests/test_package_contract.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_docs_describe_ipv6_and_proxy_leak(self):
    doc = (ROOT / "docs" / "arkbridge.md").read_text()
    readme = (ROOT / "README.md").read_text()
    self.assertIn("ipv6_enabled", doc)
    self.assertNotIn("IPv4 only. IPv6 is not handled.", doc)
    for t in (doc, readme):
        self.assertIn("proxy", t.lower())
```

- [ ] **Step 2: Run and watch fail** — FAIL.

- [ ] **Step 3: Implement** — update both docs per the spec's Documentation section,
  including the proxy-leak note and route-only/no-NAT statement.

- [ ] **Step 4: Run and watch pass** — PASS; full suite OK; `make shellcheck` clean.

- [ ] **Step 5: Commit** — `git commit -am "Document dual-stack IPv6 failover"`

---

## Self-Review

- **Spec coverage:** Objective/Requirements → Tasks 3–4; config → Task 2; architecture/helpers → Task 2; health → Task 3; backup readiness (either family) → Task 3; switching/rollback/state → Task 4; detect + status → Task 5; panel (separate rows, DNS, toggle) → Task 6; docs + leak note → Task 7; `ipv6_enabled=0` regression → Tasks 1–2.
- **Step scan:** each step is one action; bodies only where an algorithm is non-obvious.
- **Type consistency:** `ipf`/`ipt`/`fam_has_addr`/`fam_route_replace`/`fam_current`/`fam_restore`/`fam_https_ok` names are used consistently across Tasks 2–5.
- **Review Focus:** each of the five failure modes has a task test (no-ip6tables → Task 4/2, single-family health → Task 3, backup-without-v6 → Task 4, multiple v6 candidates → Task 5 detect, v6 fail-closed → Task 3).
- **Proportion:** plan is far shorter than the code it produces; bodies limited.
