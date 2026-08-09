CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY AUTOINCREMENT,
    login_email TEXT,
    sso_id TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_users_login_email ON users (login_email);
CREATE INDEX IF NOT EXISTS idx_users_sso_id ON users (sso_id);

CREATE TABLE IF NOT EXISTS categories (
    category_id INTEGER PRIMARY KEY AUTOINCREMENT,
    category_name TEXT NOT NULL,
    sub_category_name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_categories_pair
ON categories (category_name, sub_category_name);

CREATE TABLE IF NOT EXISTS stores (
    store_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    alias TEXT,
    address TEXT,
    branch_name TEXT,
    tel TEXT,
    fax TEXT,
    registration_number TEXT,
    country_code TEXT DEFAULT 'JP',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_stores_name ON stores (name);

CREATE TABLE IF NOT EXISTS store_name_aliases (
    alias_id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id INTEGER NOT NULL,
    alias_value TEXT NOT NULL,
    alias_normalized TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (store_id) REFERENCES stores(store_id) ON DELETE CASCADE,
    UNIQUE (store_id, alias_normalized)
);

CREATE INDEX IF NOT EXISTS idx_store_name_alias_normalized
ON store_name_aliases (alias_normalized);

CREATE TABLE IF NOT EXISTS store_address_aliases (
    alias_id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id INTEGER NOT NULL,
    alias_value TEXT NOT NULL,
    alias_normalized TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (store_id) REFERENCES stores(store_id) ON DELETE CASCADE,
    UNIQUE (store_id, alias_normalized)
);

CREATE INDEX IF NOT EXISTS idx_store_address_alias_normalized
ON store_address_aliases (alias_normalized);

CREATE TABLE IF NOT EXISTS items (
    item_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    normalized_name TEXT,
    category_id INTEGER,
    needs_review INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (category_id) REFERENCES categories(category_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_items_category_id ON items (category_id);
CREATE INDEX IF NOT EXISTS idx_items_normalized_name ON items (normalized_name);

CREATE TABLE IF NOT EXISTS item_aliases (
    alias_id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL,
    store_id INTEGER NOT NULL,
    alias_value TEXT NOT NULL,
    alias_normalized TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (item_id) REFERENCES items(item_id) ON DELETE CASCADE,
    FOREIGN KEY (store_id) REFERENCES stores(store_id) ON DELETE CASCADE,
    UNIQUE (item_id, store_id, alias_normalized)
);

CREATE INDEX IF NOT EXISTS idx_item_alias_store_normalized
ON item_aliases (store_id, alias_normalized);

CREATE TABLE IF NOT EXISTS store_match_feedback (
    feedback_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    input_key TEXT NOT NULL,
    candidate_store_id INTEGER NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('confirm', 'reject')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
    FOREIGN KEY (candidate_store_id) REFERENCES stores(store_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_store_feedback_lookup
ON store_match_feedback (input_key, candidate_store_id, user_id, outcome);

CREATE TABLE IF NOT EXISTS item_match_feedback (
    feedback_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    input_key TEXT NOT NULL,
    candidate_item_id INTEGER NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('confirm', 'reject')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
    FOREIGN KEY (candidate_item_id) REFERENCES items(item_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_item_feedback_lookup
ON item_match_feedback (input_key, candidate_item_id, user_id, outcome);

CREATE TABLE IF NOT EXISTS receipts (
    receipt_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    store_id INTEGER,
    receipt_date DATE NOT NULL,
    amount REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT 'JPY',
    source TEXT,
    payment_method TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE SET NULL,
    FOREIGN KEY (store_id) REFERENCES stores(store_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_receipts_receipt_date ON receipts (receipt_date);
CREATE INDEX IF NOT EXISTS idx_receipts_store_date ON receipts (store_id, receipt_date);
CREATE INDEX IF NOT EXISTS idx_receipts_user_date ON receipts (user_id, receipt_date);

CREATE TABLE IF NOT EXISTS receipt_items (
    receipt_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
    receipt_id INTEGER,
    item_id INTEGER,
    item_name_raw TEXT NOT NULL,
    quantity REAL,
    unit_price REAL,
    price REAL,
    line_total REAL,
    line_no INTEGER,
    category_id INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (receipt_id) REFERENCES receipts(receipt_id) ON DELETE SET NULL,
    FOREIGN KEY (item_id) REFERENCES items(item_id) ON DELETE SET NULL,
    FOREIGN KEY (category_id) REFERENCES categories(category_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_receipt_items_item ON receipt_items (item_id);
CREATE INDEX IF NOT EXISTS idx_receipt_items_receipt ON receipt_items (receipt_id);
CREATE INDEX IF NOT EXISTS idx_receipt_items_category_id ON receipt_items (category_id);
CREATE INDEX IF NOT EXISTS idx_receipt_items_created_category
ON receipt_items (created_at, category_id);

CREATE TRIGGER IF NOT EXISTS trg_items_category_sync
AFTER UPDATE OF category_id ON items
FOR EACH ROW
BEGIN
    UPDATE receipt_items
    SET category_id = NEW.category_id
    WHERE item_id = NEW.item_id;
END;
