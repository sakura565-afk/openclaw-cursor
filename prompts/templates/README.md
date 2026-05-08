# Prompt templates index

This directory holds reusable prompt templates for OpenClaw-related agents and editorial workflows. Copy a template into your agent configuration or prepend its **Template body** section to a session after replacing placeholders.

## Analysis note

The repository previously had **no** `prompts/templates/` tree; these files were introduced as a structured baseline. Related project context lives in `sample_prompts.txt` (generic examples, not agent-scoped). Implementations that pair well with these prompts include:

- Self-improvement: `src/self_improvement/`
- Coordination: `src/coordination/`
- Monitoring: `src/monitoring/`, `scripts/ollama_monitor.py`
- Batch work: `scripts/ollama_batch.py`, `scripts/queue_manager.py`

## Quick reference

| File | Use when |
|------|----------|
| [self-improvement-agent-template.md](self-improvement-agent-template.md) | Post-mortems, reflection, tightening skills/scripts from evidence |
| [batch-processing-agent-template.md](batch-processing-agent-template.md) | Many similar items: queues, manifests, parallel CLI batches |
| [monitoring-health-check-agent-template.md](monitoring-health-check-agent-template.md) | Probes, pre-flight checks, cron health, SLO-style summaries |
| [coordination-agent-template.md](coordination-agent-template.md) | Multiple bots/agents, shared memory, locks, handoffs |
| [editorial-interior-template.md](editorial-interior-template.md) | Interior / home editorial copy with consistent tone and constraints |

## How each template is structured

Every template includes:

1. **Purpose** — what problem the prompt solves  
2. **Variables / placeholders** — table of `{{PLACEHOLDER}}` values to fill  
3. **Template body** — text to give the model (after substitution)  
4. **Example usage** — one concrete filled scenario  
5. **Best practices** — operational guidance for reliable agent behavior  

## Placeholder convention

- Use double curly braces: `{{NAME}}`.  
- Replace every placeholder referenced in the template body before sending to the model.  
- Keep constraints (stdlib-only, no network, paths) explicit—they sharply reduce unsafe suggestions.

## Choosing a template

- **Incidents or quality loops** → self-improvement  
- **Throughput over many inputs** → batch processing  
- **Alarms, dashboards, cron** → monitoring / health-check  
- **Shared files and multi-actor work** → coordination  
- **Published interior / home content** → editorial interior  

## Related paths

| Path | Role |
|------|------|
| `prompts/templates/*.md` | Templates indexed here |
| `sample_prompts.txt` | Short generic prompt examples |
| `README.md` (repo root) | Project overview and CLI entrypoints |
