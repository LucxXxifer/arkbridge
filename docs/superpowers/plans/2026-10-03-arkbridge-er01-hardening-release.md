# ArkBridge the side router Hardening and Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the locally verified ArkBridge watchdog and rollback changes into a reviewed, reproducible release, then deploy and validate it on the side router while preserving the fixed-IP client's fixed LAN configuration and protecting every live test with an independent rollback guard.

**Architecture:** Keep ArkBridge generic. The engine owns source-bound primary/backup probes, the 20/25-second backup watchdog, verified force-primary rollback, NAT/ownership state, and an optional asynchronous `post_switch_hook`. The the side router-specific UU Remote session refresh remains a separately backed-up site hook that closes only the fixed-IP client's <signal_domain> proxy sessions after a verified path transition.

**Tech Stack:** POSIX/BusyBox `ash`, OpenWrt UCI/procd, `ip-full`, `iptables`, `curl`, Python 3.11 split packages on the side router, unittest, ShellCheck, OpenWrt SDK 24.10.3, GitHub Actions, browser-use/CDP ttyd access.

**Spec:** Existing IPv6 design: `docs/superpowers/specs/2026-10-01-arkbridge-ipv6-design.md`. Runtime behavior and the side router evidence: `docs/arkbridge.md` and the read-only diagnosis recorded in the session on 2026-10-03.

## Global Constraints

- Keep the fixed-IP client at `<client_ip>/24`, gateway `<side_router_lan_ip>`, DNS `<side_router_lan_ip>`.
- Keep the side router LAN as `<side_router_lan_ip>/br-lan`, primary gateway `<primary_gateway>`, backup `<backup_gateway>/eth0`.
- Do not enable the side router DHCP for this topology; `dhcp.lan.ignore=1` is intentional.
- Primary and backup probes must be source-bound and fail closed when their source address is unavailable.
- At backup age 20 seconds, begin the bounded backup HTTPS decision; recovery must start before the 25-second deadline on a responsive system.
- Explicit rollback, cleanup, failed backup transactions, and watchdog recovery must share the verified force-primary transaction.
- Remove backup NAT only after the primary route is verified; retain NAT and ownership on any failed recovery.
- Ordinary dual-stack failback remains family-aware; an unhealthy IPv6 family stays on backup while healthy IPv4 returns.
- The public ArkBridge package must remain generic. The UU-specific hook is an the side router site overlay and must not contain credentials or account identifiers.
- Every the side router route/firewall experiment must have an independent detached rescue guard armed and verified before the experiment starts.
- Do not publish or push until the complete diff, fresh audit, CI build, and the side router verification have been reviewed.

## Review Focus

- A shared probe destination with different source interfaces: test that primary and backup cannot leak into each other's policy table.
- A watchdog invocation at age 20, age 24, and age 25 with slow HTTPS: test one absolute budget and no late curl after the deadline.
- A failed primary route replacement or failed NAT deletion: test that backup NAT/ownership and `rollback_pending` survive and retry before health probes.
- Multiple default routes with different metrics and device-name substrings: test exact lowest-metric route matching.
- IPv6-only backup plus an unhealthy IPv6 primary during ordinary failback: test that IPv4 recovery does not drag IPv6 back prematurely.

---

### Task 1: Freeze the live the side router baseline and preserve rollback material

**Files:**
- Create: `/root/arkbridge-before-<timestamp>` on the side router during deployment (live backup, not repository content)
- Modify: none initially
- Test: live read-only command transcript saved with the deployment record

**Interfaces:**
- Consumes: current the side router `/etc/config/arkbridge`, `/etc/config/network`, `/etc/config/firewall`, `/usr/libexec/arkbridge`, `/usr/libexec/<site-hook>` if present.
- Produces: a timestamped deployment backup, current default routes, UCI values, process list, conntrack/NAT snapshot, and current UU signal evidence.

- [ ] Capture the side router read-only state through ttyd: `ip -4 route show default`, `ip -4 rule show`, `ip -4 addr show br-lan`, `ip -4 addr show eth0`, `uci -q show arkbridge.main`, `uci -q show network.lan`, `uci -q show dhcp.lan`, `iptables-save -t nat`, and `ps w`.
- [ ] Confirm only one `/usr/libexec/arkbridge loop` is running and no `side-router-failover` process remains.
- [ ] Confirm the fixed-IP client's local route and DNS from macOS without changing them.
- [ ] Run the UU checker and record process, API HTTP status, signal socket, and current log paths.
- [ ] Before any live route change, create the detached rescue snapshot containing the current engine, UCI config, default route, backup NAT presence, and hook.
- [ ] Verify the detached guard writes its ready marker and survives closure of the interactive ttyd command.

