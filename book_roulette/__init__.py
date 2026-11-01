import os
import secrets
from pathlib import Path

from flask import Flask, render_template, request, jsonify
from werkzeug.exceptions import RequestEntityTooLarge

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
    app.config.setdefault("CATALOG_DATABASE", str(Path(app.config["DATABASE"]).with_name("catalog.sqlite3")))
    app.config.setdefault("COVER_CACHE", str(Path(app.config["CATALOG_DATABASE"]).parent / "covers"))
    Path(app.instance_path).mkdir(parents=True, exist_ok=True, mode=0o700)
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

    @app.errorhandler(400)
    @app.errorhandler(404)
    @app.errorhandler(409)
    @app.errorhandler(413)
    def request_error(error):
        if request.path.startswith("/api/"):
            return jsonify(error=str(error.description)), error.code
        return render_template("error.html", error=error), error.code

    @app.errorhandler(RequestEntityTooLarge)
    def too_large(error):
        return render_template("import.html", error="Plik jest za duży. Limit żądania wynosi 10 MB."), 413

    db.init_app(app)
    with app.app_context():
        db.init_db()

    from . import catalog
    from .catalog_download import DownloadManager
    catalog.init_app(app)
    app.extensions["catalog_downloader"] = DownloadManager(app.config["CATALOG_DATABASE"])

    from .covers import bp as covers_bp, cover_url, CoverLoader
    app.register_blueprint(covers_bp)
    app.extensions["cover_loader"] = CoverLoader(app)
    app.jinja_env.globals["cover_url"] = cover_url

    from .catalog_views import bp as catalog_bp
    app.register_blueprint(catalog_bp)

    from .views import bp
    app.register_blueprint(bp)

    return app
