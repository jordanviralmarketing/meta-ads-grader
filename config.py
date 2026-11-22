import os


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
