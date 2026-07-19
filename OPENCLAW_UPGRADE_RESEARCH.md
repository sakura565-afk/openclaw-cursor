# OpenClaw Windows upgrade blocker research

**Scope:** READ-ONLY investigation of why `openclaw update` fails with the “Package updates cannot run from inside the gateway service process” error on a Windows Scheduled Task install (2026.6.11 → 2026.7.1-2). No production code was modified; `openclaw update` was not run against a live install.

**Upstream sources examined:** `openclaw/openclaw` tag `v2026.6.11` (commit `e085fa1a3f`, matches reported `e085fa1`), tag `v2026.7.1`, tag `v2026.7.2-beta.3`, PRs `#75729`, `#75819`, `#78494`, `#107288`, `#107339`, issues `#75691`, `#78492`, docs at docs.openclaw.ai (`cli/update`, `install/updating`, `platforms/windows`, `cli/gateway`).

---

## Summary (~200 words)

**The check is triggered by X:** process environment markers `OPENCLAW_SERVICE_MARKER=openclaw` (and optional `OPENCLAW_SERVICE_KIND=gateway`) on the *updater* process — not by inspecting a live Node gateway PID tree. Those markers are written into the managed Windows launcher (`%USERPROFILE%\.openclaw\gateway.cmd` via `set …`) by `buildServiceEnvironment()`. When present, `shouldBlockMutableUpdateFromGatewayServiceEnv()` fails closed if the managed service still looks running, if runtime status is unknown/uninspectable, or if the updater could not stop it. That is why killing every `node.exe` still fails: the shell can keep the markers, and/or schtasks runtime probing can remain inconclusive (`unknown` → block). `--dry-run` skips this path entirely, which matches the observed dry-run success.

**The escape hatch is Y:** run `openclaw update` from a **fresh external PowerShell** that does **not** inherit those markers (verify with `Get-ChildItem Env:OPENCLAW_*`). If markers are present, clear them for that session (`Remove-Item Env:OPENCLAW_SERVICE_MARKER`, `Remove-Item Env:OPENCLAW_SERVICE_KIND`). There is **no** `--force` / `--allow-unsafe-update` bypass. `OPENCLAW_SUPERVISOR_MODE=external` is the opposite of a bypass (it refuses self-update). `--no-restart` does **not** help while markers + a “still running” view of the service remain.

**Run Z:** from a clean shell, clear markers if set, then `openclaw update --tag 2026.7.1-2 --yes` (preferred; coordinates stop/swap/restart). If that still hits the guard or engines preflight, stop via `openclaw gateway stop`, upgrade Node to ≥24.15.0 if needed, `npm i -g openclaw@2026.7.1-2`, then `openclaw gateway install --force` + `openclaw gateway restart`. Rollback: `openclaw update --tag 2026.6.11` or stop → `npm i -g openclaw@2026.6.11` → `gateway install --force` → `gateway restart`.

---

## 1. Exact code path that emits the error

### Current install: `v2026.6.11` / `e085fa1a3f`

| Item | Value |
| --- | --- |
| Primary file | `src/cli/update-cli/update-command.ts` |
| Marker constants | `src/daemon/constants.ts` (`GATEWAY_SERVICE_MARKER = "openclaw"`, `GATEWAY_SERVICE_KIND = "gateway"`) |
| Marker injection | `src/daemon/service-env.ts` → `buildServiceEnvironment()` |
| Windows launcher write | `src/daemon/schtasks.ts` → `buildTaskScript()` (`set KEY=value` lines into `gateway.cmd`) |
| Tests locking the message | `src/cli/update-cli.test.ts` (“inherited gateway service env…”) |

**Detection helper** (`isRunningInsideGatewayService`, lines **1000–1008** at `e085fa1a3f`):

```1000:1008:src/cli/update-cli/update-command.ts
function isRunningInsideGatewayService(
  env: Record<string, string | undefined> = process.env,
): boolean {
  if (env.OPENCLAW_SERVICE_MARKER?.trim() !== GATEWAY_SERVICE_MARKER) {
    return false;
  }
  const serviceKind = env.OPENCLAW_SERVICE_KIND?.trim();
  return !serviceKind || serviceKind === GATEWAY_SERVICE_KIND;
}
```

