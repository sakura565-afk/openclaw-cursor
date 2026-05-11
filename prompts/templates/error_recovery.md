# Error Recovery

Use this template when an operation failed and you need to recover with full context, a clear hypothesis, and a safe retry plan.

---

## Role

You are assisting with **error recovery**. Prioritize understanding root cause, preserving user data and system state, and proposing minimal, verifiable fixes.

---

## Context

| Field | Value |
|-------|-------|
| **Environment** | `{{environment}}` (e.g. OS, runtime version, CI vs local) |
| **Component / area** | `{{component}}` |
| **What was attempted** | `{{attempted_action}}` |
| **Expected outcome** | `{{expected_outcome}}` |
| **Actual outcome** | `{{actual_outcome}}` |

### Error artifacts (paste or summarize)

```
{{error_message_or_logs}}
```

### Relevant code or config (optional)

```
{{relevant_snippet}}
```

### State before failure (optional)

- Working directory: `{{cwd}}`
- Branch / commit: `{{git_ref}}`
- Recent commands: `{{recent_commands}}`

---

## Constraints

- Do **not** suggest destructive actions (e.g. `rm -rf`, force pushes, dropping databases) unless `{{explicit_approval}}` is true and alternatives are exhausted.
- Prefer **read-only** investigation first when the failure mode is unknown.
- If secrets may appear in logs, **redact** them; use `{{redaction_note}}` if already redacted.

---

## Tasks

1. **Classify** the error (syntax, dependency, permission, network, logic, timeout, resource, etc.).
2. **Isolate** the smallest reproducible surface (command, file, line, or request).
3. **Hypothesize** 1–3 likely causes ranked by probability.
4. **Propose** concrete next steps: commands to run, files to inspect, or code edits—with expected outcomes.
5. **Define success** so we know when recovery is complete.

---

## Example (filled)

**Environment:** Node 20, local macOS  
**Component:** `npm run build` in `frontend/`  
**Attempted:** Production build after dependency bump  
**Expected:** Exit 0, `dist/` populated  
**Actual:** Rollup failed with unresolved import  

```
Error: Could not resolve "./utils/format" from src/App.tsx
```

**Hypothesis:** Path case mismatch or missing file after rename.  
**Next steps:** List `src/utils/`, verify import path vs filesystem, fix import or restore file.

---

## Output format

Respond with:

1. **Summary** — One sentence on what went wrong.
2. **Root cause (best guess)** — With confidence (low/medium/high).
3. **Recovery steps** — Numbered, each step testable.
4. **Verification** — How to confirm the fix.
5. **Prevention** — Optional: guardrail (test, lint rule, doc) to avoid recurrence.

---

## Placeholders reference

| Placeholder | Description |
|-------------|-------------|
| `{{environment}}` | Where/how the failure occurred |
| `{{component}}` | Subsystem, package, or service |
| `{{attempted_action}}` | What you tried to do |
| `{{expected_outcome}}` | What should have happened |
| `{{actual_outcome}}` | What happened instead |
| `{{error_message_or_logs}}` | Raw or summarized errors |
| `{{relevant_snippet}}` | Code, config, or stack context |
| `{{cwd}}`, `{{git_ref}}`, `{{recent_commands}}` | Optional debugging context |
| `{{explicit_approval}}` | `true` / `false` for risky operations |
| `{{redaction_note}}` | Note if logs were scrubbed |
