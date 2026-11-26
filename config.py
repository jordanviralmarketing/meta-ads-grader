import os
import tempfile


def _require(name):
    """Read a required environment variable. Blank values are treated as missing."""
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        raise RuntimeError(
            "Missing required environment variable %s. "
            "Set %s in the environment before starting the app. "
            "On Render, add it to the service environment before deploying."
            % (name, name)
        )
    return value.strip()


def _setting(name, default):
    """Read an optional setting. Unset or blank values keep the non-secret default."""
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def _port(name, default):
    raw = _setting(name, str(default))
    try:
        return int(raw)
    except ValueError:
        raise RuntimeError(
            "Environment variable %s must be an integer port, got %r." % (name, raw)
        )


# Public app id. Override with META_APP_ID when deploying a different Meta app.
META_APP_ID = _setting("META_APP_ID", "997123502759841")

# No default. The process fails at import time when this is missing.
META_APP_SECRET = _require("META_APP_SECRET")

# Signs the Flask session cookie (OAuth state, access token, click IDs).
# Required so sessions survive restarts and work across gunicorn workers.
# No default. The process fails at import time when this is missing.
FLASK_SECRET_KEY = _require("FLASK_SECRET_KEY")

REDIRECT_URI = _setting(
    "REDIRECT_URI",
    "https://meta-ads-grader.onrender.com/auth/meta/callback",
)
STRATEGY_SESSION_URL = _setting(
    "STRATEGY_SESSION_URL",
    "https://theviralmarketingcompany.com/meetings/jordan-van-ahn/free-strategy-call",
)

# Email settings are not used by the current routes.
# SMTP_PASSWORD has no built-in credential; unset stays empty.
SMTP_HOST = _setting("SMTP_HOST", "smtp.example.com")
SMTP_PORT = _port("SMTP_PORT", 587)
SMTP_USER = _setting("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD") or ""
FROM_EMAIL = _setting("FROM_EMAIL", "jordan@theviralmarketingcompany.com")

# Local directory for server-side sessions and saved reports.
# On Render the default lives under the system temp dir, which is writable
# without a persistent disk and is wiped on restart or redeploy.
# Set DATA_DIR to a mounted disk path to keep data across deploys.
DATA_DIR = _setting("DATA_DIR", os.path.join(tempfile.gettempdir(), "meta-ads-grader"))

# Session backend. "cachelib" (default) and "filesystem" store sessions as
# files under SESSION_FILE_DIR. "sqlalchemy" uses DATABASE_URL when a database
# is added later. The browser cookie stores only an opaque session id.
SESSION_TYPE = _setting("SESSION_TYPE", "cachelib").lower()

# Report backend. "sqlite" (default) uses the stdlib and needs no extra service.
REPORT_STORE = _setting("REPORT_STORE", "sqlite").lower()


def session_file_dir():
    return _setting("SESSION_FILE_DIR", os.path.join(DATA_DIR, "sessions"))


def report_sqlite_path():
    return _setting("REPORT_SQLITE_PATH", os.path.join(DATA_DIR, "reports.sqlite"))


def _flag(name):
    """True/False for a set boolean env var, or None when unset or blank."""
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return None
    return value.strip().lower() in ("1", "true", "yes", "on")


def cookie_secure():
    """Secure session cookies in production.

    Render sets RENDER=true on every service, so production turns this on
    without a new environment variable. SESSION_COOKIE_SECURE overrides it.
    Local HTTP (and the test suite) leave it off so the session cookie is sent.
    """
    explicit = _flag("SESSION_COOKIE_SECURE")
    if explicit is not None:
        return explicit
    return _flag("RENDER") is True


def onrender_host_redirect_enabled():
    """301 from meta-ads-grader.onrender.com to the custom domain.

    On unless ONRENDER_HOST_REDIRECT is 0, false, no, or off. Unset or blank
    stays on. Read per request so the flag matches the process environment.
    """
    value = os.environ.get("ONRENDER_HOST_REDIRECT")
    if value is None or value.strip() == "":
        return True
    return value.strip().lower() not in ("0", "false", "no", "off")
