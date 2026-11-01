import secrets

from flask import abort, request, session


def csrf_token():
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_urlsafe(32)
    return session['csrf_token']


def protect_writes():
    if request.method in {'POST', 'PUT', 'PATCH', 'DELETE'}:
        expected = session.get('csrf_token')
        supplied = request.form.get('csrf_token', '')
        if not expected or not secrets.compare_digest(expected.encode("utf-8"), supplied.encode("utf-8")):
            abort(400, description='Sesja formularza wygasła. Odśwież stronę i spróbuj ponownie.')
