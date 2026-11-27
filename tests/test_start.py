import hashlib
import io
import logging
import os
import sys
import threading
import unittest
from datetime import timedelta
from unittest import mock
from urllib.parse import parse_qs, urlparse

os.environ.setdefault("META_APP_SECRET", "unit-test-secret")
os.environ.setdefault("FLASK_SECRET_KEY", "unit-test-flask-key")

import app as grader  # noqa: E402

THANKS = "https://theviralmarketingcompany.com/meta-ads-grader-thanks"
ACCOUNT_ID = "act_99887766"
ACCESS_TOKEN = "user-access-token-not-email"


def _json(payload, status_code=200):
    response = mock.Mock(status_code=status_code)
    response.json.return_value = payload
    return response


class StartEmailTests(unittest.TestCase):
    def setUp(self):
        self._previous_capi = os.environ.get("META_CAPI_ACCESS_TOKEN")
        os.environ.pop("META_CAPI_ACCESS_TOKEN", None)
        self.client = grader.app.test_client()

    def tearDown(self):
        if self._previous_capi is None:
            os.environ.pop("META_CAPI_ACCESS_TOKEN", None)
        else:
            os.environ["META_CAPI_ACCESS_TOKEN"] = self._previous_capi

    def _query(self, location):
        parsed = urlparse(location)
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.netloc, "theviralmarketingcompany.com")
        self.assertEqual(parsed.path, "/meta-ads-grader-thanks")
        return parse_qs(parsed.query, keep_blank_values=True)

    def test_valid_email_is_saved_and_left_out_of_the_redirect(self):
        response = self.client.get(
            "/start?email=%20Buyer@Example.com%20&utm_source=meta&utm_campaign=grader"
            "&fbclid=F1&gclid=DROP&junk=no"
        )
        self.assertEqual(response.status_code, 302)
        location = response.headers["Location"]
        self.assertTrue(location.startswith(THANKS + "?"), location)
        self.assertEqual(
            self._query(location),
            {"utm_source": ["meta"], "utm_campaign": ["grader"], "fbclid": ["F1"]},
        )
        self.assertNotIn("buyer@example.com", location.lower())
        self.assertNotIn("Buyer@Example.com", location)
        self.assertNotIn("email=", location)
        self.assertNotIn("gclid", location)
        self.assertEqual(response.get_data(), b"")
        self.assertNotIn(b"fbq", response.get_data())
        self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["X-Robots-Tag"], "noindex, nofollow")
        header = response.headers["Set-Cookie"]
        self.assertIn("SameSite=Lax", header)
        self.assertNotIn("Domain=", header)
        cookie = self.client.get_cookie("session")
        self.assertTrue(cookie.origin_only)
        self.assertEqual(cookie.same_site.lower(), "lax")
        with self.client.session_transaction() as sess:
            self.assertEqual(sess.get("email"), "buyer@example.com")

    def test_bad_email_is_not_saved_but_still_redirects(self):
        too_long = ("a" * (255 - len("@example.com"))) + "@example.com"
        self.assertEqual(len(too_long), 255)
        just_right = ("b" * (254 - len("@example.com"))) + "@example.com"
        self.assertEqual(len(just_right), 254)
        cases = (
            "/start",
            "/start?email=",
            "/start?email=not-an-email",
            "/start?email=a@b",
            "/start?email=" + too_long,
            "/start?utm_source=meta&fbclid=F9",
        )
        for path in cases:
            client = grader.app.test_client()
            response = client.get(path)
            self.assertEqual(response.status_code, 302, path)
            location = response.headers["Location"]
            self.assertTrue(location.startswith(THANKS), location)
            self.assertNotIn("@", location)
            self.assertNotIn("not-an-email", location)
            with client.session_transaction() as sess:
                self.assertNotIn("email", sess)

        client = grader.app.test_client()
        response = client.get("/start?email=" + just_right + "&utm_medium=cpc")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._query(response.headers["Location"]), {"utm_medium": ["cpc"]})
        self.assertNotIn(just_right, response.headers["Location"])
        with client.session_transaction() as sess:
            self.assertEqual(sess.get("email"), just_right)

    def test_email_and_query_string_are_not_logged(self):
        email = "logged-buyer@example.com"
        leaked = "GET /start?email=%s&utm_source=meta HTTP/1.1" % email
        with self.assertLogs("werkzeug", level="INFO") as logs:
            logging.getLogger("werkzeug").info('"%s" %s %s', leaked, "302", "-")
        text = "\n".join(logs.output)
        self.assertNotIn(email, text)
        self.assertNotIn("utm_source", text)
        self.assertNotIn("?", text)

        records = []

        class _Capture(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        handler = _Capture(level=logging.DEBUG)
        root = logging.getLogger()
        root.addHandler(handler)
        try:
            response = self.client.get("/start?email=%s&utm_source=meta&fbclid=F1" % email)
        finally:
            root.removeHandler(handler)
        self.assertEqual(response.status_code, 302)
        captured = "\n".join(records)
        self.assertNotIn(email, captured)
        self.assertNotIn("utm_source=meta", captured)
        self.assertNotIn(email, response.headers["Location"])

    def test_gunicorn_access_log_blanks_query_atoms_without_keyerror(self):
        from gunicorn.config import Config
        from gunicorn.glogging import Logger, SafeAtoms

        email = "sentinel-email@example.com"
        cookie = "opaque-session-id-sentinel"
        query = "email=%s&utm_source=meta" % email
        raw_uri = "/start?%s" % query
        fmt = (
            "%(s)s %(r)s U=%(U)s q=%(q)s qs=%({query_string}e)s "
            "raw=%({raw_uri}e)s xff=%({x-forwarded-for}i)s"
        )
        environ = {
            "REMOTE_ADDR": "127.0.0.1",
            "REQUEST_METHOD": "GET",
            "RAW_URI": raw_uri,
            "SERVER_PROTOCOL": "HTTP/1.1",
            "PATH_INFO": "/start",
            "QUERY_STRING": query,
            "HTTP_COOKIE": "session=%s" % cookie,
        }

        class _Resp(object):
            status = "302"
            headers = [("Set-Cookie", "session=%s; HttpOnly; SameSite=Lax" % cookie)]
            sent = 0

        # Header atom is missing on purpose. SafeAtoms returns "-"; a plain dict raises.
        req_headers = [("Cookie", "session=%s" % cookie)]

        atoms = SafeAtoms({
            "q": query,
            "{query_string}e": query,
            "{raw_uri}e": raw_uri,
            "r": "GET %s HTTP/1.1" % raw_uri,
            "U": "/start",
        })
        self.assertIs(type(atoms), SafeAtoms)
        record = logging.LogRecord(
            "gunicorn.access", logging.INFO, "", 0, fmt, atoms, None
        )
        self.assertTrue(grader._DropQueryStringFilter().filter(record))
        self.assertIs(type(record.args), SafeAtoms)
        self.assertEqual(record.args["{x-forwarded-for}i"], "-")
        rendered = record.getMessage()
        self.assertNotIn(email, rendered)
        self.assertNotIn(cookie, rendered)
        self.assertNotIn("KeyError", rendered)

        cfg = Config()
        cfg.set("accesslog", "-")
        cfg.set("access_log_format", fmt)
        stdout, stderr = io.StringIO(), io.StringIO()
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = stdout, stderr
        try:
            glogger = Logger(cfg)
            glogger.access(_Resp(), req_headers, environ, timedelta(microseconds=1500))
        finally:
            sys.stdout, sys.stderr = old_out, old_err
        output = stdout.getvalue() + stderr.getvalue()
        self.assertNotIn("KeyError", output)
        self.assertNotIn("Logging error", output)
        self.assertNotIn(email, output)
        self.assertNotIn(cookie, output)
        self.assertIn("xff=-", output)
        self.assertIn("U=/start", output)

    def test_broken_access_log_format_does_not_dump_email_or_cookie(self):
        from gunicorn.config import Config
        from gunicorn.glogging import Logger

        email = "sentinel-email@example.com"
        cookie = "opaque-session-id-sentinel"
        query = "email=%s&utm_source=meta" % email
        # A bad conversion forces logging to print the record to stderr.
        fmt = "%(s)s q=%(q)s cookie=%({cookie}i)s set=%({set-cookie}o)s bad=%(q)Z"
        environ = {
            "REMOTE_ADDR": "127.0.0.1",
            "REQUEST_METHOD": "GET",
            "RAW_URI": "/start?%s" % query,
            "SERVER_PROTOCOL": "HTTP/1.1",
            "PATH_INFO": "/start",
            "QUERY_STRING": query,
        }

        class _Resp(object):
            status = "302"
            headers = [("Set-Cookie", "session=%s; HttpOnly; SameSite=Lax" % cookie)]
            sent = 0

        req_headers = [("Cookie", "session=%s" % cookie)]
        cfg = Config()
        cfg.set("accesslog", "-")
        cfg.set("access_log_format", fmt)
        stdout, stderr = io.StringIO(), io.StringIO()
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = stdout, stderr
        try:
            glogger = Logger(cfg)
            glogger.access(_Resp(), req_headers, environ, timedelta(microseconds=1500))
        finally:
            sys.stdout, sys.stderr = old_out, old_err
        err = stderr.getvalue()
        out = stdout.getvalue()
        self.assertIn("Logging error", err)
        self.assertNotIn(email, err)
        self.assertNotIn(cookie, err)
        self.assertNotIn(email, out)
        self.assertNotIn(cookie, out)

    def test_onrender_start_does_not_put_email_in_location(self):
        response = self.client.get(
            "/start?email=a@b.co&utm_source=meta",
            base_url="http://meta-ads-grader.onrender.com",
        )
        self.assertEqual(response.status_code, 302)
        location = response.headers["Location"]
        self.assertTrue(location.startswith(THANKS), location)
        self.assertNotIn("a@b.co", location)
        self.assertNotIn("email", location)
        self.assertIn("utm_source=meta", location)

    def test_email_survives_connect_callback_and_token_removal(self):
        started = self.client.get(
            "/start?email=Buyer@Example.com&utm_source=meta&fbclid=click-1"
        )
        first_cookie = self.client.get_cookie("session").decoded_value
        self.assertIn("SameSite=Lax", started.headers["Set-Cookie"])
        self.assertNotIn("Domain=", started.headers["Set-Cookie"])

        connect = self.client.get("/connect")
        self.assertEqual(connect.status_code, 302)
        self.assertIn("facebook.com", connect.headers["Location"])
        self.assertEqual(self.client.get_cookie("session").decoded_value, first_cookie)
        with self.client.session_transaction() as sess:
            state = sess["oauth_state"]
            self.assertEqual(sess.get("email"), "buyer@example.com")

        token = _json({"access_token": ACCESS_TOKEN})
        accounts = _json({"data": [{"id": ACCOUNT_ID, "name": "Spring Co", "account_status": 1}]})
        with mock.patch.object(grader.requests, "get", side_effect=[token, accounts]):
            callback = self.client.get("/auth/meta/callback?code=abc&state=%s" % state)
        self.assertEqual(callback.status_code, 302)
        self.assertEqual(self.client.get_cookie("session").decoded_value, first_cookie)
        with self.client.session_transaction() as sess:
            self.assertEqual(sess.get("email"), "buyer@example.com")
            self.assertNotIn("oauth_state", sess)

        insights = _json({"data": []})
        ads = _json({"data": []})
        with mock.patch.object(grader.requests, "get", side_effect=[insights, ads]):
            created = self.client.post("/audit", data={"account_id": ACCOUNT_ID})
        self.assertEqual(created.status_code, 303)
        with self.client.session_transaction() as sess:
            self.assertEqual(sess.get("email"), "buyer@example.com")
            self.assertNotIn("access_token", sess)
        report_id = created.headers["Location"].rsplit("/", 1)[1]

        os.environ["META_CAPI_ACCESS_TOKEN"] = "unit-test-capi-token"
        captured = {}
        done = threading.Event()

        def capture(url, json=None, timeout=None):
            captured["json"] = json
            done.set()
            return mock.Mock(status_code=200)

        try:
            with mock.patch.object(grader.capi.requests, "post", capture):
                viewed = self.client.get("/report/" + report_id)
                self.assertTrue(done.wait(2))
        finally:
            os.environ.pop("META_CAPI_ACCESS_TOKEN", None)
        self.assertEqual(viewed.status_code, 200)
        posted = captured["json"]["data"][0]
        digest = hashlib.sha256(b"buyer@example.com").hexdigest()
        self.assertEqual(posted["user_data"]["em"], digest)
        self.assertNotIn("buyer@example.com", str(posted))

    def test_jordan_test_email_is_saved_but_not_sent_to_capi(self):
        self.client.get("/start?email=Jordan%2BTest@Example.com")
        with self.client.session_transaction() as sess:
            self.assertEqual(sess.get("email"), "jordan+test@example.com")
        self.client.get("/connect")
        with self.client.session_transaction() as sess:
            state = sess["oauth_state"]
        token = _json({"access_token": ACCESS_TOKEN})
        accounts = _json({"data": [{"id": ACCOUNT_ID, "name": "A", "account_status": 1}]})
        with mock.patch.object(grader.requests, "get", side_effect=[token, accounts]):
            self.client.get("/auth/meta/callback?code=abc&state=%s" % state)
        insights = _json({"data": []})
        ads = _json({"data": []})
        with mock.patch.object(grader.requests, "get", side_effect=[insights, ads]):
            created = self.client.post("/audit", data={"account_id": ACCOUNT_ID})
        report_id = created.headers["Location"].rsplit("/", 1)[1]
        os.environ["META_CAPI_ACCESS_TOKEN"] = "unit-test-capi-token"
        captured = {}
        done = threading.Event()

        def capture(url, json=None, timeout=None):
            captured["json"] = json
            done.set()
            return mock.Mock(status_code=200)

        try:
            with mock.patch.object(grader.capi.requests, "post", capture):
                self.client.get("/report/" + report_id)
                self.assertTrue(done.wait(2))
        finally:
            os.environ.pop("META_CAPI_ACCESS_TOKEN", None)
        posted = captured["json"]["data"][0]
        self.assertNotIn("em", posted["user_data"])
        self.assertNotIn("jordan+test", str(posted).lower())


if __name__ == "__main__":
    unittest.main()
