# Tool Discovery Template

> A reusable prompt template for systematically discovering, evaluating, and
> selecting tools (CLIs, libraries, APIs, MCP servers, agent functions) that
> can satisfy a stated capability requirement.

---

## Metadata

| Field            | Value                                                   |
| ---------------- | ------------------------------------------------------- |
| Template ID      | `tool-discovery`                                        |
| Version          | `1.0.0`                                                 |
| Category         | Capability mapping / Tool selection                     |
| Recommended Use  | Choosing a library, surveying an MCP catalog, scoping integrations |
| Required Inputs  | `capability_needed`, `available_tools`                  |
| Optional Inputs  | `constraints`, `existing_stack`, `evaluation_criteria`, `budget`, `prior_choices` |

---

## Variables

| Variable                  | Type   | Required | Description                                          |
| ------------------------- | ------ | -------- | ---------------------------------------------------- |
| `{{capability_needed}}`   | text   | yes      | The user-facing capability or task to enable         |
| `{{available_tools}}`     | list   | yes      | Catalog of candidate tools with names + descriptions |
| `{{?constraints}}`        | list   | no       | Hard requirements (license, latency, offline, etc.)  |
| `{{?existing_stack}}`     | text   | no       | Languages, frameworks, runtimes already in use       |
| `{{?evaluation_criteria}}`| list   | no       | Custom scoring axes (e.g., DX, maturity, cost)       |
| `{{?budget}}`             | text   | no       | Cost or token/latency budget                         |
| `{{?prior_choices}}`      | text   | no       | Previously selected tools and why                    |

---

## Prompt

```
# Role
You are a pragmatic principal engineer evaluating tools against a concrete
need. You favor boring, well-supported choices over novelty. You explicitly
distinguish between what a tool *claims* to do and what evidence shows it
*reliably* does.

# Context
## Capability needed
{{capability_needed}}

## Available tools (candidates)
{{available_tools}}

## Hard constraints
{{?constraints}}

## Existing stack
{{?existing_stack}}

## Evaluation criteria (in priority order if specified)
{{?evaluation_criteria}}

## Budget
{{?budget}}

## Prior tool choices to consider for consistency
{{?prior_choices}}

# Task
1. Restate the capability in one sentence using your own words to confirm
   understanding.
2. Decompose the capability into the minimum set of *sub-capabilities* the
   chosen tool(s) must cover.
3. For each candidate in `available_tools`, score how well it covers each
   sub-capability and note any hard-constraint violations.
4. Identify obvious gaps: sub-capabilities that no candidate covers well.
5. Recommend a primary tool (or composition of tools) and justify the
   choice. Name a runner-up.
6. List the smallest possible proof-of-concept that would validate the
   recommendation before full adoption.

# Output Format
Respond in Markdown using exactly these sections:

## Capability (Restated)
One sentence.

## Sub-Capabilities
A numbered list. Mark each as `[must]` or `[nice]`.

## Candidate Matrix
A Markdown table with columns:
`Tool | Coverage | Constraint fit | Maturity | Integration cost | Notes`

Use `Coverage` values of `Full`, `Partial`, `None`. Use `Constraint fit`
values of `Pass` or `Fail (<reason>)`. Be terse in `Notes`.

## Gaps
Bulleted list. If there are no gaps, write `None identified.`

## Recommendation
- **Primary:** `<tool>` — one-paragraph justification tied to the matrix.
- **Runner-up:** `<tool>` — one sentence on when you'd pick this instead.
- **Composition (if any):** Describe how multiple tools combine and where
  the seams are.

## Proof-of-Concept Plan
A numbered list of at most 5 steps. The final step must be a measurable
acceptance check (e.g., "round-trip latency under 200 ms on sample input X").

## Risks & Open Questions
Bulleted list of unknowns the PoC should resolve.

# Constraints
- Do not invent tools that are not in `available_tools`.
- If no candidate satisfies the must-have sub-capabilities, say so in the
  Recommendation and propose what the user should look for instead.
- Prefer a single tool over a composition unless the gap analysis forces it.
- Do not recommend tools that violate any hard constraint, even if they
  score well otherwise.
```

---

## Examples

### Example 1 — Filled-in input (abridged)

```
## Capability needed
Run small open-source LLMs locally on a laptop and expose them over an HTTP
API to a Python application.

## Available tools (candidates)
- ollama: local model runner, REST API on :11434, supports many open models
- llama.cpp: C++ inference engine, CLI + server, supports GGUF quantization
- LM Studio: GUI app with OpenAI-compatible local server
- vLLM: high-throughput server, GPU-only, designed for serving at scale

## Hard constraints
- Must run offline.
- Must work on a 16 GB Apple Silicon MacBook (no dedicated GPU).
- Must be MIT/Apache-licensed.

## Existing stack
Python 3.12, FastAPI, no Docker requirement.
```

### Example 1 — Expected response shape (abridged)

```markdown
## Capability (Restated)
Provide a local, offline HTTP endpoint that serves small open-source LLMs to
a Python FastAPI application on Apple Silicon.

## Sub-Capabilities
1. [must] Run quantized open models on Apple Silicon CPU/Metal.
2. [must] Expose an HTTP API consumable from Python.
3. [must] Operate fully offline.
4. [nice] OpenAI-compatible request/response shape.

## Candidate Matrix
| Tool      | Coverage | Constraint fit          | Maturity | Integration cost | Notes |
|-----------|----------|--------------------------|----------|------------------|-------|
| ollama    | Full     | Pass                     | High     | Low              | REST API, model pulls offline-cacheable |
| llama.cpp | Partial  | Pass                     | High     | Medium           | Server is minimal; you wire the API |
| LM Studio | Full     | Fail (closed-source GUI) | Medium   | Low              | Violates license constraint |
| vLLM      | None     | Fail (GPU required)      | High     | High             | Not viable on Apple Silicon laptop |

## Gaps
None identified for the must-haves; OpenAI compatibility requires a small
adapter on top of Ollama's native API.

## Recommendation
- **Primary:** `ollama` — covers all must-haves, lowest integration cost,
  and active community. Pair with a thin Python client to expose an
  OpenAI-shaped wrapper if needed.
- **Runner-up:** `llama.cpp` — choose this if you need lower-level control
  over quantization or want to avoid an extra daemon.

## Proof-of-Concept Plan
1. Install Ollama and pull a 3B-parameter model.
2. Confirm `ollama list` shows the model and the daemon binds :11434.
3. Implement a FastAPI route that proxies to `/api/generate`.
4. Run a 50-request load test from a Python script.
5. Acceptance: median latency for a 128-token completion is under 5 s on
   the target laptop, with no network egress.

## Risks & Open Questions
- Memory headroom while a 7B model is loaded alongside the application.
- Whether automatic model unload meets concurrency needs.
```

### Example 2 — When no tool fits

If every candidate fails a hard constraint, the Recommendation section
states this plainly and the Proof-of-Concept Plan is replaced with a
*Search Plan* describing the keywords, registries, and benchmarks the user
should consult next (e.g., "search Hugging Face for MLX-compatible servers
released in the last 6 months").

---

## Usage Notes

- Keep `available_tools` honest: include short, factual descriptions and
  links if possible. Hallucination risk drops sharply when the candidate
  set is enumerated.
- For **agent self-discovery** (e.g., MCP tool selection), feed the agent's
  registered tool catalog directly into `available_tools` and set
  `capability_needed` to the user's current request.
- Re-run this template whenever the candidate set changes; the matrix is
  cheap to regenerate and prevents stale tool choices from accumulating.
- Pairs well with `code-review-template.md` once the chosen tool is wired
  into the codebase.
