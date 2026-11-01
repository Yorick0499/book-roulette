import concurrent.futures
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from book_roulette import create_app


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = str(Path(self.directory.name) / 'test.sqlite3')
        self.config = {'TESTING': True, 'DATABASE': self.database, 'SECRET_KEY': 'fictional-test-session'}
        self.app = create_app(self.config)
        self.client = self.app.test_client()
        self.token = self.csrf(self.client)

    def csrf(self, client):
        text = client.get('/import').get_data(as_text=True)
        return re.search(r'name="csrf_token" value="([^"]+)"', text)[1]

    def post(self, url, **data):
        return self.client.post(url, data={'csrf_token': self.token, **data})

    def add_books(self, count):
        with sqlite3.connect(self.database) as connection:
            connection.executemany('INSERT INTO books(title,author,exclusive_shelf,selected) VALUES (?,?,?,1)',
                                   [(f'Książka {index:03}', 'ŻÓŁW Fikcyjny', 'to-read') for index in range(count)])

    def draw(self, key='fictional_request_key', client=None, token=None):
        return (client or self.client).post('/api/draw', json={'key': key}, headers={'X-CSRF-Token': token or self.token})

    def test_cli_import_and_localhost_lan_defaults(self):
        from book_roulette.__main__ import main
        source = Path(self.directory.name) / 'private.csv'
        source.write_text('Book Id,Title,Author\n123,Fikcyjna,Autor\n', encoding='utf-8')
        with patch('book_roulette.__main__.create_app', return_value=self.app), patch('book_roulette.__main__.serve') as serve:
            with patch('sys.argv', ['book_roulette', '--import-csv', str(source)]):
                main()
            self.assertEqual(serve.call_args.kwargs['host'], '127.0.0.1')
            self.assertFalse(serve.call_args.args[0].debug)
            with patch('sys.argv', ['book_roulette', '--lan', '--port', '8090']):
                main()
            self.assertEqual(serve.call_args.kwargs['host'], '0.0.0.0')
            self.assertEqual(serve.call_args.kwargs['port'], 8090)
        with sqlite3.connect(self.database) as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM books').fetchone()[0], 1)

    def test_empty_states_csrf_and_unknown_routes(self):
        for url in ['/', '/library', '/wheel', '/history', '/import']:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.draw().status_code, 409)
        self.assertEqual(self.client.post('/api/draw', json={'key': 'fictional_request_key'}).status_code, 400)
        self.assertEqual(self.client.post('/library/selection', data={'action': 'clear'}).status_code, 400)
        self.assertEqual(self.client.get('/missing').status_code, 404)
        self.assertEqual(self.draw('bad').status_code, 400)

    def test_demo_source_has_no_goodreads_identifiers_and_is_not_duplicated(self):
        self.assertEqual(self.post('/import/demo').status_code, 303)
        with sqlite3.connect(self.database) as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM books').fetchone()[0], 12)
            self.assertEqual(connection.execute('SELECT COUNT(goodreads_id) FROM books').fetchone()[0], 0)
        self.assertEqual(self.post('/import/demo').status_code, 409)
        self.assertIn('Ogród cichych poranków', self.client.get('/library').get_data(as_text=True))

    def test_filter_unicode_shelves_sort_and_preferences_survive_restart(self):
        with sqlite3.connect(self.database) as connection:
            connection.executemany('INSERT INTO books(title,author,exclusive_shelf,bookshelves,average_rating) VALUES (?,?,?,?,?)', [
                ('Żółw', 'Autor A', 'to-read', 'fantastyka, polskie', 3),
                ('Druga', 'Autor B', 'read', 'fantastyka', 5),
                ('Trzecia', 'Autor C', 'read', '', 4)])
        self.post('/library/filter', q='żółw', shelf='', sort='title')
        response = self.client.get('/library').get_data(as_text=True)
        self.assertIn('<h3>Żółw</h3>', response)
        self.assertNotIn('<h3>Druga</h3>', response)
        self.post('/library/filter', q='', shelf='fantastyka', sort='rating')
        response = self.client.get('/library').get_data(as_text=True)
        self.assertLess(response.index('<h3>Druga</h3>'), response.index('<h3>Żółw</h3>'))
        self.assertNotIn('<h3>Trzecia</h3>', response)
        restarted = create_app(self.config).test_client()
        self.assertIn('<h3>Druga</h3>', restarted.get('/library').get_data(as_text=True))
        self.post('/library/filter', shelf='', sort='title DESC; DROP TABLE books')
        self.assertEqual(self.client.get('/library').status_code, 200)

    def test_selection_bulk_page_validation_and_persistence(self):
        self.add_books(30)
        self.post('/library/selection', action='clear')
        ids = [str(index) for index in range(1, 25)]
        self.assertEqual(self.post('/library/selection', action='select', book_id=ids).status_code, 303)
        with sqlite3.connect(self.database) as connection:
            self.assertEqual(connection.execute('SELECT SUM(selected) FROM books').fetchone()[0], 24)
        self.assertEqual(self.post('/library/selection', action='select', book_id=ids+['25']).status_code, 400)
        self.assertEqual(self.post('/library/selection', action='select', book_id=['1 OR 1=1']).status_code, 400)
        self.post('/library/filter', shelf='', only_selected='1')
        self.assertIn('Książka 000', self.client.get('/library').get_data(as_text=True))
        self.post('/library/selection', action='deselect', book_id=['1'])
        restarted = create_app(self.config).test_client()
        self.assertNotIn('<h3>Książka 000</h3>', restarted.get('/library').get_data(as_text=True))
        self.post('/library/filter', shelf='')
        self.assertIn('Strona 2 z 2', self.client.get('/library?page=2').get_data(as_text=True))
        self.assertEqual(self.client.get('/library?page=invalid').status_code, 200)
        self.assertEqual(self.client.get('/library?page=999999999').status_code, 200)

    def test_winner_matches_snapshot_for_one_two_ten_and_hundred_books(self):
        for count in [1, 2, 10, 100]:
            with self.subTest(count=count), sqlite3.connect(self.database) as connection:
                connection.execute('UPDATE books SET selected=0')
                connection.executemany('INSERT INTO books(title,author,selected) VALUES (?,?,1)',
                                       [(f'Książka {count}-{index}', 'Fikcyjny autor') for index in range(count)])
            for index in [0, count-1]:
                with patch('book_roulette.views.secrets.randbelow', return_value=index) as random:
                    response = self.draw(f'fictional_draw_{count:03}_{index:03}')
                self.assertEqual(response.status_code, 200)
                payload = response.get_json()
                self.assertEqual(len(payload['candidates']), count)
                self.assertEqual(payload['winner'], payload['candidates'][index])
                # count=1 reuses the same key: one request, one saved result.
                if count > 1 or index == 0:
                    self.assertEqual(payload['winner_index'], index)
                if random.called:
                    random.assert_called_once_with(count)
        with sqlite3.connect(self.database) as connection:
            rows = connection.execute('SELECT title FROM draw_history').fetchall()
            self.assertEqual(len(rows), 7)
        self.assertIn('Książka 100-99', self.client.get('/history').get_data(as_text=True))
        self.assertIn('Książka 100-99', self.client.get('/').get_data(as_text=True))

    def test_retry_is_idempotent_and_history_preserves_original_title(self):
        self.add_books(2)
        first = self.draw().get_json()
        second = self.draw().get_json()
        self.assertEqual(first, second)
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE books SET title='Nowy tytuł'")
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM draw_history').fetchone()[0], 1)
        self.assertIn(first['winner']['title'], self.client.get('/history').get_data(as_text=True))
        restarted = create_app(self.config).test_client()
        self.assertIn(first['winner']['title'], restarted.get('/history').get_data(as_text=True))

    def test_concurrent_retries_create_one_history_entry(self):
        self.add_books(10)
        clients = [self.app.test_client() for _ in range(2)]
        tokens = [self.csrf(client) for client in clients]
        def run(index):
            return self.draw(client=clients[index], token=tokens[index])
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(run, range(2)))
        self.assertTrue(all(response.status_code == 200 for response in responses))
        self.assertEqual(responses[0].get_json(), responses[1].get_json())
        with sqlite3.connect(self.database) as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM draw_history').fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
