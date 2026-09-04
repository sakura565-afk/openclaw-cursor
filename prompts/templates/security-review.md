# Security-focused review

Perform a targeted security review of the change or component. Assume adversarial inputs and untrusted networks unless stated otherwise.

## Context (fill before sending)

- **Asset value**: [DATA_SECRETS_AVAILABILITY_USER_TRUST]
- **Trust boundaries**: [PUBLIC_INTERNET_INTERNAL_ADMIN_USER_INPUT_FILES]
- **Change summary or paths**: [BRIEF_OR_PATHS]

## Threat lens (answer briefly for each relevant item)

- **Injection**: SQL, command, LDAP, template, log forging.
- **AuthN / AuthZ**: session handling, token storage, privilege checks on every mutating path.
- **Secrets**: hard-coded keys, leaked env in logs, overly broad file reads.
- **Supply chain / dependencies**: new deps, script execution from remote content.
- **Serialization**: unsafe YAML/`pickle`, prototype pollution in JS contexts.
- **DoS / abuse**: unbounded payloads, missing rate limits on expensive endpoints.

## Output format

- **Risk summary**: table with columns Finding | Severity (critical/high/medium/low) | Likelihood | Mitigation.
- **Must-fix before merge**: numbered.
- **Defense-in-depth suggestions**: bullets (non-blocking).
