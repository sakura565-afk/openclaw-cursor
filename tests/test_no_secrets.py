"""Тест: в репозитории не должно быть захардкоженных секретов.

Репозиторий публичный. Секрет в коде — это утечка с первого коммита:
даже если ветку переписать, старый блоб остаётся в истории и отдаётся
анонимно через raw.githubusercontent по SHA из refs/pull/*.

Проверяются:
- присваивания вида TOKEN = "..." / API_KEY = '...' с непустым значением;
- характерные префиксы известных токенов (OAuth Яндекса, GitHub, OpenAI,
  Anthropic, Slack, Telegram-боты, приватные ключи);
- что в файлах нет строк, похожих на реальные токены Метрики.

Исключения — только явные и обоснованные: плейсхолдеры, тестовые значения,
имена переменных окружения без значения.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Каталоги, которые не коммитятся в публичный репозиторий.
SKIP_DIRS = {
    ".git", ".github", ".venv", "venv", "node_modules", "__pycache__",
    ".pytest_cache", ".cache", "build", "dist", ".mypy_cache", ".ruff_cache",
}

# Файлы, где строки-примеры допустимы и проверены вручную.
SKIP_FILES = {
    "tests/test_no_secrets.py",
    "docs/incident_metrika_token_leak.md",
}

# Значения, которые безобидны: плейсхолдеры, тестовые строки, описания.
ALLOWED_VALUES = {
    "", "none", "null", "changeme", "your_token_here", "xxx", "todo",
    "<token>", "test", "dummy", "fake", "example", "placeholder",
    "not-a-real-token", "secret-token", "secret", "not-real",
    # маркер уровня доступа в src/factcheck/claims.py (enum, не секрет)
    "redacted", "internal", "public",
}

# Строки-маркеры уровня доступа: SECRET = "secret" и подобные — это
# перечисление, а не credential.
ACCESS_LEVEL_VALUES = {"secret", "internal", "public", "restricted"}

# Тестовые файлы: в них строки-примеры — часть проверки, а не утечка.
TEST_MARKERS = ("tests/", "test_", "/test_", "_test.py")

# Литерал самого скомпрометированного токена собирается по частям: иначе
# сам тест публикует его в репозитории и становится источником утечки.
# Скан по байтам ищет непрерывную последовательность — её в файле нет.
_METRIKA_TOKEN_MARKER = b"Nast" + b"ia" + b"56"

# Характерные форматы настоящих токенов.
TOKEN_PATTERNS = [
    (re.compile(re.escape(_METRIKA_TOKEN_MARKER)), "токен Метрики"),
    (re.compile(rb"ghp_[A-Za-z0-9]{20,}"), "GitHub PAT"),
    (re.compile(rb"gho_[A-Za-z0-9]{20,}"), "GitHub OAuth"),
    (re.compile(rb"github_pat_[A-Za-z0-9_]{20,}"), "GitHub fine-grained PAT"),
    (re.compile(rb"sk-ant-[A-Za-z0-9\-_]{20,}"), "Anthropic API key"),
    (re.compile(rb"sk-[A-Za-z0-9]{32,}"), "OpenAI-подобный ключ"),
    (re.compile(rb"AKIA[0-9A-Z]{16}"), "AWS access key"),
    (re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "приватный ключ"),
    (re.compile(rb"bot\d{6,}:[A-Za-z0-9_-]{30,}"), "токен Telegram-бота"),
]

# Присваивания секретов: ИМЯ = "значение"
SECRET_ASSIGN = re.compile(
    rb"""(?i)\b(?:[A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|API_?KEY|APIKEY|CREDENTIAL)"""
    rb"""|YANDEX_LOGIN)\s*=\s*['"]([^'"\n]{6,})['"]"""
)

# Имя переменной окружения — это не секрет.
ENV_NAMES = re.compile(rb"^\$?\{?[A-Z0-9_]+\}?$")


def iter_files() -> list[Path]:
    out: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        rel = path.relative_to(ROOT).as_posix()
        if rel in SKIP_FILES:
            continue
        # бинарные файлы не проверяем
        try:
            if b"\x00" in path.read_bytes()[:4096]:
                continue
        except OSError:
            continue
        out.append(path)
    return out


class NoSecretsInRepoTests(unittest.TestCase):
    def test_no_hardcoded_secret_values(self) -> None:
        problems: list[str] = []
        for path in iter_files():
            try:
                data = path.read_bytes()
            except OSError:
                continue
            rel = path.relative_to(ROOT).as_posix()
            for pattern, label in TOKEN_PATTERNS:
                if pattern.search(data):
                    problems.append(f"{rel}: {label}")
            # присваивания проверяем только в рабочем коде: в тестах строки
            # типа token="not-a-real-token" — часть проверки, а не секрет
            is_test = any(m in rel for m in TEST_MARKERS)
            if not is_test:
                for match in SECRET_ASSIGN.finditer(data):
                    value = match.group(1).strip().decode("utf-8", "replace")
                    low = value.lower()
                    if low in ALLOWED_VALUES:
                        continue
                    if low in ACCESS_LEVEL_VALUES and rel.startswith("src/"):
                        continue
                    if ENV_NAMES.match(value.encode("utf-8")):
                        continue
                    problems.append(f"{rel}: присваивание секрета = {value[:24]!r}")
        self.assertEqual(problems, [], "секреты в коде:\n" + "\n".join(problems))

    def test_metrika_module_reads_token_from_env(self) -> None:
        """Метрика обязана брать токен из окружения, а не из константы."""
        module = ROOT / "yandex_metrika.py"
        self.assertTrue(module.exists(), "yandex_metrika.py должен существовать")
        source = module.read_text(encoding="utf-8")
        self.assertIn("os.environ", source,
                      "yandex_metrika.py должен читать токен из окружения")
        self.assertNotRegex(
            source, re.compile(r"""YANDEX_TOKEN\s*=\s*['"][^'"]+['"]"""),
            "YANDEX_TOKEN не должен быть задан литералом")

    def test_scan_covers_repository_files(self) -> None:
        # защита от «сканер молча ничего не проверяет»
        self.assertGreater(len(iter_files()), 50,
                           "скан нашёл подозрительно мало файлов — он может быть сломан")


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False, verbosity=2).result.wasSuccessful() else 1)
