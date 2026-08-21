#!/usr/bin/env python3
"""Default Ollama model fallback chain used by OpenClaw automation."""

from __future__ import annotations

import subprocess
from typing import Sequence

# Ordered cloud → local → paid fallback chain.
DEFAULT_CHAIN: tuple[str, ...] = (
    "minimax-m3:cloud",
    "qwen3-coder-next:cloud",
    "devstral-small-2:24b:cloud",
    "gpt-oss:20b:cloud",
    "gpt-oss:20b",
    "devstral:24b",
    "nemotron-3-nano:4b",
    "lfm2.5-thinking:latest",
    "grok-4.5",
)


def run_model(
    model: str,
    prompt: str,
    *,
    timeout: float | None = None,
    popen: type = subprocess.Popen,
) -> tuple[int, str, str]:
    """Run a single prompt via ``ollama run`` with stdin/stdout pipes.

    Returns ``(returncode, stdout, stderr)``. Raises
    ``subprocess.TimeoutExpired`` when *timeout* elapses.
    """
    proc = popen(
        ["ollama", "run", model],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        stdout, stderr = proc.communicate(input=prompt if prompt.endswith("\n") else prompt + "\n", timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.communicate(timeout=5)
        except Exception:
            pass
        raise
    return int(proc.returncode or 0), stdout or "", stderr or ""


def chain_models(models: Sequence[str] | None = None) -> tuple[str, ...]:
    """Return the chain to walk (defaults to ``DEFAULT_CHAIN``)."""
    if models is None:
        return DEFAULT_CHAIN
    return tuple(models)


__all__ = ["DEFAULT_CHAIN", "run_model", "chain_models"]
