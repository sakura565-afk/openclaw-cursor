# OpenClaw error patterns

Companion log for `error_log.json`. Edit **Recent patterns** freely; the section between `<!-- BEGIN_AUTO_ERROR_LOG -->` and `<!-- END_AUTO_ERROR_LOG -->` is rewritten when you run `add` or `sync-md`.

## Recent patterns

- **Parser / JSON**: Truncated or invalid structured output — validate, chunk, and retry with stricter output constraints.
- **Timeouts**: Long sessions or hung tool calls — shorten prompts, add checkpoints, and enforce deadlines.
- **Network / API**: Connection drops and rate limits — exponential backoff, smaller batches, and idempotent retries.
- **Git**: Push conflicts and auth — pull/rebase first, verify remotes and tokens.

<!-- BEGIN_AUTO_ERROR_LOG -->

_Last sync: 2026-05-08T18:11:02Z_

## Automation rule candidates

*(Categories with ≥2 entries — use as cron / agent guardrails.)*

- _(No category yet has enough repeats — add more learnings.)_

## Entries (newest first)

<!-- END_AUTO_ERROR_LOG -->
