"""
Conversation analysis prompt — extracts intent, decisions, risks, and follow-ups from dialogue.

Usage notes
-----------
- ``transcript`` is required; use consistent speaker labels if known.
- ``analysis_goals`` steers extraction (e.g. action items only vs. full thematic coding).
- For long transcripts, pre-chunk externally and pass ``segment_label`` for traceability.
- Set ``redaction_note`` when PII has been masked so the model does not "un-redact".

Example::

    from prompts.templates import render_named
    print(render_named("conversation_analysis.v1", **EXAMPLE_CONTEXT))
"""

from __future__ import annotations

from typing import Any, Final

from prompts.templates._env import render_template

TEMPLATE_ID: Final = "conversation_analysis.v1"

VARIABLES: Final[dict[str, str]] = {
    "transcript": "Conversation text with optional speaker prefixes (required).",
    "analysis_goals": "What to extract: decisions, risks, sentiment, action items, etc.",
    "speaker_labels": "Legend mapping roles or ids to names if not obvious in transcript.",
    "time_bounds": "Session date range or version context for interpretation.",
    "segment_label": "Identifier when analyzing one chunk of a longer thread.",
    "redaction_note": "How placeholders like [REDACTED_EMAIL] should be treated.",
    "output_format": "e.g. 'structured_markdown' or 'json_outline' (descriptive).",
}

EXAMPLE_CONTEXT: Final[dict[str, Any]] = {
    "transcript": """Alex: We agreed to ship the dark-mode toggle behind a flag Friday.
Jordan: QA wants one more pass on contrast in settings.
Alex: OK, flag stays default-off until Jordan signs off.""",
    "analysis_goals": "Decisions, open questions, owners, deadlines, risks.",
    "speaker_labels": "Alex=PM, Jordan=Design lead",
    "time_bounds": "Planning call, 2026-05-10",
    "segment_label": "chunk-1-of-1",
    "redaction_note": "No PII in this sample.",
    "output_format": "structured_markdown",
}

PROMPT: Final[str] = """You are an expert conversation analyst. Read the transcript and produce \
an evidence-backed analysis.

{% if segment_label %}
**Segment:** {{ segment_label }}
{% endif %}
{% if time_bounds %}
**Time / context:** {{ time_bounds }}
{% endif %}
{% if speaker_labels %}
**Speakers:** {{ speaker_labels }}
{% endif %}
{% if redaction_note %}
**Redaction:** {{ redaction_note }}
{% endif %}

## Transcript
```
{{ transcript }}
```

## Analysis goals
{{ analysis_goals }}

## Instructions
Using format: **{{ output_format | default('structured_markdown') }}**

1. **Summary** (3–5 bullets) of what materially changed in the conversation.
2. **Decisions** with direct quotes or paraphrase tied to speaker and line/turn if possible.
3. **Action items** as a table or bullet list: task, owner (if known), deadline (if known), priority.
4. **Open questions / ambiguities** not resolved in the transcript.
5. **Risks or tensions** (schedule, scope, disagreement) with suggested mitigation.
6. **Sentiment / collaboration quality** in one short paragraph—avoid pop-psych labels; stay behavioural.

If the transcript is too thin for a section, say "Not enough evidence" for that section rather than guessing.
"""


def render(**variables: Any) -> str:
    return render_template(PROMPT, **variables)
