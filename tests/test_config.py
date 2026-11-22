import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _env_without_secret():
    env = os.environ.copy()
    env.pop("META_APP_SECRET", None)
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

    def test_config_source_does_not_assign_secret_literal(self):
        text = (ROOT / "config.py").read_text()
        self.assertNotRegex(text, r'META_APP_SECRET\s*=\s*["\']')
        self.assertNotRegex(text, r'SMTP_PASSWORD\s*=\s*["\'][^"\']+["\']')


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
