#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Анализ продаж по номенклатуре за месяц.

Ассортиментные группы выводятся ИЗ НОМЕНКЛАТУРЫ (в выгрузке 1С поля
«Ассортиментная группа» нет). Порядок проверки префиксов — от специфичного
к общему, иначе «диван-кровать» попадёт в «диваны».

ПРОВЕРКА ПОКРЫТИЯ обязательна: новый товар может не попасть ни в одну
группу и молча выпасть из оборота.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import os
import sqlite3
import sys
from collections import defaultdict
from typing import Any

_SUPPLIER_GROUP = 'Прочее (поставщики/техника)'

# --- расширения под ноябрьский срез 01-18.11.2026 -------------------------
NEW_PREFIXES = {
    # оттоманка - часть модульного дивана, идёт в диваны
    'оттоманка': 'Диваны',
    # софа - разновидность дивана
    'софа': 'Диваны',
    # полукресло - разновидность кресла
    'полукресло': 'Кресла',
    # подлокотник - комплектующая модульных систем
    'подлокотник': 'Аксессуары и комплекты',
    # диван-кровать с дефисом/пробелом (варианты написания в 1С)
    'диван -кровать': 'Диван-кровати',
    'диван кровать': 'Диван-кровати',
    'диван-кровать': 'Диван-кровати',
}


def _parent_suggests_supplier(parent: str | None) -> bool:
    """Fallback для старых срезов без buyer_type на строке продажи."""
    if not parent:
        return False
    low = parent.lower()
    return 'поставщ' in low or low.startswith('техник')


def is_supplier_sale(
    buyer_type: str | None,
    parent: str | None,
    supplier_buyer_names: frozenset[str] | None = None,
) -> bool:
    """Поставщик определяется по buyer_type и справочнику buyers, не по захардкоженным именам."""
    if buyer_type == 'supplier':
        return True
    if supplier_buyer_names and parent and parent in supplier_buyer_names:
        return True
    return _parent_suggests_supplier(parent)


def group_of(
    item: str,
    parent: str | None = None,
    *,
    buyer_type: str | None = None,
    supplier_buyer_names: frozenset[str] | None = None,
) -> str:
    """Торговая классификация позиции по её названию."""
    s = item.strip()
    low = s.lower()
    if is_supplier_sale(buyer_type, parent, supplier_buyer_names):
        return _SUPPLIER_GROUP
    if s == "Ткань, м":
        return 'Ткани'
    if '"Цветы"' in s or s.startswith('№'):
        return 'Искусственные цветы'
    # --- специфичное раньше общего ---
    for pref, grp in NEW_PREFIXES.items():
        if low.startswith(pref):
            return grp
    if low.startswith('диван-кровать') or low.startswith('диван кровать'):
        return 'Диван-кровати'
    if low.startswith('диван'):
        return 'Диваны'
    if low.startswith('кровать'):
        return 'Кровати'
    if low.startswith('кресло'):
        return 'Кресла'
    if low.startswith('банкетка'):
        return 'Банкетки'
    if low.startswith('консоль'):
        return 'Консоли'
    if low.startswith('прикроватная тумбочка'):
        return 'Тумбочки'
    if low.startswith('стол кухонный'):
        return 'Кухонные столы'
    if low.startswith('стол'):
        return 'Столы'
    if low.startswith('стул'):
        return 'Стулья'
    if low.startswith('кушетка'):
        return 'Кушетки'
    if low.startswith('матрац') or low.startswith('матрас'):
        return 'Матрасы'
    if low.startswith('зеркало'):
        return 'Зеркала'
    if low.startswith('подставка'):
        return 'Подставки'
    if low.startswith('комплект'):
        return 'Аксессуары и комплекты'
    if low.startswith('подушк') or low.startswith('декоратвная подушка'):
        return 'Подушки'
    if low.startswith('покрывало'):
        return 'Покрывала'
    if low.startswith('тумба'):
        return 'Тумбы'
    if 'монреаль' in low:
        return 'Монреаль (системы)'
    return 'ПРОЧЕЕ: ' + s[:40]


