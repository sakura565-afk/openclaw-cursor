## Memory Sync Template

### Purpose
Use this template to consolidate and synchronize durable memory/context across sessions, agents, or handoffs.

### Inputs
- Source context (notes, logs, transcripts):
- Current memory state:
- New facts/decisions to merge:
- Conflicts or ambiguities:
- Retention policy (what to keep/drop):

### Required Structure
1. **Sync Objective**
   - Define what memory should be updated and why.
2. **New Information**
   - List candidate facts, grouped by topic.
3. **Conflict Resolution**
   - Identify conflicting records and chosen resolution.
4. **Canonical Memory Update**
   - Provide the finalized memory entries.
5. **Dropped/Expired Items**
   - Specify removed items and rationale.
6. **Follow-up Actions**
   - Note any actions needed to verify uncertain memories.

### Output Format
Return markdown with these headings:
- Sync Objective
- New Information
- Conflict Resolution
- Canonical Memory Update
- Dropped/Expired Items
- Follow-up Actions
