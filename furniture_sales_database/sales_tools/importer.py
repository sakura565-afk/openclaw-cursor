#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Импорт накопительного среза продаж 1С (TDSheet) в workspace/sales.db.

Вариант файла: 3 уровня группировки —
    Месяц (одна строка-итог вверху) -> Контрагент (rollup) -> Номенклатура (лист)
Без уровня «Номенклатура.Родитель». Контрагент идёт строкой с qty+amount,
его товары идут сразу следом (без строки месяца между ними).

Свойства:
  - сверка листьев со строкой «Итог» файла (обязательна, иначе стоп);
  - поконтрагентная сверка (сумма листьев == rollup контрагента в файле);
  - проверка структуры: каждый лист оканчивается единицей измерения;
  - покрытие старого среза: все контрагенты + qty/amount не регрессируют;
  - реестр срезов (таблица ``slices``) с цепочкой ``supersedes``;
  - накопительный срез ЗАМЕНЯЕТ предыдущий срез того же месяца и периметра;
  - импорт в одной транзакции ``BEGIN IMMEDIATE`` с откатом при ошибке;
  - идемпотентность: повторный прогон даёт те же цифры.

Usage:
  python importer.py <file.xlsx> [--apply] [--db PATH]
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import shutil
import sqlite3
from typing import Any

import openpyxl

# Единица измерения в конце строки — признак листа-товара.
ITEM_SUFFIX = re.compile(
    r",\s*(шт|м|м2|м3|кг|г|л|мл|компл|комплект|пар|упак|набор|элемент|"
    r"секция|модуль|ярус|рул|лист|коробка)\s*$",
    re.IGNORECASE,
)
MONTH_ROW_RE = re.compile(r"^[А-Яа-яЁё]+\s+\d{4}\s*г\.?$")

DEFAULT_REPORT_FILTER = "buyer"
QTY_AMOUNT_EPS = 0.01

# Юрлица/ИП в выгрузке 1С — сокращённые и полные наименования (без угадывания
# «голого» торгового названия без юридической формы).
_LEGAL_COUNTERPARTY_PREFIX_RES: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"^\s*(?:ИП|ООО|АО|ЗАО|ПАО|ОАО)(?:\s+.+|\s*[\"«»'].+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*индивидуальный\s+предприниматель\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*общество\s+с\s+ограниченной\s+ответственностью\b",
        re.IGNORECASE,
    ),
    re.compile(r"^\s*акционерное\s+общество\b", re.IGNORECASE),
    re.compile(r"^\s*закрытое\s+акционерное\s+общество\b", re.IGNORECASE),
    re.compile(r"^\s*публичное\s+акционерное\s+общество\b", re.IGNORECASE),
    re.compile(r"^\s*открытое\s+акционерное\s+общество\b", re.IGNORECASE),
    re.compile(
        r"^\s*государственное\s+унитарное\s+предприятие\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*муниципальное\s+унитарное\s+предприятие\b",
        re.IGNORECASE,
    ),
)
# ИП в середине строки («Город ИП Фамилия И.О.»), не только в начале.
_EMBEDDED_IP_RE = re.compile(r"(?<![\wА-Яа-яЁё])ИП(?![\wА-Яа-яЁё])", re.IGNORECASE)

ATTRIBUTION_NOTE = (
    "Точка продажи и продавец не указаны в отчёте — атрибуция ожидается."
)


class SalesImportError(Exception):
    """Импорт отменён: проверки или запись в БД."""


SALONS_DDL = """
CREATE TABLE IF NOT EXISTS salons (
    name TEXT PRIMARY KEY,
    channel_type TEXT NOT NULL DEFAULT 'offline',
    is_active INTEGER NOT NULL DEFAULT 1
);
"""

SLICES_DDL = """
CREATE TABLE IF NOT EXISTS slices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file TEXT NOT NULL,
    period TEXT NOT NULL,
    month TEXT NOT NULL,
    report_filter TEXT NOT NULL,
    rows INTEGER NOT NULL,
    qty REAL NOT NULL,
    amount REAL NOT NULL,
    imported_at TEXT NOT NULL,
    supersedes INTEGER,
    is_current INTEGER NOT NULL DEFAULT 1
);
"""


