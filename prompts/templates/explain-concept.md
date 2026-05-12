# Template: Explain a concept

## When to use

You need to understand a technology, algorithm, API, error message, or design pattern well enough to use or modify it.

## Inputs

| Placeholder | Required | Description |
| ----------- | -------- | ----------- |
| `{{TOPIC}}` | Yes | The concept, term, stack trace line, or symbol to explain. |
| `{{CONTEXT}}` | Recommended | Where you encountered it (project type, file, library version). |
| `{{LEVEL}}` | Yes | Your background: e.g. beginner / familiar with X / expert in Y. |
| `{{DEPTH}}` | Optional | `overview`, `practical`, or `deep-dive` (default: practical). |
| `{{OUTPUT}}` | Optional | Desired shape: bullets, analogy, worked example, comparison table. |

## Prompt body

```text
Explain the following topic so I can apply it in real work—not as a generic encyclopedia entry.

## Topic
{{TOPIC}}

## Where I saw it / context
{{CONTEXT}}

## My level
{{LEVEL}}

## Depth
{{DEPTH}}

## Preferred output shape
{{OUTPUT}}

## Answer structure
1. **One-sentence intuition**—what it is for, in plain language.
2. **Core mechanics**—how it works under the hood at the depth I asked for.
3. **When to use vs avoid**—tradeoffs and common pitfalls.
4. **Worked example**—tiny code or scenario tied to {{CONTEXT}} if possible.
5. **Check your understanding**—2 quick questions I should be able to answer if I got it; give answers collapsed or at the end.
Use precise terms; if you use jargon, define it once inline.
```
