# Concise summary

Use this to compress a long thread, document, or codebase explanation into a short, scannable brief. Fill the inputs, then send.

---

## Source material

[Paste the long conversation, doc excerpt, design notes, or describe what to summarize—e.g. "summarize files X and Y from repo Z" if the assistant has access.]

## Audience and purpose

- **Who will read this:** [e.g. tech lead, new teammate, stakeholder]
- **Why they need it:** [Decision, onboarding, status update, handoff]

## Output preferences

- **Target length:** [e.g. ~5 bullets, one paragraph, half a page]
- **Tone:** [Neutral technical / executive / casual]
- **Must include:** [Decisions, open questions, risks, owners—whatever cannot be dropped]
- **Must omit:** [Implementation trivia, repeated debate, PII—if applicable]

## Format

[Choose: bullet list / numbered sections / single narrative / table of topics vs. conclusions]

---

**Instructions for the assistant:** Produce a **concise** summary that:

1. Leads with the **main takeaway** or current state in one or two sentences.
2. Covers **decisions made**, **open questions**, and **next actions** (with owners if named in the source).
3. Uses the requested format and length; uses **bold labels** only if it improves scanability—avoid fluff.
4. If the source is ambiguous or incomplete, say what is **uncertain** in one line rather than inventing detail.

Do not reproduce large verbatim quotes unless the user asked for citations.
