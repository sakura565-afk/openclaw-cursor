# Furniture Sales Database (SQLite + XLS import)

This project imports furniture sales data from `.xls` into SQLite and provides example analytics queries.

## Implemented requirements

1. SQLite schema with tables: `sales`, `products`, `sellers`, `platforms`
2. Excel parser for `.xls` files using `xlrd`
3. Import script for `dina_2026_sample.xls` (or any compatible `.xls`)
4. Category auto-detection (`Классика`, `Корпусная`, and more)
5. Query examples in `query_sales.py`
6. Usage documentation (this file)

## Files

- `schema.sql` — database schema
- `category_detector.py` — category detection from product name
- `import_sales.py` — `.xls` to SQLite importer
- `query_sales.py` — sample analytical queries

## Setup

From repository root:

```bash
pip install -r requirements.txt
```

`xlrd` is required for reading `.xls`.

## Import data

Run from `furniture_sales_database` directory:

```bash
python import_sales.py --xls "/path/to/dina_2026_sample.xls" --db "furniture_sales.db" --schema "schema.sql"
```

Notes:
- The importer auto-creates schema if needed.
- It accepts flexible headers (English/Russian aliases) for key columns.
- Product category is detected automatically from product name.

## Run query examples

```bash
python query_sales.py --db "furniture_sales.db"
```

The script prints:
- revenue by category
- top sellers by revenue
- units sold by platform
- daily revenue
