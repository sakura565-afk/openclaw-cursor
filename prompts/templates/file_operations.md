# Safe File Reading and Writing

Use this template when planning or executing **filesystem work**: reading, editing, creating, or deleting files with predictable paths, backups, and verification.

---

## Role

You are operating on the **file system** with care. Minimize data loss, avoid broad destructive patterns, and keep edits traceable and reversible when possible.

---

## Operation charter

| Field | Value |
|-------|-------|
| **Intent** | `{{intent}}` (read / create / update / move / delete) |
| **Root / workspace** | `{{workspace_root}}` |
| **Target paths** | `{{target_paths}}` (comma or newline list) |
| **Must preserve** | `{{preserve_rules}}` (e.g. line endings, chmod, secrets files) |

### Constraints

- Allowed roots: `{{allowed_roots}}` (only touch files under these paths).
- Forbidden patterns: `{{forbidden_globs}}` (e.g. `**/.env`, `**/node_modules/**`).
- Max scope: `{{max_files_or_size_hint}}` (agent should refuse overly broad edits).

---

## Safe patterns (reference)

### Reading

1. **Prefer** reading explicit paths; use directory listing when the path is unknown.
2. **Avoid** dumping huge binaries; use size checks or targeted reads.
3. **Search** with bounded tools (ripgrep with path/glob limits) instead of blind `find`.

### Writing

1. **Prefer** minimal diffs: change only what the task requires.
2. **Create** parent directories deliberately; do not assume they exist.
3. **Encoding:** UTF-8 text unless `{{binary_exception}}` applies.
4. **Secrets:** never write real credentials into repo files; use `{{secret_handling}}` (env, vault, placeholder).

### Deleting / moving

1. **Never** use recursive delete on vague paths (e.g. home directory, `/`).
2. **Confirm** `{{delete_confirmed}}` is `true` before destructive operations.
3. **Prefer** trash/rename-to-archive over hard delete when exploring.

---

## Example (filled)

**Intent:** Update `config/app.yaml` default timeout  
**Root:** `/workspace/myapp`  
**Targets:** `config/app.yaml`  
**Allowed roots:** `/workspace/myapp`  
**Forbidden:** `**/.env*`  
**Procedure:** Read file → single-field edit → run `python -m pytest tests/test_config.py`  

---

## Execution checklist

Use this as a literal checklist before and after operations:

- [ ] Paths normalized and under `{{allowed_roots}}`.
- [ ] No match against `{{forbidden_globs}}`.
- [ ] For deletes/moves: `{{delete_confirmed}}` is true and scope is minimal.
- [ ] After write: file parses / lints / tests as agreed (`{{verification_command}}`).
- [ ] Sensitive content redacted from logs and summaries.

---

## Output format (when reporting file work)

1. **Paths touched** — Absolute or repo-relative list.
2. **Operation per path** — read | create | update | move | delete.
3. **Verification** — Command or manual check + outcome.
4. **Residual risks** — e.g. "Large file partially read"; follow-up if any.

---

## Placeholders reference

| Placeholder | Description |
|-------------|-------------|
| `{{intent}}` | Type of filesystem work |
| `{{workspace_root}}` | Base path for resolution |
| `{{target_paths}}` | Explicit files/dirs |
| `{{preserve_rules}}` | Formatting, permissions, conventions |
| `{{allowed_roots}}` | Safety boundary |
| `{{forbidden_globs}}` | Paths that must not be touched |
| `{{max_files_or_size_hint}}` | Scope limit |
| `{{binary_exception}}` | When non-text is allowed |
| `{{secret_handling}}` | How to handle credentials |
| `{{delete_confirmed}}` | `true` / `false` gate for destructive ops |
| `{{verification_command}}` | Post-change check |
