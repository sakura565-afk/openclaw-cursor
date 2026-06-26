from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import seo_title_generator  # noqa: E402


SAMPLE_RESPONSE = {
    "titles": [
        {"text": "Диван из бука — от 26 500 ₽ | Амадей", "score": 9},
        {"text": "Купить диван «Классик» с доставкой по РФ", "score": 8},
        {"text": "Классический диван из массива — фабрика", "score": 7},
        {"text": "Диван 3-местный из бука — закажите сегодня", "score": 8},
        {"text": "Мебель из массива: диван «Классик» в наличии", "score": 6},
    ],
    "descriptions": [
        {
            "text": "Диван «Классик» из массива бука. Доставка за 3 дня, гарантия 2 года. Звоните!",
            "score": 8,
        },
        {
            "text": "Классический 3-местный диван от фабрики Амадей. Цена от 26 500 руб. Бесплатная консультация.",
            "score": 9,
        },
        {
            "text": "Надёжный диван из бука для гостиной. 15 лет на рынке, 500+ отзывов. Оформите заказ онлайн.",
            "score": 7,
        },
    ],
}


class SeoTitleGeneratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def sample_product(self) -> seo_title_generator.Product:
        return seo_title_generator.Product(
            sku="AMD-DIV-001",
            name="Диван «Классик» 3-местный",
            category="Диваны",
            current_title="Старый title",
            current_description="Старое описание",
            keywords="диван из массива,купить диван",
        )

    def test_truncate_text_enforces_limits(self) -> None:
        long_title = "А" * 80
        truncated = seo_title_generator.truncate_text(long_title, 60)
        self.assertLessEqual(len(truncated), 60)

        long_desc = "Слово " * 40
        truncated_desc = seo_title_generator.truncate_text(long_desc, 160)
        self.assertLessEqual(len(truncated_desc), 160)

    def test_parse_generation_response_normalizes_variants(self) -> None:
        raw = json.dumps(SAMPLE_RESPONSE, ensure_ascii=False)
        result = seo_title_generator.parse_generation_response(raw)

        self.assertEqual(len(result.titles), 5)
        self.assertEqual(len(result.descriptions), 3)
        self.assertTrue(all(len(item.text) <= 60 for item in result.titles))
        self.assertTrue(all(len(item.text) <= 160 for item in result.descriptions))
        self.assertEqual(result.titles[0].score, 9.0)
        self.assertEqual(seo_title_generator.best_index(result.titles), 1)
        self.assertEqual(seo_title_generator.best_index(result.descriptions), 2)

    @patch("scripts.seo_title_generator.is_ollama_available", return_value=True)
    @patch("scripts.seo_title_generator.call_ollama")
    @patch("scripts.seo_title_generator.time.sleep")
    def test_run_writes_output_with_mocked_ollama(
        self,
        sleep_mock: MagicMock,
        call_mock: MagicMock,
        availability_mock: MagicMock,
    ) -> None:
        call_mock.return_value = json.dumps(SAMPLE_RESPONSE, ensure_ascii=False)

        input_path = self.root / "products.csv"
        output_path = self.root / "out.csv"
        input_path.write_text(
            "sku,name,category,current_title,current_description,keywords\n"
            "AMD-DIV-001,Диван,Диваны,old title,old desc,диван\n",
            encoding="utf-8",
        )

        exit_code = seo_title_generator.run(
            [
                "--input",
                str(input_path),
                "--output",
                str(output_path),
                "--model",
                "mistral-nemo:latest",
                "--batch-size",
                "1",
            ]
        )

        self.assertEqual(exit_code, 0)
        self.assertTrue(output_path.exists())
        rows = list(csv_rows(output_path))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sku"], "AMD-DIV-001")
        self.assertEqual(rows[0]["title_var_1"], SAMPLE_RESPONSE["titles"][0]["text"])
        self.assertEqual(rows[0]["best_title_idx"], "1")
        self.assertEqual(rows[0]["best_desc_idx"], "2")
        scores = json.loads(rows[0]["scores"])
        self.assertEqual(len(scores["title_scores"]), 5)
        self.assertEqual(len(scores["desc_scores"]), 3)
        call_mock.assert_called_once()
        sleep_mock.assert_called()

    @patch("scripts.seo_title_generator.is_ollama_available", return_value=False)
    def test_run_skips_when_ollama_unavailable(self, availability_mock: MagicMock) -> None:
        input_path = self.root / "products.csv"
        output_path = self.root / "out.csv"
        input_path.write_text(
            "sku,name,category,current_title,current_description,keywords\n"
            "AMD-DIV-001,Диван,Диваны,old title,old desc,диван\n",
            encoding="utf-8",
        )

        exit_code = seo_title_generator.run(
            ["--input", str(input_path), "--output", str(output_path)]
        )

        self.assertEqual(exit_code, 0)
        rows = list(csv_rows(output_path))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title_var_1"], "")
        self.assertEqual(rows[0]["scores"], "")


def csv_rows(path: Path) -> list[dict[str, str]]:
    import csv

    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


if __name__ == "__main__":
    unittest.main()
