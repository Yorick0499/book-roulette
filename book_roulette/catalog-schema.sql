CREATE TABLE IF NOT EXISTS catalog_authors (
    author_id TEXT PRIMARY KEY,
    name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS catalog_books (
    id INTEGER PRIMARY KEY,
    work_id TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    author TEXT NOT NULL DEFAULT '',
    year INTEGER
);
CREATE INDEX IF NOT EXISTS catalog_title ON catalog_books(title COLLATE NOCASE,id);
CREATE VIRTUAL TABLE IF NOT EXISTS catalog_fts USING fts5(
    title, author, content='catalog_books', content_rowid='id', tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS catalog_insert AFTER INSERT ON catalog_books BEGIN
    INSERT INTO catalog_fts(rowid,title,author) VALUES (new.id,new.title,new.author);
END;
CREATE TRIGGER IF NOT EXISTS catalog_delete AFTER DELETE ON catalog_books BEGIN
    INSERT INTO catalog_fts(catalog_fts,rowid,title,author) VALUES ('delete',old.id,old.title,old.author);
END;
CREATE TRIGGER IF NOT EXISTS catalog_update AFTER UPDATE ON catalog_books BEGIN
    INSERT INTO catalog_fts(catalog_fts,rowid,title,author) VALUES ('delete',old.id,old.title,old.author);
    INSERT INTO catalog_fts(rowid,title,author) VALUES (new.id,new.title,new.author);
END;
CREATE TABLE IF NOT EXISTS catalog_searches (
    query TEXT NOT NULL,
    page INTEGER NOT NULL,
    payload TEXT NOT NULL,
    fetched_at INTEGER NOT NULL,
    PRIMARY KEY(query,page)
);
CREATE TABLE IF NOT EXISTS catalog_jobs (
    id INTEGER PRIMARY KEY,
    state TEXT NOT NULL,
    phase TEXT NOT NULL DEFAULT '',
    records INTEGER NOT NULL DEFAULT 0,
    skipped INTEGER NOT NULL DEFAULT 0,
    downloaded INTEGER NOT NULL DEFAULT 0,
    total INTEGER,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