**Gate that decides to block** (`shouldBlockMutableUpdateFromGatewayServiceEnv`, lines **1010–1027**):

```1010:1027:src/cli/update-cli/update-command.ts
function shouldBlockMutableUpdateFromGatewayServiceEnv(params: {
  preManagedServiceStop: PreManagedServiceStop | undefined;
}): boolean {
  if (!isRunningInsideGatewayService()) {
    return false;
  }
  const stopState = params.preManagedServiceStop;
  if (!stopState?.inspected) {
    return true;
  }
  if (stopState.stopped) {
    return false;
  }
  if (!stopState.runtimeInspected) {
    return true;
  }
  return stopState.running;
}
```

**Full conditional that emits the exact operator error** (lines **3630–3641**), after optional managed-service stop / ancestry `blockMessage` handling:

```3630:3641:src/cli/update-cli/update-command.ts
    if (shouldBlockMutableUpdateFromGatewayServiceEnv({ preManagedServiceStop })) {
      stop();
      const updateLabel = updateInstallKind === "git" ? "Git updates" : "Package updates";
      defaultRuntime.error(
        [
          `${updateLabel} cannot run from inside the gateway service process.`,
          "That path replaces the active OpenClaw dist tree while the live gateway may still lazy-load old chunks.",
          `Run \`${replaceCliName(formatCliCommand("openclaw update"), CLI_NAME)}\` from a shell outside the gateway service, or stop the gateway service first and then update.`,
        ].join("\n"),
      );
      defaultRuntime.exit(1);
      throw new UpdateCommandAbort();
    }
