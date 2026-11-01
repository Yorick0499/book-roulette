"""Streaming official authors + works dumps; no bulk API harvesting."""
import fcntl
import gzip
import json
import re
import shutil
import threading
import time
from pathlib import Path

from .catalog import UPSERT_WORK, connect
from .openlibrary import AUTHOR_ID, WORK_ID, OpenLibraryError, open_stream, positive_cover_id

DUMPS = (
    ('authors', 'https://openlibrary.org/data/ol_dump_authors_latest.txt.gz'),
    ('works', 'https://openlibrary.org/data/ol_dump_works_latest.txt.gz'),
)
MIN_FREE_BYTES = 50 * 1024**3
MAX_LINE = 8 * 1024 * 1024


class CountingReader:
    def __init__(self, stream):
        self.stream = stream
        self.bytes_read = 0

    def read(self, size=-1):
        data = self.stream.read(size)
        self.bytes_read += len(data)
        return data


def import_record(connection, phase, line):
    parts = line.rstrip(b'\n').split(b'\t', 4)
    if len(parts) != 5:
        return False
    try:
        document = json.loads(parts[4])
    except (ValueError, UnicodeError):
        return False
    if not isinstance(document, dict):
        return False
    key = document.get('key', '')
    if not isinstance(key, str):
        return False
    if phase == 'authors':
        name = document.get('name')
        if not AUTHOR_ID.fullmatch(key) or not isinstance(name, str) or not name.strip():
            return False
        connection.execute('INSERT INTO catalog_authors VALUES (?,?) ON CONFLICT(author_id) DO UPDATE SET name=excluded.name',
                           (key, name.strip()[:1000]))
    else:
        title = document.get('title')
        if not WORK_ID.fullmatch(key) or not isinstance(title, str) or not title.strip():
            return False
        authors = document.get('authors', [])
        names = []
        if isinstance(authors, list):
            for author in authors[:20]:
                if not isinstance(author, dict) or not isinstance(author.get('author'), dict):
                    continue
                author_id = author['author'].get('key')
                if isinstance(author_id, str) and AUTHOR_ID.fullmatch(author_id):
                    row = connection.execute('SELECT name FROM catalog_authors WHERE author_id=?', (author_id,)).fetchone()
                    if row:
                        names.append(row['name'])
        date = document.get('first_publish_date', '')
        year = re.search(r'\b([12][0-9]{3})\b', date) if isinstance(date, str) else None
        connection.execute(UPSERT_WORK, {'work_id': key, 'title': title.strip()[:2000],
                           'author': ', '.join(names)[:2000], 'year': int(year[1]) if year else None})
        covers = document.get('covers', [])
        cover = next((value for item in covers if (value := positive_cover_id(item))), None) if isinstance(covers, list) else None
        connection.execute('UPDATE catalog_books SET cover_id=COALESCE(?,cover_id),cover_checked=? WHERE work_id=?',
                           (cover, int(time.time()), key))
    return True


class DownloadManager:
    def __init__(self, database):
        self.database = str(database)
        self.lock_path = str(Path(database).with_suffix('.download.lock'))
        self.thread = None
        # A lock, rather than a stale PID, detects an interrupted previous process.
        handle = self._lock()
        if handle:
            try:
                with connect(self.database) as connection:
                    connection.execute("UPDATE catalog_jobs SET state='interrupted',error='Serwer został zatrzymany. Możesz ponowić pobieranie od początku.' WHERE state='running'")
                connection.close()
            finally:
                handle.close()

    def _lock(self):
        handle = open(self.lock_path, 'a')
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            return None
        return handle

    def start(self):
        if shutil.disk_usage(Path(self.database).parent).free < MIN_FREE_BYTES:
            raise OpenLibraryError('Pełny katalog wymaga co najmniej 50 GiB wolnego miejsca na start. Skorzystaj na razie z wyszukiwania online.')
        handle = self._lock()
        if handle is None:
            raise OpenLibraryError('Pobieranie katalogu już trwa.')
        try:
            with connect(self.database) as connection:
                connection.execute("UPDATE catalog_jobs SET state='interrupted' WHERE state='running'")
                cursor = connection.execute("INSERT INTO catalog_jobs(state) VALUES ('running')")
                identifier = cursor.lastrowid
            connection.close()
            self.thread = threading.Thread(target=self._run, args=(identifier, handle), daemon=True,
                                           name='open-library-catalog')
            self.thread.start()
            return identifier
        except Exception:
            handle.close()
            raise

    def _run(self, identifier, handle):
        connection = connect(self.database)
        records = skipped = 0
        counter = None
        try:
            for phase, url in DUMPS:
                with connection:
                    connection.execute('UPDATE catalog_jobs SET phase=?,downloaded=0,total=NULL WHERE id=?', (phase, identifier))
                valid = 0
                with open_stream(url) as response:
                    raw_length = response.headers.get('Content-Length', '')
                    total = int(raw_length) if raw_length.isdigit() else None
                    counter = CountingReader(response)
                    with connection:
                        connection.execute('UPDATE catalog_jobs SET total=? WHERE id=?', (total, identifier))
                    last_progress = time.monotonic()
                    with gzip.GzipFile(fileobj=counter, mode='rb') as stream:
                        while True:
                            line = stream.readline(MAX_LINE + 1)
                            if not line:
                                break
                            if len(line) > MAX_LINE:
                                while line and not line.endswith(b'\n'):
                                    line = stream.readline(MAX_LINE + 1)
                                skipped += 1
                            elif import_record(connection, phase, line):
                                records += 1
                                valid += 1
                            else:
                                skipped += 1
                            if (records + skipped) % 1000 == 0 or time.monotonic() - last_progress >= 1:
                                connection.commit()
                                if self._progress(connection, identifier, records, skipped, counter.bytes_read):
                                    return
                                if shutil.disk_usage(Path(self.database).parent).free < 1024**3:
                                    raise OpenLibraryError('Pobieranie zatrzymane: pozostało mniej niż 1 GiB wolnego miejsca. Częściowy katalog zachowano.')
                                last_progress = time.monotonic()
                    connection.commit()
                    if self._progress(connection, identifier, records, skipped, counter.bytes_read):
                        return
                    if not valid:
                        raise OpenLibraryError('Zrzut nie zawiera poprawnych rekordów oczekiwanego typu. Częściowy katalog zachowano.')
            with connection:
                connection.execute("UPDATE catalog_jobs SET state='completed' WHERE id=?", (identifier,))
        except Exception as error:
            # Keep committed batches, never report an interrupted/invalid gzip as complete.
            connection.rollback()
            message = str(error) if isinstance(error, OpenLibraryError) else 'Błąd odczytu zrzutu lub zapisu katalogu. Częściowe dane zachowano; sprawdź miejsce na dysku i spróbuj ponownie.'
            with connection:
                connection.execute("UPDATE catalog_jobs SET state='failed',error=? WHERE id=?", (message, identifier))
        finally:
            connection.close()
            handle.close()

    def _progress(self, connection, identifier, records, skipped, downloaded):
        with connection:
            connection.execute('UPDATE catalog_jobs SET records=?,skipped=?,downloaded=? WHERE id=?',
                               (records, skipped, downloaded, identifier))
            cancelled = connection.execute('SELECT cancel_requested FROM catalog_jobs WHERE id=?', (identifier,)).fetchone()[0]
            if cancelled:
                connection.execute("UPDATE catalog_jobs SET state='cancelled' WHERE id=?", (identifier,))
        return bool(cancelled)
