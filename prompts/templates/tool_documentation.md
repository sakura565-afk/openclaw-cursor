# New tool documentation

**Purpose:** Produce clear, accurate documentation for a newly introduced tool (CLI, MCP server, script, API, or agent capability) so operators and other agents can use it safely.

**When to use:** When adding or exposing a tool, command, or integration that others will invoke from prompts, CI, or local workflows.

---

## Variables to fill

| Variable | Description |
|----------|-------------|
| `{{agent_role}}` | Writer role (e.g. “technical writer + SRE”). |
| `{{repository_context}}` | Where the tool lives and how it is distributed. |
| `{{tool_name}}` | Stable name as users see it (command, MCP id, or endpoint). |
| `{{tool_purpose}}` | One paragraph: problem it solves and non-goals. |
| `{{interface_spec}}` | Signature: flags, JSON schema, env vars, auth, rate limits. |
| `{{behavior_notes}}` | Side effects, idempotency, destructive modes, data residency. |
| `{{examples}}` | One minimal happy path; one edge or failure example if applicable. |
| `{{related_links}}` | Source files, design doc, or `none`. |
| `{{output_contract}}` | Doc format (README section, man page style, internal wiki Markdown). |

---

## Prompt body (render after filling variables)

You are **{{agent_role}}** documenting **{{tool_name}}** in **{{repository_context}}**.

### Tool overview

**Purpose:** {{tool_purpose}}

### Interface

{{interface_spec}}

### Behavior and safety

{{behavior_notes}}

### Examples (ground truth for doc)

{{examples}}

### References

{{related_links}}

### Instructions

1. Write for **scanability**: start with “what it does” and “when not to use it”.
2. Document **inputs and outputs** precisely; if schema exists, mirror it (tables welcome).
3. Cover **auth**, **permissions**, **side effects**, and **exit codes** or HTTP status mapping if relevant.
4. Include **troubleshooting**: top 3 failure modes tied to observable signals.
5. Add **versioning** note if behavior may change (flag to lock behavior, config version, etc.).
6. Avoid marketing language; prefer imperative steps and neutral tone.

### Output

{{output_contract}}

---

## Example (filled)

**agent_role:** Platform engineer documenting an internal CLI.

**repository_context:** `acme/devtools`, binary `acme-doctor` shipped with devcontainer image `2026.05.1`.

**tool_name:** `acme-doctor`

**tool_purpose:** Validates local kube context, docker daemon, and required env vars before running integration tests; does not modify cluster state.

**interface_spec:** `acme-doctor [check|fix] [--json]`. Subcommand `fix` only writes `~/.acme/local.json` when all checks pass. Env: `ACME_CLUSTER` required. Exit `0` ok, `1` user-fixable, `2` internal error.

**behavior_notes:** Read-only against cluster except `fix` touching local file. No network except optional `GET /health` on configured URL.

**examples:** `acme-doctor check` → table output; `acme-doctor check --json` → machine-readable.

**related_links:** `tools/doctor/README.md` (source), runbook `docs/onboarding.md#doctor`.

**output_contract:** Markdown suitable for pasting under `## acme-doctor` in the monorepo README: Overview, Requirements, Usage, Exit codes, Troubleshooting, Changelog pointer.

*(Rendered prompt = body above with variables substituted.)*