### Task 2: Stabilize the generic engine contract with tests first

**Files:**
- Modify: `package/arkbridge/files/usr/libexec/arkbridge`
- Modify: `package/arkbridge/files/etc/config/arkbridge`
- Modify: `tests/test_arkbridge_engine.py`
- Modify: `tests/test_package_contract.py`

**Interfaces:**
- Consumes: UCI options `backup_source_rule_pref`, `backup_source_rule_pref6`, `backup_probe_targets6`, `backup_grace_seconds`, `backup_watchdog_seconds`, and `post_switch_hook`.
- Produces: source-bound `install_probe`, `backup_family_ok`, `rollback_to_primary`, `failed_transition`, `check_watchdog`, `post_switch`, and status fields `rollback_pending`, `dead`, and `degraded`.

- [ ] Add or retain failing tests for source-bound primary and backup probes using literal sources `192.0.2.2` and `198.51.100.2`, including a shared destination target.
- [ ] Add or retain tests for IPv6 source-bound backup probes and IPv6-only backup readiness.
- [ ] Add tests for absolute watchdog timing at ages 20, 24, 25, and a late tick; assert no new curl after the deadline when no fresh proof exists.
- [ ] Add tests for a successful proof during the grace window; assert the initial watchdog is disarmed while normal future health monitoring remains active.
- [ ] Add tests for failed route restore, failed NAT query/delete, and an interrupted rollback; assert route/NAT/ownership markers and `rollback_pending` remain recoverable.
- [ ] Add tests for ordinary family-aware failback, explicit force-primary rollback, and failed backup transaction rollback.
- [ ] Add tests for the asynchronous hook: it runs only after verified transition, receives `<new> <previous>`, does not hold the lock, and is skipped on failed transition.
- [ ] Implement only the smallest production changes needed for those tests.
- [ ] Add package contract assertions for every new UCI default and the status JSON fields.
- [ ] Run `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests` and `make shellcheck`; both must pass before proceeding.

### Task 3: Keep the the side router UU integration as a site overlay

**Files:**
- Create during deployment: `/usr/libexec/<site-hook>` on the side router
- Modify: `/etc/config/arkbridge` on the side router only: `option post_switch_hook '/usr/libexec/<site-hook>'`
- Modify: `docs/arkbridge.md` to document the generic hook contract and site-overlay boundary
- Test: an the side router-side hook test using ShellCrash controller metadata

**Interfaces:**
- Consumes: hook arguments `<new-path> <previous-path>`, ShellCrash controller `GET /connections`, `metadata.sourceIP`, `metadata.remoteDestination`, `metadata.host`, and DELETE `/connections/<id>`.
- Produces: refresh only when the source is `<client_ip>` and the host ends with <signal_domain>; no selector changes, no full conntrack flush, no DHCP/DNS modification.

- [ ] Back up the old hook before replacing it.
- [ ] Verify the hook disables proxy use for its controller request and handles missing controller/API gracefully.
- [ ] Verify it closes only matching the fixed-IP client UU sessions and removes matching conntrack entries for the current endpoint.
- [ ] Run it once while primary is active and confirm it does not touch unrelated sessions.
- [ ] Run it after backup transition and confirm the log records exactly the matching session count.
- [ ] Run it after rollback and confirm it can refresh the newly selected primary session.

### Task 4: Reconcile documentation and release metadata

**Files:**
- Modify: `docs/arkbridge.md`
- Modify: `README.md` if release behavior or hook configuration is described there
- Modify: `package/arkbridge/Makefile` only if the production dependency set changes
- Modify: `.github/workflows/build.yml` only if a build contract needs updating

**Interfaces:**
- Consumes: the final engine/config behavior and the the side router dependency evidence.
- Produces: generic user documentation, explicit rollback/watchdog semantics, source-bound probe documentation, and a release candidate version plan.

