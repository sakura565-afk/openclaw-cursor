#!/usr/bin/env python3
"""Generate A/B meta title and description variants for furniture product cards via Ollama."""

from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
from tqdm import tqdm

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL = "mistral-nemo:latest"
DEFAULT_INPUT = "data/sample_products.csv"
DEFAULT_BATCH_SIZE = 0
RATE_LIMIT_SECONDS = 0.5
TITLE_MAX_LEN = 60
DESC_MAX_LEN = 160
TITLE_COUNT = 5
DESC_COUNT = 3

REQUIRED_COLUMNS = (
    "sku",
    "name",
    "category",
    "current_title",
    "current_description",
    "keywords",
)

OUTPUT_COLUMNS = (
    "sku",
    "name",
    "category",
    "title_var_1",
    "title_var_2",
    "title_var_3",
    "title_var_4",
    "title_var_5",
    "desc_var_1",
    "desc_var_2",
    "desc_var_3",
    "best_title_idx",
    "best_desc_idx",
    "scores",
)

logger = logging.getLogger(__name__)


@dataclass
class Product:
    sku: str
    name: str
    category: str
    current_title: str
    current_description: str
    keywords: str


@dataclass
class Variant:
    text: str
    score: float


@dataclass
class GenerationResult:
    titles: list[Variant] = field(default_factory=list)
    descriptions: list[Variant] = field(default_factory=list)


@dataclass
class RunStats:
    total: int = 0
    processed: int = 0
    skipped: int = 0
    failed: int = 0
    score_sum: float = 0.0
    score_count: int = 0

    @property
    def average_score(self) -> float:
        if self.score_count == 0:
            return 0.0
        return self.score_sum / self.score_count


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate SEO meta title/description A/B variants for furniture products "
            "using a local Ollama model."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--input",
        default=DEFAULT_INPUT,
        help="Input CSV with product rows.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Output CSV path. Default: data/seo_titles_YYYYMMDD_HHMMSS.csv",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help="Ollama model name.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help="Max products to process (0 = all rows).",
    )
    parser.add_argument(
        "--ollama-url",
        default=DEFAULT_OLLAMA_URL,
        help="Ollama API base URL.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=180.0,
        help="HTTP timeout in seconds for each Ollama request.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging.",
    )
    return parser.parse_args(argv)


def configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )


def default_output_path() -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path("data") / f"seo_titles_{stamp}.csv"


def load_products(path: Path) -> list[Product]:
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")

    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("CSV file has no header row.")

        missing = [column for column in REQUIRED_COLUMNS if column not in reader.fieldnames]
        if missing:
            raise ValueError(f"Missing required CSV columns: {', '.join(missing)}")

        products: list[Product] = []
        for row_number, row in enumerate(reader, start=2):
            sku = (row.get("sku") or "").strip()
            if not sku:
                logger.warning("Skipping row %s: empty sku", row_number)
                continue
            products.append(
                Product(
                    sku=sku,
                    name=(row.get("name") or "").strip(),
                    category=(row.get("category") or "").strip(),
                    current_title=(row.get("current_title") or "").strip(),
                    current_description=(row.get("current_description") or "").strip(),
                    keywords=(row.get("keywords") or "").strip(),
                )
            )
    return products


def is_ollama_available(ollama_url: str, timeout: float = 5.0) -> bool:
    try:
        response = requests.get(f"{ollama_url.rstrip('/')}/api/tags", timeout=timeout)
        return response.status_code == 200
    except requests.RequestException:
        return False


