# Agent guidance

This repository includes tooling for autonomous and semi-autonomous agents. Use it to stay consistent with project conventions and operational guardrails.

## Error learning (`scripts/error_learning.py`)

When a run fails or needs a manual fix, record it so future sessions can reuse the mitigation:

```bash
python scripts/error_learning.py log <error_type> "<description>" "<fix>"
```

Optional context (paths, tool names, session identifiers):

```bash
python scripts/error_learning.py log <error_type> "<description>" "<fix>" --context "…"
```

Review stored learnings:

```bash
python scripts/error_learning.py show
python scripts/error_learning.py show --category api_error --limit 10
python scripts/error_learning.py show --unresolved
```

In code, use `log_agent_error(...)` to append entries and `retrieve_past_errors(...)` to query them (newest first). Entries are stored under `.learnings/error_log.json` by default.

### Canonical error categories

Each log line is tagged automatically from the combined text of `error_type`, `description`, `fix`, and optional `context`. If `error_type` is already one of the slugs below, that category is used as-is.

| Category | Typical signals |
| --- | --- |
| `tool_failure` | MCP/tool invocation errors, subprocess failures, bad exit codes, plugin issues |
| `context_limit` | Token or window limits, prompts too long, truncated inputs |
| `api_error` | Provider HTTP errors (5xx), rate limiting (429), upstream outages |
| `authentication_error` | 401/403, invalid API keys, OAuth/JWT/credential problems |
| `logic_error` | Wrong reasoning, flawed plan, contradictions, avoidable mistakes |
| `network_error` | Timeouts, connection refused, DNS/TLS/socket issues |
| `parsing_error` | JSON/YAML/structured output parse failures, malformed responses |
| `configuration_error` | Missing env vars, invalid options, wrong paths, misconfiguration |
| `resource_limit` | OOM, disk quota, memory pressure |
| `unknown` | No rule matched; still worth logging for human triage |

These values are stable filters for `show --category` and for programmatic retrieval.
