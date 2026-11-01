"""Goodreads CSV adapter. Uploaded files are parsed in memory, never retained."""
import csv
import io
import math
import re


class ImportError(ValueError):
    pass


FIELDS = {
    'Book Id': 'goodreads_id', 'Title': 'title', 'Author': 'author',
    'Additional Authors': 'additional_authors', 'ISBN': 'isbn', 'ISBN13': 'isbn13',
    'Bookshelves': 'bookshelves', 'Exclusive Shelf': 'exclusive_shelf',
    'Date Read': 'date_read', 'Date Added': 'date_added',
}


def rating(value, integer=False):
    if not value:
        return None
    number = float(value)
    if not math.isfinite(number) or not 0 <= number <= 5 or (integer and not number.is_integer()):
        raise ValueError
    return int(number) if integer else number


def parse_csv(content):
    try:
        text = content.decode('utf-8-sig')
    except UnicodeDecodeError as error:
        raise ImportError('Plik musi mieć kodowanie UTF-8. Wyeksportuj ponownie bibliotekę Goodreads.') from error
    if '\x00' in text:
        raise ImportError('Plik zawiera niedozwolone znaki binarne.')
    reader = csv.DictReader(io.StringIO(text, newline=''), strict=True)
    try:
        headers = reader.fieldnames
        if not headers or not {'Book Id', 'Title', 'Author'} <= set(headers):
            raise ImportError('Brakuje nagłówków Book Id, Title lub Author. Wybierz eksport CSV Goodreads.')
        if len(headers) != len(set(headers)):
            raise ImportError('Nagłówki CSV nie mogą się powtarzać.')
        books, seen, skipped = [], set(), 0
        for index, row in enumerate(reader, 1):
            if index > 50000:
                raise ImportError('Plik przekracza limit 50 000 wierszy.')
            if None in row or any(value is None for value in row.values()):
                raise ImportError('Nieprawidłowa liczba kolumn w pliku CSV.')
            book = {field: row.get(header, '').strip() for header, field in FIELDS.items()}
            identifier = book['goodreads_id']
            if not re.fullmatch(r'[0-9]{1,20}', identifier) or not book['title'] or identifier in seen:
                skipped += 1
                continue
            try:
                book['my_rating'] = rating(row.get('My Rating', '').strip(), integer=True)
                book['average_rating'] = rating(row.get('Average Rating', '').strip())
            except ValueError:
                skipped += 1
                continue
            for field in ('isbn', 'isbn13'):
                # Goodreads wraps ISBNs as spreadsheet strings: ="0123456789".
                value = book[field]
                if value.startswith('="') and value.endswith('"'):
                    value = value[2:-1]
                book[field] = value
            seen.add(identifier)
            books.append(book)
        return books, skipped
    except csv.Error as error:
        raise ImportError('Nie można odczytać CSV. Sprawdź separator i cudzysłowy w pliku.') from error


def import_books(connection, content):
    books, skipped = parse_csv(content)
    added = updated = 0
    # Parse first, then commit everything atomically. Local selection is never updated.
    with connection:
        for book in books:
            existing = connection.execute('SELECT id FROM books WHERE goodreads_id = ?',
                                          (book['goodreads_id'],)).fetchone()
            if existing:
                fields = [field for field in book if field != 'goodreads_id']
                connection.execute('UPDATE books SET ' + ', '.join(f'{field} = ?' for field in fields)
                                   + ' WHERE goodreads_id = ?',
                                   [book[field] for field in fields] + [book['goodreads_id']])
                updated += 1
            else:
                fields = list(book)
                connection.execute('INSERT INTO books (' + ', '.join(fields) + ') VALUES ('
                                   + ', '.join('?' for _ in fields) + ')', list(book.values()))
                added += 1
    return {'added': added, 'updated': updated, 'skipped': skipped}


def load_demo(connection):
    """An explicit, empty-library-only source adapter with no Goodreads IDs."""
    from pathlib import Path
    source = Path(__file__).resolve().parent.parent / 'examples' / 'library-demo.csv'
    with connection:
        connection.execute('BEGIN IMMEDIATE')
        if connection.execute('SELECT COUNT(*) FROM books').fetchone()[0]:
            return False
        with source.open(encoding='utf-8', newline='') as stream:
            for row in csv.DictReader(stream):
                connection.execute('INSERT INTO books(title,author,bookshelves,exclusive_shelf) VALUES (?,?,?,?)',
                                   (row['Title'], row['Author'], row['Bookshelves'], row['Exclusive Shelf']))
    return True
