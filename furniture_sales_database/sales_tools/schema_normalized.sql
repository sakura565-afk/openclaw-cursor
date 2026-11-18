PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS suppliers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    contact TEXT,
    phone TEXT,
    email TEXT,
    website TEXT,
    address TEXT,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sku TEXT UNIQUE,
    name TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL DEFAULT '',
    brand TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sellers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    buyer_type TEXT,
    channel_type TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('salon', 'platform')),
    channel_type TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (name, kind)
);

CREATE TABLE IF NOT EXISTS counterparties (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    buyer_type TEXT,
    channel_type TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sales (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sale_date TEXT NOT NULL,
    period TEXT NOT NULL,
    period_start TEXT,
    period_end TEXT,
    month TEXT,
    product_id INTEGER NOT NULL,
    seller_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    counterparty_id INTEGER,
    quantity REAL NOT NULL,
    unit_price REAL NOT NULL,
    total_amount REAL NOT NULL,
    parent_category TEXT,
    item_characteristic TEXT,
    source_file TEXT,
    report_filter TEXT,
    is_return INTEGER NOT NULL DEFAULT 0,
    source_flat_id INTEGER NOT NULL UNIQUE,
    loaded_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (product_id) REFERENCES products (id),
    FOREIGN KEY (seller_id) REFERENCES sellers (id),
    FOREIGN KEY (channel_id) REFERENCES channels (id),
    FOREIGN KEY (counterparty_id) REFERENCES counterparties (id)
);

CREATE INDEX IF NOT EXISTS idx_sales_month ON sales (month);
CREATE INDEX IF NOT EXISTS idx_sales_sale_date ON sales (sale_date);
CREATE INDEX IF NOT EXISTS idx_sales_channel_id ON sales (channel_id);
CREATE INDEX IF NOT EXISTS idx_sales_counterparty_id ON sales (counterparty_id);
CREATE INDEX IF NOT EXISTS idx_sales_product_id ON sales (product_id);