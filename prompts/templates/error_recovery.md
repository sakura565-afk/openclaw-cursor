## Error Recovery Template

### Purpose
Use this template when implementation or execution fails and a recovery path is needed.

### Inputs
- Failed task / command:
- Error output (exact text):
- Context (recent changes, environment, dependencies):
- Severity (blocking, degraded, non-blocking):
- Recovery constraints (time, rollback tolerance, data safety):

### Required Structure
1. **Failure Summary**
   - Describe what failed and impact.
2. **Observed Signals**
   - Logs, stack traces, status codes, reproducibility.
3. **Likely Causes**
   - Rank top 2-4 hypotheses.
4. **Immediate Stabilization**
   - Safe actions to stop further impact.
5. **Recovery Plan**
   - Ordered steps to restore working state.
6. **Verification**
   - How to confirm recovery success.
7. **Prevention Actions**
   - Long-term fixes and monitoring updates.

### Output Format
Return markdown with these headings:
- Failure Summary
- Observed Signals
- Likely Causes
- Immediate Stabilization
- Recovery Plan
- Verification
- Prevention Actions
