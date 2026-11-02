# Repository hygiene

`scripts/repo_hygiene.py` reviews open GitHub pull requests and remote branches
against `main`. It can close PRs whose head branch is already merged and, with a
separate opt-in, delete fully merged remote branches.

The tool uses the GitHub REST API and Python's standard library only. It reads a
token from a file; the token is sent in the `Authorization` header and is never
printed or included in a URL.

## Dry run first

Dry run is the default. It fetches repository state and prints every decision,
but sends no `PATCH` or `DELETE` requests.

```bash
python scripts/repo_hygiene.py \
  --repo sakura565-afk/openclaw-cursor \
  --token-file C:/Users/user/AppData/Local/hermes/github_token.txt \
  --include-merged
```

Example decisions:

```text
Mode: DRY RUN
Pull requests:
  CLOSE #7 Merged change [feature/old] — head branch is fully merged with main (ahead_by=0)
Branches:
  KEEP main — protected default branch
  KEEP feature/old — open PR #7
  DELETE feature/archived — fully merged with main (ahead_by=0)
```

Without `--include-merged`, merged branches are reported but are not deletion
candidates. This flag enables branch deletion; `--apply` is still required to
perform it.

## Apply a reviewed plan

```bash
# Close eligible PRs only; do not delete branches.
python scripts/repo_hygiene.py --apply

# Close eligible PRs and delete eligible merged branches.
python scripts/repo_hygiene.py --include-merged --apply

# Limit PR actions and cap the combined number of mutations.
python scripts/repo_hygiene.py --pr 7 9 --max 1 --apply

# Machine-readable dry-run output.
python scripts/repo_hygiene.py --include-merged --json
```

`--pr` limits which PRs appear as action items. All open PRs are still fetched
and used to protect their head branches. Operations are performed in PR-number
order, then branch-name order; `--max` caps both kinds together.

## Why `ahead_by` matters

For each remote branch, the tool requests:

```text
GET /repos/{owner}/{repo}/compare/main...{branch}
```

- `ahead_by == 0`: the branch has no commits that are absent from `main`. Its
  unique work is already reachable from `main`, so deleting the branch ref does
  not discard unique commits. The branch may still be behind `main`; that is
  safe for this cleanup criterion.
- `ahead_by > 0`: the branch contains commits absent from `main`. Merge those
  commits first, or consciously abandon them through a separate explicit
  action. This tool always keeps such branches.

`main` is never deleted. A branch with an open same-repository PR is also never
deleted, even when that PR is a close candidate in the same plan. Review the
closed PR and rerun the tool later if its branch should then be removed.
Fork-based PRs and PRs whose head branch cannot be found are kept.

## Flags

| Flag | Meaning |
| --- | --- |
| `--repo owner/name` | Target repository; defaults to `sakura565-afk/openclaw-cursor`. |
| `--token-file PATH` | GitHub token file; defaults to `C:/Users/user/AppData/Local/hermes/github_token.txt`. |
| `--apply` | Execute the reviewed mutations. Without it, the run is read-only. |
| `--pr N [N ...]` | Limit PR close candidates to the listed numbers. |
| `--max N` | Limit the combined number of close/delete operations. |
| `--include-merged` | Allow deletion of eligible branches with `ahead_by=0`. |
| `--json` | Print the plan and operation list as JSON. |
