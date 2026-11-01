from flask import Blueprint, abort, current_app, jsonify, redirect, render_template, request, session, url_for

from .catalog import add_to_library, cached_search, fetch_search, get_catalog, local_search
from .db import get_db
from .openlibrary import WORK_ID, OpenLibraryError

bp = Blueprint('catalog', __name__)


def page_number(value):
    try:
        return min(1000000000, max(1, int(value)))
    except (ValueError, TypeError):
        return 1


def latest_job():
    row = get_catalog().execute('SELECT * FROM catalog_jobs ORDER BY id DESC LIMIT 1').fetchone()
    return dict(row) if row else None


def render_catalog(error=None, code=200):
    query = request.values.get('q', '').strip()[:200]
    page = page_number(request.values.get('page', '1'))
    mode = 'online' if request.values.get('mode') == 'online' else 'local'
    connection = get_catalog()
    if mode == 'online':
        page = min(1000, page)
        result = cached_search(connection, query, page) or {'works': [], 'total': 0}
        result = {**result, 'page': page, 'pages': min(1000, max(1, (result['total'] + 23) // 24))}
    else:
        result = local_search(connection, query, page)
    saved = {row[0] for row in get_db().execute('SELECT open_library_id FROM books WHERE open_library_id IS NOT NULL')}
    total = connection.execute('SELECT COUNT(*) FROM catalog_books').fetchone()[0]
    return render_template('catalog.html', q=query, mode=mode, result=result, saved=saved, total=total,
                           job=latest_job(), error=error, summary=session.pop('catalog_result', None)), code


@bp.get('/catalog')
def index():
    return render_catalog()


@bp.post('/catalog/online')
def online():
    query = request.form.get('q', '').strip()
    if not 2 <= len(query) <= 200:
        return render_catalog('Wpisz od 2 do 200 znaków tytułu lub nazwiska autora.', 400)
    page = min(1000, page_number(request.form.get('page', '1')))
    try:
        fetch_search(get_catalog(), query, page)
    except OpenLibraryError as error:
        return render_catalog(str(error), 502)
    return redirect(url_for('catalog.index', q=query, mode='online', page=page), code=303)


@bp.post('/catalog/add')
def add():
    identifiers = request.form.getlist('work_id')
    if not identifiers or len(identifiers) > 24 or any(not WORK_ID.fullmatch(identifier) for identifier in identifiers):
        abort(400, description='Nieprawidłowy wybór książek z katalogu.')
    try:
        session['catalog_result'] = add_to_library(get_db(), get_catalog(), identifiers)
    except ValueError as error:
        abort(400, description=str(error))
    return redirect(url_for('catalog.index', q=request.form.get('q', '')[:200],
                            mode='online' if request.form.get('mode') == 'online' else 'local',
                            page=page_number(request.form.get('page', '1'))), code=303)


@bp.post('/catalog/download')
def download():
    if request.form.get('confirm') != 'yes':
        return render_catalog('Potwierdź rozmiar pobierania. Dla kilku książek wybierz wyszukiwanie online.', 400)
    try:
        current_app.extensions['catalog_downloader'].start()
    except OpenLibraryError as error:
        return render_catalog(str(error), 409)
    return redirect(url_for('catalog.index') + '#download', code=303)


@bp.get('/catalog/status')
def status():
    return jsonify(job=latest_job())


@bp.post('/catalog/cancel')
def cancel():
    connection = get_catalog()
    with connection:
        connection.execute("UPDATE catalog_jobs SET cancel_requested=1 WHERE state='running'")
    return redirect(url_for('catalog.index') + '#download', code=303)
