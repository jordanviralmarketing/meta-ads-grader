import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _env_without_secret():
    """Environment with no Meta app secret but a valid Flask session key."""
    env = os.environ.copy()
    env.pop("META_APP_SECRET", None)
    env["FLASK_SECRET_KEY"] = "unit-test-flask-key"
    return env


def _env_without_flask_key():
    """Environment with a Meta app secret but no Flask session key."""
    env = os.environ.copy()
    env.pop("FLASK_SECRET_KEY", None)
    env["META_APP_SECRET"] = "unit-test-secret"
    return env


class ConfigStartupTests(unittest.TestCase):
    def test_import_fails_without_secret(self):
        result = subprocess.run(
            [sys.executable, "-c", "import config"],
            cwd=ROOT,
            env=_env_without_secret(),
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("META_APP_SECRET", result.stderr)
        self.assertIn("Missing required environment variable", result.stderr)

    def test_app_import_fails_without_secret(self):
        result = subprocess.run(
            [sys.executable, "-c", "import app"],
            cwd=ROOT,
            env=_env_without_secret(),
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("META_APP_SECRET", result.stderr)

    def test_blank_secret_is_rejected(self):
        env = _env_without_secret()
        env["META_APP_SECRET"] = "   "
        result = subprocess.run(
            [sys.executable, "-c", "import config"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("META_APP_SECRET", result.stderr)

    def test_secret_comes_from_environment(self):
        env = _env_without_secret()
        env["META_APP_SECRET"] = "unit-test-secret"
        for name in (
            "META_APP_ID",
            "REDIRECT_URI",
            "STRATEGY_SESSION_URL",
            "SMTP_HOST",
            "SMTP_PORT",
            "SMTP_USER",
            "SMTP_PASSWORD",
            "FROM_EMAIL",
        ):
            env.pop(name, None)
        code = (
            "import config\n"
            "assert config.META_APP_SECRET == 'unit-test-secret'\n"
            "assert config.META_APP_ID == '997123502759841'\n"
            "assert config.REDIRECT_URI == "
            "'https://meta-ads-grader.onrender.com/auth/meta/callback'\n"
            "assert config.SMTP_HOST == 'smtp.example.com'\n"
            "assert config.SMTP_PORT == 587\n"
            "assert config.SMTP_USER == ''\n"
            "assert config.SMTP_PASSWORD == ''\n"
            "assert config.FROM_EMAIL == 'jordan@theviralmarketingcompany.com'\n"
            "assert 'free-strategy-call' in config.STRATEGY_SESSION_URL\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_optional_settings_can_be_overridden(self):
        env = _env_without_secret()
        env.update(
            {
                "META_APP_SECRET": "unit-test-secret",
                "META_APP_ID": "111",
                "REDIRECT_URI": "https://example.com/callback",
                "STRATEGY_SESSION_URL": "https://example.com/book",
                "SMTP_HOST": "smtp.example.org",
                "SMTP_PORT": "2525",
                "SMTP_USER": "mailer",
                "SMTP_PASSWORD": "not-a-real-password",
                "FROM_EMAIL": "ops@example.com",
            }
        )
        code = (
            "import config\n"
            "assert config.META_APP_ID == '111'\n"
            "assert config.REDIRECT_URI == 'https://example.com/callback'\n"
            "assert config.STRATEGY_SESSION_URL == 'https://example.com/book'\n"
            "assert config.SMTP_HOST == 'smtp.example.org'\n"
            "assert config.SMTP_PORT == 2525\n"
            "assert config.SMTP_USER == 'mailer'\n"
            "assert config.SMTP_PASSWORD == 'not-a-real-password'\n"
            "assert config.FROM_EMAIL == 'ops@example.com'\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_invalid_smtp_port_fails_clearly(self):
        env = _env_without_secret()
        env["META_APP_SECRET"] = "unit-test-secret"
        env["SMTP_PORT"] = "not-a-port"
        result = subprocess.run(
            [sys.executable, "-c", "import config"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SMTP_PORT", result.stderr)

    def test_import_fails_without_flask_secret_key(self):
        for target in ("config", "app"):
            result = subprocess.run(
                [sys.executable, "-c", "import %s" % target],
                cwd=ROOT,
                env=_env_without_flask_key(),
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0, target)
            self.assertIn("FLASK_SECRET_KEY", result.stderr)

    def test_blank_flask_secret_key_is_rejected(self):
        env = _env_without_flask_key()
        env["FLASK_SECRET_KEY"] = "   "
        result = subprocess.run(
            [sys.executable, "-c", "import config"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("FLASK_SECRET_KEY", result.stderr)

    def test_flask_secret_key_comes_from_environment(self):
        env = _env_without_flask_key()
        env["FLASK_SECRET_KEY"] = "unit-test-flask-key"
        code = (
            "import app\n"
            "assert app.app.secret_key == 'unit-test-flask-key'\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_config_source_does_not_assign_secret_literal(self):
        text = (ROOT / "config.py").read_text()
        self.assertNotRegex(text, r'META_APP_SECRET\s*=\s*["\']')
        self.assertNotRegex(text, r'FLASK_SECRET_KEY\s*=\s*["\']')
        self.assertNotRegex(text, r'META_CAPI_ACCESS_TOKEN\s*=\s*["\'][^"\']+["\']')
        app_text = (ROOT / "app.py").read_text()
        self.assertNotIn("token_hex", app_text)
        self.assertIn("scope=ads_read", app_text)
        self.assertNotIn("business_management", app_text)
        self.assertNotRegex(text, r'SMTP_PASSWORD\s*=\s*["\'][^"\']+["\']')
        capi_text = (ROOT / "capi.py").read_text()
        self.assertNotIn("print(", capi_text)
        self.assertNotRegex(capi_text, r'META_CAPI_ACCESS_TOKEN\s*=\s*["\'][^"\']+["\']')


class SessionStoreConfigTests(unittest.TestCase):
    def test_session_and_report_defaults(self):
        env = _env_without_secret()
        env["META_APP_SECRET"] = "unit-test-secret"
        for name in (
            "DATA_DIR",
            "SESSION_TYPE",
            "SESSION_FILE_DIR",
            "REPORT_STORE",
            "REPORT_SQLITE_PATH",
            "SESSION_COOKIE_SECURE",
            "RENDER",
            "DATABASE_URL",
            "META_CAPI_ACCESS_TOKEN",
        ):
            env.pop(name, None)
        code = (
            "import os, tempfile, config\n"
            "assert config.SESSION_TYPE == 'cachelib'\n"
            "assert config.REPORT_STORE == 'sqlite'\n"
            "assert config.DATA_DIR == os.path.join(tempfile.gettempdir(), 'meta-ads-grader')\n"
            "assert config.session_file_dir().endswith('/sessions')\n"
            "assert config.report_sqlite_path().endswith('reports.sqlite')\n"
            "assert config.cookie_secure() is False\n"
            "print('ok')\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ok", result.stdout)

    def test_secure_cookies_when_render_is_set(self):
        env = _env_without_secret()
        env["META_APP_SECRET"] = "unit-test-secret"
        env["RENDER"] = "true"
        env.pop("SESSION_COOKIE_SECURE", None)
        code = (
            "import app\n"
            "client = app.app.test_client()\n"
            "response = client.get('/connect')\n"
            "assert response.status_code == 302\n"
            "cookie = client.get_cookie('session')\n"
            "assert cookie is not None\n"
            "assert cookie.secure is True\n"
            "assert cookie.http_only is True\n"
            "assert cookie.same_site.lower() == 'lax'\n"
            "assert app.app.config['SESSION_COOKIE_SECURE'] is True\n"
            "assert app.app.config['SESSION_COOKIE_HTTPONLY'] is True\n"
            "print('ok')\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ok", result.stdout)

    def test_session_cookie_secure_can_override_render(self):
        env = _env_without_secret()
        env["META_APP_SECRET"] = "unit-test-secret"
        env["RENDER"] = "true"
        env["SESSION_COOKIE_SECURE"] = "false"
        code = (
            "import config\n"
            "assert config.cookie_secure() is False\n"
            "print('ok')\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_filesystem_session_type_is_an_alias(self):
        env = _env_without_secret()
        env["META_APP_SECRET"] = "unit-test-secret"
        env["SESSION_TYPE"] = "filesystem"
        code = (
            "import app\n"
            "assert app.app.config['SESSION_TYPE'] == 'cachelib'\n"
            "print('ok')\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ok", result.stdout)

    def test_sqlalchemy_session_requires_database_url(self):
        env = _env_without_secret()
        env["META_APP_SECRET"] = "unit-test-secret"
        env["SESSION_TYPE"] = "sqlalchemy"
        env.pop("DATABASE_URL", None)
        result = subprocess.run(
            [sys.executable, "-c", "import app"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DATABASE_URL", result.stderr)

    def test_unknown_session_and_report_backends_fail(self):
        for var, value, expected in (
            ("SESSION_TYPE", "redis", "SESSION_TYPE"),
            ("REPORT_STORE", "postgres", "REPORT_STORE"),
        ):
            env = _env_without_secret()
            env["META_APP_SECRET"] = "unit-test-secret"
            env[var] = value
            result = subprocess.run(
                [sys.executable, "-c", "import app"],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0, var)
            self.assertIn(expected, result.stderr)


class OAuthRouteTests(unittest.TestCase):
    def test_connect_redirect_uses_public_settings_only(self):
        env = _env_without_secret()
        env["META_APP_SECRET"] = "unit-test-secret"
        code = (
            "import app\n"
            "client = app.app.test_client()\n"
            "root = client.get('/')\n"
            "assert root.status_code == 302\n"
            "assert '/connect' in root.headers['Location']\n"
            "response = client.get('/connect')\n"
            "assert response.status_code == 302\n"
            "location = response.headers['Location']\n"
            "assert 'client_id=997123502759841' in location\n"
            "assert 'redirect_uri=https://meta-ads-grader.onrender.com/auth/meta/callback' in location\n"
            "assert 'scope=ads_read' in location\n"
            "assert 'business_management' not in location\n"
            "assert 'unit-test-secret' not in location\n"
            "assert 'client_secret' not in location\n"
            "print('ok')\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ok", result.stdout)


if __name__ == "__main__":
    unittest.main()