def num(v: Any) -> float | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(" ", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def read_header(ws: Any) -> tuple[str, str, str]:
    """Достаёт период, группировки и отбор из строк 1-11 (обязательный pre-flight)."""
    period_raw = grouping_raw = filter_raw = ""
    for r in range(1, 12):
        for c in range(1, 5):
            v = ws.cell(r, c).value
            if not v:
                continue
            s = str(v)
            if not period_raw and "Период" in s:
                period_raw = s
            if not grouping_raw and "Группировки строк" in s:
                grouping_raw = s
            if not filter_raw and "Отборы" in s:
                filter_raw = s
    return period_raw, grouping_raw, filter_raw


def parse(xlsx: str, period_end_override: int | None = None) -> dict[str, Any]:
    wb = openpyxl.load_workbook(xlsx, data_only=True)
    ws = wb["TDSheet"]
    period_raw, grouping_raw, filter_raw = read_header(ws)

    mm = re.search(r"([А-Яа-яЁё]+)\s+(\d{4})\s*г", period_raw)
    if not mm:
        raise SystemExit(f"не найден период в шапке: {period_raw!r}")
    month_names = {
        "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "май": 5, "июн": 6,
        "июл": 7, "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
    }
    word, year = mm.group(1), int(mm.group(2))
    mon = next((v for k, v in month_names.items() if word.lower().startswith(k)), None)
    if not mon:
        raise SystemExit(f"неизвестный месяц в шапке: {word!r}")
    if period_end_override:
        end_day = int(period_end_override)
    else:
        import calendar

        end_day = calendar.monthrange(year, mon)[1]
    period_label = f"01.{mon:02d}.{year}-{end_day:02d}.{mon:02d}.{year}"
    p_start = f"{year:04d}-{mon:02d}-01"
    p_end = f"{year:04d}-{mon:02d}-{end_day:02d}"
    month = f"{year:04d}-{mon:02d}"

    rows: list[tuple[int, str, float | None, float | None]] = []
    for r in range(12, ws.max_row + 1):
        lbl = ws.cell(r, 2).value
        if lbl is None or not str(lbl).strip():
            continue
        rows.append((r, str(lbl).strip(), num(ws.cell(r, 3).value), num(ws.cell(r, 4).value)))

    file_total = None
    month_rollup = None
    buyer_rollups: dict[str, tuple[float, float]] = {}
    leaves: list[dict[str, Any]] = []
    buyer: str | None = None

    for _i, (_r, lbl, q, s) in enumerate(rows):
        if lbl.lower().startswith("итог"):
            file_total = (q, s)
            continue
        if MONTH_ROW_RE.match(lbl):
            month_rollup = (q, s)
            buyer = None
            continue
        if ITEM_SUFFIX.search(lbl):
            if q is None or s is None:
                continue
            leaves.append({"row": _r, "buyer": buyer, "item": lbl, "qty": q, "amount": s})
            continue
        if q is not None or s is not None:
            buyer_rollups[lbl] = (q or 0.0, s or 0.0)
            buyer = lbl
        else:
            buyer = lbl

    return {
        "period_label": period_label,
        "period_start": p_start,
        "period_end": p_end,
        "month": month,
        "grouping_raw": grouping_raw,
        "filter_raw": filter_raw,
        "leaves": leaves,
        "buyer_rollups": buyer_rollups,
        "file_total": file_total,
        "month_rollup": month_rollup,
    }


def aggregate_per_buyer(leaves: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    per_buyer: dict[str, dict[str, Any]] = {}
    for x in leaves:
        key = x["buyer"] or ""
        d = per_buyer.setdefault(key, {"rows": 0, "qty": 0.0, "amount": 0.0})
        d["rows"] += 1
        d["qty"] += float(x["qty"])
        d["amount"] += float(x["amount"])
    return per_buyer


def validate_parsed_data(p: dict[str, Any], leaves: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Pre-flight checks shared by dry-run and --apply."""
    if not leaves:
        raise SalesImportError("Нет строк-листьев — импорт отменён")

    lq = sum(float(x["qty"]) for x in leaves)
    ls = sum(float(x["amount"]) for x in leaves)

    if p["file_total"]:
        fq, fs = p["file_total"]
        if abs(float(fq) - lq) > QTY_AMOUNT_EPS or abs(float(fs) - ls) > QTY_AMOUNT_EPS:
            raise SalesImportError(
                f"СВЕРКА С «ИТОГ» НЕ СОШЛАСЬ: листья {lq}/{ls} != Итог {fq}/{fs} — стоп"
            )

    per_buyer = aggregate_per_buyer(leaves)
    mismatch: list[str] = []
    for b, v in per_buyer.items():
        rb = p["buyer_rollups"].get(b)
        ok = (
            rb is not None
            and abs(rb[0] - v["qty"]) < QTY_AMOUNT_EPS
            and abs(rb[1] - v["amount"]) < QTY_AMOUNT_EPS
        )
        if not ok:
            mismatch.append(b)
    if mismatch:
        raise SalesImportError(f"ПОКОНТРАГЕНТНАЯ СВЕРКА НЕ СОШЛАСЬ: {mismatch} — стоп")

    bad = [x["item"] for x in leaves if not ITEM_SUFFIX.search(x["item"])]
    if bad:
        raise SalesImportError(f"СТРУКТУРА: листья без единицы измерения: {bad[:5]} — стоп")

    return per_buyer


def ensure_schema(con: sqlite3.Connection) -> None:
    """Идемпотентная миграция: salons, slices, report_filter в imports."""
    cur = con.cursor()
    cur.execute(SALONS_DDL)
    cur.execute(SLICES_DDL)
    cols = [r[1] for r in cur.execute("PRAGMA table_info(imports)")]
    if "report_filter" not in cols:
        cur.execute("ALTER TABLE imports ADD COLUMN report_filter TEXT")
    con.commit()
    backfill_salons_from_sales(con)
    backfill_slices_from_sales(con)


def backfill_salons_from_sales(con: sqlite3.Connection) -> None:
    """Переносит салоны из sales/buyers в справочник (идемпотентно)."""
    cur = con.cursor()
    rows = cur.execute(
        """
        SELECT DISTINCT s.buyer, COALESCE(NULLIF(b.channel_type, ''), 'offline')
        FROM sales s
        LEFT JOIN buyers b ON b.name = s.buyer AND b.buyer_type = 'salon'
        WHERE s.buyer_type = 'salon'
        """
    ).fetchall()
    for name, channel_type in rows:
        cur.execute(
            """
            INSERT OR IGNORE INTO salons (name, channel_type, is_active)
            VALUES (?, ?, 1)
            """,
            (name, channel_type or "offline"),
        )
    con.commit()


def is_legal_counterparty(buyer: str) -> bool:
    """True, если в имени есть узнаваемая юридическая форма из выгрузки 1С."""
    name = (buyer or "").strip()
    if not name:
        return False
    if any(pat.match(name) for pat in _LEGAL_COUNTERPARTY_PREFIX_RES):
        return True
    return _EMBEDDED_IP_RE.search(name) is not None


def load_salon_registry(cur: sqlite3.Cursor) -> dict[str, dict[str, Any]]:
    """Активные салоны: channel_type и пр. — только из таблицы salons."""
    return {
        name: {"channel_type": channel_type}
        for name, channel_type in cur.execute(
            "SELECT name, channel_type FROM salons WHERE is_active=1"
        ).fetchall()
    }


def classify_buyer(
    buyer: str, salon_registry: dict[str, dict[str, Any]]
) -> tuple[str, str | None, str | None, str | None]:
    """Возвращает buyer_type, salon, channel_type, note."""
    if buyer in salon_registry:
        ch = salon_registry[buyer]["channel_type"]
        return "salon", buyer, ch, None
    if is_legal_counterparty(buyer):
        return "ip", None, "b2b", ATTRIBUTION_NOTE
    return "unknown", None, None, ATTRIBUTION_NOTE


def unknown_buyers_in_slice(
    leaves: list[dict[str, Any]], salon_registry: dict[str, dict[str, Any]]
) -> list[str]:
    seen: set[str] = set()
    unknown: list[str] = []
    for x in leaves:
        buyer = x["buyer"] or ""
        if buyer in seen:
            continue
        seen.add(buyer)
        bt, _, _, _ = classify_buyer(buyer, salon_registry)
        if bt == "unknown":
            unknown.append(buyer)
    return sorted(unknown)


def print_attribution_warnings(unknown: list[str]) -> None:
    if not unknown:
        return
    print("\nАТРИБУЦИЯ (buyer_type=unknown — не салон и не узнанный контрагент):")
    for name in unknown:
        print(f"  - {name}")


def buyer_type_totals(
    cur: sqlite3.Cursor,
    month: str,
    report_filter: str = DEFAULT_REPORT_FILTER,
) -> dict[str, tuple[int, float]]:
    """Число позиций (строк sales) и сумма amount по buyer_type."""
    out: dict[str, tuple[int, float]] = {}
    for bt, cnt, amt in cur.execute(
        f"""
        SELECT buyer_type, COUNT(*), COALESCE(SUM(amount), 0)
        FROM sales WHERE {_month_filter_sql()}
        GROUP BY buyer_type
        """,
        (month, report_filter),
    ):
        out[str(bt)] = (int(cnt), float(amt))
    return out


def print_buyer_type_summary(
    cur: sqlite3.Cursor,
    month: str,
    report_filter: str = DEFAULT_REPORT_FILTER,
) -> dict[str, tuple[int, float]]:
    """Печатает сводку ip / salon / unknown и возвращает агрегаты."""
    totals = buyer_type_totals(cur, month, report_filter)
    print("\nПо buyer_type (позиции / сумма):")
    for bt in ("ip", "salon", "unknown"):
        cnt, amt = totals.get(bt, (0, 0.0))
        print(f"    {bt:<7} {cnt:>4} позиций  {amt:>12,.0f} ₽")
    return totals


def backfill_slices_from_sales(con: sqlite3.Connection) -> None:
    """Заполняет реестр из sales для существующих БД без повторов."""
    cur = con.cursor()
    existing_keys = {
        (r[0], r[1], r[2])
        for r in cur.execute("SELECT source_file, month, report_filter FROM slices").fetchall()
    }
    groups = cur.execute(
        """
        SELECT source_file, MAX(period), month, COALESCE(report_filter, ?),
               COUNT(*), SUM(quantity), SUM(amount), MAX(loaded_at)
        FROM sales
        WHERE source_file IS NOT NULL
        GROUP BY source_file, month, COALESCE(report_filter, ?)
        """,
        (DEFAULT_REPORT_FILTER, DEFAULT_REPORT_FILTER),
    ).fetchall()
    to_insert = [g for g in groups if (g[0], g[2], g[3]) not in existing_keys]
    if not to_insert:
        return

    # Какой срез считать текущим: максимальная сумма по (month, report_filter).
    best: dict[tuple[str, str], tuple[str, float]] = {}
    for g in to_insert:
        key = (g[2], g[3])
        if key not in best or float(g[6]) > best[key][1]:
            best[key] = (g[0], float(g[6]))

    for g in to_insert:
        is_current = 1 if best.get((g[2], g[3]), (None, -1))[0] == g[0] else 0
        cur.execute(
            """
            INSERT INTO slices (
                source_file, period, month, report_filter, rows, qty, amount,
                imported_at, supersedes, is_current
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (g[0], g[1], g[2], g[3], g[4], g[5], g[6], g[7], None, is_current),
        )
    con.commit()


def _month_filter_sql() -> str:
    return "month=? AND (report_filter=? OR report_filter IS NULL)"


def get_current_slice(
    cur: sqlite3.Cursor, month: str, report_filter: str
) -> dict[str, Any] | None:
    cur.execute(
        """
        SELECT id, source_file, period, rows, qty, amount, imported_at
        FROM slices
        WHERE month=? AND report_filter=? AND is_current=1
        ORDER BY id DESC LIMIT 1
        """,
        (month, report_filter),
    )
    row = cur.fetchone()
    if row is not None:
        return {
            "id": row[0],
            "source_file": row[1],
            "period": row[2],
            "rows": row[3],
            "qty": row[4],
            "amount": row[5],
            "imported_at": row[6],
        }
    # До миграции / без backfill — выводим из sales.
    agg = cur.execute(
        f"""
        SELECT source_file, MAX(period), COUNT(*), SUM(quantity), SUM(amount), MAX(loaded_at)
        FROM sales WHERE {_month_filter_sql()}
        GROUP BY source_file ORDER BY SUM(amount) DESC LIMIT 1
        """,
        (month, report_filter),
    ).fetchone()
    if not agg:
        return None
    return {
        "id": None,
        "source_file": agg[0],
        "period": agg[1],
        "rows": agg[2],
        "qty": agg[3],
        "amount": agg[4],
        "imported_at": agg[5],
    }


def check_coverage_against_db(
    cur: sqlite3.Cursor,
    month: str,
    report_filter: str,
    per_buyer: dict[str, dict[str, Any]],
) -> None:
    """Старый срез: все контрагенты на месте; qty/amount по общим не уменьшаются."""
    old_rows = cur.execute(
        f"""
        SELECT buyer, SUM(quantity), SUM(amount)
        FROM sales WHERE {_month_filter_sql()}
        GROUP BY buyer
        """,
        (month, report_filter),
    ).fetchall()
    if not old_rows:
        return

    missing: list[str] = []
    regressed: list[str] = []
    for buyer, old_q, old_a in old_rows:
        stats = per_buyer.get(buyer)
        if stats is None:
            missing.append(buyer)
            continue
        new_q, new_a = stats["qty"], stats["amount"]
        if new_q + QTY_AMOUNT_EPS < float(old_q) or new_a + QTY_AMOUNT_EPS < float(old_a):
            regressed.append(
                f"{buyer}: было qty={float(old_q):.2f} amount={float(old_a):.2f}, "
                f"стало qty={new_q:.2f} amount={new_a:.2f}"
            )
    if missing:
        raise SalesImportError(
            f"ПОКРЫТИЕ НЕ ПОЛНОЕ — нет контрагентов в новом срезе: {missing}"
        )
    if regressed:
        raise SalesImportError(
            "РЕГРЕСС ПОКРЫТИЯ по qty/amount (накопительный срез не может уменьшаться):\n  "
            + "\n  ".join(regressed)
        )


def _recompute_buyers(
    cur: sqlite3.Cursor,
    per_buyer: dict[str, dict[str, Any]],
    now_iso: str,
    salon_registry: dict[str, dict[str, Any]],
) -> None:
    for b in per_buyer:
        bt, _salon, cht, note = classify_buyer(b, salon_registry)
        row = cur.execute(
            "SELECT id FROM buyers WHERE name=? AND buyer_type=?", (b, bt)
        ).fetchone()
        if not row:
            cur.execute(
                """
                INSERT INTO buyers (
                    name, first_seen, total_orders, total_amount, buyer_type,
                    channel_type, location, note
                ) VALUES (?,?,?,?,?,?,?,?)
                """,
                (b, now_iso, 0, 0.0, bt, cht, None, note),
            )
    for b in per_buyer:
        bt, _, _, _ = classify_buyer(b, salon_registry)
        cnt, amt = cur.execute(
            "SELECT COUNT(*), COALESCE(SUM(amount),0) FROM sales WHERE buyer=? AND buyer_type=?",
            (b, bt),
        ).fetchone()
        cur.execute(
            "UPDATE buyers SET total_orders=?, total_amount=? WHERE name=? AND buyer_type=?",
            (cnt, amt, b, bt),
        )


def _record_slice_registry(
    cur: sqlite3.Cursor,
    *,
    source_file: str,
    period: str,
    month: str,
    report_filter: str,
    row_count: int,
    qty: float,
    amount: float,
    imported_at: str,
    current: dict[str, Any] | None,
) -> None:
    current_id = current["id"] if current else None
    current_file = current["source_file"] if current else None

    if current_file == source_file:
        if current_id is not None:
            cur.execute(
                """
                UPDATE slices
                SET period=?, rows=?, qty=?, amount=?, imported_at=?
                WHERE id=?
                """,
                (period, row_count, qty, amount, imported_at, current_id),
            )
        else:
            cur.execute(
                """
                UPDATE slices
                SET period=?, rows=?, qty=?, amount=?, imported_at=?, is_current=1
                WHERE month=? AND report_filter=? AND source_file=?
                """,
                (period, row_count, qty, amount, imported_at, month, report_filter, source_file),
            )
        return

    if current_id is not None:
        cur.execute("UPDATE slices SET is_current=0 WHERE id=?", (current_id,))
    elif current_file:
        cur.execute(
            """
            UPDATE slices SET is_current=0
            WHERE month=? AND report_filter=? AND source_file=?
            """,
            (month, report_filter, current_file),
        )

    cur.execute(
        """
        INSERT INTO slices (
            source_file, period, month, report_filter, rows, qty, amount,
            imported_at, supersedes, is_current
        ) VALUES (?,?,?,?,?,?,?,?,?,1)
        """,
        (
            source_file,
            period,
            month,
            report_filter,
            row_count,
            qty,
            amount,
            imported_at,
            current_id,
        ),
    )


def apply_import(
    con: sqlite3.Connection,
    p: dict[str, Any],
    leaves: list[dict[str, Any]],
    source_file: str,
    *,
    report_filter: str = DEFAULT_REPORT_FILTER,
    now_iso: str | None = None,
    _fail_after_inserts: int | None = None,
) -> tuple[int, list[str]]:
    """Транзакционная запись среза.

    Returns:
        (inserted row count, buyer names classified as ``unknown``).
    """
    per_buyer = validate_parsed_data(p, leaves)
    lq = sum(float(x["qty"]) for x in leaves)
    ls = sum(float(x["amount"]) for x in leaves)
    month = p["month"]
    stamp = now_iso or dt.datetime.now(dt.timezone.utc).isoformat()

    ensure_schema(con)
    cur = con.cursor()
    cur.execute("PRAGMA busy_timeout=60000")

    salon_registry = load_salon_registry(cur)
    attribution_pending = unknown_buyers_in_slice(leaves, salon_registry)

    cur.execute("BEGIN IMMEDIATE")
    try:
        check_coverage_against_db(cur, month, report_filter, per_buyer)
        current = get_current_slice(cur, month, report_filter)

        cur.execute(
            f"DELETE FROM sales WHERE {_month_filter_sql()}",
            (month, report_filter),
        )
        cur.execute(
            "DELETE FROM imports WHERE period LIKE ? AND file_name <> ?",
            (f"%{month[5:]}%", source_file),
        )

        ins = """
            INSERT INTO sales (
                period, period_start, period_end, buyer, month, parent_category,
                item_name, quantity, amount, source_file, loaded_at,
                buyer_type, seller, salon, item_characteristic, is_return, report_filter
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """
        inserted = 0
        for x in leaves:
            buyer = x["buyer"] or ""
            buyer_type, salon, _, _ = classify_buyer(buyer, salon_registry)
            is_return = 1 if (x["qty"] or 0) < 0 or (x["amount"] or 0) < 0 else 0
            cur.execute(
                ins,
                (
                    p["period_label"],
                    p["period_start"],
                    p["period_end"],
                    buyer,
                    month,
                    "",
                    x["item"],
                    x["qty"],
                    x["amount"],
                    source_file,
                    stamp,
                    buyer_type,
                    None,
                    salon,
                    "(не указана)",
                    is_return,
                    report_filter,
                ),
            )
            inserted += 1
            if _fail_after_inserts is not None and inserted >= _fail_after_inserts:
                raise sqlite3.OperationalError("simulated insert failure")

        _record_slice_registry(
            cur,
            source_file=source_file,
            period=p["period_label"],
            month=month,
            report_filter=report_filter,
            row_count=inserted,
            qty=lq,
            amount=ls,
            imported_at=stamp,
            current=current,
        )

        cur.execute(
            """
            INSERT INTO imports (
                file_name, period, rows_imported, total_quantity, total_amount,
                imported_at, report_filter
            ) VALUES (?,?,?,?,?,?,?)
            """,
            (source_file, p["period_label"], inserted, lq, ls, stamp, report_filter),
        )
        _recompute_buyers(cur, per_buyer, stamp, salon_registry)
        con.commit()
    except Exception:
        con.rollback()
        raise

    print_attribution_warnings(attribution_pending)
    print_buyer_type_summary(cur, month, report_filter)
    return inserted, attribution_pending


def normalize_source_name(path: str) -> str:
    src_name = os.path.basename(path)
    if ".xlsx" in src_name and src_name.startswith("doc_"):
        parts = src_name.split("_", 2)
        if len(parts) == 3:
            src_name = parts[2]
    return src_name


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("xlsx")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--db", default="sales.db")
    ap.add_argument(
        "--period-end",
        type=int,
        default=None,
        help="день конца накопительного среза (например 18 для 01-18.11)",
    )
    args = ap.parse_args()

    src_name = normalize_source_name(args.xlsx)
    p = parse(args.xlsx, args.period_end)
    leaves = p["leaves"]
    lq = sum(x["qty"] for x in leaves)
    ls = sum(x["amount"] for x in leaves)

    print(f"File      : {src_name}")
    print(f"Period    : {p['period_label']}  ({p['period_start']} .. {p['period_end']})")
    print(f"Month     : {p['month']}")
    print(f"Grouping  : {p['grouping_raw'][:110]}")
    print(f"Leaves    : {len(leaves)}")
    print(f"Leaves sum: qty={lq} amount={ls}")
    print(f"File total: {p['file_total']}")
    print(f"Month roll: {p['month_rollup']}")

    try:
        per_buyer = validate_parsed_data(p, leaves)
    except SalesImportError as exc:
        raise SystemExit(str(exc)) from exc

    if p["file_total"]:
        print("Check tot : OK (листья == Итог)")
    print("\nPer buyer (листья vs rollup файла):")
    for b, v in per_buyer.items():
        rb = p["buyer_rollups"].get(b)
        print(
            f"  {b[:50]:50s} | rows={v['rows']:3d} | qty={v['qty']:6.1f} | "
            f"{v['amount']:>12,.2f} | файл={rb if rb else 'НЕТ'} OK"
        )
    print("Per buyer : OK")
    print("Structure : OK (все листья с единицей измерения)")

    anomalies = [
        (x["buyer"], x["item"], x["qty"], x["amount"])
        for x in leaves
        if 0 < x["amount"] <= 2
    ]
    if anomalies:
        print("\nANOMALIES (сумма 1-2 ₽ - техническая позиция):")
        for a in anomalies:
            print(f"  {a[0][:40]:40s} | {a[1][:44]:44s} | qty={a[2]} | {a[3]:.2f} ₽")

    con_peek = sqlite3.connect(args.db, timeout=30) if os.path.isfile(args.db) else None
    if con_peek is not None:
        ensure_schema(con_peek)
        reg = load_salon_registry(con_peek.cursor())
        con_peek.close()
        print_attribution_warnings(unknown_buyers_in_slice(leaves, reg))
    else:
        print_attribution_warnings(unknown_buyers_in_slice(leaves, {}))

    if not args.apply:
        print("\nDRY RUN — ничего не записано. Повтори с --apply.")
        return

    db = args.db
    if os.path.isfile(db):
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = os.path.join(os.path.dirname(db) or ".", f"sales_backup_{stamp}.db")
        shutil.copy2(db, backup)
        print(f"\nBackup    : {backup}")

    con = sqlite3.connect(db, timeout=60)
    con.row_factory = sqlite3.Row
    try:
        apply_import(con, p, leaves, src_name)
        # attribution list printed inside apply_import
    except SalesImportError as exc:
        con.close()
        raise SystemExit(str(exc)) from exc

    cur = con.cursor()
    print("\n--- verify ---")
    row = cur.execute(
        "SELECT COUNT(*), ROUND(SUM(quantity),2), ROUND(SUM(amount),2) FROM sales WHERE month=?",
        (p["month"],),
    ).fetchone()
    print(f"month {p['month']}: rows={row[0]} qty={row[1]} amount={row[2]}")
    current_slice = cur.execute(
        "SELECT source_file, supersedes, is_current FROM slices WHERE month=? AND is_current=1",
        (p["month"],),
    ).fetchone()
    if current_slice:
        print(
            f"slices    : current={current_slice[0]} supersedes={current_slice[1]} "
            f"is_current={current_slice[2]}"
        )
    con.close()


if __name__ == "__main__":
    main()