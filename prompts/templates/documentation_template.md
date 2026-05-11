# Documentation writing template

Use this template when asking an agent or author to **produce or revise documentation**: README sections, internal runbooks, API guides, or contributor docs.

---

## How to use

1. Pick the **document type** and **audience** first; tone and depth follow from that.
2. State **single main goal**: what the reader should be able to do after reading.
3. Provide **source of truth**: code paths, OpenAPI, tickets, or SMEs to align with.
4. Fill **outline** or attach an existing doc to **revise**.
5. Define **done** with concrete checks (links work, commands run, glossary complete).

---

## Task prompt (copy from here)

### Document metadata

- **Title:** `DOCUMENT_TITLE`
- **Type:** `README` | `RUNBOOK` | `API_REFERENCE` | `ARCHITECTURE` | `CONTRIBUTING` | `USER_GUIDE` | `OTHER`
- **Audience:** `READER_ROLE_AND_ASSUMED_BACKGROUND`
- **Location in repo or site:** `PATH_OR_URL_WHERE_IT_WILL_LIVE`
- **Related work:** `ISSUE_OR_EPIC_LINK`

### Goal

After reading this document, the reader can: `ONE_CLEAR_OUTCOME`

*Example: Deploy the worker to staging using Helm values only; no access to production secrets required.*

### Source of truth

Align content with:

- **Code / config paths:** `PATHS`
- **API spec or schema:** `LINK_OR_FILE`
- **Subject matter experts:** `NAMES_OR_CHANNELS` *(if applicable)*

### Scope

**In scope:** `TOPICS_TO_COVER`

**Out of scope:** `WHAT_TO_EXCLUDE`

*Example:*

- **In:** Install, local dev, common troubleshooting, link to full API reference.
- **Out:** Historical design decisions (link to ADR-004 instead).

### Required sections

Check what applies; unchecked sections may be omitted.

- [ ] Overview / purpose
- [ ] Prerequisites and versions
- [ ] Quick start (minimal path to success)
- [ ] Configuration reference (env vars, flags)
- [ ] Operations (deploy, rollback, scaling)
- [ ] Security notes (auth, secrets handling)
- [ ] Troubleshooting / FAQ
- [ ] Changelog pointer or “last reviewed” date

### Outline (optional seed)

1. `SECTION_1_HEADING` — `BULLET_POINTS_OF_CONTENT`
2. `SECTION_2_HEADING` — `...`
3. `SECTION_3_HEADING` — `...`

### Examples and assets to include

- **Commands:** `COPY_PASTE_COMMANDS_WITH_PLACEHOLDERS`
- **Diagrams:** `MERMAID_OR_ASSET_PATHS`
- **Screenshots:** `WHICH_SCREENS_IF_UI`

*Example commands block:*

```bash
cp .env.example .env
# Edit .env: set DATABASE_URL
npm install
npm run dev
```

### Style and conventions

- **Voice:** `SECOND_PERSON_OR_NEUTRAL`
- **Terminology:** `PRODUCT_TERMS_GLOSSARY_OR_LINK`
- **Code blocks:** specify language tags; mark output with comments if mixed.
- **Links:** prefer stable anchors; no broken relative links.

### Acceptance criteria

- [ ] `CRITERION_1` *(example: Every `kubectl` example was executed successfully in staging.)*
- [ ] `CRITERION_2` *(example: All internal wiki links return 200.)*
- [ ] `CRITERION_3` *(example: “Last reviewed” footer set to `YYYY-MM-DD`.)*

---

## Instructions for the documentation agent

1. **Inventory** existing docs on the same topic; extend or deduplicate rather than fork silently.
2. **Validate** commands and paths against the current repo state; fix drift you find.
3. **Write** for scanning: short paragraphs, meaningful headings, tables for reference material.
4. **Link** to deeper material instead of copying large APIs; keep the doc maintainable.
5. **End** with troubleshooting for the top two failure modes you anticipate.

---

## Example (filled snippet)

**Title:** Batch inference worker — operations runbook  
**Audience:** On-call engineers familiar with Kubernetes but not with this service  
**Goal:** Restart failed workers safely and interpret common alerts  
**In scope:** Pod layout, env vars, logs, restart procedure, escalation  
**Out of scope:** Model training; GPU driver installation  

**Acceptance:** Restart procedure tested in dev cluster; alert “QueueDepthHigh” mapped to actions; “Last reviewed: 2026-05-11” in footer.
