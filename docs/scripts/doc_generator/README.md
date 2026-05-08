# doc_generator.py

<!-- doc-generator:start -->
## Source

`scripts/doc_generator.py`

## Description

Automated markdown documentation generator for OpenClaw scripts.

This utility scans the scripts/ directory, parses Python files with the ast
module, and generates:

* One README.md per script under docs/scripts/<script-name>/README.md
* A master index at docs/README.md

It is designed for both manual execution and pre-commit integration:

    python scripts/doc_generator.py
    python scripts/doc_generator.py --check

Suggested pre-commit hook:

    - repo: local
      hooks:
        - id: doc-generator
          name: Generate script documentation
          entry: python scripts/doc_generator.py --check
          language: system
          pass_filenames: false

CLI description: Generate markdown documentation for scripts.

## Usage examples

```bash
python scripts/doc_generator.py --help
python scripts/doc_generator.py filenames ...
python scripts/doc_generator.py filenames ... --scripts-dir scripts_dir
```

## Arguments

| Argument | Required | Default | Details |
| --- | --- | --- | --- |
| filenames | No | - | Optional filenames from pre-commit; matching scripts are regenerated. Nargs: `*`. |
| --scripts-dir | No | scripts | Directory containing Python scripts to scan. |
| --docs-dir | No | docs | Directory where generated markdown files are written. |
| --check | No | - | Check whether generated documentation is up to date without writing files. |
| --no-color | No | - | Disable ANSI color output. |

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Successful execution. |
<!-- doc-generator:end -->
