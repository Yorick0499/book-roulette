"""Queries and preferences for a single, private local library."""
import math

SORTS = {
    'title': ('Tytuł A–Z', 'title COLLATE NOCASE, id'),
    'author': ('Autor A–Z', 'author COLLATE NOCASE, title COLLATE NOCASE, id'),
    'rating': ('Najwyższa ocena', 'average_rating DESC, title COLLATE NOCASE, id'),
    'newest': ('Ostatnio dodane', 'date_added DESC, id DESC'),
}
PAGE_SIZE = 24


def shelves_for(book):
    return set(filter(None, [book['exclusive_shelf']] +
                      [shelf.strip() for shelf in book['bookshelves'].split(',')]))


def get_preferences(connection):
    saved = dict(connection.execute('SELECT key, value FROM settings').fetchall())
    return {'q': saved.get('q', ''), 'shelf': saved.get('shelf', 'to-read'),
            'sort': saved.get('sort', 'title'), 'only_selected': saved.get('only_selected', '0')}


def normalize_preferences(values):
    return {'q': values.get('q', '').strip()[:200],
            'shelf': values.get('shelf', 'to-read').strip()[:200],
            'sort': values.get('sort') if values.get('sort') in SORTS else 'title',
            'only_selected': '1' if values.get('only_selected') == '1' else '0'}


def library_page(connection, preferences, page):
    # Casefold handles Polish Unicode; SQLite's built-in NOCASE is ASCII only.
    books = connection.execute('SELECT * FROM books ORDER BY ' + SORTS[preferences['sort']][1]).fetchall()
    shelves = sorted(set().union(*(shelves_for(book) for book in books))) if books else []
    query = preferences['q'].casefold()
    filtered = [book for book in books
                if (not query or query in (book['title'] + ' ' + book['author']).casefold())
                and (not preferences['shelf'] or preferences['shelf'] in shelves_for(book))
                and (preferences['only_selected'] != '1' or book['selected'])]
    pages = max(1, math.ceil(len(filtered) / PAGE_SIZE))
    page = min(max(1, page), pages)
    return {'books': filtered[(page-1)*PAGE_SIZE:page*PAGE_SIZE], 'shelves': shelves,
            'count': len(filtered), 'page': page, 'pages': pages}
