---
id: code_review
title: Code review
description: >-
  Structured review of a change or file for correctness, safety, maintainability,
  and alignment with project conventions.
usage: |
  1. Load with ``load_template("code_review")``.
  2. Fill every ``{{ placeholder }}`` in the body (see ``variables``). Use concise,
     factual inputs; paste or summarize diffs rather than whole repositories when possible.
  3. Pass the rendered string to your model or agent as the user or system message.
  4. For large changes, split by module or commit and run multiple passes, then merge findings.

  Python example::

      from prompts import load_template
      t = load_template("code_review")
      prompt = t.render(
          language_stack="Python 3.12",
          change_summary="Add retry logic to HTTP client",
          code_or_diff="```diff\n...",
          focus_areas="error handling, tests, API stability",
          project_conventions="Use pathlib; prefer dataclasses for DTOs",
      )

variables:
  - name: language_stack
    description: Languages, runtime versions, and key frameworks.
    required: true
  - name: change_summary
    description: One short paragraph on intent and scope of the change.
    required: true
  - name: code_or_diff
    description: Relevant code excerpt, unified diff, or file paths with snippets.
    required: true
  - name: focus_areas
    description: What the reviewer should emphasize (e.g. security, performance, UX).
    required: true
  - name: project_conventions
    description: Link or bullet list of naming, architecture, or style rules to enforce.
    required: true
---

You are an experienced software engineer performing a **code review**.

## Context

- **Stack:** {{ language_stack }}
- **Change summary:** {{ change_summary }}
- **Conventions / constraints:** {{ project_conventions }}
- **Focus areas:** {{ focus_areas }}

## Code or diff to review

{{ code_or_diff }}

## Instructions

1. Summarize what the change appears to do in one or two sentences.
2. List **blocking issues** (correctness bugs, security holes, data loss risks) with severity and a concrete fix suggestion.
3. List **non-blocking improvements** (clarity, tests, docs, small refactors).
4. Note anything **unclear** that should be clarified with the author.
5. If tests are missing for new behavior, say what cases should be covered.
6. Keep feedback specific and actionable; avoid generic praise or nitpicks without payoff.

Use clear headings and bullet points in your response.
