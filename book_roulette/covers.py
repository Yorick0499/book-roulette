"""Lazy, same-origin Covers API proxy with persistent local and negative caches."""
import os
from http.client import HTTPException
import tempfile
import threading
import time
from pathlib import Path

from flask import Blueprint, Response, current_app, url_for

from .catalog import get_catalog
from .db import get_db
from .openlibrary import WORK_ID, OpenLibraryError, open_stream, work_cover_id

bp = Blueprint('covers', __name__)
MAX_IMAGE_BYTES = 3 * 1024 * 1024
# Serialize each cover/work only, without blocking unrelated cached covers.
_locks = [threading.Lock() for _ in range(32)]


def cover_url(identifier):
    if not isinstance(identifier, str) or not WORK_ID.fullmatch(identifier):
        return None
    return url_for('covers.image', olid=identifier.rsplit('/', 1)[1])


def image_type(content):
    if content.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if content.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if content.startswith(b'RIFF') and content[8:12] == b'WEBP':
        return 'image/webp'
    return None


def resolve_cover(identifier):
    connection = get_catalog()
    row = connection.execute('SELECT cover_id,cover_checked FROM catalog_books WHERE work_id=?', (identifier,)).fetchone()
    if row and row['cover_id']:
        return row['cover_id']
    if row and time.time() - row['cover_checked'] < 3600:
        return None
    # Only resolve records already in our catalog or private library.
    if row is None:
        book = get_db().execute('SELECT title,author FROM books WHERE open_library_id=?', (identifier,)).fetchone()
        if book is None:
            return None
        with connection:
            connection.execute('INSERT OR IGNORE INTO catalog_books(work_id,title,author) VALUES (?,?,?)',
                               (identifier, book['title'], book['author']))
    try:
        cover = work_cover_id(identifier)
    except OpenLibraryError:
        cover = None
    with connection:
        connection.execute('UPDATE catalog_books SET cover_id=?,cover_checked=? WHERE work_id=?',
                           (cover, int(time.time()), identifier))
    return cover


@bp.get('/covers/<olid>')
def image(olid):
    identifier = '/works/' + olid
    if not WORK_ID.fullmatch(identifier):
        return Response(status=204)
    with _locks[hash(identifier) % len(_locks)]:
        cover = resolve_cover(identifier)
        if cover is None:
            return Response(status=204)
        directory = Path(current_app.config['COVER_CACHE'])
        path = directory / f'{cover}-M.img'
        if path.is_file() and path.stat().st_size <= MAX_IMAGE_BYTES:
            content = path.read_bytes()
            if mime := image_type(content):
                return Response(content, mimetype=mime)
        connection = get_catalog()
        failure = connection.execute('SELECT retry_after FROM cover_failures WHERE cover_id=?', (cover,)).fetchone()
        if failure and failure['retry_after'] > time.time():
            return Response(status=204)
        try:
            with open_stream(f'https://covers.openlibrary.org/b/id/{cover}-M.jpg?default=false', timeout=8) as response:
                content = response.read(MAX_IMAGE_BYTES + 1)
            mime = image_type(content)
            if len(content) > MAX_IMAGE_BYTES or not mime:
                raise OpenLibraryError('Nieprawidłowy obraz okładki.')
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=directory, prefix='.cover-', delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(content)
                os.replace(temporary, path)
            finally:
                if temporary and temporary.exists():
                    temporary.unlink()
        except (OpenLibraryError, OSError, TimeoutError, HTTPException):
            with connection:
                connection.execute('INSERT INTO cover_failures VALUES (?,?) ON CONFLICT(cover_id) DO UPDATE SET retry_after=excluded.retry_after',
                                   (cover, int(time.time()) + 1800))
            return Response(status=204)
        with connection:
            connection.execute('DELETE FROM cover_failures WHERE cover_id=?', (cover,))
        return Response(content, mimetype=mime)
