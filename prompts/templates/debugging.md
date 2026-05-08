---
title: Systematic debugging
category: debugging
---

## Purpose

Reduce guesswork by enforcing reproduce → hypothesize → isolate → fix → verify, with explicit evidence at each step.

## When to use

- Intermittent failures, flaky tests, or production incidents with logs.
- “Works on my machine” or environment-specific bugs.
- Regressions after a refactor or dependency upgrade.

## Placeholders

| Placeholder | Description |
|-------------|-------------|
| `{{SYMPTOM}}` | What users or tests observe (actual vs expected) |
| `{{ENVIRONMENT}}` | OS, runtime versions, config flags, deployment target |
| `{{REPRO_STEPS}}` | Minimal steps or a failing command |
| `{{LOGS_OR_TRACES}}` | Relevant excerpts (sanitize secrets) |
| `{{RECENT_CHANGES}}` | Git range, deploys, or config changes |
| `{{CODE_POINTER}}` | Suspected files, modules, or stack traces |

## Usage example (filled)

**Symptom:** `ollama_model_manager pull` exits 0 but model not listed afterward.  
**Environment:** Linux, Python 3.12, Ollama 0.5.x.  
**Repro:** `python -m scripts.ollama_model_manager pull tinyllama` twice in a row.  
**Logs:** Second run shows “already exists” from subprocess but `list` empty.  
**Recent changes:** None on list parsing.  
**Code:** `scripts/ollama_model_manager.py` — `run_ollama` and `cmd_list`.

---

## Template

You are debugging a software issue using a systematic method.

**Observed problem**

- Symptom: {{SYMPTOM}}
- Environment: {{ENVIRONMENT}}
- Minimal reproduction: {{REPRO_STEPS}}
- Evidence (logs, traces, screenshots described): {{LOGS_OR_TRACES}}
- Recent changes: {{RECENT_CHANGES}}
- Starting hypothesis area: {{CODE_POINTER}}

**Process (follow in order)**

1. Restate the bug as a falsifiable claim (one sentence).
2. List 3–5 hypotheses ranked by likelihood. For each, say what evidence would confirm or rule it out.
3. Propose the **smallest** next experiment (command, log line, assert, or bisect) to discriminate hypotheses.
4. After reasoning from available evidence, identify the most probable root cause and explain the causal chain.
5. Propose a fix with minimal blast radius; note alternatives and trade-offs.
6. Define **verification**: exact commands or checks proving the fix; add a regression test idea.

**Output constraints**

- Separate facts (from logs/code) from guesses; label guesses clearly.
- If information is missing, list precise questions or artifacts needed — do not invent details.
