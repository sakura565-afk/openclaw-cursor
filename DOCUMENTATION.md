# OpenClaw Workflow Documentation (Cursor Cloud Agents)

This document describes a practical, production-oriented model for running OpenClaw workflows with Cursor Cloud Agents.

## 1. Operating Model

Use this repository as the control plane for:
- defining workflow tasks in `tasks/`,
- implementing execution logic in `src/openclaw_orchestration/`,
- automating routine operations via `scripts/`,
- enforcing quality and repeatability through GitHub Actions in `.github/workflows/`.

Cloud Agents are responsible for implementing and validating changes through branch-based development and pull requests.

## 2. Core Components

### Task Specs (`tasks/`)
- YAML files that define executable tasks.
- Each task has:
  - `name`
  - `description`
  - `steps[]` with `action` and optional `parameters`.

### Runner (`src/openclaw_orchestration/runner.py`)
- Loads task specs.
- Validates structure.
- Executes or dry-runs steps.
- Emits deterministic logs for CI and audit trails.

### Utility Scripts (`scripts/`)
- `bootstrap.sh`: developer and CI setup.
- `healthcheck.sh`: environment validation.
- `run_task.py`: command-line task execution.
- `process_images.py`: reusable image processing utility for workflow pipelines.

## 3. Cursor Cloud Agent Workflow

Recommended workflow:

1. **Scope and task definition**
   - Add/update task YAML under `tasks/`.
   - Keep steps composable and explicit.

2. **Implementation**
   - Extend `runner.py` to support new step types.
   - Add script-level tools if operationally needed.

3. **Validation**
   - Run:
     - `./scripts/healthcheck.sh`
     - `python3 scripts/run_task.py --task <task-file> --dry-run`
   - Ensure CI passes on the PR branch.

4. **Promotion**
   - Merge PR after checks pass.
   - Let scheduled workflows execute approved automation tasks.

## 4. Production Practices

### Safety Controls
- Use `--dry-run` by default during development.
- Enforce mandatory PR reviews for task changes.
- Protect `main` branch with required status checks.
- Restrict workflow permissions to minimum required scopes.

### Observability
- Keep runner output structured and concise.
- Store critical artifacts and logs for post-run inspection.
- Add step-level timing and failure context if expanding runner capabilities.

### Change Management
- Make small, scoped commits.
- Include task-spec changes and runner changes in the same PR when coupled.
- Document any new step types in this file and README.

## 5. Extending Task Execution

To add a new step type:

1. Introduce handler logic in `execute_task`.
2. Validate required parameters for that type.
3. Add an example in `tasks/examples/`.
4. Add at least one CI invocation that exercises dry-run behavior.

Example step ideas:
- `api_call` (internal service orchestration)
- `db_maintenance` (safeguarded maintenance jobs)
- `artifact_publish` (post-processing outputs to storage)

## 6. CI/CD Strategy

### CI (`.github/workflows/ci.yml`)
- Runs on push/PR.
- Verifies script executability and Python syntax.
- Performs dry-run task validations.

### Nightly Automation (`.github/workflows/nightly-automation.yml`)
- Executes scheduled maintenance and smoke tasks.
- Keeps routine operations deterministic and observable.

Tune frequency and scope to your reliability and cost requirements.

## 7. Command Reference

```bash
# Setup
./scripts/bootstrap.sh

# Environment checks
./scripts/healthcheck.sh

# Dry-run a task
python3 scripts/run_task.py --task tasks/examples/maintenance.yaml --dry-run

# Execute a task
python3 scripts/run_task.py --task tasks/examples/maintenance.yaml

# Process images for workflow artifacts
python3 scripts/process_images.py --input ./input-images --output ./output-images --max-width 1280
```

## 8. Security Notes

- Do not hardcode credentials in tasks or scripts.
- Use repository/environment secrets for sensitive values in CI.
- Keep shell script strict modes enabled (`set -euo pipefail`).
- Prefer idempotent steps for repeatable orchestration.

