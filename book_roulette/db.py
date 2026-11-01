import sqlite3
from pathlib import Path

from flask import current_app, g


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA busy_timeout = 5000")
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(error=None):
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


def init_db():
    schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
    connection = get_db()
    connection.executescript(schema)
    columns = {row['name'] for row in connection.execute('PRAGMA table_info(books)')}
    if 'open_library_id' not in columns:
        connection.execute('ALTER TABLE books ADD COLUMN open_library_id TEXT')
    connection.execute('CREATE UNIQUE INDEX IF NOT EXISTS books_open_library_id ON books(open_library_id)')
    connection.commit()


def init_app(app):
    app.teardown_appcontext(close_db)