def build_prompt(product: Product) -> str:
    keyword_list = [item.strip() for item in product.keywords.split(",") if item.strip()]
    keywords_text = ", ".join(keyword_list) if keyword_list else product.category

    return f"""Ты SEO-копирайтер для мебельных интернет-магазинов amadey.ru и divaninfo.ru.
Сгенерируй варианты meta title и meta description для карточки товара.

Товар:
- SKU: {product.sku}
- Название: {product.name}
- Категория: {product.category}
- Текущий title: {product.current_title or "—"}
- Текущий description: {product.current_description or "—"}
- Ключевые слова: {keywords_text}

Требования:
1. Ровно {TITLE_COUNT} разных meta title (до {TITLE_MAX_LEN} символов каждый).
2. Ровно {DESC_COUNT} разных meta description (до {DESC_MAX_LEN} символов каждый).
3. Используй ключевые слова из категории и списка keywords естественно.
4. Стиль цепляющий: конкретные выгоды, цифры (цена, срок, размер), призыв к действию.
5. Упоминай преимущества: массив дерева, доставка, фабрика, классический стиль — где уместно.
6. Для каждого варианта оцени CTR-потенциал score от 1 до 10 (целое или с одним знаком после запятой).
7. Варианты должны отличаться по углу подачи (цена, качество, срочность, экспертность).

Верни ТОЛЬКО валидный JSON без markdown:
{{
  "titles": [{{"text": "...", "score": 8}}, ...],
  "descriptions": [{{"text": "...", "score": 7}}, ...]
}}"""


def truncate_text(text: str, max_len: int) -> str:
    cleaned = re.sub(r"\s+", " ", text.strip())
    if len(cleaned) <= max_len:
        return cleaned

    cut = cleaned[:max_len]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(".,;:- ")


def clamp_score(value: Any) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 1.0
    return max(1.0, min(10.0, score))


def normalize_variants(
    items: list[Any] | None,
    expected_count: int,
    max_len: int,
) -> list[Variant]:
    variants: list[Variant] = []
    for item in items or []:
        if isinstance(item, dict):
            text = str(item.get("text", "")).strip()
            score = clamp_score(item.get("score", 1))
        else:
            text = str(item).strip()
            score = 5.0
        if not text:
            continue
        variants.append(Variant(text=truncate_text(text, max_len), score=score))
        if len(variants) >= expected_count:
            break

    while len(variants) < expected_count:
        variants.append(Variant(text="", score=0.0))
    return variants[:expected_count]


def extract_json_payload(raw_text: str) -> dict[str, Any]:
    text = raw_text.strip()
    if not text:
        raise ValueError("Empty model response")

    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    if fence_match:
        text = fence_match.group(1)

    try:
        payload = json.loads(text)
        if isinstance(payload, dict):
            return payload
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("Response does not contain JSON object")
    return json.loads(text[start : end + 1])


def parse_generation_response(raw_text: str) -> GenerationResult:
    payload = extract_json_payload(raw_text)
    titles = normalize_variants(payload.get("titles"), TITLE_COUNT, TITLE_MAX_LEN)
    descriptions = normalize_variants(payload.get("descriptions"), DESC_COUNT, DESC_MAX_LEN)
    return GenerationResult(titles=titles, descriptions=descriptions)


def call_ollama(
    ollama_url: str,
    model: str,
    prompt: str,
    timeout: float,
    session: requests.Session | None = None,
) -> str:
    client = session or requests
    url = f"{ollama_url.rstrip('/')}/api/generate"
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.8},
    }
    response = client.post(url, json=payload, timeout=timeout)
    response.raise_for_status()
    body = response.json()
    content = body.get("response", "")
    if not isinstance(content, str):
        raise ValueError("Ollama response field is missing or not a string")
    return content


def best_index(variants: list[Variant]) -> int:
    best_score = -1.0
    best_idx = 1
    for index, variant in enumerate(variants, start=1):
        if variant.text and variant.score >= best_score:
            best_score = variant.score
            best_idx = index
    return best_idx


def empty_output_row(product: Product) -> dict[str, str]:
    row = {
        "sku": product.sku,
        "name": product.name,
        "category": product.category,
        "best_title_idx": "",
        "best_desc_idx": "",
        "scores": "",
    }
    for index in range(1, TITLE_COUNT + 1):
        row[f"title_var_{index}"] = ""
    for index in range(1, DESC_COUNT + 1):
        row[f"desc_var_{index}"] = ""
    return row