def brand_of(item: str) -> str:
    """Бренд извлекается из названия товара."""
    low = item.lower()
    if 'амадей' in low:
        return 'Амадей'
    if 'монреаль' in low:
        return 'Монреаль'
    if 'аризона' in low:
        return 'Аризона'
    if 'николети' in low or 'николетти' in low:
        return 'Николетти'
    if 'империал' in low or 'империя' in low:
        return 'Империя'
    if 'барокко' in low:
        return 'Барокко'
    if 'престиж' in low:
        return 'Престиж'
    if 'марсель' in low:
        return 'Марсель'
    if 'селва' in low:
        return 'Селва'
    if 'сенатор' in low:
        return 'Сенатор'
    if 'ветторе' in low:
        return 'Ветторе'
    if 'эверест' in low:
        return 'Эверест'
    if 'лагуна' in low:
        return 'Лагуна'
    if 'меркуриус' in low or 'тресла' in low:
        return 'Mercurius'
    if 'венеция' in low:
        return 'Венеция'
    if 'сканди' in low:
        return 'Сканди'
    if 'лорд' in low:
        return 'Лорд'
    if 'major' in low:
        return 'Major'
    if 'актив люкс' in low:
        return 'Актив Люкс'
    if 'хард' in low:
        return 'Хард'
    if 'британия' in low:
        return 'Британия'
    return '(прочее)'


def period_days(period: str) -> int:
    """Число дней в периоде вида '01.11.2026-18.11.2026'.

    У даты тоже есть дефис-разделитель — режем по '-' только один раз.
    Короткий формат ``YYYY-MM`` — число дней в календарном месяце.
    """
    if "." in period and period.count(".") >= 4:
        a, b = period.split("-", 1)
        d1 = dt.date(*map(int, a.split(".")[::-1]))
        d2 = dt.date(*map(int, b.split(".")[::-1]))
        return (d2 - d1).days + 1
    if len(period) == 7 and period[4] == "-":
        year, month = map(int, period.split("-"))
        start = dt.date(year, month, 1)
        if month == 12:
            end = dt.date(year + 1, 1, 1)
        else:
            end = dt.date(year, month + 1, 1)
        return (end - start).days
    raise ValueError(f"unrecognized period format: {period!r}")


def _prev_month(month: str) -> str:
    year_s, mon_s = month.split("-", 1)
    year, mon = int(year_s), int(mon_s)
    if mon == 1:
        return f"{year - 1}-12"
    return f"{year}-{mon - 1:02d}"


def _load_month_slice(
    cur: sqlite3.Cursor, month: str
) -> tuple[str, str, int, float, float, list[tuple[Any, ...]]]:
    slices = cur.execute(
        "SELECT source_file, period, COUNT(*), SUM(quantity), SUM(amount) FROM sales"
        " WHERE month=? GROUP BY source_file, period ORDER BY SUM(amount) DESC",
        (month,),
    ).fetchall()
    if not slices:
        raise ValueError(f"нет срезов за месяц {month}")
    src, period, row_count, qty, amount = slices[0]
    rows = cur.execute(
        "SELECT item_name, COALESCE(parent_category,''), buyer, COALESCE(salon,''), "
        "buyer_type, quantity, amount, is_return FROM sales WHERE source_file=?",
        (src,),
    ).fetchall()
    return src, period, row_count, qty or 0.0, amount or 0.0, rows


def _load_supplier_buyer_names(cur: sqlite3.Cursor) -> frozenset[str]:
    try:
        rows = cur.execute(
            "SELECT name FROM buyers WHERE buyer_type='supplier'",
        ).fetchall()
    except sqlite3.Error:
        return frozenset()
    return frozenset(r[0] for r in rows)


def _group_totals_from_rows(
    rows: list[tuple[Any, ...]],
    supplier_buyer_names: frozenset[str],
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, dict[str, float]],
    list[str],
]:
    groups: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"items": {}, "qty": 0.0, "amt": 0.0, "pos": 0}
    )
    brands: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"qty": 0.0, "amt": 0.0, "pos": 0, "items": set()}
    )
    cross: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    unassigned: list[str] = []

    for item, parent, buyer, salon, btype, q, a, _is_ret in rows:
        g = group_of(
            item,
            parent,
            buyer_type=btype,
            supplier_buyer_names=supplier_buyer_names,
        )
        if g.startswith("ПРОЧЕЕ"):
            unassigned.append(item)
        d = groups[g]
        d["items"][item] = d["items"].get(item, 0.0) + a
        d["qty"] += q
        d["amt"] += a
        d["pos"] += 1
        b = brand_of(item)
        brands[b]["qty"] += q
        brands[b]["amt"] += a
        brands[b]["pos"] += 1
        brands[b]["items"].add(item)
        ch = salon or buyer
        cross[g][ch] += a

    return groups, brands, cross, unassigned


