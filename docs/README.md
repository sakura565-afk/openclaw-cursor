# Script Documentation

<!-- doc-generator:start -->
## How to run
Manually:

```bash
python scripts/doc_generator.py
```

With a pre-commit hook:

```yaml
- repo: local
  hooks:
    - id: doc-generator
      name: Generate script documentation
      entry: python scripts/doc_generator.py --check
      language: system
      pass_filenames: false
```

## Script index
| Script | Summary | Source |
| --- | --- | --- |
| [doc_generator.py](scripts/doc_generator/README.md) | Automated markdown documentation generator for OpenClaw scripts. | `scripts/doc_generator.py` |
<!-- doc-generator:end -->
