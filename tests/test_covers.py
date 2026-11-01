import base64
import io
import json
import re
import sqlite3
import tempfile
import unittest
import threading
import time
from pathlib import Path
from unittest.mock import patch

from book_roulette import create_app
from book_roulette.catalog import UPSERT_WORK, fetch_search, get_catalog
from book_roulette.openlibrary import OpenLibraryError, normalize_work

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jZQAAAABJRU5ErkJggg==')
WORK = {'work_id': '/works/OL123W', 'title': 'Fikcyjna z okładką', 'author': 'Autor', 'year': 2020, 'cover_id': 1234}


class CoverTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = str(Path(self.directory.name) / 'private.sqlite3')
        self.config = {'TESTING': True, 'DATABASE': self.database, 'SECRET_KEY': 'fictional-cover-test'}
        metadata = patch('book_roulette.covers.work_cover_id', return_value=None)
        metadata.start()
        self.addCleanup(metadata.stop)
        editions = patch('book_roulette.covers.work_edition_sources', return_value=[])
        editions.start()
        self.addCleanup(editions.stop)
        self.app = create_app(self.config)
        self.addCleanup(self.stop_loader)
        self.client = self.app.test_client()
        with self.app.app_context(), patch('book_roulette.openlibrary.search', return_value={'works': [WORK], 'total': 1}):
            fetch_search(get_catalog(), 'Fikcyjna', 1)

    def stop_loader(self):
        loader = self.app.extensions['cover_loader']
        loader.wait_idle()
        loader.shutdown()

    def image(self, path='/covers/OL123W'):
        response = self.client.get(path)
        if response.status_code == 202:
            self.assertTrue(self.app.extensions['cover_loader'].wait_idle())
            response = self.client.get(path)
        return response

    def test_cover_field_validation_and_search_persistence(self):
        for invalid in [None, -1, 0, '123', True, 10**18, 'https://evil.example']:
            result = normalize_work({'key': '/works/OL1W', 'title': 'Test', 'cover_i': invalid})
            self.assertIsNone(result['cover_id'])
        result = normalize_work({'key': '/works/OL1W', 'title': 'Test', 'cover_i': 99})
        self.assertEqual(result['cover_id'], 99)
        with self.app.app_context():
            self.assertEqual(get_catalog().execute('SELECT cover_id FROM catalog_books').fetchone()[0], 1234)
        self.assertIn('/covers/OL123W', self.client.get('/catalog').get_data(as_text=True))

    def test_image_cache_is_offline_and_persists_across_restart(self):
        with patch('book_roulette.covers.open_stream', return_value=io.BytesIO(PNG)) as fetch:
            response = self.image()
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.mimetype, 'image/png')
            self.assertEqual(response.data, PNG)
            fetch.assert_called_once_with('https://covers.openlibrary.org/b/id/1234-M.jpg?default=false', timeout=5)
        restarted = create_app(self.config).test_client()
        with patch('book_roulette.covers.open_stream', side_effect=AssertionError('Must use disk cache')):
            self.assertEqual(restarted.get('/covers/OL123W').data, PNG)

    def test_network_error_and_missing_cover_have_negative_cache(self):
        with patch('book_roulette.covers.open_stream', side_effect=OpenLibraryError('404')) as fetch:
            self.assertEqual(self.image().status_code, 204)
            self.assertEqual(self.image().status_code, 204)
            fetch.assert_called_once()
        self.assertEqual(self.client.get('/covers/not-valid').status_code, 204)
        self.assertEqual(self.client.get('/covers/OL999W').status_code, 204)

    def test_bad_or_oversized_content_is_not_cached(self):
        for data in [b'<svg onload="alert(1)"></svg>', PNG*10]:
            with self.subTest(size=len(data)), patch('book_roulette.covers.open_stream', return_value=io.BytesIO(data)), patch('book_roulette.covers.MAX_IMAGE_BYTES', 100):
                with self.app.app_context():
                    connection = get_catalog()
                    with connection:
                        connection.execute('DELETE FROM cover_work_status')
                self.assertEqual(self.image().status_code, 204)
                self.assertFalse((Path(self.app.config['COVER_CACHE'])/'1234-M.img').exists())

    def test_pending_image_does_not_block_pages_or_duplicate_download(self):
        entered, release = threading.Event(), threading.Event()
        def slow_image(*args, **kwargs):
            entered.set()
            release.wait(3)
            return io.BytesIO(PNG)
        with patch('book_roulette.covers.open_stream', side_effect=slow_image) as fetch:
            try:
                start = time.monotonic()
                response = self.client.get('/covers/OL123W')
                self.assertEqual(response.status_code, 202)
                self.assertLess(time.monotonic()-start, .5)
                self.assertTrue(entered.wait(1))
                for _ in range(3):
                    self.assertEqual(self.client.get('/covers/OL123W', headers={'Accept': 'application/json'}).get_json()['state'], 'pending')
                start = time.monotonic()
                self.assertEqual(self.client.get('/library').status_code, 200)
                self.assertLess(time.monotonic()-start, .5)
                fetch.assert_called_once()
            finally:
                release.set()
                self.assertTrue(self.app.extensions['cover_loader'].wait_idle())
        self.assertEqual(self.image().data, PNG)

    def test_temporary_503_is_retried_without_caching_as_missing(self):
        with patch('book_roulette.covers.open_stream', side_effect=[OpenLibraryError('Unavailable', status=503), io.BytesIO(PNG)]) as fetch:
            self.assertEqual(self.image().status_code, 200)
            self.assertEqual(fetch.call_count, 2)
        status = self.client.get('/covers/OL123W', headers={'Accept': 'application/json'}).get_json()
        self.assertEqual(status['state'], 'ready')

    def test_failed_cover_id_falls_back_to_edition(self):
        with self.app.app_context():
            connection = get_catalog()
            with connection:
                connection.execute("UPDATE catalog_books SET cover_edition='OL123M'")
        with patch('book_roulette.covers.open_stream', side_effect=[OpenLibraryError('Missing', status=404), io.BytesIO(PNG)]) as fetch:
            self.assertEqual(self.image().data, PNG)
            self.assertIn('/olid/OL123M-M.jpg', fetch.call_args.args[0])

    def test_new_cover_metadata_invalidates_an_old_negative_result(self):
        with self.app.app_context():
            connection = get_catalog()
            with connection:
                connection.execute("INSERT INTO cover_work_status VALUES (?,'missing',NULL,?)", (WORK['work_id'], int(time.time())+3600))
            new = {**WORK, 'cover_id': 55, 'cover_edition': 'OL55M'}
            with patch('book_roulette.openlibrary.search', return_value={'works': [new], 'total': 1}):
                fetch_search(connection, 'Nowe metadane', 1)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM cover_work_status').fetchone()[0], 0)
        with patch('book_roulette.covers.open_stream', return_value=io.BytesIO(PNG)) as fetch:
            self.assertEqual(self.image().data, PNG)
            self.assertIn('/id/55-M.jpg', fetch.call_args.args[0])

    def test_missing_search_cover_does_not_suppress_metadata_lookup(self):
        with self.app.app_context():
            connection = get_catalog()
            with connection:
                connection.execute('UPDATE catalog_books SET cover_id=NULL,cover_checked=?', (int(time.time()),))
        with patch('book_roulette.covers.work_cover_id', return_value=55) as metadata, patch('book_roulette.covers.open_stream', return_value=io.BytesIO(PNG)):
            self.assertEqual(self.image().data, PNG)
            metadata.assert_called_once()

    def test_real_absence_is_different_from_service_failure(self):
        with patch('book_roulette.covers.open_stream', side_effect=OpenLibraryError('Missing', status=404)):
            self.assertEqual(self.image().status_code, 204)
        self.assertEqual(self.client.get('/covers/OL123W', headers={'Accept': 'application/json'}).get_json()['state'], 'missing')
        with self.app.app_context():
            connection = get_catalog()
            with connection:
                connection.execute('DELETE FROM cover_work_status')
        with patch('book_roulette.covers.open_stream', side_effect=OpenLibraryError('Blocked', status=403)):
            self.assertEqual(self.image().status_code, 204)
        self.assertEqual(self.client.get('/covers/OL123W', headers={'Accept': 'application/json'}).get_json()['state'], 'unavailable')

    def test_old_search_cache_refresh_and_old_saved_book_backfill(self):
        with self.app.app_context():
            connection = get_catalog()
            old = {'works': [{k:v for k,v in WORK.items() if k != 'cover_id'}], 'total': 1}
            with connection:
                connection.execute('UPDATE catalog_searches SET payload=?', (json.dumps(old),))
            with patch('book_roulette.openlibrary.search', return_value={'works': [WORK], 'total': 1}) as fetch:
                fetch_search(connection, 'Fikcyjna', 1)
                fetch.assert_called_once()
            with connection:
                connection.execute('UPDATE catalog_books SET cover_id=NULL,cover_checked=0')
        with patch('book_roulette.covers.work_cover_id', return_value=55) as metadata, patch('book_roulette.covers.open_stream', return_value=io.BytesIO(PNG)):
            self.assertEqual(self.image().status_code, 200)
            metadata.assert_called_once_with(WORK['work_id'])
        with self.app.app_context():
            connection = get_catalog()
            with connection:
                connection.execute('DELETE FROM catalog_books')
        with sqlite3.connect(self.database) as connection:
            connection.execute("INSERT INTO books(open_library_id,title,author,selected,exclusive_shelf) VALUES (?,?,?,1,'to-read')",
                               (WORK['work_id'], WORK['title'], WORK['author']))
        with patch('book_roulette.covers.work_cover_id', return_value=55), patch('book_roulette.covers.open_stream', side_effect=AssertionError('Cached file should survive metadata loss')):
            self.assertEqual(self.image().data, PNG)
        self.assertIn('/covers/OL123W', self.client.get('/library').get_data(as_text=True))
        with sqlite3.connect(self.database) as connection:
            self.assertEqual(connection.execute('SELECT selected FROM books').fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
