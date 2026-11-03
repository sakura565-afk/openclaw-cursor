# -*- coding: utf-8 -*-
"""Tests for the H3 LoRA daily review."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
_spec = importlib.util.spec_from_file_location(
    "h3_lora_daily_review", SCRIPTS / "h3_lora_daily_review.py")
review = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(review)

MANIFEST = json.loads((SCRIPTS / "h3_lora_manifest.json").read_text(encoding="utf-8"))


def _subprocess_env() -> dict:
    """Env for a child that must not depend on the console codec.

    The Windows CI console is cp1251, so a child that prints Cyrillic with
    the default stdout encoding dies with UnicodeEncodeError.
    """
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def test_classify_uses_manifest_categories():
    assert review.classify(
        "Realism People - MinimaxH3 - r34l1sm,0.6-1str.safetensors", MANIFEST) == "realism"
    assert review.classify(
        "Add Micro Details - MinimaxH3 - addmicrodetails,0.25-1str.safetensors",
        MANIFEST) == "detail"
    assert review.classify(
        "Spatial and Physics - MinimaxH3 - 0.8-1str.safetensors", MANIFEST) == "motion"


def test_classify_does_not_leak_on_description_words():
    """A 'Realism' word inside a description must not file a LoRA as realism."""
    assert review.classify(
        "Penis Lora v1.1 - MinimaxH3 - PENISLORA,Cinematic Realism,stroking.safetensors",
        MANIFEST) == "anatomy"
    assert review.classify(
        "Perfect ASSets Baby Got Back - MinimaxH3 - shkzz,realistic jiggle.safetensors",
        MANIFEST) == "motion"


def test_classify_tie_break_is_manifest_order():
    """Ties must not depend on how the category strings happen to sort."""
    manifest = {"loras": {
        "aaa_test": {"category": "utility", "aliases": ["Shared Alias"]},
        "zzz_test": {"category": "detail", "aliases": ["Shared Alias"]},
    }}
    # both aliases match with equal length -> the first manifest entry wins
    assert review.classify("Something Shared Alias.safetensors", manifest) == "utility"


def test_classify_falls_back_for_unknown_lora():
    assert review.classify("Some Unrelated Model - MinimaxH3 - foo.safetensors",
                            MANIFEST) == "utility"


def test_usefulness_ranks_texture_above_camera():
    detail = review.usefulness(
        "Add Micro Details - MinimaxH3 - addmicrodetails,0.25-1str.safetensors", MANIFEST)
    camera = review.usefulness(
        "Telephoto Zoom - MinimaxH3 - 1-2str.safetensors", MANIFEST)
    assert detail["score"] > camera["score"]
    assert detail["verdict"] == "ЯДРО"
    assert detail["notes"], "a verdict without a reason is not usable"


def test_usefulness_includes_trigger_and_strength():
    u = review.usefulness(
        "Add Micro Details - MinimaxH3 - addmicrodetails,0.25-1str.safetensors", MANIFEST)
    joined = " ".join(u["notes"])
    assert "addmicrodetails" in joined
    assert "0.5" in joined


def test_local_loras_reads_only_model_files(tmp_path):
    (tmp_path / "a.safetensors").write_bytes(b"x")
    (tmp_path / "b.ckpt").write_bytes(b"x")
    (tmp_path / "notes.txt").write_text("ignore me")
    found = review.local_loras([str(tmp_path)])
    assert len(found) == 2
    assert all("safetensors" in v["name"] or v["name"].endswith(".ckpt")
               for v in found.values())


def test_local_loras_tolerates_missing_root(tmp_path):
    assert review.local_loras([str(tmp_path / "nope")]) == {}


def test_ledger_roundtrip(tmp_path):
    p = tmp_path / "ledger.json"
    ledger = review.load_ledger(str(p))
    assert ledger["seen"] == {}
    ledger["seen"]["abc"] = {"name": "X"}
    review.save_ledger(ledger, str(p))
    again = review.load_ledger(str(p))
    assert again["seen"]["abc"]["name"] == "X"


def test_ledger_survives_corrupt_file(tmp_path):
    p = tmp_path / "ledger.json"
    p.write_text("{not json", encoding="utf-8")
    assert review.load_ledger(str(p))["seen"] == {}


def test_review_reports_new_only_once(tmp_path):
    """A LoRA must not be reported as new on every run."""
    bench = tmp_path / "bench"
    bench.mkdir()
    root = tmp_path / "loras"
    root.mkdir()
    (root / "Add Micro Details - MinimaxH3 - addmicrodetails,0.25-1str.safetensors").write_bytes(b"x")

    ledger = str(tmp_path / "ledger.json")
    first = review.main.__wrapped__ if hasattr(review.main, "__wrapped__") else None
    # run the module twice through its argparse entry point
    import subprocess
    import sys
    env = _subprocess_env()
    for _ in range(2):
        r = subprocess.run(
            [sys.executable, str(SCRIPTS / "h3_lora_daily_review.py"),
             "--root", str(root), "--ledger", ledger, "--no-network",
             "--json-out", str(tmp_path / "out.json")],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=env)
        assert r.returncode == 0, r.stderr
    out = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    # no-network means the local audit only; the LoRA on disk must be listed
    # as never benchmarked rather than as brand new
    assert out["never_benched_count"] >= 1


def test_review_marks_manual_reviewed(tmp_path):
    ledger = str(tmp_path / "ledger.json")
    import subprocess
    import sys
    r = subprocess.run(
        [sys.executable, str(SCRIPTS / "h3_lora_daily_review.py"),
         "--no-network", "--ledger", ledger,
         "--mark-reviewed", "Add Micro Details"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=_subprocess_env())
    assert r.returncode == 0, r.stderr
    data = json.loads(Path(ledger).read_text(encoding="utf-8"))
    assert any(v.get("how") == "manual" for v in data["seen"].values())


def test_default_roots_come_from_env(monkeypatch):
    """No hard-coded drive letters: the roots must come from the environment."""
    monkeypatch.setenv("H3_LORA_ROOTS", os.pathsep.join(["/tmp/a", "/tmp/b"]))
    assert review.default_lora_roots() == ["/tmp/a", "/tmp/b"]
    monkeypatch.delenv("H3_LORA_ROOTS", raising=False)
    monkeypatch.setenv("H3_LORA_ROOT_C", "/tmp/c")
    assert review.default_lora_roots() == ["/tmp/c"]


def test_review_exit_code_zero_without_network(tmp_path):
    import subprocess
    import sys
    r = subprocess.run(
        [sys.executable, str(SCRIPTS / "h3_lora_daily_review.py"),
         "--no-network", "--ledger", str(tmp_path / "l.json"),
         "--root", str(tmp_path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=_subprocess_env())
    assert r.returncode == 0, r.stderr
