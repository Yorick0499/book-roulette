import csv
import io
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

from book_roulette import create_app
from book_roulette.importer import ImportError, import_books, parse_csv


def export(rows):
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow(['Book Id', 'Title', 'Author', 'ISBN', 'My Rating', 'Average Rating', 'Exclusive Shelf'])
    writer.writerows(rows)
    return output.getvalue().encode('utf-8-sig')


ROW = ['123', 'Zażółć, gęślą jaźń', 'Fikcyjny Autor', '="0123456789"', '4', '3.75', 'to-read']


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = str(Path(self.directory.name) / 'library.sqlite3')
        self.app = create_app({'TESTING': True, 'DATABASE': self.database, 'SECRET_KEY': 'test-only-not-a-real-secret'})
        self.client = self.app.test_client()

    def test_unicode_missing_optional_fields_and_isbn(self):
        books, skipped = parse_csv(export([ROW]))
        self.assertEqual(skipped, 0)
        self.assertEqual(books[0]['isbn'], '0123456789')
        self.assertEqual(books[0]['title'], ROW[1])
        self.assertEqual(books[0]['additional_authors'], '')
        minimal, _ = parse_csv(b'Book Id,Title,Author\n1,Test,\n')
        self.assertIsNone(minimal[0]['my_rating'])

    def test_reimport_preserves_selection_history_settings_and_absent_books(self):
        with sqlite3.connect(self.database) as connection:
            result = import_books(connection, export([ROW, ROW, ['456', 'Inna', '', '', '', '', 'read']]))
            self.assertEqual(result, {'added': 2, 'updated': 0, 'skipped': 1})
            connection.execute('UPDATE books SET selected=1 WHERE goodreads_id=?', ('123',))
            connection.execute("INSERT INTO settings VALUES ('shelf', 'to-read')")
            connection.execute("INSERT INTO draw_history (book_id,title,author) VALUES (1,'Dawny tytuł','Autor')")
            connection.commit()
            changed = ROW.copy()
            changed[1] = 'Nowy tytuł'
            self.assertEqual(import_books(connection, export([changed])), {'added': 0, 'updated': 1, 'skipped': 0})
            self.assertEqual(connection.execute('SELECT title,selected FROM books WHERE goodreads_id=?', ('123',)).fetchone(), ('Nowy tytuł', 1))
            for table, count in [('books', 2), ('settings', 1), ('draw_history', 1)]:
                self.assertEqual(connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], count)

    def test_invalid_files_and_atomicity(self):
        for content in [b'', b'Title,Author\nTest,Test', b'\xff', b'Book Id,Title,Author\n1,"unterminated,Test', b'Book Id,Title,Author\n1,Title,Author,extra', b'Book Id,Title,Author,Author\n1,T,A,A', b'\x00']:
            with self.subTest(content=content), self.assertRaises(ImportError):
                parse_csv(content)
        with sqlite3.connect(self.database) as connection:
            with self.assertRaises(ImportError):
                import_books(connection, b'Book Id,Title,Author\n1,Valid,Author\n2,"broken')
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM books').fetchone()[0], 0)
        bad = ROW.copy()
        bad[4] = 'nan'
        self.assertEqual(parse_csv(export([bad, ['', '', '', '', '', '', '']]))[1], 2)
        self.assertEqual(parse_csv(b'Book Id,Title,Author\n')[0], [])

    def token(self):
        response = self.client.get('/import')
        return re.search(r'name="csrf_token" value="([^"]+)"', response.get_data(as_text=True))[1]

    def test_upload_csrf_redirect_summary_and_escaped_library(self):
        self.assertEqual(self.client.post('/import').status_code, 400)
        token = self.token()
        self.assertEqual(self.client.post('/import', data={'csrf_token': 'żółw'}).status_code, 400)
        row = ROW.copy()
        row[1] = '<script>alert(1)</script>'
        response = self.client.post('/import', data={'csrf_token': token, 'file': (io.BytesIO(export([row])), 'export.csv')})
        self.assertEqual(response.status_code, 303)
        page = self.client.get(response.location).get_data(as_text=True)
        self.assertIn('Dodano: 1', page)
        page = self.client.get('/').get_data(as_text=True)
        self.assertIn('&lt;script&gt;', page)
        self.assertNotIn('<script>alert', page)
        self.assertIn("frame-ancestors 'none'", self.client.get('/').headers['Content-Security-Policy'])
        self.assertEqual(self.client.post('/import', data={'csrf_token': token, 'file': (io.BytesIO(b'invalid'), '../../private.csv')}).status_code, 400)
        self.assertEqual(self.client.post('/import', data={'csrf_token': token, 'file': (io.BytesIO(export([ROW])), 'bad.txt')}).status_code, 400)
        self.app.config['MAX_CONTENT_LENGTH'] = 100
        self.assertEqual(self.client.post('/import', data={'csrf_token': token, 'file': (io.BytesIO(b'x'*200), 'big.csv')}).status_code, 413)


if __name__ == '__main__':
    unittest.main()
