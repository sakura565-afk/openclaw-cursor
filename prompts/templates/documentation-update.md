# Documentation update aligned with code

Update documentation so it matches the current code and operational reality.

## Inputs (fill before sending)

- **Audience**: [DEVELOPER_OPERATOR_END_USER]
- **Doc surfaces to update**: [README_MD_DOCS_FOLDER_INLINE_HELP_OPENAPI]
- **Related change**: [PR_LINK_OR_SUMMARY_OF_CODE_CHANGE]
- **Out of scope**: [SECTIONS_TO_LEAVE_UNCHANGED]

## Guidelines

- Prefer accurate, scannable docs: short paragraphs, tables for options and env vars, copy-pasteable commands.
- Document defaults, required vs optional configuration, and failure modes (what log or error to expect).
- Remove or fix stale examples; do not document unimplemented flags or endpoints.
- Keep terminology consistent with the codebase (same names for CLI flags, env vars, modules).

## Output format

- **Summary of doc changes**: bullet list by file.
- **Verification**: how a reader can confirm each updated procedure (command or URL).
