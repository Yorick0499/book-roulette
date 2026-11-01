import gzip
import io
import json
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError

from book_roulette import create_app
from book_roulette.catalog import UPSERT_WORK, get_catalog
from book_roulette.catalog_download import DownloadManager, import_record
from book_roulette.importer import import_books
from book_roulette.openlibrary import OpenLibraryError, normalize_work, open_stream, search, validate_url

WORK = {'work_id': '/works/OL123W', 'title': 'Fikcyjna książka', 'author': 'Fikcyjny Autor', 'year': 2020}


def tsv(key, **values):
    kind = 'author' if '/authors/' in key else 'work'
    document = {'key': key, **values}
    return f'/type/{kind}\t{key}\t1\t2020-01-01T00:00:00\t{json.dumps(document)}\n'.encode()


class Response(io.BytesIO):
    def __init__(self, content):
        super().__init__(content)
        self.headers = {'Content-Length': str(len(content))}


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = str(Path(self.directory.name) / 'private.sqlite3')
        self.app = create_app({'TESTING': True, 'DATABASE': self.database, 'SECRET_KEY': 'fictional-test-key'})
        self.client = self.app.test_client()
        self.token = re.search(r'name="csrf_token" value="([^"]+)"', self.client.get('/import').get_data(as_text=True))[1]
        with self.app.app_context():
            connection = get_catalog()
            with connection:
                connection.execute(UPSERT_WORK, WORK)

    def post(self, url, **data):
        return self.client.post(url, data={'csrf_token': self.token, **data})

    def test_local_search_offline_and_safe_html(self):
        with patch('book_roulette.openlibrary.open_stream', side_effect=AssertionError('Offline must not use network')):
            response = self.client.get('/catalog?q=fikcyjna')
            self.assertEqual(response.status_code, 200)
            self.assertIn(WORK['title'], response.get_data(as_text=True))
            self.assertNotIn(WORK['title'], self.client.get('/').get_data(as_text=True))
            self.assertEqual(self.client.get('/catalog?q=%22%20OR%20DROP').status_code, 200)
        with self.app.app_context():
            connection = get_catalog()
            with connection:
                connection.execute(UPSERT_WORK, {**WORK, 'title': '<script>Nie wykonuj</script>'})
        html = self.client.get('/catalog').get_data(as_text=True)
        self.assertIn('&lt;script&gt;Nie wykonuj&lt;/script&gt;', html)

    def test_api_explicit_request_cache_and_network_failure(self):
        with patch('book_roulette.openlibrary.search', return_value={'works': [WORK], 'total': 1}) as fetch:
            self.assertEqual(self.post('/catalog/online', q='Fikcyjna', mode='online').status_code, 303)
            self.assertEqual(self.post('/catalog/online', q='Fikcyjna', mode='online').status_code, 303)
            fetch.assert_called_once_with('Fikcyjna', 1)
        self.assertEqual(self.client.get('/catalog?q=Fikcyjna&mode=online').status_code, 200)
        with patch('book_roulette.openlibrary.search', side_effect=OpenLibraryError('Brak połączenia')):
            response = self.post('/catalog/online', q='Inna fikcyjna', mode='online')
            self.assertEqual(response.status_code, 502)
            self.assertIn('Brak połączenia', response.get_data(as_text=True))
        self.assertIn(WORK['title'], self.client.get('/catalog').get_data(as_text=True))
        self.assertEqual(self.client.post('/catalog/online', data={'q': 'test'}).status_code, 400)
        self.assertEqual(self.post('/catalog/online', q='x').status_code, 400)

    def test_add_deduplication_selection_goodreads_and_draw(self):
        self.assertEqual(self.post('/catalog/add', work_id=WORK['work_id']).status_code, 303)
        with sqlite3.connect(self.database) as connection:
            row = connection.execute('SELECT id,goodreads_id,open_library_id,selected,exclusive_shelf FROM books').fetchone()
            self.assertEqual(row[1:], (None, WORK['work_id'], 0, 'to-read'))
            connection.execute('UPDATE books SET selected=1')
        self.post('/catalog/add', work_id=WORK['work_id'])
        with sqlite3.connect(self.database) as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM books').fetchone()[0], 1)
            result = import_books(connection, 'Book Id,Title,Author\n456,Fikcyjna książka,Fikcyjny Autor\n'.encode())
            self.assertEqual(result, {'added': 0, 'updated': 1, 'skipped': 0})
            self.assertEqual(connection.execute('SELECT selected,goodreads_id,open_library_id FROM books').fetchone(),
                             (1, '456', WORK['work_id']))
        response = self.client.post('/api/draw', json={'key': 'fictional_catalog_draw'}, headers={'X-CSRF-Token': self.token})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['winner']['open_library_id'], WORK['work_id'])
        self.assertEqual(self.post('/catalog/add', work_id='/works/OL999W').status_code, 400)
        self.assertEqual(self.post('/catalog/add', work_id='https://evil.example/').status_code, 400)
        self.assertEqual(self.client.post('/catalog/add').status_code, 400)

    def test_link_existing_goodreads_book_does_not_overwrite_it(self):
        with sqlite3.connect(self.database) as connection:
            connection.execute('INSERT INTO books(title,author,goodreads_id,selected,my_rating) VALUES (?,?,?,1,5)',
                               (WORK['title'], WORK['author'], '789'))
        self.post('/catalog/add', work_id=WORK['work_id'])
        with sqlite3.connect(self.database) as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM books').fetchone()[0], 1)
            self.assertEqual(connection.execute('SELECT selected,my_rating,goodreads_id,open_library_id FROM books').fetchone(),
                             (1, 5, '789', WORK['work_id']))

    def test_legacy_schema_upgrade_preserves_books_and_choices(self):
        old_database = Path(self.directory.name) / 'legacy.sqlite3'
        schema = Path('book_roulette/schema.sql').read_text().replace('    open_library_id TEXT,\n', '')
        with sqlite3.connect(old_database) as connection:
            connection.executescript(schema)
            connection.execute("INSERT INTO books(title,selected) VALUES ('Stara książka',1)")
        config = {'TESTING': True, 'DATABASE': str(old_database), 'SECRET_KEY': 'test-legacy'}
        create_app(config)
        create_app(config)
        with sqlite3.connect(old_database) as connection:
            self.assertEqual(connection.execute('SELECT title,selected,open_library_id FROM books').fetchone(), ('Stara książka', 1, None))

    def test_confirmation_and_disk_floor_block_unintended_download(self):
        self.assertEqual(self.post('/catalog/download').status_code, 400)
        with patch('book_roulette.catalog_download.shutil.disk_usage', return_value=SimpleNamespace(free=0)):
            self.assertEqual(self.post('/catalog/download', confirm='yes').status_code, 409)
        self.assertEqual(self.client.get('/catalog/status').get_json()['job'], None)

    def run_download(self, sources):
        manager = self.app.extensions['catalog_downloader']
        with patch('book_roulette.catalog_download.open_stream', side_effect=sources), patch('book_roulette.catalog_download.MIN_FREE_BYTES', 0):
            manager.start()
            manager.thread.join(timeout=10)
            self.assertFalse(manager.thread.is_alive())
        return self.client.get('/catalog/status').get_json()['job']

    def test_official_dump_streaming_author_resolution_and_restart(self):
        authors = gzip.compress(tsv('/authors/OL42A', name='Autor ze zrzutu'))
        works = gzip.compress(tsv('/works/OL42W', title='Fikcyjny utwór', authors=[{'author': {'key': '/authors/OL42A'}}], first_publish_date='2001'))
        job = self.run_download([Response(authors), Response(works)])
        self.assertEqual(job['state'], 'completed')
        self.assertEqual(job['records'], 2)
        self.assertIn('Autor ze zrzutu', self.client.get('/catalog?q=utwór').get_data(as_text=True))
        job = self.run_download([Response(authors), Response(works)])
        self.assertEqual(job['state'], 'completed')
        with self.app.app_context():
            self.assertEqual(get_catalog().execute('SELECT COUNT(*) FROM catalog_books WHERE work_id=?', ('/works/OL42W',)).fetchone()[0], 1)
            self.assertEqual(get_catalog().execute("SELECT COUNT(*) FROM catalog_fts WHERE catalog_fts MATCH 'utwór'").fetchone()[0], 1)

    def test_corrupt_dump_is_failed_and_previous_catalog_remains(self):
        job = self.run_download([Response(b'not a gzip')])
        self.assertEqual(job['state'], 'failed')
        self.assertIn(WORK['title'], self.client.get('/catalog').get_data(as_text=True))

    def test_cancellation_and_worker_exclusion(self):
        manager = self.app.extensions['catalog_downloader']
        with self.app.app_context():
            connection = get_catalog()
            with connection:
                identifier = connection.execute("INSERT INTO catalog_jobs(state,cancel_requested) VALUES ('running',1)").lastrowid
        handle = manager._lock()
        self.assertIsNotNone(handle)
        with patch('book_roulette.catalog_download.MIN_FREE_BYTES', 0), self.assertRaises(OpenLibraryError):
            manager.start()
        with patch('book_roulette.catalog_download.open_stream', return_value=Response(gzip.compress(tsv('/authors/OL1A', name='Test')))):
            manager._run(identifier, handle)
        self.assertEqual(self.client.get('/catalog/status').get_json()['job']['state'], 'cancelled')
        with self.app.app_context():
            connection = get_catalog()
            with connection:
                connection.execute("INSERT INTO catalog_jobs(state) VALUES ('running')")
        DownloadManager(self.app.config['CATALOG_DATABASE'])
        self.assertEqual(self.client.get('/catalog/status').get_json()['job']['state'], 'interrupted')


