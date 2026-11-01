CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY,
    goodreads_id TEXT UNIQUE,
    title TEXT NOT NULL,
    author TEXT NOT NULL DEFAULT '',
    additional_authors TEXT NOT NULL DEFAULT '',
    isbn TEXT NOT NULL DEFAULT '',
    isbn13 TEXT NOT NULL DEFAULT '',
    my_rating INTEGER CHECK (my_rating BETWEEN 0 AND 5),
    average_rating REAL,
    bookshelves TEXT NOT NULL DEFAULT '',
    exclusive_shelf TEXT NOT NULL DEFAULT '',
    date_read TEXT,
    date_added TEXT,
    selected INTEGER NOT NULL DEFAULT 0 CHECK (selected IN (0, 1))
);

CREATE TABLE IF NOT EXISTS draw_history (
    id INTEGER PRIMARY KEY,
    book_id INTEGER REFERENCES books(id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    author TEXT NOT NULL,
    drawn_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Persist only the latest retry payloads; history remains independent.
CREATE TABLE IF NOT EXISTS draw_requests (
    key TEXT PRIMARY KEY,
    payload TEXT NOT NULL
);
