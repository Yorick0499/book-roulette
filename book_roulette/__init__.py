from pathlib import Path

from flask import Flask, render_template

from . import db


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        DATABASE=str(Path(app.instance_path) / "library.sqlite3"),
        MAX_CONTENT_LENGTH=10 * 1024 * 1024,
    )
    if test_config:
        app.config.update(test_config)
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    db.init_app(app)
    with app.app_context():
        db.init_db()

    @app.get("/")
    def index():
        connection = db.get_db()
        stats = connection.execute(
            "SELECT COUNT(*) AS total, COALESCE(SUM(selected), 0) AS selected FROM books"
        ).fetchone()
        return render_template("index.html", stats=stats)

    return app
