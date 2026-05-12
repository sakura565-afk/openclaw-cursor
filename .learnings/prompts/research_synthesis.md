# Research synthesis ({{topic}})

## Description

Turn scattered sources into a concise brief: claims, confidence, gaps, and recommended next actions. Use for literature review, technology evaluation, or competitive landscape scans.

## Variables

Substitutions use mustache-style placeholders in the **task block** below (e.g. `{{` `topic` `}}`).

| Variable | Role |
|----------|------|
| `{{` `topic` `}}` | Question or decision to answer. |
| `{{` `audience` `}}` | Who will read this (engineers, execs, mixed) and their assumed background. |
| `{{` `sources` `}}` | Links, PDFs, internal docs, datasets, or “use reputable public sources as of today”. |
| `{{` `deliverable_shape` `}}` | Desired output (memo, table, ADR sections, talk outline). |
| `{{` `time_horizon` `}}` | How current the answer must be (e.g. last 12 months, include preprints or not). |

## Instruction structure

1. **Scope restatement**: One paragraph reframing the topic as a decision or knowledge gap.
2. **Key findings**: 5–10 bullets, each with a short claim and why it matters to the intended audience.
3. **Evidence map**: Group findings by mechanism, vendor, or paper; note strength of evidence (high/medium/low).
4. **Conflicts & uncertainties**: Where sources disagree; what would resolve the debate.
5. **Gaps**: What was not found or not trustworthy; what to search next.
6. **Recommendations**: Ordered actions with rationale and “if false, then” branches.
7. **References**: Normalized list mapping back to the sources you were told to prioritize.

## Examples

**Illustrative:** topic = “Should we adopt Rust for our CLI tools?”, audience = “platform team + PM”, sources = “internal ADRs + 2024 surveys + three OSS CLIs”, deliverable_shape = “1-page memo + comparison table”, time_horizon = “prefer 2023–2026 primary sources”.

## Tips for best results

- If sources are open-ended, require the model to separate verified citations from general background.
- Put required sections or length caps in the deliverable shape field.
- Name sensitivity for the audience (e.g. “no hype, quantify costs”) to control tone.

---

You are a research analyst producing a synthesis for **{{audience}}**.

**Topic / decision:** {{topic}}

**Sources to prioritize:** {{sources}}

**Deliverable shape:** {{deliverable_shape}}

**Recency / horizon:** {{time_horizon}}

Follow the instruction structure above. Label uncertain claims. Do not invent citations; if a fact cannot be sourced from the prioritized sources, say it is unknown and suggest how to verify.
