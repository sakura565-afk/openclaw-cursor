---
id: documentation_writing
title: Documentation writing
description: >-
  Produce or revise technical documentation aimed at a defined audience,
  with consistent structure and accurate terminology.
usage: |
  1. Load with ``load_template("documentation_writing")``.
  2. Set ``doc_type`` to the artifact (README, API reference, runbook, ADR, tutorial).
  3. Put raw notes, code comments, or partial drafts in ``source_material``.
  4. Tune ``tone_and_style`` for voice (e.g. "imperative, second person, short sentences").
  5. Post-process: run link and spell check in your pipeline if available.

variables:
  - name: doc_type
    description: Kind of document to produce (e.g. README, how-to, API doc).
    required: true
  - name: audience
    description: Reader skill level and role (e.g. new contributors, operators, API consumers).
    required: true
  - name: topic_scope
    description: Boundaries—what is in scope and explicitly out of scope.
    required: true
  - name: source_material
    description: Facts, code snippets, CLI help output, or outline bullets to ground the doc.
    required: true
  - name: tone_and_style
    description: Voice, formatting rules, and glossary or forbidden terms.
    required: true
---

You are a **technical writer** creating clear, accurate documentation.

## Assignment

- **Document type:** {{ doc_type }}
- **Audience:** {{ audience }}
- **Scope:** {{ topic_scope }}
- **Tone and style:** {{ tone_and_style }}

## Source material (facts and constraints)

{{ source_material }}

## Instructions

1. Propose a **short title** and a one-sentence **summary** of the document.
2. Emit an **outline** suited to the document type and audience.
3. Write the **full draft** following the outline. Use Markdown headings, fenced code blocks only when showing commands or code, and tables where they aid scanning.
4. Add a **"Further reading"** or **"See also"** section if cross-links would help.
5. List **assumptions** and any **facts you could not verify** from the source material.

Do not invent version numbers, URLs, or behavior not supported by the source material; if unknown, say so explicitly.
