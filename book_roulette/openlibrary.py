"""Small, user-initiated API lookups. Bulk imports use official dumps instead."""
import json
import re
import threading
import time
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

WORK_ID = re.compile(r'/works/OL[0-9]{1,18}W')
AUTHOR_ID = re.compile(r'/authors/OL[0-9]{1,18}A')
USER_AGENT = 'BookRoulette/1.0 (+https://github.com/Yorick0499/book-roulette)'
MAX_RESPONSE = 4 * 1024 * 1024
_network_lock = threading.Lock()
_last_request = 0.0


class OpenLibraryError(ValueError):
    pass


def validate_url(url):
    parsed = urlsplit(url)
    host = parsed.hostname or ''
    if (parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None, 443)
            or not (host in ('openlibrary.org', 'covers.openlibrary.org') or host == 'archive.org' or host.endswith('.archive.org'))):
        raise OpenLibraryError('Niedozwolony adres źródła Open Library.')
    return url


class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def open_stream(url, timeout=25):
    validate_url(url)
    request = Request(url, headers={'User-Agent': USER_AGENT, 'Accept-Encoding': 'identity'})
    try:
        return build_opener(SafeRedirect()).open(request, timeout=timeout)
    except HTTPError as error:
        if error.code == 429:
            message = 'Open Library ogranicza ruch (429). Poczekaj chwilę przed ponowieniem.'
        else:
            message = f'Źródło Open Library jest niedostępne (HTTP {error.code}). Sprawdź dostęp sieciowy i spróbuj później.'
        raise OpenLibraryError(message) from error
    except (URLError, TimeoutError, OSError) as error:
        raise OpenLibraryError('Brak połączenia z Open Library. Zapisany katalog nadal działa offline.') from error


def normalize_work(document):
    if not isinstance(document, dict):
        return None
    key = document.get('key', '')
    title = document.get('title', '')
    if not isinstance(key, str) or not WORK_ID.fullmatch(key) or not isinstance(title, str) or not title.strip():
        return None
    names = document.get('author_name', [])
    if not isinstance(names, list):
        names = []
    year = document.get('first_publish_year')
    return {'work_id': key, 'title': title.strip()[:2000],
            'author': ', '.join(name[:300] for name in names[:20] if isinstance(name, str))[:2000],
            'year': year if type(year) is int and 0 < year <= 9999 else None,
            'cover_id': positive_cover_id(document.get('cover_i'))}


def read_json(url):
    global _last_request
    # One shared limiter per local server, including concurrent LAN users.
    with _network_lock:
        delay = 1.1 - (time.monotonic() - _last_request)
        if delay > 0:
            time.sleep(delay)
        _last_request = time.monotonic()
        try:
            with open_stream(url) as response:
                content = response.read(MAX_RESPONSE + 1)
        except (OSError, TimeoutError, HTTPException) as error:
            raise OpenLibraryError('Przerwano odpowiedź Open Library. Spróbuj później; zapisany katalog pozostaje dostępny.') from error
    if len(content) > MAX_RESPONSE:
        raise OpenLibraryError('Odpowiedź Open Library przekracza bezpieczny limit rozmiaru.')
    try:
        return json.loads(content)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise OpenLibraryError('Open Library zwróciło nieprawidłowe dane. Spróbuj później.') from error


def positive_cover_id(value):
    return value if type(value) is int and 0 < value < 10**18 else None


def work_cover_id(identifier):
    if not WORK_ID.fullmatch(identifier):
        return None
    payload = read_json('https://openlibrary.org' + identifier + '.json')
    covers = payload.get('covers', []) if isinstance(payload, dict) else []
    return next((value for item in covers if (value := positive_cover_id(item))), None) if isinstance(covers, list) else None


def search(query, page=1):
    params = {'q': query, 'page': page, 'limit': 24,
              'fields': 'key,title,author_name,first_publish_year,cover_i'}
    payload = read_json('https://openlibrary.org/search.json?' + urlencode(params))
    try:
        if not isinstance(payload, dict) or not isinstance(payload.get('docs'), list):
            raise ValueError
        documents = payload['docs']
        if len(documents) > 24:
            raise ValueError
        works = [work for document in documents if (work := normalize_work(document))]
        total = payload.get('numFound', payload.get('num_found', 0))
        return {'works': works, 'total': max(0, total) if type(total) is int else len(works)}
    except (ValueError, UnicodeError, RecursionError) as error:
        raise OpenLibraryError('Open Library zwróciło nieprawidłowe dane. Spróbuj później.') from error
