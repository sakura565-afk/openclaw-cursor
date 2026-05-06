## Code Review Template

### Purpose
Use this template to review code for correctness, regressions, maintainability, and test quality.

### Inputs
- Pull request / diff summary:
- Changed files:
- Intended behavior:
- Related issue/spec:

### Required Structure
1. **Review Scope**
   - What changed and what was expected to change.
2. **Findings (Ordered by Severity)**
   - Critical
   - High
   - Medium
   - Low
3. **Evidence**
   - File paths and line references for each finding.
4. **Behavioral Risk**
   - Potential regressions and impacted user flows.
5. **Test Coverage Assessment**
   - What is tested, what is not, and suggested additions.
6. **Recommendation**
   - Approve, request changes, or follow-up tasks.

### Output Format
Return markdown with these headings:
- Review Scope
- Findings
- Evidence
- Behavioral Risk
- Test Coverage Assessment
- Recommendation
