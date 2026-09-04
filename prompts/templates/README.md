# Agent prompt template library

Reusable prompt skeletons for common software engineering tasks. Copy a template, fill the bracketed placeholders, and attach code, logs, or links as needed.

Each template uses the same core sections:

| Section | Purpose |
|--------|---------|
| **Context** | What the agent must know (repo area, stack, prior decisions, links). |
| **Task description** | What to do, in outcome-oriented language. |
| **Constraints** | Non-negotiables (style, scope, security, performance, tooling). |
| **Expected output format** | How to structure the reply so results are scannable and actionable. |

---

## 1. Code review

```text
## Context
- Repository / service: [name]
- Change under review: [PR link, branch, or paste diff summary]
- Runtime / language versions: [e.g. Node 22, Python 3.12]
- Risk profile: [user-facing / payments / auth / data migration / none]
- Reviewer goal: [ship confidence / teach patterns / find blockers only]

## Task description
Review the change for correctness, security, maintainability, and test coverage.
Call out must-fix issues vs nice-to-haves. Suggest concrete fixes (patches or pseudocode) where helpful.
Assume the author will apply your feedback; avoid generic advice unrelated to this diff.

## Constraints
- Do not rewrite the whole change unless necessary; prefer minimal diffs.
- Respect existing project conventions (lint rules, architecture, naming).
- Flag breaking API or schema changes explicitly.
- If something is unclear from the diff, state assumptions and still give best-effort guidance.

## Expected output format
1. **Summary** — 2–4 bullets: what the change does and overall risk.
2. **Must fix** — numbered list; each item: location (file/function), problem, suggested fix.
3. **Should fix / suggestions** — shorter list; optional improvements.
4. **Questions / assumptions** — bullets for anything that blocked certainty.
5. **Test gaps** — what to add or run (commands or case names).
```

---

## 2. Bug investigation

```text
## Context
- Symptom: [what users or systems observe]
- Expected behavior: [reference spec, ticket, or plain description]
- Environment: [prod/staging/local; OS; region; feature flags]
- Timeline: [when it started; recent deploys or config changes]
- Artifacts: [logs, traces, screenshots, request IDs, sample payloads — attach or summarize]

## Task description
Form hypotheses, narrow root cause, and propose a verification path before coding.
If code is available, point to likely failure points and data/control flow.
Distinguish confirmed facts from guesses.

## Constraints
- Do not propose fixes until root cause is justified (or clearly mark speculative fixes as such).
- Prefer the smallest reproduction or observability step that reduces uncertainty.
- Consider concurrency, caching, clocks, I/O, and partial failure modes where relevant.

## Expected output format
1. **Problem statement** — one tight paragraph restating the bug.
2. **Facts collected** — bullets (only verified information).
3. **Hypotheses** — table or list: hypothesis | evidence for | evidence against | next check.
4. **Most likely root cause** — explanation tied to code paths or infra.
5. **Reproduction / verification plan** — ordered steps, including log points or queries.
6. **Fix options** — if appropriate: option A/B with tradeoffs and recommendation.
```

---

## 3. Feature design

```text
## Context
- Product / initiative: [name]
- Users and primary jobs-to-be-done: [who; what they need]
- Current system: [relevant services, data stores, integrations]
- Success metrics: [latency, adoption, error rate, revenue, support volume]
- Hard deadlines or compliance: [if any]

## Task description
Design a feature that meets the goal without over-scoping.
Cover API/UI behavior, data model impacts, rollout, and observability.
Highlight tradeoffs and non-goals explicitly.

## Constraints
- Align with existing architecture; avoid new dependencies unless justified.
- Security and privacy: [auth model, PII, retention, audit needs]
- Performance and scale: [rough QPS, data size, latency budget]
- Backward compatibility: [migrations, API versioning]

## Expected output format
1. **Goal and non-goals** — bullets.
2. **User-visible behavior** — scenarios or flow (happy path + key edge cases).
3. **Architecture sketch** — components and responsibilities (diagram in text is fine).
4. **Data model / API** — fields, endpoints, events; migration notes if any.
5. **Rollout plan** — feature flags, phased rollout, rollback.
6. **Risks and open questions** — ranked list with mitigations or decisions needed.
7. **Milestone breakdown** — ordered work items sized for incremental delivery (no calendar estimates required).
```

---

## 4. Refactoring plan