- [ ] Document that backup internet readiness uses source-bound HTTPS and no longer treats gateway ping alone as internet readiness.
- [ ] Document that explicit rollback and cleanup use verified force-primary recovery.
- [ ] Document the generic hook contract without embedding the the side router UU domain or device-specific private details in the public package.
- [ ] Document the the side router dependency additions separately from the generic package dependency list.
- [ ] Update the planned release from `v0.2.2` to the next patch version only after the final diff is approved.

### Task 5: Fresh audit, build, and release candidate verification

**Files:**
- Modify: none after Task 4 unless audit finds a defect
- Test: local suite, ShellCheck, GitHub Actions, two model audits

**Interfaces:**
- Consumes: the complete uncommitted diff and final tests.
- Produces: a fresh `AUDIT: PASS`, green tests, three SDK artifacts, and a release candidate commit.

- [ ] Run the complete local suite again: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests`.
- [ ] Run `make shellcheck`, `sh -n package/arkbridge/files/usr/libexec/arkbridge`, and `git diff --check`.
- [ ] Send a focused audit prompt to `luciferai-gpt2/gpt-6.1-sol max` without subagents; if it times out, record that fact and do not treat it as a pass.
- [ ] Send the same focused audit to `hohai/deepseek-v4.1-flash xhigh`; require concrete findings and `AUDIT: PASS`.
- [ ] Inspect `git diff`, `git status`, and recent history; stage only intended repository files.
- [ ] Commit the final source/docs/tests as one focused commit.
- [ ] Push the protected branch only after reviewing the complete diff.
- [ ] Trigger the existing build workflow and require tests plus `aarch64_cortex-a53`, `x86_64`, and `mipsel_24kc` builds to pass.
- [ ] Publish the next patch release only after CI artifacts and audit evidence are recorded.

### Task 6: Deploy the released package to the side router with a detached guard

**Files:**
- Live: the side router package files, `/etc/config/arkbridge`, `/usr/libexec/<site-hook>`
- Live backup: `/root/arkbridge-before-<timestamp>`
- Test: deployment status and rollback guard transcript

**Interfaces:**
- Consumes: architecture-matched `arkbridge` ipk, current the side router config backup, site hook, and detached rescue guard.
- Produces: installed release, enabled service, primary route, source-bound backup settings, and `rollback_pending=false`.

- [ ] Verify the downloaded ipk checksum and package architecture before installation.
- [ ] Arm the detached guard and verify readiness before stopping ArkBridge.
- [ ] Back up `/etc/config/arkbridge`, `/etc/config/network`, `/etc/config/firewall`, the old engine, and the old hook.
- [ ] Stop ArkBridge, install the new package, restore or set only the intended ArkBridge options, install the site hook, and start the service.
- [ ] Verify service status, one loop, primary route, status JSON, no temporary trial rules, and UU established signal.
- [ ] Confirm the fixed-IP client fixed IP/gateway/DNS are unchanged.
- [ ] Confirm the detached guard disarms only after all post-install checks pass.

### Task 7: Run the staged the side router acceptance test

**Files:**
- Live logs: `/var/run/arkbridge/log`, `/var/run/arkbridge/state`, hook log, conntrack capture
- Test record: `docs/verification/2026-10-03-er01-acceptance.md`

**Interfaces:**
- Consumes: deployed package, site hook, detached guard, the fixed-IP client, Huawei `eth0`, ShellCrash controller, and UU mobile observation.
- Produces: a pass/fail matrix for primary, backup, UU refresh, watchdog rollback, and cleanup.

- [ ] Baseline primary: the fixed-IP client internet/DNS, UU connected, the side router status primary.
- [ ] Arm detached guard for at least 35 seconds.
- [ ] Isolate only primary probe targets or the primary WAN path; do not block ttyd, Tailscale, or all LAN traffic.
- [ ] Verify backup switch, `eth0` source `<backup_router_ip>`, backup MASQUERADE, and source-bound backup probe rules.
- [ ] Verify hook closes only the the fixed-IP client UU signal session and that a new `ASSURED` connection uses `<backup_router_ip>`.
- [ ] Force backup HTTPS failure after switch; verify degraded state at 20 seconds, primary recovery by 25 seconds, `backup_dead=true`, and no repeated backup flap.
- [ ] Restore primary, verify UU reconnects, and clear all test rules/conntrack entries only for the test endpoint.
- [ ] Confirm Tailscale access, ttyd access, primary route, and one ArkBridge loop after cleanup.
- [ ] If any step fails, let the detached guard perform recovery before opening another session.

### Task 8: Closeout and operational handoff

**Files:**
- Create: `docs/verification/2026-10-03-er01-acceptance.md`
- Modify: `docs/arkbridge.md` only for verified operational instructions

- [ ] Record exact package version, engine hash, config values, dependency list, audit output, test count, CI run, and the side router acceptance results.
- [ ] Record the manual rollback command and the the side router backup path.
- [ ] Confirm no temporary HTTP delivery server, guard, trial process, reject rule, or broad conntrack flush remains.
- [ ] Confirm the public repository is clean after commit/push and the released artifact matches the reviewed commit.
- [ ] Report the remaining scope limit explicitly: UU mobile screen-control itself was not driven by the agent; signal transport and the side router-side state were verified.

### Task 9: Prove the cloud-only GitHub Actions build lane

**Files:**
- Modify: `.github/workflows/build.yml` only if the cloud lane needs an additional verification step
- Create remotely: a temporary GitHub branch such as `cloud-build-proof/<date>` through GitHub Web Editor or Codespaces
- Test: the dispatched GitHub Actions run and downloaded architecture-scoped artifacts

**Interfaces:**
- Consumes: a commit made entirely through GitHub Web Editor/Codespaces, the existing `workflow_dispatch` inputs `version` and `publish`, and the existing OpenWrt SDK matrix.
- Produces: a cloud-run test result, three architecture-scoped `.ipk` artifact sets, checksums, and a documented answer about whether local SDK compilation is necessary.

- [ ] Start from the reviewed source commit on a temporary cloud branch; do not run an OpenWrt SDK build on the Mac.
- [ ] Make one harmless, reviewable cloud-only change on that branch, such as a documentation or package-contract assertion change, and commit it through GitHub Web Editor/Codespaces.
- [ ] Confirm the existing workflow checks out the selected branch, runs the unittest job, and uses the SDK matrix for `aarch64_cortex-a53`, `x86_64`, and `mipsel_24kc`.
- [ ] If the workflow does not run `make shellcheck`, add that command to the cloud test job and add a workflow contract test in `tests/test_package_contract.py`.
- [ ] Dispatch the workflow from the temporary branch with `publish=false`; do not create a public Release during this proof.
- [ ] Watch the run to completion with `gh run watch`, then download artifacts with `gh run download` and verify that each architecture contains `arkbridge`, `luci-app-arkbridge`, and `usb-uplink` packages.
- [ ] Calculate SHA-256 checksums for the downloaded artifacts and record the workflow run ID, commit SHA, target architectures, test count, and artifact names in the verification report.
- [ ] Confirm the cloud build uses the repository's package files and OpenWrt SDK only; no local compiler, SDK archive, or local build output is part of the result.
- [ ] If the cloud proof passes, make cloud Actions the standard release build path: edit code in GitHub Web Editor/Codespaces, dispatch the workflow, review artifacts, then publish with `publish=true` only after approval.
- [ ] Delete the temporary cloud branch after artifact verification, preserving the run URL and checksums in the report. Do not delete the protected release branch or existing releases.

## Execution Order

Tasks 1–3 can begin after plan approval, but no live route experiment precedes Task 1's detached guard. Task 4 follows the final code behavior. Task 5 must pass before Task 6. Task 7 is the final field gate before the release is called operational.

Task 9 is a separate release-process proof. It is appended as the final stage, but its successful result becomes a prerequisite for future cloud-only releases; it does not alter the already verified the side router runtime deployment.

## Self-Review

- The source-bound probe requirement is covered by Tasks 2 and 7.
- The 20/25 timing and late-tick behavior is covered by Task 2's tests and Task 7's field timing.
- NAT/ownership retention and retry behavior is covered by Tasks 2, 6, and 7.
- The the side router-specific UU behavior is isolated in Task 3 and does not become a generic package dependency.
- The plan explicitly separates manual live deployment from public release and requires fresh audit/build evidence before publishing.
- The cloud-only build question is covered by Task 9: a remote branch edit, workflow dispatch with `publish=false`, three SDK targets, artifact checksums, and no local SDK compilation.
