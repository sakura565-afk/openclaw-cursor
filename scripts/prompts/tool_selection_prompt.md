# Tool Selection Prompt

Choose tools (CLI, APIs, libraries, internal scripts) deliberately: match capability to task, reduce redundancy, and surface trade-offs.

---

## Variables (fill before sending)

| Placeholder | Description |
|-------------|-------------|
| `{{AGENT_NAME}}` | Acting agent identifier (optional). |
| `{{TASK_DESCRIPTION}}` | Clear statement of what needs to be done end-to-end. |
| `{{AVAILABLE_TOOLS}}` | Catalog: names, one-line purpose, constraints (rate limits, auth). |
| `{{PREFERENCES}}` | Org or user preferences (e.g. “prefer ripgrep over find”, “no network”). |
| `{{PRIOR_ATTEMPTS}}` | What was already tried and outcome (or “none”). |

---

## Instructions for the responding agent

1. **Decompose** `{{TASK_DESCRIPTION}}` into atomic steps (read, search, transform, execute, verify).
2. For each step, list **candidate tools** from `{{AVAILABLE_TOOLS}}` only; do not assume tools that were not listed unless marked as “standard library / shell builtins”.
3. **Selection criteria:** correctness first, then determinism, observability, least privilege, cost/latency, alignment with `{{PREFERENCES}}`.
4. **Pick one primary tool per step**; note alternates if the primary fails (fallback chain).
5. **Anti-patterns:** call out risky pairs (e.g. parsing XML with regex for complex schemas) and suggest safer options from the catalog.
6. Output a **minimal tool plan**: ordered steps with rationale in one sentence each.
7. If the catalog is insufficient, state **exactly** what capability is missing—do not hallucinate tool features.

---

## Output format

1. Task decomposition  
2. Tool mapping (step → primary tool → why)  
3. Fallbacks / edge cases  
4. Risks & mitigations  
5. Verification step (how to confirm success)  
6. Gaps in `{{AVAILABLE_TOOLS}}` (if any)  

---

## Example (filled placeholders)

**`{{TASK_DESCRIPTION}}`:** Find all TODO comments in Python files under `src/` and count occurrences per file.

**`{{AVAILABLE_TOOLS}}`:**
- `rg` (ripgrep): fast regex search in trees  
- `grep`: POSIX search  
- `python -c`: quick scripting  

**`{{PREFERENCES}}`:** Prefer `rg`; avoid spawning editors.

**Example response excerpt:**

- Step 1: traverse + filter `*.py` → primary `rg --files -g '*.py' src` or single invocation `rg 'TODO|FIXME' src --type py -n`.
- Fallback: if `rg` unavailable, `grep -R --include='*.py'` with note about speed.
- Verification: compare hit count to manual spot-check on one directory.

---

## Empty template (copy-paste)

```
You are {{AGENT_NAME}} selecting tools for a task.

Task:
{{TASK_DESCRIPTION}}

Available tools:
{{AVAILABLE_TOOLS}}

Preferences / constraints:
{{PREFERENCES}}

Prior attempts:
{{PRIOR_ATTEMPTS}}

Follow the Instructions and Output format in scripts/prompts/tool_selection_prompt.md.
```
