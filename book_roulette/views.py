import json
import math
import re
import secrets

from flask import Blueprint, abort, jsonify, redirect, render_template, request, session, url_for

from .db import get_db
from .covers import cover_url
from .importer import ImportError, import_books, load_demo
from .library import SORTS, get_preferences, library_page, normalize_preferences

bp = Blueprint('web', __name__)


def positive_page():
    try:
        return max(1, int(request.args.get('page', '1')))
    except ValueError:
        return 1


def stats():
    return get_db().execute('SELECT COUNT(*) AS total, COALESCE(SUM(selected),0) AS selected FROM books').fetchone()


def last_draw():
    return get_db().execute('SELECT * FROM draw_history ORDER BY id DESC LIMIT 1').fetchone()


@bp.get('/')
def index():
    connection = get_db()
    books = connection.execute('SELECT * FROM books ORDER BY id DESC LIMIT 6').fetchall()
    return render_template('index.html', stats=stats(), books=books, last_draw=last_draw())


@bp.get('/library')
def library():
    connection = get_db()
    preferences = get_preferences(connection)
    return render_template('library.html', stats=stats(), preferences=preferences, sorts=SORTS,
                           **library_page(connection, preferences, positive_page()))


@bp.post('/library/filter')
def filter_library():
    preferences = normalize_preferences(request.form)
    connection = get_db()
    with connection:
        connection.executemany('INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                               preferences.items())
    return redirect(url_for('web.library'), code=303)


@bp.post('/library/selection')
def select_books():
    action = request.form.get('action')
    if action not in {'select', 'deselect', 'clear'}:
        abort(400, description='Nieprawidłowa operacja wyboru.')
    identifiers = request.form.getlist('book_id')
    if len(identifiers) > 24 or any(not re.fullmatch(r'[0-9]{1,18}', identifier) for identifier in identifiers):
        abort(400, description='Nieprawidłowy zestaw książek.')
    connection = get_db()
    with connection:
        if action == 'clear':
            connection.execute('UPDATE books SET selected=0')
        else:
            connection.executemany('UPDATE books SET selected=? WHERE id=?',
                                   [(int(action == 'select'), identifier) for identifier in identifiers])
    if request.accept_mimetypes.best == 'application/json':
        return jsonify(selected=stats()['selected'])
    return redirect(url_for('web.library', page=positive_page()), code=303)


@bp.route('/import', methods=['GET', 'POST'])
def import_library():
    if request.method == 'GET':
        return render_template('import.html', result=session.pop('import_result', None), empty=stats()['total'] == 0)
    upload = request.files.get('file')
    if not upload or not upload.filename or not upload.filename.lower().endswith('.csv'):
        return render_template('import.html', error='Wybierz plik z rozszerzeniem .csv.'), 400
    try:
        result = import_books(get_db(), upload.read())
    except ImportError as error:
        return render_template('import.html', error=str(error)), 400
    session['import_result'] = result
    return redirect(url_for('web.import_library'), code=303)


@bp.post('/import/demo')
def import_demo():
    if not load_demo(get_db()):
        return render_template('import.html', error='Dane demonstracyjne można dodać tylko do pustej biblioteki.'), 409
    session['import_result'] = {'added': 12, 'updated': 0, 'skipped': 0}
    return redirect(url_for('web.import_library'), code=303)


def candidates():
    return [{**dict(book), 'cover_url': cover_url(book['open_library_id'])} for book in get_db().execute(
        'SELECT id,title,author,exclusive_shelf,goodreads_id,open_library_id FROM books WHERE selected=1 ORDER BY id').fetchall()]


@bp.get('/wheel')
def wheel():
    return render_template('wheel.html', candidates=candidates(), draw_key=secrets.token_urlsafe(24))


@bp.post('/api/draw')
def draw():
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not re.fullmatch(r'[A-Za-z0-9_-]{16,64}', str(data.get('key', ''))):
        abort(400, description='Nieprawidłowy identyfikator losowania. Odśwież stronę.')
    connection = get_db()
    # A write transaction makes selection snapshot + history + retry key atomic.
    with connection:
        connection.execute('BEGIN IMMEDIATE')
        previous = connection.execute('SELECT payload FROM draw_requests WHERE key=?', (data['key'],)).fetchone()
        if previous:
            return jsonify(json.loads(previous['payload']))
        pool = candidates()
        if not pool:
            return jsonify(error='Zaznacz przynajmniej jedną książkę w bibliotece.'), 409
        winner_index = secrets.randbelow(len(pool))
        winner = pool[winner_index]
        cursor = connection.execute('INSERT INTO draw_history(book_id,title,author) VALUES (?,?,?)',
                                    (winner['id'], winner['title'], winner['author']))
        payload = {'candidates': pool, 'winner_index': winner_index, 'winner': winner,
                   'history_id': cursor.lastrowid, 'next_key': secrets.token_urlsafe(24)}
        connection.execute('DELETE FROM draw_requests WHERE rowid NOT IN (SELECT rowid FROM draw_requests ORDER BY rowid DESC LIMIT 99)')
        connection.execute('INSERT INTO draw_requests(key,payload) VALUES (?,?)',
                           (data['key'], json.dumps(payload, ensure_ascii=False)))
    return jsonify(payload)


@bp.get('/history')
def history():
    connection = get_db()
    total = connection.execute('SELECT COUNT(*) FROM draw_history').fetchone()[0]
    pages = max(1, math.ceil(total / 30))
    page = min(positive_page(), pages)
    draws = connection.execute('SELECT * FROM draw_history ORDER BY id DESC LIMIT 30 OFFSET ?', ((page-1)*30,)).fetchall()
    return render_template('history.html', draws=draws, count=total, page=page, pages=pages)
