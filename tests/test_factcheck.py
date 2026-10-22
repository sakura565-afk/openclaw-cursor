"""Тесты пакета src/factcheck.

Тесты **не зависят от диска хоста**: реестр корней подменяется на tmp_path.
Проверки на реальном хосте — отдельный режим CLI (``--live``).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.factcheck import claims as C  # noqa: E402
from src.factcheck import paths as P  # noqa: E402
from src.factcheck import verify as V  # noqa: E402


@pytest.fixture
def host(tmp_path: Path) -> P.HostPaths:
    """Реестр корней, указывающий на временный каталог."""
    root = tmp_path / "roots"
    (root / "loras").mkdir(parents=True)
    (root / "scripts").mkdir(parents=True)
    return P.HostPaths(
        lora_roots=(str(root / "loras"),),
        comfy_roots=(),
        bin_roots=(),
        workspace_roots=(str(root),),
        repo_roots=(str(root / "scripts"),),
        extra_roots=(),
    )


# ---------------------------------------------------------------------------
# Извлечение кандидатов
# ---------------------------------------------------------------------------


def test_extract_windows_path() -> None:
    found = C.extract_claims("Модель лежит в C:\\Users\\user\\models\\loras\\a.safetensors")
    assert any(c.kind is C.ClaimKind.PATH and "models" in c.value for c in found)


def test_extract_filename() -> None:
    found = C.extract_claims("Подключи LoRA NSGIRL-MiniMax-H3-LoRA-By-MM744.safetensors")
    names = [c.value for c in found if c.kind is C.ClaimKind.FILENAME]
    assert "NSGIRL-MiniMax-H3-LoRA-By-MM744.safetensors" in names


def test_extract_number() -> None:
    found = C.extract_claims("strength_model: 0.45")
    numbers = [c.value for c in found if c.kind is C.ClaimKind.NUMBER]
    assert "strength_model=0.45" in numbers


def test_extract_secret() -> None:
    # Значение синтетическое: настоящий ключ в тестах быть не должен.
    fake = "crsr_" + "0123456789abcdef" * 2
    found = C.extract_claims(f'KEY = "{fake}"')
    secrets = [c.value for c in found if c.kind is C.ClaimKind.SECRET]
    assert len(secrets) == 1
    assert secrets[0].startswith("crsr_")


def test_diff_only_additions_are_checked() -> None:
    diff = "\n".join([
        "--- a/f.py",
        "+++ b/f.py",
        "-REMOVED = r\"C:\\gone\\x.py\"",
        "+ADDED = r\"C:\\here\\y.py\"",
    ])
    found = C.extract_from_diff(diff)
    values = [c.value for c in found if c.kind is C.ClaimKind.PATH]
    assert any("here" in v for v in values)
    assert not any("gone" in v for v in values)


def test_path_containing_filename_not_duplicated() -> None:
    found = C.extract_claims("см. C:\\dir\\file.py подробнее")
    kinds = [(c.kind, c.value) for c in found]
    assert (C.ClaimKind.PATH, "C:\\dir\\file.py") in kinds
    assert not any(k is C.ClaimKind.FILENAME and v == "file.py" for k, v in kinds)


def test_comment_lines_are_skipped() -> None:
    found = C.extract_claims("# пример: C:\\path\\in\\comment.py")
    assert not [c for c in found if c.kind is C.ClaimKind.PATH]


# ---------------------------------------------------------------------------
# Проверка
# ---------------------------------------------------------------------------


def test_existing_file_is_ok(host: P.HostPaths) -> None:
    (host.existing_roots()[0] / "good.safetensors").write_bytes(b"x")
    claim = C.Claim(kind=C.ClaimKind.FILENAME, value="good.safetensors")
    result = V.verify_claim(claim, host)
    assert result.verdict is V.Verdict.OK


def test_missing_file_is_not_found(host: P.HostPaths) -> None:
    claim = C.Claim(kind=C.ClaimKind.FILENAME, value="absent.safetensors")
    result = V.verify_claim(claim, host)
    assert result.verdict is V.Verdict.NOT_FOUND


def test_missing_file_suggests_similar(host: P.HostPaths) -> None:
    """Выдуманное имя должно предлагать реальный кандидат."""
    (host.existing_roots()[0] / "minimax-h3-facial-realism-closeup.safetensors").write_bytes(b"x")
    claim = C.Claim(
        kind=C.ClaimKind.FILENAME,
        value="h3-facial-realism-t2v-i2v-r2v.safetensors",
    )
    result = V.verify_claim(claim, host)
    assert result.verdict is V.Verdict.NOT_FOUND
    assert "похожий" in result.suggestion


def test_secret_in_code_is_not_found(host: P.HostPaths) -> None:
    claim = C.Claim(kind=C.ClaimKind.SECRET, value="crsr_" + "a" * 40)
    result = V.verify_claim(claim, host)
    assert result.verdict is V.Verdict.NOT_FOUND
    assert "секрет" in result.evidence


def test_expected_missing_is_ok(host: P.HostPaths) -> None:
    claim = C.Claim(kind=C.ClaimKind.FILENAME, value="example.safetensors")
    result = V.verify_claim(claim, host)
    assert result.verdict is V.Verdict.OK


def test_file_created_by_task_is_ok(host: P.HostPaths) -> None:
    """Файл, который задача сама создаёт, не считается опровергнутым."""
    claim = C.Claim(
        kind=C.ClaimKind.FILENAME,
        value="brand_new_module.py",
        context="Deliverable 1: создать brand_new_module.py",
    )
    result = V.verify_claim(claim, host)
    assert result.verdict is V.Verdict.OK


def test_existing_path_is_ok(host: P.HostPaths) -> None:
    target = host.existing_roots()[0] / "here" / "file.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x", encoding="utf-8")
    claim = C.Claim(kind=C.ClaimKind.PATH, value=str(target))
    result = V.verify_claim(claim, host)
    assert result.verdict is V.Verdict.OK


def test_missing_path_is_not_found(host: P.HostPaths) -> None:
    claim = C.Claim(kind=C.ClaimKind.PATH, value=r"C:\definitely\not\here\file.txt")
    result = V.verify_claim(claim, host)
    assert result.verdict is V.Verdict.NOT_FOUND


def test_number_without_registry_entry_is_unverifiable(host: P.HostPaths) -> None:
    claim = C.Claim(kind=C.ClaimKind.NUMBER, value="strength_model=0.45")
    result = V.verify_claim(claim, host)
    assert result.verdict is V.Verdict.UNVERIFIABLE


def test_number_with_known_value_is_checked(host: P.HostPaths) -> None:
    P.VERIFIED_NUMBERS["strength_model"] = 0.45
    try:
        good = V.verify_claim(
            C.Claim(kind=C.ClaimKind.NUMBER, value="strength_model=0.45"), host
        )
        bad = V.verify_claim(
            C.Claim(kind=C.ClaimKind.NUMBER, value="strength_model=1.0"), host
        )
    finally:
        P.VERIFIED_NUMBERS["strength_model"] = None
    assert good.verdict is V.Verdict.OK
    assert bad.verdict is V.Verdict.NOT_FOUND
    assert "0.45" in bad.suggestion


def test_no_live_mode_reports_unverifiable(host: P.HostPaths) -> None:
    claim = C.Claim(kind=C.ClaimKind.FILENAME, value="whatever.safetensors")
    result = V.verify_claim(claim, host, live=False)
    assert result.verdict is V.Verdict.UNVERIFIABLE


def test_summarize_counts(host: P.HostPaths) -> None:
    results = V.verify_claims(
        [
            C.Claim(kind=C.ClaimKind.SECRET, value="crsr_" + "b" * 40),
            C.Claim(kind=C.ClaimKind.FILENAME, value="absent.safetensors"),
        ],
        host,
    )
    counts = V.summarize(results)
    assert counts[V.Verdict.NOT_FOUND.value] == 2