```text
## Context
- Area to refactor: [module, package, bounded context]
- Pain signals: [bugs, velocity, complexity metrics, onboarding cost]
- Coupling hotspots: [files, cycles, shared mutable state]
- Tests today: [coverage notes; critical paths; flaky tests]

## Task description
Produce a safe refactoring sequence that preserves behavior.
Identify seams (interfaces, adapters) and incremental steps.
Call out behavior-preserving checkpoints (tests or runtime checks).

## Constraints
- No behavior change unless listed as an explicit sub-task.
- Minimize blast radius; prefer strangler patterns and feature flags only if already used.
- Match team workflow: [monorepo tools, release cadence, review norms]

## Expected output format
1. **Current state** — short diagnosis with 2–3 concrete examples of debt.
2. **Target state** — desired boundaries and responsibilities.
3. **Migration map** — old → new mapping (types, modules, or routes).
4. **Step-by-step plan** — ordered steps; each step: scope, commands if any, verification.
5. **Risk register** — risk | likelihood | impact | mitigation.
6. **Definition of done** — checklist including tests and observability updates.
```

---

## 5. Test writing

```text
## Context
- Code under test: [path(s); entry points; public API]
- Frameworks: [e.g. pytest, Jest, Go test, Playwright]
- Existing patterns: [fixtures, builders, snapshot usage, integration vs unit split]
- Flakiness history: [known timing or env issues]

## Task description
Add or extend tests to cover specified behavior and guard regressions.
Prefer fast, deterministic tests; use integration tests only where justified.
Name tests so failures read like specifications.

## Constraints
- Do not weaken assertions to make tests pass.
- Avoid testing implementation details unless necessary for safety-critical code.
- External I/O must be mocked/faked or run in hermetic sandboxes per project norms.
- Coverage target: [lines/branches or explicit scenarios to cover]

## Expected output format
1. **Test plan** — bullet list of scenarios (happy, edge, failure).
2. **New/updated tests** — file paths and brief description per file.
3. **Code** — complete test code blocks ready to paste (or diff-style if preferred by repo).
4. **How to run** — exact commands for local and CI if different.
5. **Gaps** — what remains untested and why (acceptable debt or follow-ups).
```

---

## 6. README creation

```text
## Context
- Project name and one-line purpose: [ ]
- Audience: [end users / contributors / operators]
- Install & runtime requirements: [OS, language versions, secrets, services]
- Entry points: [CLI commands, main URL, package name]
- License and governance: [if applicable]

## Task description
Write or rewrite README so a newcomer can install, run, and contribute with minimal friction.
Include troubleshooting for the top 2–3 likely failures.
Keep marketing tone light; optimize for clarity and accuracy.

## Constraints
- Follow repository doc conventions: [e.g. keep under docs/, ADR links, style guide]
- Do not document secrets; use placeholders and point to secret manager / `.env.example`.
- Link to deeper docs instead of duplicating architecture essays.

## Expected output format
1. **Suggested README sections** — outline tailored to this repo.
2. **Full README.md draft** — Markdown ready to commit.
3. **Optional files** — `.env.example`, CONTRIBUTING pointers, or Makefile targets if missing and needed.
4. **Review checklist** — accuracy, commands verified mentally against project layout, next doc steps.
```

---

## 7. Error debugging workflow

```text
## Context
- Command or URL that failed: [ ]
- Full error output (verbatim): [paste]
- Exit code / HTTP status: [ ]
- Recent changes: [git range, deps, infra]
- Where it runs: [CI job name, container image, local shell]

## Task description
Turn the error into a resolved outcome: diagnose cause, apply fix, and prevent recurrence.
Stay close to the message and stack trace first; broaden only when evidence supports it.

## Constraints
- Do not suggest destructive commands (reset DB, mass delete) without explicit confirmation context; here prefer safe diagnostics.
- Prefer official docs or repo sources over guesswork when versions matter.
- If multiple issues are stacked, peel from the innermost/first failure outward.

## Expected output format
1. **Error parse** — translate jargon; identify failing subsystem (build, test, network, permissions, etc.).
2. **Immediate checks** — ordered list (commands or UI steps) with expected good/bad signals.
3. **Root cause** — single paragraph with evidence citations (log lines, file paths).
4. **Fix** — exact steps or patch; note side effects.
5. **Prevention** — docs, CI guard, lint rule, or test to add so this fails earlier next time.
6. **If still blocked** — minimal extra information to request from the environment.
```

---

## Usage tips

- **Combine templates**: e.g. run **Bug investigation** first, then **Test writing** once the cause is fixed.
- **Attach artifacts** after the template: logs, metrics, `git diff`, or failing CI links.
- **Tighten constraints** for agents: language, max length, and “no unrelated refactors” reduce drift.
- **Version outputs**: ask for “machine-readable JSON” or “markdown only” in **Expected output format** when tooling consumes the reply.