```

**Why `--dry-run --json` succeeds:** `opts.dryRun` returns early around lines **3449–3520** with a plan only. It never reaches `stopManagedServiceBeforeMutableUpdate` / the service-env guard (and also never reaches the later Node `engines` preflight at **3560–3574**).

**Related (different message):** PPID/ancestry guard `formatGatewayAncestryBlockMessage` (lines **811–815**) says *“running inside the gateway process tree”* — that is **not** the error you hit. Your literal string is the **service-marker** path above.

### Target line / v7.x

At `v2026.7.1`, the same helpers remain with the same semantics (`isRunningInsideGatewayService` ~1345, `shouldBlockMutableUpdateFromGatewayServiceEnv` ~1355, error emit ~4214). Naming shifted earlier from “package” to “mutable” update, but the marker + fail-closed logic is unchanged. v7 also adds Windows task auto-start recovery bookkeeping around stop paths; it does **not** remove the env-marker guard.

Upstream introduction: PR **#75729** / merge commit `0b09cfb8cd` — *“fix(cli): block package updates from inside running gateway service”*.

---

## 2. Specific marker being checked (candidate matrix)

| Candidate | Verdict | Evidence |
| --- | --- | --- |
| **Env vars `OPENCLAW_SERVICE_MARKER` / `OPENCLAW_SERVICE_KIND`** | **yes, this is it** | `isRunningInsideGatewayService()` requires `OPENCLAW_SERVICE_MARKER === "openclaw"`; kind empty or `"gateway"`. Injected by `buildServiceEnvironment()` (`service-env.ts` ~439–440). On Windows, copied into `gateway.cmd` as `set` lines (`schtasks.ts` `buildTaskScript`). Issue [#78492](https://github.com/openclaw/openclaw/issues/78492) reproduces the **exact** error from inherited markers; workaround is strip markers. |
| Lockfile / sentinel under `%TEMP%` / `~/.openclaw/` | **no, not relevant** for this error | Guard reads `process.env` + managed-service stop/runtime state. No temp lockfile check in the emit path. (Separate update/restart *sentinels* exist for handoff health, not this message.) |
| Windows Scheduled Task registration (`schtasks` / ITaskService) | **partially relevant as secondary input, not the trigger** | Markers alone arm the guard. Then `maybeStopManagedServiceBeforeMutableUpdate` → `readGatewayServiceState` / schtasks runtime. `deriveScheduledTaskRuntimeStatus` treats Last Run Result `0x41301` as running; missing numeric result + locale Status → **`unknown`**. `unknown` ⇒ `runtimeInspected: false` ⇒ **block** when markers present. Disabling/deleting the task does **not** clear markers in your shell and can leave inspection inconclusive. |
| Windows Service / SCM | **no, not relevant** | Native Windows uses Scheduled Task / Startup-folder login item, not a Win32 service (`docs/platforms/windows.md`). |
| Named pipe `\\.\pipe\openclaw-*` | **no, not relevant** | Not referenced by the update guard. |
| Port / listener fallback | **secondary only** | `resolveListenerBackedScheduledTaskRuntime` can still mark status `running` if something listens on the gateway port even when schtasks disagrees — still only matters **after** markers are set. |
| Registry key | **no, not relevant** | No registry read in this guard path. |
| `service-env/` files | **macOS/Linux-oriented; not the Windows trigger** | Launchd uses `~/.openclaw/service-env/*.env`. Windows path is `gateway.cmd` `set` lines. Files on disk do not auto-load into a random PowerShell session. |
| Live process-tree / PPID walk | **no for this error string** | Separate guard (`gatewayAncestryBlockMessage` / PR #75819). Different text. Explains why kill-all alone does not address **this** message. |
| `OPENCLAW_GATEWAY_PORT` / `OPENCLAW_SUPERVISOR_MODE` / `OPENCLAW_WRAPPER` | **no as the trigger** | Port is service config. `OPENCLAW_SUPERVISOR_MODE=external` (7.2+) **refuses** self-update. Wrapper is install metadata; regenerating it does not remove service markers. |

### Why failed workarounds still saw the error

1. **Markers still present in the updater’s environment** (agent/Telegram/exec child, shell started under gateway context, or user/system env persistence). Diagnostic: `Get-ChildItem Env:OPENCLAW_*`.
2. Even with markers + “all Node dead”, if OpenClaw’s runtime probe returns **`unknown`** or still **`running`** (stale `0x41301`, port busy, probe failure), `shouldBlockMutableUpdateFromGatewayServiceEnv` returns **true**. Unit tests explicitly refuse updates when “runtime probe fails” / “runtime status is unknown” with inherited markers (`update-cli.test.ts`).
3. Tests also show: markers + **confirmed stopped** runtime **allows** update. So “kill everything” is not equivalent to “OpenClaw inspected stopped.”

---

## 3. Documented escape hatches

| Mechanism | Works as bypass? | Notes |
| --- | --- | --- |
| Fresh shell **without** service markers | **yes (supported)** | Error text + docs: run update outside the gateway service. |
| Unset `OPENCLAW_SERVICE_MARKER` / `OPENCLAW_SERVICE_KIND` | **yes (documented workaround)** | [#78492](https://github.com/openclaw/openclaw/issues/78492) / PR [#78494](https://github.com/openclaw/openclaw/pull/78494): `env -u OPENCLAW_SERVICE_MARKER -u OPENCLAW_SERVICE_KIND openclaw update --yes`. PowerShell: `Remove-Item Env:OPENCLAW_SERVICE_MARKER,Env:OPENCLAW_SERVICE_KIND -ErrorAction SilentlyContinue`. |
| `openclaw update` with restart enabled after real stop | **yes (supported)** | Docs: updater stops managed service, swaps package, refreshes metadata, restarts (`docs/cli/update.md`, `docs/install/updating.md`). |
| Manual: `gateway stop` → package manager → `gateway install --force` → `gateway restart` | **yes (supported)** | Explicit for supervised installs when not using the updater’s coordination (`docs/install/updating.md`). |
| `--dry-run` | preview only | Skips guard; does not update. |
| `--no-restart` | **no** while service appears running under markers | Test: refuses package updates when `--no-restart` leaves gateway running with inherited markers. |
| `--force` / `--allow-unsafe-update` / `--unsafe-update` | **do not exist** on `openclaw update` | |
| `--acknowledge-clawhub-risk` | **no** | ClawHub plugin trust only. |
| `OPENCLAW_SUPERVISOR_MODE=external` | **no — anti-escape** | Changelog / `docs/cli/gateway.md` (7.2+): **OpenClaw self-update is refused** so an external supervisor can own stop/replace/restart. Do **not** set this to clear the 6.11 guard. |
| `openclaw gateway install --wrapper …` | **no for this guard** | Persists `OPENCLAW_WRAPPER`; service markers are still always set in `buildServiceEnvironment()`. Regenerating the wrapper does not drop “inside service” detection. |

**Windows Scheduled Task procedure (docs):** use `openclaw gateway install|status|restart|stop` and prefer `openclaw update` for supervised installs. There is no separate “schtasks /DISABLE then npm” recipe in official docs; raw schtasks disable/end is what you already tried and is **not** the supported stop path.

---

## 4. Upstream PRs / commits / issues

| Item | Relation to this blocker |
| --- | --- |
| **PR [#75729](https://github.com/openclaw/openclaw/pull/75729)** (`0b09cfb8cd`) | **Introduced** the service-marker package-update block and the exact error string. |
| **Issue [#75691](https://github.com/openclaw/openclaw/issues/75691)** | Requested PPID-ancestry detection for in-gateway `exec` updates. |
| **PR [#75819](https://github.com/openclaw/openclaw/pull/75819)** | Added **ancestry** block (different error text). Complementary, not the string you see. |
| **Issue [#78492](https://github.com/openclaw/openclaw/issues/78492)** + **PR [#78494](https://github.com/openclaw/openclaw/pull/78494)** | Same error when auto-update child **inherits** markers; fix strips markers for the auto-update child. Confirms env inheritance as the real-world trigger. |
| **PR [#107288](https://github.com/openclaw/openclaw/pull/107288)** | Telegram durable-ingress / PID-TID claim liveness + update-id watermark. **Did not** add or change the gateway-service package-update protection. |
| **PR [#107339](https://github.com/openclaw/openclaw/pull/107339)** | Restart-admission fence / `GatewayDrainingError` wedge fix. **Did not** add `OPENCLAW_SUPERVISOR_MODE=external`. |
| **`OPENCLAW_SUPERVISOR_MODE=external`** | Appears in **2026.7.2-beta** changelog as a **separate** “external gateway supervision” feature (thanks @shakkernerd), not as part of #107339. |
| Open issues labeled `update`+`windows` for this exact string | GitHub search for the literal error returned related items (#78492 etc.); combined `label:windows`+`label:update` search returned **0** open hits at research time. Closest historical Windows update pain is EBUSY file-lock work (e.g. #40540 / #41994), which is a different failure mode. |

---

## 5. Recommended Windows-safe update sequence

### Preconditions / diagnostics (do these first)

Run in a **new** PowerShell window started from Start Menu / Win+X — **not** from Telegram, agent `exec`, or a console spawned by the gateway task:

```powershell
# 1) Prove whether the guard is armed in THIS shell
Get-ChildItem Env:OPENCLAW_* | Format-Table Name,Value -AutoSize

# 2) Confirm what OpenClaw thinks about the managed service
openclaw gateway status --json
openclaw update --dry-run --json
```

If you see `OPENCLAW_SERVICE_MARKER` / `OPENCLAW_SERVICE_KIND` / `OPENCLAW_WINDOWS_TASK_NAME` set in this shell, clear them for the session (and check User env via System Properties if they reappear in every new window):

```powershell
Remove-Item Env:OPENCLAW_SERVICE_MARKER -ErrorAction SilentlyContinue
Remove-Item Env:OPENCLAW_SERVICE_KIND -ErrorAction SilentlyContinue
Remove-Item Env:OPENCLAW_GATEWAY_SERVICE_PID -ErrorAction SilentlyContinue
# Optional: do NOT set OPENCLAW_SUPERVISOR_MODE=external
```

**Node engines note (secondary risk after the guard):** `openclaw@2026.7.1-2` declares `node: '>=22.22.3 <23 || >=24.15.0 <25 || >=25.9.0'`. Host Node **24.14.1** is below the 24.x floor. `--dry-run` skips the mutating-path engines preflight; a real update may still fail after the service-env guard is cleared. Prefer upgrading Node to **≥24.15.0** (or a compliant 22.x/25.x) before the package swap.

### Preferred supported path (minimize downtime; updater owns stop/swap/restart)

Idempotent / retryable. Typical Telegram gap is the stop→restart window OpenClaw already orchestrates.

```powershell
# A) Backup (supported recovery point)
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\Backups\openclaw" | Out-Null
openclaw backup create --output "$env:USERPROFILE\Backups\openclaw" --verify

# B) Optional but recommended: Node floor for 2026.7.1-2
node -v   # expect >= v24.15.0 (or other engines-satisfying range)
# If needed, upgrade Node first, then reopen a clean PowerShell and re-clear markers.

# C) Update (coordinates service stop + package replace + metadata refresh + restart)
openclaw update --tag 2026.7.1-2 --yes

# D) Verify
openclaw --version
openclaw gateway status --deep --json
# expect version 2026.7.1-2 and listener healthy on 127.0.0.1:18789
curl.exe -fsS http://127.0.0.1:18789/readyz
openclaw doctor --lint --json
```

Do **not** pass `--no-restart` unless you intentionally accept a longer dual-window (old process still holding the dist tree).

### Fallback supported path (if `openclaw update` still refuses after a clean env)

Matches `docs/install/updating.md` supervised manual flow:

```powershell
# Ensure clean env (no service markers) in this shell first — see above.

openclaw gateway stop

# Confirm port free / task not running
openclaw gateway status --json
# Optional: schtasks /Query /TN "OpenClaw Gateway" /V /FO LIST

npm i -g openclaw@2026.7.1-2

openclaw gateway install --force
openclaw gateway restart

openclaw --version
curl.exe -fsS http://127.0.0.1:18789/readyz
openclaw gateway status --deep --json
```

Avoid raw `schtasks /DISABLE` + `Stop-Process` as the primary stop mechanism; use `openclaw gateway stop` so OpenClaw’s runtime view matches reality.

### Explicit rollback

```powershell
# Preferred rollback via updater (asks for downgrade confirmation; --yes skips prompts)
Remove-Item Env:OPENCLAW_SERVICE_MARKER,Env:OPENCLAW_SERVICE_KIND,Env:OPENCLAW_GATEWAY_SERVICE_PID -ErrorAction SilentlyContinue
openclaw update --tag 2026.6.11 --yes

# Or manual (docs rollback recipe)
openclaw gateway stop
npm i -g openclaw@2026.6.11
openclaw gateway install --force   # regenerates Scheduled Task / gateway.cmd from this install
openclaw gateway restart

openclaw --version
openclaw gateway status --deep --json
```

If config/state is incompatible with the older build, restore the verified backup from step A (see Backup docs); code-only rollback first.

### What not to do (already disproven or unsupported)

1. Rely on `schtasks /DISABLE` + `/End` + `Stop-Process` alone while markers remain / runtime probe is `unknown`.
2. Kill all `node.exe` expecting the env-marker guard to clear.
3. Set `OPENCLAW_SUPERVISOR_MODE=external` hoping to bypass the guard (it blocks self-update).
4. Use `--no-restart` as a bypass while the service still appears running under markers.
5. Run `openclaw update` from Telegram / agent exec (inherits gateway service env by design).

---

## Appendix: quick “is the guard armed?” one-liner

```powershell
if ($env:OPENCLAW_SERVICE_MARKER) {
  "ARMED marker=$($env:OPENCLAW_SERVICE_MARKER) kind=$($env:OPENCLAW_SERVICE_KIND)"
} else {
  "markers absent — service-env package-update guard should not fire"
}
```

---

## References

- Source (tag `v2026.6.11` / `e085fa1a3f`): `src/cli/update-cli/update-command.ts`, `src/daemon/service-env.ts`, `src/daemon/schtasks.ts`, `src/daemon/constants.ts`, `src/cli/update-cli.test.ts`
- Docs: https://docs.openclaw.ai/cli/update · https://docs.openclaw.ai/install/updating · https://docs.openclaw.ai/platforms/windows · https://docs.openclaw.ai/cli/gateway
- PRs/issues: #75729, #75819, #78494, #78492, #75691, #107288, #107339
