---
title: Tool Discovery & Documentation
purpose: Standardize how new or changed tools are captured for agent reuse and verification
version: "1.0"
workflow: self-improving-agent
tags: [tools, mcp, cli, api, registry]
when_to_use: When encountering a new command, API, MCP server, script, or internal helper worth reusing
outputs_to: "[[downstream_artifact]]"
outputs_hint: "e.g. team_tooling_index, skill_snippet, onboarding"
---

# Tool Discovery Sheet

Document tools so **future-you** (or another agent) can invoke them safely without rediscovery cost.

## Identity

| Field | Value |
| --- | --- |
| **Tool name** | [[tool_name]] |
| **Kind** | [[tool_kind]] <!-- cli / library / mcp / http / ui / internal_script -->
| **Version / commit** | [[version_or_commit]] |
| **Source / install** | [[install_or_location]] |
| **Access / auth** | [[auth_requirements]] |

## Purpose & fit

- **Problem it solves:** [[problem_solved]]
- **When to use (green lights):** [[when_to_use]]
- **When not to use (red lights):** [[when_not_to_use]]
- **Alternatives considered:** [[alternatives]]

## Interface

- **Entry command or symbol:** [[primary_invocation]]
- **Key flags / options:** [[important_flags]]
- **Input shape:** [[input_schema_or_example]]
- **Output shape:** [[output_schema_or_example]]
- **Rate limits / quotas:** [[limits]]
- **Side effects:** [[side_effects]] <!-- writes, network, billing -->

## Examples

### Minimal success path

```[[language_or_shell]]
[[minimal_working_example]]
```

### Failure / edge case

```[[language_or_shell]]
[[edge_case_example]]
```

## Reliability & verification

- **How to verify it worked:** [[success_criteria]]
- **Known flaky behavior:** [[flakiness_notes]]
- **Observed latency / cost:** [[latency_or_cost_notes]]

## Integration for agents

- **Suggested skill hook:** [[skill_hook]] <!-- e.g. “run before deploy” checklist -->
- **Error strings to grep for:** [[error_signatures]]
- **Safe defaults:** [[safe_defaults]]

## Maintenance

- **Owner / team:** [[owner]]
- **Last validated:** [[last_validated_date]]
- **Deprecation risk:** [[deprecation_notes]]

---

## Usage example

```markdown
## Identity
| **Tool name** | `rq` (ripgrep) |
| **Kind** | cli |
| **Source** | `brew install ripgrep` / ships in CI image |

## Purpose & fit
- **Problem:** Fast codebase search with respect to `.gitignore`.
- **Green lights:** Finding symbol definitions, tracing error strings.
- **Red lights:** Semantic “why” questions without reading code.

## Interface
- **Entry:** `rg "pattern" path`
- **Key flags:** `-n` line numbers, `-l` files with matches, `--glob '*.ts'`

## Reliability
- **Success criteria:** Exit 0 with match lines printed; exit 1 means no matches (not an error).

## Integration for agents
- **Error signatures:** `regex parse error` → fix pattern quoting.
```
