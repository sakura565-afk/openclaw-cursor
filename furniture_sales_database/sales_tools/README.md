# sales_tools — инструменты работы с базой продаж

Модули вынесены из рабочей машины в репозиторий, чтобы изменения проходили
ревью и тестировались в CI. Логика не менялась — только пути к БД стали
параметрами (`--db`, по умолчанию `sales.db` в текущем каталоге).

## Модули

| Модуль | Назначение |
|---|---|
| `importer.py` | Импорт накопительного среза выгрузки 1С (TDSheet) в SQLite. Поддерживает вариант файла из 3 уровней (`По месяцам; Контрагент; Номенклатура` — без уровня «Номенклатура.Родитель»). |
| `analyzer.py` | Анализ среза по номенклатуре: ассортиментные группы, кросс-таблица группа × точка, бренды, концентрация, возвраты, динамика к прошлому месяцу. |

## Схема таблицы `sales` (плоская, одна база — `sales.db`)

```sql
CREATE TABLE sales (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    period TEXT NOT NULL,          -- '01.11.2026-18.11.2026'
    period_start TEXT,             -- '2026-11-01'
    period_end TEXT,               -- '2026-11-18'
    buyer TEXT NOT NULL,           -- КТО купил (контрагент)
    month TEXT,                    -- '2026-11'
    parent_category TEXT,          -- узел иерархии 1С (в 3-уровневом файле пусто)
    item_name TEXT NOT NULL,       -- номенклатура
    quantity REAL NOT NULL,
    amount REAL NOT NULL,
    source_file TEXT,              -- basename без префикса doc_<hash>_
    loaded_at TEXT NOT NULL,
    buyer_type TEXT,               -- 'salon' | 'manager' | 'ip' | 'supplier'
    seller TEXT,                   -- человек-продавец, когда buyer = юрлицо
    salon TEXT,                    -- точка, через которую прошла б2б-продажа
    item_characteristic TEXT,      -- ткань/цвет (в 3-уровневом файле '(не указана)')
    is_return INTEGER,             -- 1 = возврат (отрицательные qty/amount)
    report_filter TEXT             -- 'buyer' | 'manager' — периметр отчёта
);
CREATE TABLE buyers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    total_orders INTEGER DEFAULT 0,
    total_amount REAL DEFAULT 0,
    buyer_type TEXT,
    channel_type TEXT,             -- 'online' | 'offline' | 'mixed' | 'b2b_cashless' | 'b2b' | 'supplier'
    location TEXT,
    note TEXT,
    UNIQUE(name, buyer_type)       -- составной UNIQUE: одно имя может быть под двумя типами
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
```

## Ключевые правила (проверены на реальных выгрузках)

1. **Три измерения одной продажи:** `buyer` — кто купил (юрлицо/ИП),
   `salon` — через какую точку прошла продажа, `seller` — человек-продавец.
   Б2Б-контрагент — это `buyer_type='ip'`, а НЕ `'salon'`, иначе б2б попадает
   в выручку салонов.
2. **Розничные салоны получают `salon = buyer` автоматически** — это их
   собственная точка продаж, не выдуманная атрибуция.
3. **Атрибуция не выдумывается.** Если пользователь не назвал салон/продавца —
   остаётся NULL, а в `buyers.note` пишется, что атрибуция ожидается.
4. **Агрегаты `buyers` пересчитываются с фильтром `buyer_type`.** Без него
   имя, лежащее под двумя типами (поставщики как `salon` и `supplier`),
   считается дважды.
5. **Накопительный срез ЗАМЕНЯЕТ предыдущий срез того же месяца.**
   Перед удалением проверяется покрытие ПО ПОКУПАТЕЛЯМ (не по суммам).
6. **`source_file` = basename БЕЗ префикса `doc_<hash>_`** — Hermes так
   именует загруженные файлы, и префикс меняется при каждой загрузке.
7. **Ассортиментные группы выводятся ИЗ НОМЕНКЛАТУРЫ** — поля «Ассортиментная
   группа» в выгрузке 1С нет. `parent_category` — узел иерархии 1С, это не
   торговая классификация.
8. **Проверка покрытия обязательна:** новый товар молча выпадает из оборота,
   если не попал ни в один префикс.
9. **Ширина периода разбирается через `split("-", 1)`** — у даты тоже есть дефис.

## Фикстуры

`furniture_sales_database/sales_tools/fixtures/sales_sample.db` — обезличенная
копия рабочей базы (контрагенты, салоны и менеджеры заменены псевдонимами;
структура, объёмы и соотношения сумм сохранены). Используется в тестах.
