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
  - покрытие старого среза проверяется ДО удаления (по контрагентам);
  - накопительный срез ЗАМЕНЯЕТ предыдущий срез того же месяца и периметра;
  - идемпотентность: повторный прогон даёт те же цифры.

Usage:
  python import_sales_3level.py <file.xlsx> [--apply] [--db PATH]
"""
import argparse
import datetime as dt
import os
import re
import shutil
import sqlite3

import openpyxl

# Единица измерения в конце строки — признак листа-товара.
ITEM_SUFFIX = re.compile(
    r",\s*(шт|м|м2|м3|кг|г|л|мл|компл|комплект|пар|упак|набор|элемент|"
    r"секция|модуль|ярус|рул|лист|коробка)\s*$",
    re.IGNORECASE,
)
MONTH_ROW_RE = re.compile(r"^[А-Яа-яЁё]+\s+\d{4}\s*г\.?$")

# Розничные салоны -> их собственная точка продаж (salon = buyer).
KNOWN_SALONS = {
    "Салон Север": "Салон Север",
    "Салон Запад": "Салон Запад",
    "Салон Центр": "Салон Центр",
}


def num(v):
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(" ", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def read_header(ws):
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


def parse(xlsx, period_end_override=None):
    wb = openpyxl.load_workbook(xlsx, data_only=True)
    ws = wb["TDSheet"]
    period_raw, grouping_raw, filter_raw = read_header(ws)

    # Период: строка 2 = «Ноябрь 2026 г.» -> помесячный срез.
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
    # Конец среза: по умолчанию — последний день месяца; можно переопределить
    # (для накопительного среза «на текущую дату»).
    if period_end_override:
        end_day = int(period_end_override)
    else:
        import calendar
        end_day = calendar.monthrange(year, mon)[1]
    period_label = f"01.{mon:02d}.{year}-{end_day:02d}.{mon:02d}.{year}"
    p_start = f"{year:04d}-{mon:02d}-01"
    p_end = f"{year:04d}-{mon:02d}-{end_day:02d}"
    month = f"{year:04d}-{mon:02d}"

    # Строки данных: подпись в колонке B, количество в C, сумма в D.
    rows = []
    for r in range(12, ws.max_row + 1):
        lbl = ws.cell(r, 2).value
        if lbl is None or not str(lbl).strip():
            continue
        rows.append((r, str(lbl).strip(), num(ws.cell(r, 3).value), num(ws.cell(r, 4).value)))

    file_total = None
    month_rollup = None
    buyer_rollups = {}   # имя -> (qty, amount)
    leaves = []
    buyer = None

    for i, (r, lbl, q, s) in enumerate(rows):
        if lbl.lower().startswith("итог"):
            file_total = (q, s)
            continue
        if MONTH_ROW_RE.match(lbl):
            # Строка месяца: в этом варианте она одна, вверху, и несёт итог.
            month_rollup = (q, s)
            buyer = None
            continue
        if ITEM_SUFFIX.search(lbl):
            # Лист-товар.
            if q is None or s is None:
                continue
            leaves.append({"row": r, "buyer": buyer, "item": lbl, "qty": q, "amount": s})
            continue
        # Не лист: это контрагент (rollup) — уровня «Родитель» в файле нет.
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("xlsx")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--db", default="sales.db")
    ap.add_argument("--period-end", type=int, default=None,
                    help="день конца накопительного среза (например 18 для 01-18.11)")
    args = ap.parse_args()

    src_name = os.path.basename(args.xlsx)
    # Hermes кладёт файлы как doc_<hash>_<оригинал>.xlsx -> в source_file только
    # базовое имя, иначе дедупликация по source_file ломается.
    if ".xlsx" in src_name and src_name.startswith("doc_"):
        parts = src_name.split("_", 2)
        if len(parts) == 3:
            src_name = parts[2]

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

    if not leaves:
        raise SystemExit("Нет строк-листьев — импорт отменён")

    # 1) сверка со строкой «Итог»
    if p["file_total"]:
        fq, fs = p["file_total"]
        if abs(fq - lq) > 0.01 or abs(fs - ls) > 0.01:
            raise SystemExit(f"СВЕРКА С «ИТОГ» НЕ СОШЛАСЬ: листья {lq}/{ls} != Итог {fq}/{fs} — стоп")
        print("Check tot : OK (листья == Итог)")

    # 2) поконтрагентная сверка: сумма листьев == rollup контрагента в файле
    per_buyer = {}
    for x in leaves:
        d = per_buyer.setdefault(x["buyer"], {"rows": 0, "qty": 0.0, "amount": 0.0})
        d["rows"] += 1
        d["qty"] += x["qty"]
        d["amount"] += x["amount"]
    print("\nPer buyer (листья vs rollup файла):")
    mismatch = []
    for b, v in per_buyer.items():
        rb = p["buyer_rollups"].get(b)
        ok = rb and abs(rb[0] - v["qty"]) < 0.01 and abs(rb[1] - v["amount"]) < 0.01
        if not ok:
            mismatch.append(b)
        print(f"  {b[:50]:50s} | rows={v['rows']:3d} | qty={v['qty']:6.1f} | "
              f"{v['amount']:>12,.2f} | файл={rb if rb else 'НЕТ'} {'OK' if ok else 'РАСХОЖДЕНИЕ'}")
    if mismatch:
        raise SystemExit(f"ПОКОНТРАГЕНТНАЯ СВЕРКА НЕ СОШЛАСЬ: {mismatch} — стоп")
    print("Per buyer : OK")

    # 3) структурная проверка: каждый лист с единицей измерения
    bad = [x["item"] for x in leaves if not ITEM_SUFFIX.search(x["item"])]
    if bad:
        raise SystemExit(f"СТРУКТУРА: листья без единицы измерения: {bad[:5]} — стоп")
    print("Structure : OK (все листья с единицей измерения)")

    # 4) аномалии: технические позиции с суммой 1-2 рубля
    anomalies = [(x["buyer"], x["item"], x["qty"], x["amount"])
                 for x in leaves if 0 < x["amount"] <= 2]
    if anomalies:
        print("\nANOMALIES (сумма 1-2 ₽ - техническая позиция):")
        for a in anomalies:
            print(f"  {a[0][:40]:40s} | {a[1][:44]:44s} | qty={a[2]} | {a[3]:.2f} ₽")

    if not args.apply:
        print("\nDRY RUN — ничего не записано. Повтори с --apply.")
        return

    db = args.db
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = os.path.join(os.path.dirname(db), f"sales_backup_{stamp}.db")
    shutil.copy2(db, backup)
    print(f"\nBackup    : {backup}")

    now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
    rf = "buyer"   # группировка по контрагентам

    con = sqlite3.connect(db, timeout=60)
    cur = con.cursor()
    cur.execute("PRAGMA busy_timeout=60000")

    # 5) ПОКРЫТИЕ старого среза проверяем ДО удаления (по контрагентам).
    old = cur.execute(
        "SELECT source_file, COUNT(*), ROUND(SUM(quantity),2), ROUND(SUM(amount),2) "
        "FROM sales WHERE month=? AND (report_filter=? OR report_filter IS NULL) "
        "GROUP BY source_file", (p["month"], rf)).fetchall()
    print(f"\nСтарые срезы за {p['month']} (периметр {rf}):")
    for r in old:
        print(f"  {r[0]}  rows={r[1]} qty={r[2]} amount={r[3]}")
    if old:
        old_buyers = cur.execute(
            "SELECT DISTINCT buyer FROM sales WHERE month=? AND (report_filter=? OR report_filter IS NULL)",
            (p["month"], rf)).fetchall()
        new_buyers = set(per_buyer)
        not_covered = [b[0] for b in old_buyers if b[0] not in new_buyers]
        if not_covered:
            print(f"  НЕ ПОКРЫТЫ новым срезом: {not_covered}")
            raise SystemExit("ПОКРЫТИЕ НЕ ПОЛНОЕ — старый срез не удаляем, импорт отменён")
        print("  Покрытие по контрагентам: ПОЛНОЕ")

    # 6) удаляем прежний срез этого месяца (он короче) + его строку в imports.
    for r in old:
        cur.execute("DELETE FROM sales WHERE source_file=? AND month=?", (r[0], p["month"]))
        print(f"Removing  : source_file={r[0]} rows={r[1]}")
    cur.execute("DELETE FROM imports WHERE period LIKE ? AND file_name <> ?", (f"%{p['month'][5:]}%", src_name))
    cur.execute("DELETE FROM sales WHERE month=? AND source_file<>? AND (report_filter=? OR report_filter IS NULL)",
                (p["month"], src_name, rf))
    deleted = cur.rowcount
    print(f"Removed   : {deleted} rows of month {p['month']}")

    # 7) вставляем новые строки
    ins = """
        INSERT INTO sales (period, period_start, period_end, buyer, month, parent_category,
                           item_name, quantity, amount, source_file, loaded_at,
                           buyer_type, seller, salon, item_characteristic, is_return, report_filter)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """
    inserted = 0
    for x in leaves:
        buyer = x["buyer"] or ""
        salon = KNOWN_SALONS.get(buyer)
        buyer_type = "salon" if salon else "ip"
        is_return = 1 if (x["qty"] or 0) < 0 or (x["amount"] or 0) < 0 else 0
        cur.execute(ins, (
            p["period_label"], p["period_start"], p["period_end"],
            buyer, p["month"], "", x["item"], x["qty"], x["amount"],
            src_name, now_iso, buyer_type, None, salon, "(не указана)", is_return, rf,
        ))
        inserted += 1
    print(f"Inserted  : {inserted} rows")

    # 8) таблица imports
    cols = [r[1] for r in cur.execute("PRAGMA table_info(imports)")]
    if "report_filter" not in cols:
        cur.execute("ALTER TABLE imports ADD COLUMN report_filter TEXT")
    cur.execute(
        "INSERT INTO imports (file_name, period, rows_imported, total_quantity, total_amount, imported_at, report_filter)"
        " VALUES (?,?,?,?,?,?,?)",
        (src_name, p["period_label"], inserted, lq, ls, now_iso, rf))
    print(f"Imports   : recorded {src_name}")

    # 9) buyers: новые контрагенты + пересчёт агрегатов (с фильтром buyer_type!)
    for b in per_buyer:
        row = cur.execute("SELECT id FROM buyers WHERE name=? AND buyer_type=?",
                          (b, "salon" if KNOWN_SALONS.get(b) else "ip")).fetchone()
        if not row:
            salon = KNOWN_SALONS.get(b)
            bt = "salon" if salon else "ip"
            if salon:
                note, cht = None, ("online" if salon == "Салон Север"
                                   else "mixed" if salon == "Салон Запад" else "offline")
            else:
                note = ("Точка продажи и продавец не указаны в отчёте — атрибуция ожидается.")
                cht = "b2b"
            cur.execute(
                "INSERT INTO buyers (name, first_seen, total_orders, total_amount, buyer_type,"
                " channel_type, location, note) VALUES (?,?,?,?,?,?,?,?)",
                (b, now_iso, 0, 0.0, bt, cht, None, note))
            print(f"Buyer new : {b} ({bt})")
    for b in per_buyer:
        bt = "salon" if KNOWN_SALONS.get(b) else "ip"
        cnt, amt = cur.execute(
            "SELECT COUNT(*), COALESCE(SUM(amount),0) FROM sales WHERE buyer=? AND buyer_type=?",
            (b, bt)).fetchone()
        cur.execute("UPDATE buyers SET total_orders=?, total_amount=? WHERE name=? AND buyer_type=?",
                    (cnt, amt, b, bt))
    print(f"Buyers    : aggregates recomputed for {len(per_buyer)} buyers")

    con.commit()

    # 10) проверки после записи
    print("\n--- verify ---")
    row = cur.execute(
        "SELECT COUNT(*), ROUND(SUM(quantity),2), ROUND(SUM(amount),2) FROM sales WHERE month=?",
        (p["month"],)).fetchone()
    print(f"month {p['month']}: rows={row[0]} qty={row[1]} amount={row[2]}")
    for r in cur.execute(
        "SELECT buyer, COUNT(*), ROUND(SUM(quantity),1), ROUND(SUM(amount),2) FROM sales"
        " WHERE month=? GROUP BY buyer ORDER BY 4 DESC", (p["month"],)):
        print(f"   {r[0][:52]:52s} | {r[1]:3d} | {r[2]:6.1f} | {r[3]:>12,.2f}")
    bad = cur.execute(
        "SELECT COUNT(*) FROM buyers b WHERE b.total_orders <> ("
        "  SELECT COUNT(*) FROM sales s WHERE s.buyer=b.name AND s.buyer_type=b.buyer_type)"
        " OR ABS(b.total_amount - (SELECT COALESCE(SUM(amount),0) FROM sales s"
        "  WHERE s.buyer=b.name AND s.buyer_type=b.buyer_type)) > 0.01").fetchone()[0]
    print(f"buyers aggregate mismatch: {bad} (должно быть 0)")
    ic = list(cur.execute("PRAGMA integrity_check"))[0][0]
    print(f"integrity_check: {ic}")
    con.close()


if __name__ == "__main__":
    main()
