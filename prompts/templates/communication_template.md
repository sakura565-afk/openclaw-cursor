# Communication Template

## Purpose

Use this template to draft clear user-facing messages that explain status, decisions, blockers, or results without unnecessary ambiguity.

## Recommended placeholders

- `{{context}}`: Relevant conversation history or technical background
- `{{task}}`: What the agent is responding to
- `{{goal}}`: The outcome the message should help achieve
- `{{audience}}`: User, reviewer, collaborator, or operator
- `{{tone}}`: Concise, calm, direct, friendly, or formal
- `{{key_points}}`: Facts, decisions, constraints, or updates to include
- `{{requested_action}}`: What the recipient should do next, if anything

## Template

```md
You are writing a user-facing OpenClaw message.

Context:
{{context}}

Task:
{{task}}

Goal:
{{goal}}

Audience:
{{audience}}

Tone:
{{tone}}

Key points:
{{key_points}}

Requested action:
{{requested_action}}

Instructions:
1. Write in a way that is easy to scan and hard to misinterpret.
2. Lead with the most important outcome or update.
3. Include only the context needed for the recipient to act or stay informed.
4. Be transparent about uncertainty, blockers, and trade-offs.
5. When asking for action, make the ask explicit and specific.
6. Avoid unnecessary jargon unless the audience expects it.

Expected output format:
- Message objective:
- Final message:
  [ready-to-send text]
- Optional follow-up bullets:
  - ...
```
