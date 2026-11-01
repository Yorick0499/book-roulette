"""A public, local catalog kept separate from the user's private books."""
import json
import re
import sqlite3
import time
from pathlib import Path

from flask import current_app, g

from . import openlibrary

UPSERT_WORK = '''INSERT INTO catalog_books(work_id,title,author,year) VALUES (:work_id,:title,:author,:year)
    ON CONFLICT(work_id) DO UPDATE SET title=excluded.title,author=excluded.author,year=excluded.year'''


def connect(path):
    connection = sqlite3.connect(path, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA journal_mode=WAL')
    connection.execute('PRAGMA busy_timeout=15000')
    return connection


def get_catalog():
    if 'catalog_db' not in g:
        g.catalog_db = connect(current_app.config['CATALOG_DATABASE'])
    return g.catalog_db


def init_app(app):
    @app.teardown_appcontext
    def close_catalog(error=None):
        connection = g.pop('catalog_db', None)
        if connection is not None:
            connection.close()
    with connect(app.config['CATALOG_DATABASE']) as connection:
        connection.executescript(Path(__file__).with_name('catalog-schema.sql').read_text(encoding='utf-8'))
        columns = {row['name'] for row in connection.execute('PRAGMA table_info(catalog_books)')}
        for name, definition in [('cover_id', 'INTEGER'), ('cover_checked', 'INTEGER NOT NULL DEFAULT 0'), ('cover_edition', 'TEXT'), ('cover_resolved', 'INTEGER NOT NULL DEFAULT 0')]:
            if name not in columns:
                connection.execute(f'ALTER TABLE catalog_books ADD COLUMN {name} {definition}')
        connection.execute('CREATE TABLE IF NOT EXISTS cover_work_status (work_id TEXT PRIMARY KEY, state TEXT NOT NULL, cache_key TEXT, retry_after INTEGER NOT NULL DEFAULT 0)')
        connection.execute('CREATE TABLE IF NOT EXISTS cover_failures (cover_id INTEGER PRIMARY KEY, retry_after INTEGER NOT NULL)')
    connection.close()


def fetch_search(connection, query, page):
    cached = connection.execute('SELECT payload,fetched_at FROM catalog_searches WHERE query=? AND page=?',
                                (query, page)).fetchone()
    if cached and time.time() - cached['fetched_at'] < 86400 and all('cover_edition' in work for work in json.loads(cached['payload'])['works']):
        return json.loads(cached['payload'])
    result = openlibrary.search(query, page)
    result['works'] = [{**work, 'cover_id': work.get('cover_id'), 'cover_edition': work.get('cover_edition')} for work in result['works']]
    previous = {work['work_id']: connection.execute('SELECT cover_id,cover_edition FROM catalog_books WHERE work_id=?',
                                                  (work['work_id'],)).fetchone() for work in result['works']}
    with connection:
        connection.executemany(UPSERT_WORK, result['works'])
        for work in result['works']:
            old = previous[work['work_id']]
            changed = any(work.get(field) is not None and (old is None or work[field] != old[field])
                          for field in ('cover_id', 'cover_edition'))
            if changed:
                connection.execute("DELETE FROM cover_work_status WHERE work_id=? AND state!='ready'", (work['work_id'],))
        connection.executemany('UPDATE catalog_books SET cover_id=COALESCE(?,cover_id),cover_edition=COALESCE(?,cover_edition) WHERE work_id=?',
                               [(work.get('cover_id'), work.get('cover_edition'), work['work_id']) for work in result['works']])
        connection.execute('INSERT INTO catalog_searches VALUES (?,?,?,?) ON CONFLICT(query,page) '
                           'DO UPDATE SET payload=excluded.payload,fetched_at=excluded.fetched_at',
                           (query, page, json.dumps(result), int(time.time())))
    return result


def cached_search(connection, query, page):
    cached = connection.execute('SELECT payload FROM catalog_searches WHERE query=? AND page=?', (query, page)).fetchone()
    return json.loads(cached['payload']) if cached else None


def local_search(connection, query, page):
    tokens = re.findall(r'\w+', query, re.UNICODE)[:10]
    if tokens:
        expression = ' AND '.join('"' + token + '"*' for token in tokens)
        condition = 'WHERE id IN (SELECT rowid FROM catalog_fts WHERE catalog_fts MATCH ?)'
        values = (expression,)
    else:
        condition, values = '', ()
    count = connection.execute('SELECT COUNT(*) FROM catalog_books ' + condition, values).fetchone()[0]
    pages = max(1, (count + 23) // 24)
    page = min(max(1, page), pages)
    works = connection.execute('SELECT * FROM catalog_books ' + condition + ' ORDER BY title COLLATE NOCASE,id LIMIT 24 OFFSET ?',
                               (*values, (page-1)*24)).fetchall()
    return {'works': works, 'total': count, 'page': page, 'pages': pages}


def add_to_library(library, catalog, work_ids):
    added = skipped = 0
    with library:
        library.execute('BEGIN IMMEDIATE')
        for identifier in dict.fromkeys(work_ids):
            existing = library.execute('SELECT id FROM books WHERE open_library_id=?', (identifier,)).fetchone()
            if existing:
                skipped += 1
                continue
            work = catalog.execute('SELECT * FROM catalog_books WHERE work_id=?', (identifier,)).fetchone()
            if work is None:
                raise ValueError('Książki nie ma w zapisanym katalogu. Ponów wyszukiwanie.')
            # Link an exact title/author match without changing its local metadata or selection.
            match = library.execute('SELECT id,open_library_id FROM books WHERE title=? AND author=?',
                                    (work['title'], work['author'])).fetchone()
            if match and not match['open_library_id']:
                library.execute('UPDATE books SET open_library_id=? WHERE id=?', (identifier, match['id']))
                skipped += 1
            else:
                library.execute("INSERT INTO books(open_library_id,title,author,exclusive_shelf) VALUES (?,?,?,'to-read')",
                                (identifier, work['title'], work['author']))
                added += 1
    return {'added': added, 'skipped': skipped}
