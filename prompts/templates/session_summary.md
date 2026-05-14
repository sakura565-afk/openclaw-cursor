# Session activity summary

**Purpose:** Turn raw session data (commits, tool calls, decisions, blockers) into a concise handoff suitable for humans or the next agent turn—without losing critical context.

**When to use:** End of a cloud agent run, shift handoff, incident bridge notes, or periodic status when many small steps occurred.

---

## Variables to fill

| Variable | Description |
|----------|-------------|
| `{{agent_role}}` | Who performed the work (agent persona or team). |
| `{{repository_context}}` | Repo, branch, issue/PR links, environment. |
| `{{session_goal}}` | Original objective for the session. |
| `{{activity_log}}` | Chronological notes, command list, or transcript excerpt. |
| `{{artifacts_produced}}` | PRs, files, docs, tickets, or `none`. |
| `{{decisions_made}}` | ADRs, chosen libraries, tradeoffs, or `none`. |
| `{{open_issues}}` | Blockers, risks, follow-ups, or `none`. |
| `{{audience}}` | Who reads this (teammate, on-call, future you, next agent). |
| `{{output_contract}}` | Length and format constraints. |

---

## Prompt body (render after filling variables)

You are **{{agent_role}}** preparing a summary for **{{audience}}** in **{{repository_context}}**.

### Session framing

- **Stated goal:** {{session_goal}}

### Raw activity (source of truth)

{{activity_log}}

### Outcomes

- **Artifacts produced:** {{artifacts_produced}}
- **Decisions made:** {{decisions_made}}
- **Open issues / risks:** {{open_issues}}

### Instructions

1. Lead with **what changed** relative to **{{session_goal}}** (done / partial / not started).
2. Summarize **work completed** as bullet points; each bullet must be checkable (link, path, or command).
3. Capture **decisions** in one line each: choice + reason + consequence.
4. List **blockers** with owner suggestion if unknown.
5. Add **recommended next actions** (max 5), ordered by impact; mark which are optional.
6. Omit blow-by-blow tool chatter; keep facts that affect the next operator.

### Output

{{output_contract}}

---

## Example (filled)

**agent_role:** Cursor Cloud Agent (coding).

**repository_context:** `acme/mobile`, branch `feat/push-notifications`, issue #902.

**session_goal:** Implement FCM token refresh and add unit tests for token store.

**activity_log:** (Bullets: implemented `TokenStore.refresh`, added `TokenStoreTest`, fixed lint on `AppDelegate`, CI green on fork.)

**artifacts_produced:** Commit `a1b2c3d`, draft PR link, no release notes yet.

**decisions_made:** Chose in-memory cache for dev builds only; production still uses Keychain (documented in code comment).

**open_issues:** Need product sign-off on permission copy; Android parity not in scope this session.

**audience:** Teammate picking up tomorrow.

**output_contract:** At most 25 lines Markdown: Goal status, Shipped, Decisions, Risks, Next actions (numbered), Links.

*(Rendered prompt = body above with variables substituted.)*