class ClientTests(unittest.TestCase):
    def test_url_and_remote_document_validation(self):
        for url in ['http://openlibrary.org/search.json', 'https://evil.example/', 'https://openlibrary.org.evil.example/',
                    'https://user:pass@openlibrary.org/', 'https://openlibrary.org:8000/']:
            with self.subTest(url=url), self.assertRaises(OpenLibraryError):
                validate_url(url)
        self.assertEqual(validate_url('https://ia123.us.archive.org/data'), 'https://ia123.us.archive.org/data')
        self.assertIsNone(normalize_work({'key': 'https://evil.example', 'title': 'Test'}))
        self.assertIsNone(normalize_work({'key': '/works/OL1W', 'title': []}))
        normalized = normalize_work({'key': '/works/OL1W', 'title': 'Test', 'author_name': ['Autor', 123], 'first_publish_year': True})
        self.assertEqual(normalized['author'], 'Autor')
        self.assertIsNone(normalized['year'])

    def test_rate_limit_and_redirect_failures_are_friendly(self):
        for status in [403, 429, 503]:
            with self.subTest(status=status), patch('book_roulette.openlibrary.build_opener') as opener:
                opener.return_value.open.side_effect = HTTPError('https://openlibrary.org/search.json', status, 'Unavailable', {}, None)
                with self.assertRaises(OpenLibraryError) as error:
                    open_stream('https://openlibrary.org/search.json')
                self.assertIn(str(status), str(error.exception))

    def test_api_shape_query_encoding_and_response_limit(self):
        body = json.dumps({'numFound': 1, 'docs': [{'key': '/works/OL1W', 'title': 'Fikcyjna', 'author_name': ['Autor']}]}).encode()
        with patch('book_roulette.openlibrary.open_stream', return_value=Response(body)) as fetch, patch('book_roulette.openlibrary.time.sleep'):
            result = search('Żółw & tytuł', 2)
            self.assertEqual(result['works'][0]['work_id'], '/works/OL1W')
            self.assertIn('page=2', fetch.call_args.args[0])
            self.assertIn('%26', fetch.call_args.args[0])
        with patch('book_roulette.openlibrary.open_stream', return_value=Response(b'invalid')), patch('book_roulette.openlibrary.time.sleep'):
            with self.assertRaises(OpenLibraryError):
                search('test')
        with patch('book_roulette.openlibrary.open_stream', return_value=Response(b'x'*100)), patch('book_roulette.openlibrary.MAX_RESPONSE', 10), patch('book_roulette.openlibrary.time.sleep'):
            with self.assertRaises(OpenLibraryError):
                search('test')


if __name__ == '__main__':
    unittest.main()