def result_to_row(product: Product, result: GenerationResult) -> dict[str, str]:
    row = empty_output_row(product)
    title_scores: list[float] = []
    desc_scores: list[float] = []

    for index, variant in enumerate(result.titles, start=1):
        row[f"title_var_{index}"] = variant.text
        title_scores.append(variant.score)

    for index, variant in enumerate(result.descriptions, start=1):
        row[f"desc_var_{index}"] = variant.text
        desc_scores.append(variant.score)

    row["best_title_idx"] = str(best_index(result.titles))
    row["best_desc_idx"] = str(best_index(result.descriptions))
    row["scores"] = json.dumps(
        {"title_scores": title_scores, "desc_scores": desc_scores},
        ensure_ascii=False,
    )
    return row


def write_output(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def update_stats(stats: RunStats, result: GenerationResult) -> None:
    for variant in result.titles + result.descriptions:
        if variant.text and variant.score > 0:
            stats.score_sum += variant.score
            stats.score_count += 1


def process_products(
    products: list[Product],
    *,
    ollama_url: str,
    model: str,
    timeout: float,
    batch_size: int,
    session: requests.Session | None = None,
) -> tuple[list[dict[str, str]], RunStats]:
    stats = RunStats(total=len(products))
    rows: list[dict[str, str]] = []
    limit = batch_size if batch_size > 0 else len(products)
    selected = products[:limit]

    for product in tqdm(selected, desc="Generating SEO variants", unit="product"):
        prompt = build_prompt(product)
        try:
            raw = call_ollama(ollama_url, model, prompt, timeout, session=session)
            result = parse_generation_response(raw)
            rows.append(result_to_row(product, result))
            stats.processed += 1
            update_stats(stats, result)
        except requests.RequestException as exc:
            logger.warning("Ollama request failed for sku=%s: %s", product.sku, exc)
            rows.append(empty_output_row(product))
            stats.failed += 1
        except (ValueError, json.JSONDecodeError) as exc:
            logger.warning("Failed to parse response for sku=%s: %s", product.sku, exc)
            rows.append(empty_output_row(product))
            stats.failed += 1
        finally:
            time.sleep(RATE_LIMIT_SECONDS)

    stats.skipped = stats.total - len(selected)
    return rows, stats


def print_summary(stats: RunStats, output_path: Path) -> None:
    print()
    print("=== SEO Title Generator — summary ===")
    print(f"Products in input:     {stats.total}")
    print(f"Successfully processed:{stats.processed}")
    print(f"Failed/skipped rows:   {stats.failed + stats.skipped}")
    print(f"Average CTR score:     {stats.average_score:.2f}")
    print(f"Output file:           {output_path}")


def run(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    configure_logging(args.verbose)

    input_path = Path(args.input)
    output_path = Path(args.output) if args.output else default_output_path()

    try:
        products = load_products(input_path)
    except (OSError, ValueError) as exc:
        logger.error("%s", exc)
        return 1

    if not products:
        logger.warning("No products to process in %s", input_path)
        write_output(output_path, [])
        print_summary(RunStats(), output_path)
        return 0

    if not is_ollama_available(args.ollama_url):
        logger.warning(
            "Ollama is not available at %s — skipping generation. "
            "Start Ollama (`ollama serve`) and ensure the model is pulled.",
            args.ollama_url,
        )
        rows = [empty_output_row(product) for product in products]
        write_output(output_path, rows)
        stats = RunStats(total=len(products), skipped=len(products))
        print_summary(stats, output_path)
        return 0

    rows, stats = process_products(
        products,
        ollama_url=args.ollama_url,
        model=args.model,
        timeout=args.timeout,
        batch_size=args.batch_size,
    )
    write_output(output_path, rows)
    print_summary(stats, output_path)
    return 0


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
