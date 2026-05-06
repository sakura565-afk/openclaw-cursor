---
# Memory search — retrieve and synthesize information from stored memory or notes.

title: Memory Search
purpose: >
  Guide search over episodic notes, knowledge bases, logs, or vector stores
  so answers cite what was found and what was not.

parameters:
  - name: information_need
    required: true
    description: The question or topic the user needs answered from memory.
  - name: memory_sources
    required: false
    description: Where to look (paths, collections, tags, tools available).
  - name: time_scope
    required: false
    description: Relevant date range or "all time".
    default: "all time"
  - name: exclusion_criteria
    required: false
    description: Topics or sources to ignore to reduce noise.
  - name: recall_style
    required: false
    description: "precise" (facts and quotes), "summarize", or "exploratory".
    default: precise
  - name: max_items
    required: false
    description: Soft cap on distinct memory items to surface.
    default: "10"

schema_version: 1
---

You are answering a question using **memory** and **stored knowledge** (not general training knowledge unless labeled as inference).

## Information need

{{information_need}}

## Sources to use

{{memory_sources}}

## Time scope

{{time_scope}}

## Exclude

{{exclusion_criteria}}

## Recall style

{{recall_style}} (aim for up to {{max_items}} salient items unless the need is narrower).

## Instructions

1. **Search strategy** — list the queries, tags, or access patterns you would use (or used) against the available memory tools or corpora.
2. **Findings** — for each relevant memory item:
   - **Source** (path, id, or label).
   - **Excerpt or summary** tied to the information need.
   - **Recency / confidence** if metadata exists.
3. **Synthesis** — direct answer to the information need, clearly separating:
   - **Supported by memory** (with pointers to items above).
   - **Gaps** — what was not found in scope.
4. **Inference** — only if needed: label inferred content as **not in memory** and keep it minimal.
5. If no relevant memory exists, say so and suggest **what to store next time** to improve future recall.

Do not fabricate sources. If tools return nothing, report empty results honestly.
