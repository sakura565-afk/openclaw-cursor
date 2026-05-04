# OpenClaw Orchestration via Cursor Cloud Agent

Production-ready starter repository for orchestrating OpenClaw workflows using Cursor Cloud Agents.

This repository provides:
- **Agent-aware project structure** for repeatable workflow automation.
- **Task definitions** for OpenClaw automation pipelines.
- **Operational scripts** for setup, health checks, image processing, and task execution.
- **GitHub Actions CI/CD** templates for linting, testing, and scheduled automation.
- **Documentation** focused on Cloud Agent collaboration patterns.

## What is OpenClaw orchestration?

OpenClaw orchestration is the practice of defining, executing, and monitoring repeatable automation tasks (such as image transforms, data syncs, and batch operations) through structured task specs and runners.

This scaffold makes it easy to:
- Standardize task inputs/outputs.
- Execute tasks locally or in CI.
- Delegate implementation and operations work to Cursor Cloud Agents.

## Repository Structure

```text
.
├── .github/
│   └── workflows/
│       ├── ci.yml
│       └── nightly-automation.yml
├── scripts/
│   ├── bootstrap.sh
│   ├── healthcheck.sh
│   ├── process_images.py
│   └── run_task.py
├── src/
│   └── openclaw_orchestration/
│       ├── __init__.py
│       └── runner.py
├── tasks/
│   └── examples/
│       ├── image_pipeline.yaml
│       └── maintenance.yaml
├── DOCUMENTATION.md
└── README.md
```

## Features

- **Declarative tasks** using YAML specs in `tasks/examples/`.
- **Extensible Python runner** (`src/openclaw_orchestration/runner.py`) for task loading and execution.
- **Operational utilities** in `scripts/`:
  - project bootstrap,
  - environment health validation,
  - image processing automation,
  - task execution wrapper.
- **CI baseline** with GitHub Actions for:
  - formatting/lint checks,
  - smoke task validation,
  - scheduled nightly orchestration runs.

## Quick Start

1. Create and activate a virtual environment:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

2. Bootstrap the repository:
   ```bash
   ./scripts/bootstrap.sh
   ```

3. Run a health check:
   ```bash
   ./scripts/healthcheck.sh
   ```

4. Execute an example OpenClaw task:
   ```bash
   python3 scripts/run_task.py --task tasks/examples/maintenance.yaml --dry-run
   ```

5. Run example image processing:
   ```bash
   python3 scripts/process_images.py --input ./input-images --output ./output-images --max-width 1280
   ```

## Local Development

- Format/lint:
  ```bash
  python3 -m compileall src scripts
  ```
- Validate task specs:
  ```bash
  python3 scripts/run_task.py --task tasks/examples/image_pipeline.yaml --dry-run
  ```

## CI/CD

The default workflows in `.github/workflows/` provide:
- PR and push validation (`ci.yml`)
- a scheduled nightly automation execution (`nightly-automation.yml`)

Adjust triggers, secrets, and environments based on your deployment model.

## Using Cursor Cloud Agents

See **[DOCUMENTATION.md](./DOCUMENTATION.md)** for detailed guidance on:
- task lifecycle management with Cloud Agents,
- branch/PR workflows,
- safety controls for production automation.

