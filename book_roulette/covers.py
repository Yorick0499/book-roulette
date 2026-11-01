"""Bounded background cover loading: HTTP workers only read local cache/status."""
import os
import queue
import re
import tempfile
import threading
import time
from http.client import HTTPException
from pathlib import Path

from flask import Blueprint, Response, current_app, jsonify, request, url_for

from .catalog import get_catalog
from .db import get_db
from .openlibrary import WORK_ID, OpenLibraryError, edition_key, open_stream, positive_cover_id, work_cover_id, work_edition_sources

bp = Blueprint('covers', __name__)
MAX_IMAGE_BYTES = 3 * 1024 * 1024
CACHE_KEY = re.compile(r'(?:[0-9]{1,18}|OL[0-9]{1,18}M)')


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


def cache_path(key):
    if not isinstance(key, str) or not CACHE_KEY.fullmatch(key):
        return None
    return Path(current_app.config['COVER_CACHE']) / f'{key}-M.img'


def cached_image(key):
    path = cache_path(key)
    try:
        if path and path.is_file() and path.stat().st_size <= MAX_IMAGE_BYTES:
            content = path.read_bytes()
            if mime := image_type(content):
                return content, mime
    except OSError:
        pass
    return None


def known_work(identifier):
    row = get_catalog().execute('SELECT * FROM catalog_books WHERE work_id=?', (identifier,)).fetchone()
    if row:
        return row
    book = get_db().execute('SELECT title,author FROM books WHERE open_library_id=?', (identifier,)).fetchone()
    if book:
        connection = get_catalog()
        with connection:
            connection.execute('INSERT OR IGNORE INTO catalog_books(work_id,title,author) VALUES (?,?,?)',
                               (identifier, book['title'], book['author']))
        return connection.execute('SELECT * FROM catalog_books WHERE work_id=?', (identifier,)).fetchone()
    return None


def record_status(identifier, state, key=None, delay=0):
    connection = get_catalog()
    with connection:
        connection.execute('INSERT INTO cover_work_status VALUES (?,?,?,?) ON CONFLICT(work_id) DO UPDATE '
                           'SET state=excluded.state,cache_key=excluded.cache_key,retry_after=excluded.retry_after',
                           (identifier, state, key, int(time.time()) + delay))


def fetch_image(key):
    if image := cached_image(key):
        return image
    kind = 'olid' if key.startswith('OL') else 'id'
    for attempt in range(2):
        try:
            with open_stream(f'https://covers.openlibrary.org/b/{kind}/{key}-M.jpg?default=false', timeout=5) as response:
                content = response.read(MAX_IMAGE_BYTES + 1)
            mime = image_type(content)
            if len(content) > MAX_IMAGE_BYTES or not mime:
                raise OpenLibraryError('Nieprawidłowy obraz okładki.')
            path = cache_path(key)
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.cover-', delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(content)
                os.replace(temporary, path)
            finally:
                if temporary and temporary.exists():
                    temporary.unlink()
            return content, mime
        except OpenLibraryError as error:
            if attempt == 0 and error.status in (500, 502, 503, 504) and not error.retry_after:
                time.sleep(.25)
                continue
            raise


def download_cover(identifier):
    row = known_work(identifier)
    if row is None:
        return
    visited = set()
    unavailable = False
    retry_after = 60

    def try_source(key):
        nonlocal unavailable, retry_after
        if key is None or key in visited:
            return False
        visited.add(key)
        try:
            fetch_image(key)
        except OpenLibraryError as error:
            if error.status != 404:
                unavailable = True
                retry_after = max(retry_after, error.retry_after or 0)
            return False
        except (OSError, TimeoutError, HTTPException):
            unavailable = True
            return False
        record_status(identifier, 'ready', key)
        return True

    primary = positive_cover_id(row['cover_id'])
    edition = edition_key(row['cover_edition'])
    if try_source(str(primary) if primary else None) or try_source(edition):
        return
    if unavailable and (primary or edition):
        record_status(identifier, 'unavailable', delay=retry_after)
        return
    # Search lacking cover_i does not prove that work/edition metadata has no cover.
    # Legacy negative checks were also written during searches; only resolved checks count.
    if not row['cover_resolved'] or time.time() - row['cover_checked'] >= 3600:
        try:
            alternative = work_cover_id(identifier)
            connection = get_catalog()
            with connection:
                connection.execute('UPDATE catalog_books SET cover_resolved=1,cover_checked=?,cover_id=COALESCE(?,cover_id) WHERE work_id=?',
                                   (int(time.time()), alternative, identifier))
            if alternative and try_source(str(alternative)):
                return
            for source in work_edition_sources(identifier):
                if try_source(source):
                    return
        except OpenLibraryError:
            unavailable = True
    record_status(identifier, 'unavailable' if unavailable else 'missing', delay=retry_after if unavailable else 3600)


class CoverLoader:
    def __init__(self, app):
        self.app = app
        self.jobs = queue.Queue(maxsize=48)
        self.pending = set()
        self.lock = threading.Lock()
        self.threads = []
        self.closed = threading.Event()

    def submit(self, identifier):
        with self.lock:
            if identifier in self.pending or self.closed.is_set():
                return
            if not self.threads:
                for index in range(2):
                    thread = threading.Thread(target=self._work, name=f'book-cover-{index}', daemon=True)
                    self.threads.append(thread)
                    thread.start()
            try:
                self.jobs.put_nowait(identifier)
            except queue.Full:
                return
            self.pending.add(identifier)

    def _work(self):
        while not self.closed.is_set():
            try:
                identifier = self.jobs.get(timeout=.2)
            except queue.Empty:
                continue
            try:
                with self.app.app_context():
                    try:
                        download_cover(identifier)
                    except Exception:
                        try:
                            record_status(identifier, 'unavailable', delay=60)
                        except Exception:
                            self.app.logger.warning('Nie można zapisać stanu pobierania okładki.')
            finally:
                with self.lock:
                    self.pending.discard(identifier)
                self.jobs.task_done()

    def wait_idle(self, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.lock:
                if not self.pending:
                    return True
            time.sleep(.01)
        return False

    def shutdown(self):
        self.closed.set()
        for thread in self.threads:
            thread.join(timeout=.3)


def current_state(identifier):
    row = known_work(identifier)
    if row is None:
        return 'missing', None
    status = get_catalog().execute('SELECT * FROM cover_work_status WHERE work_id=?', (identifier,)).fetchone()
    if status and status['state'] == 'ready' and cached_image(status['cache_key']):
        return 'ready', status['cache_key']
    if status and status['state'] != 'ready' and status['retry_after'] > time.time():
        return status['state'], None
    # Reuse older image cache immediately, even without internet or status metadata.
    primary = positive_cover_id(row['cover_id'])
    if primary and cached_image(str(primary)):
        return 'ready', str(primary)
    current_app.extensions['cover_loader'].submit(identifier)
    return 'pending', None


@bp.get('/covers/<olid>')
def image(olid):
    identifier = '/works/' + olid
    state, key = current_state(identifier) if WORK_ID.fullmatch(identifier) else ('missing', None)
    if request.accept_mimetypes.best == 'application/json':
        return jsonify(state=state, url=cover_url(identifier) if state == 'ready' else None)
    if state == 'ready':
        cached = cached_image(key)
        if cached:
            content, mime = cached
            return Response(content, mimetype=mime)
    response = Response(status=202 if state == 'pending' else 204)
    response.headers['X-Cover-State'] = state
    if state == 'pending':
        response.headers['Retry-After'] = '1'
    return response
