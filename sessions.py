"""Server-side sessions.

The cookie stores an opaque session id (Flask-Session / CacheLib). Session
data, including the Meta access token until a report is saved, stays on the
server. SESSION_TYPE selects the backend so this can move to a database later
without changing callers.
"""

import os
from datetime import timedelta

from cachelib.file import FileSystemCache
from flask_session import Session

import config


def init_server_session(app):
    backend = config.SESSION_TYPE
    if backend in ("cachelib", "filesystem", "file"):
        directory = config.session_file_dir()
        os.makedirs(directory, exist_ok=True)
        os.chmod(directory, 0o700)
        app.config["SESSION_TYPE"] = "cachelib"
        app.config["SESSION_CACHELIB"] = FileSystemCache(
            cache_dir=directory,
            threshold=500,
            mode=0o600,
        )
        app.config["SESSION_FILE_DIR"] = directory
    elif backend == "sqlalchemy":
        _init_sqlalchemy_session(app)
    else:
        raise RuntimeError(
            "Unsupported SESSION_TYPE %r. Use cachelib (default; files on disk, "
            "no extra service) or sqlalchemy with DATABASE_URL." % backend
        )

    # The id itself is an unguessable token. Flask-Session's signer is deprecated
    # and would only wrap that id; leave it off so the cookie value is the id.
    app.config["SESSION_USE_SIGNER"] = False
    app.config["SESSION_ID_LENGTH"] = 32
    app.config["SESSION_PERMANENT"] = False
    # Server-side TTL. The cookie itself stays a browser session cookie.
    # Two hours is long enough to finish OAuth and view the grade, and short
    # enough that an abandoned access token does not sit on disk for 31 days.
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=2)
    app.config["SESSION_COOKIE_NAME"] = "session"
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    # Lax (not Strict) so the Facebook OAuth callback, a top-level cross-site GET,
    # still sends the session cookie that holds oauth_state.
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = config.cookie_secure()
    Session(app)


def _init_sqlalchemy_session(app):
    database_url = (os.environ.get("DATABASE_URL") or "").strip()
    if not database_url:
        raise RuntimeError(
            "SESSION_TYPE=sqlalchemy requires DATABASE_URL "
            "(for example a Render Postgres connection string)."
        )
    # Render's Postgres URLs use the postgres:// scheme, which SQLAlchemy rejects.
    if database_url.startswith("postgres://"):
        database_url = "postgresql://" + database_url[len("postgres://") :]
    try:
        from flask_sqlalchemy import SQLAlchemy
    except ImportError as exc:
        raise RuntimeError(
            "SESSION_TYPE=sqlalchemy requires the flask-sqlalchemy package. "
            "Install it before pointing sessions at a database."
        ) from exc

    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db = SQLAlchemy(app)
    app.config["SESSION_TYPE"] = "sqlalchemy"
    app.config["SESSION_SQLALCHEMY"] = db
    app.config["SESSION_SQLALCHEMY_TABLE"] = "flask_sessions"
    app.config["SESSION_CLEANUP_N_REQUESTS"] = 100
