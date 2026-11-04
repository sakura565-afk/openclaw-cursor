"""Allow ``python -m scripts.chain_texture_gate`` without import-time execution."""

from .runner import main

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
