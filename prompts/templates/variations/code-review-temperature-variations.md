# Code Review Prompt Variations (Temperature + Instruction Style)

Use these variants with `tasks/code-review-template.md` to control creativity vs determinism for the same task type.

---

## Variation A: Deterministic / Safety-First

### Model Settings

- **temperature**: `0.1`
- **top_p**: `0.9`

### Instruction Add-On

```text
Prioritize correctness and reproducibility. Avoid speculation. Only report issues directly supported by provided evidence. If uncertain, label as "needs verification". Keep recommendations minimal and low-risk.
```

### Best For

- Release-blocking checks
- Security-sensitive changes
- Compliance-heavy repositories

---

## Variation B: Balanced Reviewer

### Model Settings

- **temperature**: `0.4`
- **top_p**: `0.95`

### Instruction Add-On

```text
Balance strict bug detection with practical developer experience. Report confirmed issues first, then suggest maintainability improvements. Include likely impact and minimal fix direction.
```

### Best For

- Day-to-day pull request reviews
- Mixed quality/velocity teams
- Refactoring with moderate risk

---

## Variation C: Exploratory / Architecture-Oriented

### Model Settings

- **temperature**: `0.7`
- **top_p**: `0.98`

### Instruction Add-On

```text
In addition to concrete defects, propose architectural alternatives and identify long-term design risks. Clearly separate "confirmed issues" from "strategic suggestions" to avoid confusion.
```

### Best For

- Early design-phase reviews
- Large system-level changes
- Platform or framework evolution

---

## AGENTS.md and SOUL.md Integration Note

When using any variation:

1. Load `AGENTS.md` for workflow, role boundaries, and tool constraints.
2. Load `SOUL.md` for voice, communication style, and persona alignment.
3. If either file is unavailable, continue and include: `Persona guide missing: <filename>`.
