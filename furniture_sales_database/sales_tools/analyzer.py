#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Анализ продаж по номенклатуре за месяц.

Ассортиментные группы выводятся ИЗ НОМЕНКЛАТУРЫ (в выгрузке 1С поля
«Ассортиментная группа» нет). Порядок проверки префиксов — от специфичного
к общему, иначе «диван-кровать» попадёт в «диваны».

ПРОВЕРКА ПОКРЫТИЯ обязательна: новый товар может не попасть ни в одну
группу и молча выпасть из оборота.
"""
import argparse
import csv
import os
import sqlite3
from collections import defaultdict

SUPPLIER_PARENTS = {
    'Поставщик Махачкала', 'Поставщик Цветы',
}

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


def group_of(item, parent=None):
    """Торговая классификация позиции по её названию."""
    s = item.strip()
    low = s.lower()
    if parent in SUPPLIER_PARENTS:
        return 'Прочее (поставщики/техника)'
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


def brand_of(item):
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sales.db")
    ap.add_argument("--month", required=True)
    ap.add_argument("--csv", default=None)
    ap.add_argument("--compare-month", default=None)
    args = ap.parse_args()

    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True, timeout=30)
    cur = con.cursor()

    # 1) ОДИН срез за месяц (не суммировать)
    slices = cur.execute(
        "SELECT source_file, period, COUNT(*), SUM(quantity), SUM(amount) FROM sales"
        " WHERE month=? GROUP BY source_file, period ORDER BY SUM(amount) DESC", (args.month,)
    ).fetchall()
    print(f"=== СРЕЗЫ за {args.month} ===")
    for r in slices:
        print(f"  {r[0]:<28} {r[1]:<24} строк={r[2]:<4} шт={r[3]:<8} ₽={r[4]:,.0f}")
    if not slices:
        raise SystemExit("нет срезов за месяц")
    src = slices[0][0]
    period = slices[0][1]
    slice_total = slices[0][4]
    if len(slices) > 1:
        print(f"  ВНИМАНИЕ: в базе {len(slices)} среза за месяц - берётся самый полный, они НЕ складываются")

    rows = cur.execute(
        "SELECT item_name, COALESCE(parent_category,''), buyer, COALESCE(salon,''), "
        "buyer_type, quantity, amount, is_return FROM sales WHERE source_file=?",
        (src,)).fetchall()
    con.close()

    # 2) разбивка по группам
    groups = defaultdict(lambda: {"items": {}, "qty": 0.0, "amt": 0.0, "pos": 0})
    brands = defaultdict(lambda: {"qty": 0.0, "amt": 0.0, "pos": 0, "items": set()})
    cross = defaultdict(lambda: defaultdict(float))   # группа -> точка -> ₽
    cross_qty = defaultdict(lambda: defaultdict(float))
    unassigned = []
    for item, parent, buyer, salon, btype, q, a, is_ret in rows:
        g = group_of(item, parent)
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
        cross_qty[g][ch] += q

    # 3) ПРОВЕРКА ПОКРЫТИЯ
    n_unique = len({r[0] for r in rows})
    assert not unassigned, f"НЕ РАСПРЕДЕЛЕНО: {unassigned}"
    assert sum(len(v["items"]) for v in groups.values()) == n_unique, "не все товары распределены"
    assert abs(sum(v["amt"] for v in groups.values()) - slice_total) < 0.01, "сумма групп != итог среза"
    print(f"\nПокрытие OK: {n_unique} моделей, {len(groups)} групп, "
          f"сумма {sum(v['amt'] for v in groups.values()):,.0f} ₽ == срез {slice_total:,.0f} ₽")

    total_amt = sum(v["amt"] for v in groups.values())
    total_qty = sum(v["qty"] for v in groups.values())

    # 4) сводка по группам
    print("\n=== СВОДКА ПО АССОРТИМЕНТНЫМ ГРУППАМ ===")
    print(f"{'группа':<28}{'поз':>5}{'мод':>5}{'шт':>7}{'₽':>13}{'доля':>8}{'₽/шт':>10}")
    for g, v in sorted(groups.items(), key=lambda x: -x[1]["amt"]):
        share = v["amt"] / total_amt * 100
        per = v["amt"] / v["qty"] if v["qty"] else 0
        print(f"{g:<28}{v['pos']:>5}{len(v['items']):>5}{v['qty']:>7.0f}"
              f"{v['amt']:>13,.0f}{share:>7.1f}%{per:>10,.0f}")

    # 5) кросс-таблица группа x точка
    chans = sorted({ch for g in cross for ch in cross[g]},
                    key=lambda ch: -sum(cross[g][ch] for g in cross))
    print("\n=== КРОСС-ТАБЛИЦА: ГРУППА x ТОЧКА (₽) ===")
    print(f"{'группа':<28}" + "".join(f"{ch[:16]:>18}" for ch in chans) + f"{'ИТОГО':>13}")
    for g in sorted(groups, key=lambda x: -groups[x]["amt"]):
        line = f"{g:<28}"
        for ch in chans:
            v = cross[g].get(ch, 0.0)
            line += f"{(f'{v:,.0f}' if v else '—'):>18}"
        line += f"{groups[g]['amt']:>13,.0f}"
        print(line)
    print(f"{'ИТОГО':<28}" + "".join(
        f"{sum(cross[g].get(ch,0) for g in cross):>18,.0f}" for ch in chans) + f"{total_amt:>13,.0f}")

    # 6) бренды
    print("\n=== БРЕНДЫ ===")
    for b, v in sorted(brands.items(), key=lambda x: -x[1]["amt"]):
        print(f"  {b:<16} поз={v['pos']:>3} мод={len(v['items']):>3} "
              f"шт={v['qty']:>6.0f} {v['amt']:>12,.0f} ₽ ({v['amt']/total_amt*100:>4.1f}%)")

    # 7) концентрация
    items_sorted = sorted((v for g in groups.values() for v in g["items"].values()), reverse=True)
    print("\n=== КОНЦЕНТРАЦИЯ ПО МОДЕЛЯМ ===")
    for n in (1, 3, 5, 10):
        s = sum(items_sorted[:n])
        print(f"  топ-{n:<3} {s:>12,.0f} ₽ = {s/total_amt*100:>5.1f}% оборота")

    # 8) возвраты
    rets = [r for r in rows if r[7] == 1]
    print(f"\n=== ВОЗВРАТЫ: {len(rets)} строк ===")
    if rets:
        for r in rets:
            print(f"  {r[2][:30]:30s} | {r[0][:44]:44s} | qty={r[5]} | {r[6]:,.0f} ₽")
    else:
        print("  возвратов нет")

    # 9) технические позиции
    tech = [(r[2], r[0], r[5], r[6]) for r in rows if 0 < (r[6] or 0) <= 2]
    if tech:
        print("\n=== ТЕХНИЧЕСКИЕ ПОЗИЦИИ (1-2 ₽) ===")
        for t in tech:
            print(f"  {t[0][:30]:30s} | {t[1][:44]:44s} | qty={t[2]} | {t[3]:.2f} ₽")

    # 10) сравнение с прошлым месяцем по группам
    if args.compare_month:
        con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True, timeout=30)
        c2 = con.cursor()
        prev = c2.execute(
            "SELECT source_file, period, SUM(amount), SUM(quantity) FROM sales WHERE month=?"
            " GROUP BY source_file, period ORDER BY SUM(amount) DESC LIMIT 1", (args.compare_month,)
        ).fetchone()
        if prev:
            pm, pp, pamt, pqty = prev
            prows = c2.execute("SELECT item_name, COALESCE(parent_category,''), quantity, amount"
                               " FROM sales WHERE source_file=?", (pm,)).fetchall()
            pg = defaultdict(lambda: {"qty": 0.0, "amt": 0.0})
            for item, parent, q, a in prows:
                g = group_of(item, parent)
                pg[g]["qty"] += q
                pg[g]["amt"] += a
            con.close()
            import datetime as dt

            def _period_days(p):
                """Число дней в периоде вида '01.11.2026-18.11.2026'.

                Внимание: у даты тоже есть дефис-разделитель, поэтому период
                режем по '-' только один раз (maxsplit=1), а не split().
                """
                a, b = p.split("-", 1)
                d1 = dt.date(*map(int, a.split(".")[::-1]))
                d2 = dt.date(*map(int, b.split(".")[::-1]))
                return (d2 - d1).days + 1

            days_new = _period_days(period)
            days_old = _period_days(pp)
            print(f"\n=== ДИНАМИКА ПО ГРУППАМ: {args.month} ({days_new} дн) vs {args.compare_month} ({days_old} дн) ===")
            print(f"{'группа':<28}{'шт':>6}{'₽':>12}{'₽/день':>11}{'Δ₽/день':>11}")
            keys = set(groups) | set(pg)
            for g in sorted(keys, key=lambda x: -groups.get(x, {"amt":0})["amt"]):
                v = groups.get(g, {"qty": 0.0, "amt": 0.0})
                o = pg.get(g, {"qty": 0.0, "amt": 0.0})
                pd_new = v["amt"] / days_new
                pd_old = o["amt"] / days_old
                d = f"{(pd_new/pd_old-1)*100:>+10.1f}%" if pd_old else "       —"
                print(f"{g:<28}{v['qty']:>6.0f}{v['amt']:>12,.0f}{pd_new:>11,.0f}{d:>11}")
        else:
            con.close()

    # 11) CSV
    if args.csv:
        with open(args.csv, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["группа", "покупатель", "точка", "позиция", "кол-во", "сумма", "период"])
            for item, parent, buyer, salon, btype, q, a, is_ret in sorted(
                    rows, key=lambda r: -r[6]):
                w.writerow([group_of(item, parent), buyer, salon or "", item, q, a, period])
        print(f"\nCSV: {args.csv} ({os.path.getsize(args.csv)} Б)")


if __name__ == "__main__":
    main()
