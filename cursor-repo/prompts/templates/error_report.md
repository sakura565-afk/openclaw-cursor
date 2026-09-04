# Error report

Structured capture for failures, incidents, or defect reports. Fill each `{{placeholder}}`; omit optional blocks if not applicable.

---

## Metadata

| Field | Value |
|-------|-------|
| Report ID | `{{report_id}}` |
| Severity | `{{severity}}` |
| Status | `{{status}}` |
| Reporter | `{{reporter}}` |
| Detected at | `{{detected_at}}` |
| Environment | `{{environment}}` |

---

## Summary

**One-line description:** `{{short_summary}}`

**Impact:** `{{user_or_system_impact}}`

---

## Expected vs actual

**Expected behavior:** `{{expected_behavior}}`

**Actual behavior:** `{{actual_behavior}}`

---

## Reproduction

**Steps to reproduce:**

1. `{{repro_step_1}}`
2. `{{repro_step_2}}`
3. `{{repro_step_n}}`

**Reproducibility:** `{{always_intermittent_once}}`

**Minimal example / test case:**  
```text
{{minimal_example}}
```

---

## Context

**Component / service:** `{{component}}`

**Version / commit / build:** `{{version_or_commit}}`

**Related tickets / links:** `{{related_links}}`

---

## Diagnostics

**Error messages (verbatim):**  
```text
{{error_messages}}
```

**Logs (snippet):**  
```text
{{log_snippet}}
```

**Stack trace:**  
```text
{{stack_trace}}
```

---

## Scope

**Affected users / workloads:** `{{affected_scope}}`

**Blast radius:** `{{blast_radius}}`

---

## Attempted mitigation

**What was tried:** `{{mitigation_attempts}}`

**Current workaround:** `{{workaround}}`

---

## Root cause (when known)

**Hypothesis or confirmed cause:** `{{root_cause}}`

**Contributing factors:** `{{contributing_factors}}`

---

## Follow-up

**Owner:** `{{owner}}`

**Next actions:** `{{next_actions}}`

**Attachments / artifacts:** `{{attachments}}`

---

## Dynamic notes

`{{additional_notes}}`
