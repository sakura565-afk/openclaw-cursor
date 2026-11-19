#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Сборка СИНТЕТИЧЕСКОЙ фикстуры sales_sample.db.

Фикстура нужна тестам пакета sales_tools. Она полностью синтетическая:
ни одного реального контрагента, салона, сотрудника, поставщика, телефона
или адреса — всё генерируется детерминированно из фиксированного seed.

Реальные выгрузки 1С в публичный репозиторий не попадают никогда.

Запуск:
    python furniture_sales_database/sales_tools/fixtures/build_synthetic_fixture.py

Контракт фикстуры (на нём держатся тесты):
  * месяцы 2026-09, 2026-10, 2026-11 — по одному срезу на месяц;
  * ноябрь: 112 строк, 268 шт, buyer_type salon=100 / ip=12, unknown=0;
  * в ноябре нет строк поставщиков (группа «Прочее (поставщики/техника)» = 0);
  * в октябре есть строки с parent_category вида «Поставщик …» — по ним
    проверяется старое правило классификации поставщиков;
  * в обоих месяцах есть группа «Стулья» — по ней считается динамика;
  * каждая позиция попадает в известную группу (покрытие = 100 %);
  * единица измерения в конце названия — признак листа-товаров.
"""
from __future__ import annotations

import argparse
import random
import sqlite3
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT_DEFAULT = HERE / "sales_sample.db"

SEED = 20261119

# --- салоны (точки продаж) -----------------------------------------------
SALONS = (
    ("Салон Север", "online"),
    ("Салон Центр", "offline"),
    ("Салон Запад", "mixed"),
)

# --- продавцы (персоналии — обезличенные) --------------------------------
MANAGERS = (
    ("Менеджер А", "online"),
    ("Менеджер Б", "mixed"),
    ("Менеджер В", "offline"),
    ("Менеджер Г", "offline"),
    ("Менеджер Д", "offline"),
)

# --- b2b-контрагенты: юридическая форма в имени обязательна ---------------
IP_BUYERS = (
    "ООО «Контрагент 1»",
    "ООО «Контрагент 2»",
    "ООО «Контрагент 3»",
    "ООО «Контрагент 4»",
    "ООО «Контрагент 5»",
    "ИП Контрагент 6",
    "ИП Контрагент 7",
    "ИП Контрагент 8",
    "ИП Контрагент 9",
    "АО «Контрагент 10»",
    "ИП Контрагент 11",
    "ИП Контрагент 12",
    "ИП Контрагент 13",
    "ООО «Контрагент 14»",
    "ООО «Контрагент 15»",
)

# --- поставщики ------------------------------------------------------------
SUPPLIERS = ("Поставщик А", "Поставщик Б", "Поставщик В")

# --- ассортиментные группы и цены ----------------------------------------
# Префикс — по нему analyzer.py раскладывает позицию в группу.
CATALOG: tuple[tuple[str, float], ...] = (
    ("Стул", 8500.0),
    ("Стул с подлокотниками", 9900.0),
    ("Диван", 32000.0),
    ("Кресло", 14000.0),
    ("Полукресло", 12500.0),
    ("Кровать", 21000.0),
    ("Стол", 12000.0),
    ("Стол кухонный", 15000.0),
    ("Банкетка", 11000.0),
    ("Консоль", 9000.0),
    ("Прикроватная тумбочка", 6000.0),
    ("Тумба", 6500.0),
    ("Кушетка", 16000.0),
    ("Матрас", 18000.0),
    ("Зеркало", 5500.0),
    ("Подставка", 4500.0),
    ("Комплект", 25000.0),
    ("Подушка", 1200.0),
    ("Покрывало", 3500.0),
    ("Диван-кровать", 29000.0),
    ("Диван-кровать угловой", 33500.0),
    ("Оттоманка", 13000.0),
    ("Софа", 28000.0),
    ("Система «Монреаль»", 45000.0),
)

MONTHS = {
    "2026-09": ("01.09.2026-28.09.2026", "2026-09-01", "2026-09-28"),
    "2026-10": ("01.10.2026-31.10.2026", "2026-10-01", "2026-10-31"),
    "2026-11": ("01.11.2026-18.11.2026", "2026-11-01", "2026-11-18"),
}

# ноябрь: ровно 112 строк / 268 шт / 100 salon + 12 ip
NOV_ROWS = 112
NOV_QTY = 268
NOV_SALON_ROWS = 100
NOV_IP_ROWS = 12
NOV_TECH_ROW_INDEX = 5  # строка с копеечной суммой — проверка «требует внимания»

# октябрь: 205 строк, из них часть — строки поставщиков по старому правилу
OCT_ROWS = 205
OCT_SUPPLIER_ROWS = 9
OCT_SUPPLIER_AMT = 98500.0

# сентябрь: 173 строки, есть менеджеры, салоны, b2b и поставщики
SEP_MANAGER_ROWS = 87
SEP_SALON_ROWS = 78
SEP_IP_ROWS = 3
SEP_SUPPLIER_ROWS = 5

DDL = """
CREATE TABLE sales (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    period TEXT NOT NULL,
    period_start TEXT,
    period_end TEXT,
    buyer TEXT NOT NULL,
    month TEXT,
    parent_category TEXT,
    item_name TEXT NOT NULL,
    quantity REAL NOT NULL,
    amount REAL NOT NULL,
    source_file TEXT,
    loaded_at TEXT NOT NULL,
    buyer_type TEXT,
    seller TEXT,
    salon TEXT,
    item_characteristic TEXT,
    is_return INTEGER,
    report_filter TEXT
);
CREATE TABLE imports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_name TEXT NOT NULL,
    period TEXT,
    rows_imported INTEGER,
    total_quantity REAL,
    total_amount REAL,
    imported_at TEXT NOT NULL,
    report_filter TEXT
);
CREATE TABLE buyers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    total_orders INTEGER DEFAULT 0,
    total_amount REAL DEFAULT 0,
    buyer_type TEXT,
    channel_type TEXT,
    location TEXT,
    note TEXT,
    UNIQUE(name, buyer_type)
);
CREATE INDEX idx_sales_buyer ON sales(buyer);
CREATE INDEX idx_sales_month ON sales(month);
CREATE INDEX idx_sales_parent ON sales(parent_category);
CREATE INDEX idx_sales_period ON sales(period);
"""


def _item(rng: random.Random, seq: int) -> tuple[str, float]:
    """Название позиции и цена."""
    prefix, price = CATALOG[rng.randrange(len(CATALOG))]
    return f'{prefix} "Модель {100 + seq % 900}", шт', price


def _qtys_for_november() -> list[int]:
    """Количества по строкам ноябрьского среза с суммой ровно NOV_QTY.

    Техническая строка (копеечная позиция) всегда 1 шт и в подгонке не
    участвует — иначе её количество пересчитывалось бы дважды.
    """
    qtys = ([1] * 60 + [2] * 34 + [3] * 12 + [4] * 6)[:NOV_ROWS]
    while len(qtys) < NOV_ROWS:
        qtys.append(1)

    tech = NOV_TECH_ROW_INDEX
    qtys[tech] = 1
    target = NOV_QTY  # сумма считается по всем строкам, включая техническую

    # подгоняем остальные строки до target, не трогая техническую
    order = [i for i in range(NOV_ROWS) if i != tech]
    diff = sum(qtys) - target
    pos = 0
    while diff != 0:
        i = order[pos % len(order)]
        if diff > 0:
            if qtys[i] > 1:
                step = min(diff, qtys[i] - 1)
                qtys[i] -= step
                diff -= step
        else:
            qtys[i] += 1
            diff += 1
        pos += 1
    return qtys


def _row(
    buyer: str,
    buyer_type: str,
    salon: str | None,
    seller: str | None,
    parent: str,
    item: str,
    qty: float,
    amount: float,
) -> dict:
    return {
        "buyer": buyer,
        "buyer_type": buyer_type,
        "salon": salon,
        "seller": seller,
        "parent": parent,
        "item": item,
        "qty": qty,
        "amount": amount,
        "characteristic": None,
        "is_return": 0,
    }


def _rows_november(rng: random.Random) -> list[dict]:
    rows: list[dict] = []
    qtys = _qtys_for_november()
    for idx in range(NOV_ROWS):
        qty = qtys[idx]
        name, price = _item(rng, idx)
        amount = round(qty * price, 2)
        if idx == NOV_TECH_ROW_INDEX:
            # копеечная позиция — отрабатывает предупреждение «требует внимания»
            name, qty, amount = "Ткань, м", 1, 1.0
        if idx < NOV_SALON_ROWS:
            salon, _channel = SALONS[idx % len(SALONS)]
            rows.append(_row(salon, "salon", salon, None, "", name, qty, amount))
        else:
            buyer = IP_BUYERS[(idx - NOV_SALON_ROWS) % len(IP_BUYERS)]
            rows.append(_row(buyer, "ip", None, None, "", name, qty, amount))
    return rows


def _rows_october(rng: random.Random) -> list[dict]:
    rows: list[dict] = []
    for idx in range(OCT_ROWS):
        name, price = _item(rng, idx)
        qty = rng.choice((1, 1, 1, 2, 2, 3))
        if idx < OCT_SUPPLIER_ROWS:
            # строки поставщиков: parent_category начинается с «Поставщик»
            supplier = SUPPLIERS[idx % len(SUPPLIERS)]
            salon, _channel = SALONS[idx % len(SALONS)]
            # сумма делится без остатка: последняя строка добирает копейки
            share = round(OCT_SUPPLIER_AMT / OCT_SUPPLIER_ROWS, 2)
            amount = (
                round(OCT_SUPPLIER_AMT - share * (OCT_SUPPLIER_ROWS - 1), 2)
                if idx == OCT_SUPPLIER_ROWS - 1
                else share
            )
            rows.append(_row(salon, "salon", salon, None, supplier, name, qty, amount))
            continue
        if idx % 4 == 0:
            buyer = IP_BUYERS[idx % len(IP_BUYERS)]
            rows.append(_row(buyer, "ip", None, None, "", name, qty, round(qty * price, 2)))
        else:
            salon, _channel = SALONS[idx % len(SALONS)]
            rows.append(_row(salon, "salon", salon, None, "", name, qty, round(qty * price, 2)))
    return rows


def _rows_september(rng: random.Random) -> list[dict]:
    rows: list[dict] = []
    plan = (
        ("manager", SEP_MANAGER_ROWS),
        ("salon", SEP_SALON_ROWS),
        ("ip", SEP_IP_ROWS),
        ("supplier", SEP_SUPPLIER_ROWS),
    )
    idx = 0
    for kind, count in plan:
        for _ in range(count):
            name, price = _item(rng, idx)
            qty = rng.choice((1, 1, 2, 2, 3, 4))
            amount = round(qty * price, 2)
            if kind == "manager":
                manager, _channel = MANAGERS[idx % len(MANAGERS)]
                rows.append(_row(manager, "manager", None, manager, "", name, qty, amount))
            elif kind == "salon":
                salon, _channel = SALONS[idx % len(SALONS)]
                rows.append(_row(salon, "salon", salon, None, "", name, qty, amount))
            elif kind == "ip":
                buyer = IP_BUYERS[idx % len(IP_BUYERS)]
                rows.append(_row(buyer, "ip", None, None, "", name, qty, amount))
            else:
                supplier = SUPPLIERS[idx % len(SUPPLIERS)]
                rows.append(_row(supplier, "supplier", None, None, supplier, name, qty, amount))
            idx += 1
    return rows


def _insert_buyer(
    con: sqlite3.Connection,
    name: str,
    buyer_type: str,
    channel: str | None,
    first_seen: str,
) -> None:
    con.execute(
        """
        INSERT INTO buyers (
            name, first_seen, total_orders, total_amount, buyer_type,
            channel_type, location, note
        ) VALUES (?,?,0,0.0,?,?,NULL,NULL)
        """,
        (name, first_seen, buyer_type, channel),
    )


def build(out_path: Path) -> dict:
    rng = random.Random(SEED)
    if out_path.exists():
        out_path.unlink()

    con = sqlite3.connect(out_path)
    con.executescript(DDL)

    plan = (
        ("2026-11", _rows_november(rng)),
        ("2026-10", _rows_october(rng)),
        ("2026-09", _rows_september(rng)),
    )

    summary: dict = {}
    for month, rows in plan:
        period, p_start, p_end = MONTHS[month]
        source_file = f"report_{month}.xlsx"
        loaded_at = f"{month}-01T09:00:00"
        for r in rows:
            con.execute(
                """
                INSERT INTO sales (
                    period, period_start, period_end, buyer, month, parent_category,
                    item_name, quantity, amount, source_file, loaded_at,
                    buyer_type, seller, salon, item_characteristic, is_return, report_filter
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    period, p_start, p_end, r["buyer"], month, r["parent"],
                    r["item"], r["qty"], r["amount"], source_file, loaded_at,
                    r["buyer_type"], r["seller"], r["salon"], r["characteristic"],
                    r["is_return"], "buyer",
                ),
            )
        rows_n = len(rows)
        qty = sum(r["qty"] for r in rows)
        amt = round(sum(r["amount"] for r in rows), 2)
        con.execute(
            """
            INSERT INTO imports (
                file_name, period, rows_imported, total_quantity, total_amount,
                imported_at, report_filter
            ) VALUES (?,?,?,?,?,?,?)
            """,
            (source_file, period, rows_n, qty, amt, loaded_at, "buyer"),
        )
        summary[month] = {"rows": rows_n, "qty": round(qty, 2), "amount": amt}

    first_seen = "2026-09-01T09:00:00"
    for name, channel in SALONS:
        _insert_buyer(con, name, "salon", channel, first_seen)
    for name, channel in MANAGERS:
        _insert_buyer(con, name, "manager", channel, first_seen)
    for name in IP_BUYERS:
        _insert_buyer(con, name, "ip", "b2b", first_seen)
    for name in SUPPLIERS:
        _insert_buyer(con, name, "supplier", None, first_seen)

    for name, btype in list(con.execute("SELECT name, buyer_type FROM buyers")):
        cnt, amt = con.execute(
            "SELECT COUNT(*), COALESCE(SUM(amount),0) FROM sales WHERE buyer=? AND buyer_type=?",
            (name, btype),
        ).fetchone()
        con.execute(
            "UPDATE buyers SET total_orders=?, total_amount=? WHERE name=? AND buyer_type=?",
            (int(cnt), float(amt), name, btype),
        )
    con.commit()
    con.execute("VACUUM")
    con.close()
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description="Сборка синтетической фикстуры продаж")
    ap.add_argument("--out", default=str(OUT_DEFAULT), help="путь к sales_sample.db")
    args = ap.parse_args()
    out = Path(args.out)
    summary = build(out)
    print(f"фикстура собрана: {out}")
    for month, s in sorted(summary.items()):
        print(f"  {month}: {s['rows']} строк, {s['qty']} шт, {s['amount']} руб")


if __name__ == "__main__":
    main()
