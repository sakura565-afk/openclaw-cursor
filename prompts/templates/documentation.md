---
title: Documentation authoring
category: documentation
---

## Purpose

Produce or improve documentation that matches audience needs: quickstart, reference, architecture, and operational runbooks.

## When to use

- New features need README or API docs.
- Onboarding material for a subsystem.
- Runbooks for deploy, rollback, or incident response.

## Placeholders

| Placeholder | Description |
|-------------|-------------|
| `{{AUDIENCE}}` | e.g. new contributor, end user, on-call engineer |
| `{{ARTIFACT_TYPE}}` | README section, module docstring, OpenAPI, ADR, runbook |
| `{{TOPIC}}` | What to document |
| `{{SOURCE_OF_TRUTH}}` | Code paths, tickets, or existing docs to align with |
| `{{EXAMPLES_NEEDED}}` | CLI examples, request/response samples, diagrams |
| `{{STYLE_NOTES}}` | Tone, terminology, internal wiki format |

## Usage example (filled)

**Audience:** Contributors running local Ollama tooling.  
**Artifact:** README subsection under “Ollama Model Manager”.  
**Topic:** `cleanup --days` behavior and safety notes.  
**Source:** `scripts/ollama_model_manager.py` argparse help and cleanup logic.  
**Examples:** Sample `cleanup` dry-run vs `--yes`.  
**Style:** Match existing README headings and bash code fences.

---

## Template

You are writing or revising technical documentation.

**Brief**

- Audience: {{AUDIENCE}}
- Deliverable type: {{ARTIFACT_TYPE}}
- Topic: {{TOPIC}}
- Align with: {{SOURCE_OF_TRUTH}}
- Required examples: {{EXAMPLES_NEEDED}}
- Style / constraints: {{STYLE_NOTES}}

**Instructions**

1. State the **single primary goal** of this doc (what the reader can do after reading it).
2. Outline sections appropriate to the artifact type (do not use a one-size-fits-all skeleton if inappropriate).
3. Write concise prose: short paragraphs, imperative voice for procedures, accurate terminology from the codebase.
4. Include at least one **worked example** (command, snippet, or scenario) with placeholders only where the reader must substitute real values; label required vs optional steps.
5. Add a **Troubleshooting** or **FAQ** subsection if operational failure modes are likely.
6. Cross-check claims against `{{SOURCE_OF_TRUTH}}`; flag anything uncertain rather than guessing.

**Output**

- Final documentation body ready to paste into the target location.
- Optional: a checklist of follow-up items (screenshots, i18n, links) if out of scope here.
