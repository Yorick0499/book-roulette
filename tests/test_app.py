import sqlite3
import tempfile
import unittest
from pathlib import Path

from book_roulette import create_app


class AppTests(unittest.TestCase):
    def test_home_and_persistent_database(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "test.sqlite3")
            config = {"TESTING": True, "DATABASE": database}
            app = create_app(config)
            response = app.test_client().get("/")
            self.assertEqual(response.status_code, 200)
            self.assertIn("Twoja biblioteka", response.get_data(as_text=True))
            self.assertIn(b'lang="pl"', response.data)
            with sqlite3.connect(database) as connection:
                connection.execute("INSERT INTO books (title, author, selected) VALUES (?, ?, 1)",
                                   ("Zażółć gęślą jaźń", "Autor"))
            restarted = create_app(config)
            response = restarted.test_client().get("/")
            self.assertIn("<strong>1</strong>", response.get_data(as_text=True))
            with sqlite3.connect(database) as connection:
                self.assertEqual(connection.execute("SELECT title FROM books").fetchone()[0],
                                 "Zażółć gęślą jaźń")
            with restarted.test_client().get("/static/style.css") as stylesheet:
                self.assertEqual(stylesheet.status_code, 200)


if __name__ == "__main__":
    unittest.main()
