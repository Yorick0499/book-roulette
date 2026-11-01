from pathlib import Path
import secrets
import os

from flask import Flask, render_template, request, redirect, session, url_for
from werkzeug.exceptions import RequestEntityTooLarge

from .importer import ImportError, import_books
from .security import csrf_token, protect_writes

from . import db


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        DATABASE=str(Path(app.instance_path) / "library.sqlite3"),
        MAX_CONTENT_LENGTH=10 * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )
    if test_config:
        app.config.update(test_config)
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    if not app.config.get("SECRET_KEY"):
        secret_path = Path(app.instance_path) / "session.key"
        try:
            descriptor = open(secret_path, "xb", opener=lambda path, flags: os.open(path, flags, 0o600))
            with descriptor:
                descriptor.write(secrets.token_bytes(32))
        except FileExistsError:
            pass
        app.config["SECRET_KEY"] = secret_path.read_bytes()
    app.before_request(protect_writes)
    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(RequestEntityTooLarge)
    def too_large(error):
        return render_template("import.html", error="Plik jest za duży. Limit żądania wynosi 10 MB."), 413

    @app.route("/import", methods=["GET", "POST"])
    def import_library():
        if request.method == "GET":
            return render_template("import.html", result=session.pop("import_result", None))
        upload = request.files.get("file")
        if not upload or not upload.filename or not upload.filename.lower().endswith(".csv"):
            return render_template("import.html", error="Wybierz plik z rozszerzeniem .csv."), 400
        try:
            result = import_books(db.get_db(), upload.read())
        except ImportError as error:
            return render_template("import.html", error=str(error)), 400
        session["import_result"] = result
        return redirect(url_for("import_library"), code=303)

    db.init_app(app)
    with app.app_context():
        db.init_db()

    @app.get("/")
    def index():
        connection = db.get_db()
        stats = connection.execute(
            "SELECT COUNT(*) AS total, COALESCE(SUM(selected), 0) AS selected FROM books"
        ).fetchone()
        books = connection.execute("SELECT title, author, exclusive_shelf FROM books ORDER BY title COLLATE NOCASE").fetchall()
        return render_template("index.html", stats=stats, books=books)

    return app