def analyze(
    db_path: str,
    month: str,
    compare_month: str | None = None,
) -> dict[str, Any]:
    """Собрать структуру отчёта по номенклатуре за месяц."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30)
    cur = con.cursor()

    supplier_buyer_names = _load_supplier_buyer_names(cur)
    src, period, row_count, slice_qty, slice_total, rows = _load_month_slice(cur, month)
    slice_info = {
        "source_file": src,
        "period": period,
        "rows": row_count,
        "qty": slice_qty,
        "amount": slice_total,
    }

    groups, brands, cross, unassigned = _group_totals_from_rows(rows, supplier_buyer_names)
    n_unique = len({r[0] for r in rows})
    n_assigned = sum(len(v["items"]) for v in groups.values())
    groups_sum = sum(v["amt"] for v in groups.values())
    sum_ok = abs(groups_sum - slice_total) < 0.01 and not unassigned and n_assigned == n_unique

    total_amt = groups_sum

    group_list = []
    for name, v in sorted(groups.items(), key=lambda x: -x[1]["amt"]):
        share = v["amt"] / total_amt * 100 if total_amt else 0.0
        avg = v["amt"] / v["qty"] if v["qty"] else 0.0
        group_list.append({
            "name": name,
            "positions": v["pos"],
            "models": len(v["items"]),
            "qty": v["qty"],
            "amount": v["amt"],
            "share_pct": share,
            "avg_per_unit": avg,
        })

    cross_table: dict[str, dict[str, float]] = {
        g: dict(channels) for g, channels in cross.items()
    }

    brand_list = []
    for name, v in sorted(brands.items(), key=lambda x: -x[1]["amt"]):
        brand_list.append({
            "name": name,
            "positions": v["pos"],
            "models": len(v["items"]),
            "qty": v["qty"],
            "amount": v["amt"],
            "share_pct": v["amt"] / total_amt * 100 if total_amt else 0.0,
        })

    items_sorted = sorted(
        (amt for g in groups.values() for amt in g["items"].values()),
        reverse=True,
    )
    concentration: dict[str, float] = {}
    for n in (1, 3, 5, 10):
        s = sum(items_sorted[:n])
        concentration[f"top{n}_pct"] = s / total_amt * 100 if total_amt else 0.0

    returns = [
        {
            "buyer": r[2],
            "item": r[0],
            "qty": r[5],
            "amount": r[6],
        }
        for r in rows if r[7] == 1
    ]

    technical_rows = [
        {
            "buyer": r[2],
            "item": r[0],
            "qty": r[5],
            "amount": r[6],
        }
        for r in rows if 0 < (r[6] or 0) <= 2
    ]

    positions = [
        {
            "group": group_of(
                item,
                parent,
                buyer_type=btype,
                supplier_buyer_names=supplier_buyer_names,
            ),
            "buyer": buyer,
            "salon": salon or "",
            "item": item,
            "qty": q,
            "amount": a,
            "period": period,
        }
        for item, parent, buyer, salon, btype, q, a, _is_ret in rows
    ]

    dynamics: list[dict[str, Any]] = []
    dynamics_meta: dict[str, Any] = {}
    cmp_month = compare_month if compare_month is not None else _prev_month(month)
    try:
        _p_src, p_period, _p_rows, _p_qty, _p_amt, prev_rows = _load_month_slice(cur, cmp_month)
        prev_groups, _, _, _ = _group_totals_from_rows(prev_rows, supplier_buyer_names)
        days_new = period_days(period)
        days_old = period_days(p_period)
        dynamics_meta = {
            "compare_month": cmp_month,
            "compare_period": p_period,
            "days_current": days_new,
            "days_compare": days_old,
        }
        keys = set(groups) | set(prev_groups)
        for g in sorted(keys, key=lambda x: -groups.get(x, {"amt": 0.0})["amt"]):
            v = groups.get(g, {"qty": 0.0, "amt": 0.0})
            o = prev_groups.get(g, {"qty": 0.0, "amt": 0.0})
            per_day = v["amt"] / days_new if days_new else 0.0
            pd_old = o["amt"] / days_old if days_old else 0.0
            delta_pct = (per_day / pd_old - 1) * 100 if pd_old else None
            dynamics.append({
                "group": g,
                "amount": v["amt"],
                "per_day": per_day,
                "delta_pct": delta_pct,
            })
    except ValueError:
        pass

    n_slices = cur.execute(
        "SELECT COUNT(*) FROM ("
        " SELECT 1 FROM sales WHERE month=? GROUP BY source_file, period)",
        (month,),
    ).fetchone()[0]
    con.close()

    slice_warnings: list[str] = []
    if n_slices > 1:
        slice_warnings.append(
            f"в базе {n_slices} среза за месяц — берётся самый полный, они НЕ складываются"
        )

    return {
        "month": month,
        "slice": slice_info,
        "slice_warnings": slice_warnings,
        "groups": group_list,
        "cross_table": cross_table,
        "brands": brand_list,
        "concentration": concentration,
        "returns": returns,
        "technical_rows": technical_rows,
        "coverage": {
            "models": n_unique,
            "assigned": n_assigned,
            "unassigned": unassigned,
            "sum_ok": sum_ok,
        },
        "dynamics": dynamics,
        "dynamics_meta": dynamics_meta,
        "positions": positions,
    }


def render_text(data: dict[str, Any]) -> str:
    """Человекочитаемый отчёт (прежний формат stdout)."""
    out = io.StringIO()
    month = data["month"]
    sl = data["slice"]

    print(f"=== СРЕЗЫ за {month} ===", file=out)
    print(
        f"  {sl['source_file']:<28} {sl['period']:<24} "
        f"строк={sl['rows']:<4} шт={sl['qty']:<8} ₽={sl['amount']:,.0f}",
        file=out,
    )
    for warn in data.get("slice_warnings", []):
        print(f"  ВНИМАНИЕ: {warn}", file=out)

    cov = data["coverage"]
    groups_by_name = {g["name"]: g for g in data["groups"]}
    total_amt = sl["amount"]
    groups_sum = sum(g["amount"] for g in data["groups"])
    print(
        f"\nПокрытие OK: {cov['models']} моделей, {len(data['groups'])} групп, "
        f"сумма {groups_sum:,.0f} ₽ == срез {total_amt:,.0f} ₽",
        file=out,
    )

    print("\n=== СВОДКА ПО АССОРТИМЕНТНЫМ ГРУППАМ ===", file=out)
    print(
        f"{'группа':<28}{'поз':>5}{'мод':>5}{'шт':>7}{'₽':>13}{'доля':>8}{'₽/шт':>10}",
        file=out,
    )
    for g in data["groups"]:
        print(
            f"{g['name']:<28}{g['positions']:>5}{g['models']:>5}{g['qty']:>7.0f}"
            f"{g['amount']:>13,.0f}{g['share_pct']:>7.1f}%"
            f"{g['avg_per_unit']:>10,.0f}",
            file=out,
        )

    chans = sorted(
        {ch for g in data["cross_table"] for ch in data["cross_table"][g]},
        key=lambda ch: -sum(data["cross_table"][g].get(ch, 0) for g in data["cross_table"]),
    )
    print("\n=== КРОСС-ТАБЛИЦА: ГРУППА x ТОЧКА (₽) ===", file=out)
    print(
        f"{'группа':<28}" + "".join(f"{ch[:16]:>18}" for ch in chans) + f"{'ИТОГО':>13}",
        file=out,
    )
    cross = data["cross_table"]
    for g in data["groups"]:
        name = g["name"]
        line = f"{name:<28}"
        for ch in chans:
            v = cross.get(name, {}).get(ch, 0.0)
            line += f"{(f'{v:,.0f}' if v else '—'):>18}"
        line += f"{g['amount']:>13,.0f}"
        print(line, file=out)
    print(
        f"{'ИТОГО':<28}"
        + "".join(
            f"{sum(cross.get(g, {}).get(ch, 0) for g in cross):>18,.0f}" for ch in chans
        )
        + f"{total_amt:>13,.0f}",
        file=out,
    )

    print("\n=== БРЕНДЫ ===", file=out)
    for b in data["brands"]:
        print(
            f"  {b['name']:<16} poz={b['positions']:>3} mod={b['models']:>3} "
            f"шт={b['qty']:>6.0f} {b['amount']:>12,.0f} ₽ ({b['share_pct']:>4.1f}%)",
            file=out,
        )

    print("\n=== КОНЦЕНТРАЦИЯ ПО МОДЕЛЯМ ===", file=out)
    conc = data["concentration"]
    for n in (1, 3, 5, 10):
        pct = conc.get(f"top{n}_pct", 0.0)
        s = total_amt * pct / 100
        print(f"  топ-{n:<3} {s:>12,.0f} ₽ = {pct:>5.1f}% оборота", file=out)

    rets = data["returns"]
    print(f"\n=== ВОЗВРАТЫ: {len(rets)} строк ===", file=out)
    if rets:
        for r in rets:
            print(
                f"  {r['buyer'][:30]:30s} | {r['item'][:44]:44s} | "
                f"qty={r['qty']} | {r['amount']:,.0f} ₽",
                file=out,
            )
    else:
        print("  возвратов нет", file=out)

    tech = data["technical_rows"]
    if tech:
        print("\n=== ТЕХНИЧЕСКИЕ ПОЗИЦИИ (1-2 ₽) ===", file=out)
        for t in tech:
            print(
                f"  {t['buyer'][:30]:30s} | {t['item'][:44]:44s} | "
                f"qty={t['qty']} | {t['amount']:.2f} ₽",
                file=out,
            )

    dyn = data["dynamics"]
    meta = data.get("dynamics_meta") or {}
    if dyn and meta:
        print(
            f"\n=== ДИНАМИКА ПО ГРУППАМ: {month} ({meta['days_current']} дн) "
            f"vs {meta['compare_month']} ({meta['days_compare']} дн) ===",
            file=out,
        )
        print(f"{'группа':<28}{'шт':>6}{'₽':>12}{'₽/день':>11}{'Δ₽/день':>11}", file=out)
        for row in dyn:
            gname = row["group"]
            gmeta = groups_by_name.get(gname)
            qty = gmeta["qty"] if gmeta else 0.0
            pd_new = row["per_day"]
            d_pct = row.get("delta_pct")
            d = f"{d_pct:>+10.1f}%" if d_pct is not None else "       —"
            print(
                f"{gname:<28}{qty:>6.0f}{row['amount']:>12,.0f}{pd_new:>11,.0f}{d:>11}",
                file=out,
            )

    return out.getvalue()


def render_json(data: dict[str, Any]) -> str:
    """JSON без вспомогательного поля positions (для компактности)."""
    payload = {
        k: v for k, v in data.items()
        if k not in ("positions", "slice_warnings")
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def render_csv(data: dict[str, Any]) -> str:
    """Плоская таблица позиций."""
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["группа", "покупатель", "точка", "позиция", "кол-во", "сумма", "период"])
    for row in sorted(data.get("positions", []), key=lambda r: -r["amount"]):
        w.writerow([
            row["group"],
            row["buyer"],
            row["salon"],
            row["item"],
            row["qty"],
            row["amount"],
            row["period"],
        ])
    return buf.getvalue()


def _ensure_utf8_stdout() -> None:
    """Best-effort UTF-8 stdout so Cyrillic names survive a cp1251 console.

    Под планировщиком Windows нет LANG, и stdout кодируется по кодовой
    странице (cp1251). Родительский процесс, который читает вывод как
    utf-8, получает «�» вместо кириллицы — и одинаковые по длине
    названия групп схлопываются в одну строку.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main() -> None:
    _ensure_utf8_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sales.db")
    ap.add_argument("--month", required=True)
    ap.add_argument("--format", choices=("text", "json", "csv"), default="text")
    ap.add_argument("--output", "-o", default=None, help="Файл вывода (иначе stdout)")
    ap.add_argument("--compare-month", default=None)
    ap.add_argument("--csv", default=None, help="(устар.) путь к CSV, эквивалент --format csv -o")
    args = ap.parse_args()

    try:
        report = analyze(args.db, args.month, compare_month=args.compare_month)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    if args.csv:
        csv_body = render_csv(report)
        with open(args.csv, "w", encoding="utf-8-sig", newline="") as f:
            f.write(csv_body)
        if args.format == "text":
            text = render_text(report)
            sys.stdout.write(text)
            print(f"\nCSV: {args.csv} ({os.path.getsize(args.csv)} Б)")
            return

    if args.format == "text":
        body = render_text(report)
    elif args.format == "json":
        body = render_json(report)
    else:
        body = render_csv(report)

    if args.output:
        enc = "utf-8-sig" if args.format == "csv" else "utf-8"
        with open(args.output, "w", encoding=enc, newline="") as f:
            f.write(body)
    else:
        sys.stdout.write(body)


if __name__ == "__main__":
    main()