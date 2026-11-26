import base64
import hashlib
import os
import re
import tempfile
import threading
import time
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

os.environ.setdefault("META_APP_SECRET", "unit-test-secret")
os.environ.setdefault("FLASK_SECRET_KEY", "unit-test-flask-key")

import app as grader  # noqa: E402
import capi  # noqa: E402
import storage  # noqa: E402

ACCOUNT_ID = "act_99887766"
ACCESS_TOKEN = "user-access-token-" + ("Z" * 400)
CTA_URL = (
    "https://audit.theviralmarketingcompany.com/"
    "?utm_source=grader&amp;utm_medium=cta&amp;utm_campaign=grade_screen"
)


def _opaque_nbytes(value):
    padded = value + "=" * (-len(value) % 4)
    try:
        return len(base64.urlsafe_b64decode(padded.encode("ascii")))
    except Exception:
        return 0


def _json_response(payload, status_code=200):
    response = mock.Mock(status_code=status_code)
    response.json.return_value = payload
    return response


class ReportFlowTests(unittest.TestCase):
    def setUp(self):
        self._previous_capi = os.environ.get("META_CAPI_ACCESS_TOKEN")
        os.environ.pop("META_CAPI_ACCESS_TOKEN", None)
        self.client = grader.app.test_client()

    def tearDown(self):
        if self._previous_capi is None:
            os.environ.pop("META_CAPI_ACCESS_TOKEN", None)
        else:
            os.environ["META_CAPI_ACCESS_TOKEN"] = self._previous_capi

    def _connect_and_callback(self):
        connect = self.client.get("/connect?fbclid=click-1&utm_source=meta&junk=no")
        self.assertEqual(connect.status_code, 302)
        self.assertIn("scope=ads_read", connect.headers["Location"])
        self.assertNotIn("business_management", connect.headers["Location"])
        with self.client.session_transaction() as sess:
            state = sess["oauth_state"]

        token = _json_response({"access_token": ACCESS_TOKEN})
        accounts = _json_response(
            {"data": [{"id": ACCOUNT_ID, "name": "Spring Co", "account_status": 1}]}
        )
        with mock.patch.object(grader.requests, "get", side_effect=[token, accounts]):
            callback = self.client.get("/auth/meta/callback?code=abc&state=%s" % state)
        self.assertEqual(callback.status_code, 302)
        self.assertTrue(callback.headers["Location"].startswith("/select-account?"))
        self.assertNotIn(ACCOUNT_ID, callback.headers["Location"])
        self.assertNotIn(ACCESS_TOKEN, callback.headers["Location"])
        return callback

    def _session_cookie(self):
        cookie = self.client.get_cookie("session")
        self.assertIsNotNone(cookie)
        return cookie

    def _assert_cookie_is_only_an_opaque_id(self, cookie):
        value = cookie.decoded_value
        self.assertLess(len(value), 200, value)
        self.assertGreaterEqual(_opaque_nbytes(value), 16)
        self.assertRegex(value, r"^[A-Za-z0-9_-]+$")
        self.assertNotIn(".", value)
        self.assertNotIn(ACCESS_TOKEN, value)
        self.assertNotIn("access_token", value)
        self.assertNotIn(ACCOUNT_ID, value)
        self.assertTrue(cookie.http_only)
        self.assertEqual(cookie.same_site.lower(), "lax")
        self.assertNotIn(ACCESS_TOKEN, cookie.value)

    def _session_files_contain(self, snippet):
        root = Path(grader.app.config["SESSION_FILE_DIR"])
        needle = snippet.encode()
        for path in root.rglob("*"):
            if path.is_file() and needle in path.read_bytes():
                return True
        return False

    def test_callback_and_report_cookies_hold_only_an_opaque_session_id(self):
        self._connect_and_callback()
        self._assert_cookie_is_only_an_opaque_id(self._session_cookie())
        with self.client.session_transaction() as sess:
            self.assertEqual(sess.get("access_token"), ACCESS_TOKEN)
        self.assertTrue(self._session_files_contain(ACCESS_TOKEN))

        select = self.client.get("/select-account")
        self.assertEqual(select.status_code, 200)
        page = select.get_data(as_text=True)
        self.assertIn('action="/audit"', page)
        self.assertNotIn("/audit/%s" % ACCOUNT_ID, page)
        self.assertNotRegex(page, r'href="[^"]*act_')
        self.assertIn("Spring Co", page)

        rejected = self.client.post("/audit", data={"account_id": "act_not_mine"})
        self.assertEqual(rejected.status_code, 400)
        with self.client.session_transaction() as sess:
            self.assertEqual(sess.get("access_token"), ACCESS_TOKEN)
        self._assert_cookie_is_only_an_opaque_id(self._session_cookie())

        insights = _json_response(
            {
                "data": [
                    {
                        "account_id": ACCOUNT_ID,
                        "campaign_name": "Spring Sale",
                        "spend": "6000",
                        "impressions": "1000",
                        "clicks": "80",
                        "ctr": "1.6",
                        "cpc": "1",
                        "cpm": "2",
                        "actions": [],
                        "reach": "100",
                        "frequency": "1.1",
                    }
                ]
            }
        )
        ad_insights = _json_response(
            {
                "data": [
                    {
                        "account_id": ACCOUNT_ID,
                        "ad_name": "Video A",
                        "spend": "6000",
                        "impressions": "1000",
                        "clicks": "80",
                        "ctr": "1.6",
                    }
                ]
            }
        )
        with mock.patch.object(grader.requests, "get", side_effect=[insights, ad_insights]):
            created = self.client.post("/audit", data={"account_id": ACCOUNT_ID})

        self.assertEqual(created.status_code, 303)
        location = created.headers["Location"]
        self.assertRegex(location, r"^/report/[A-Za-z0-9_-]{22,}$")
        self.assertNotIn(ACCOUNT_ID, location)
        self.assertNotIn(ACCESS_TOKEN, location)
        report_id = location.rsplit("/", 1)[1]
        self.assertGreaterEqual(_opaque_nbytes(report_id), 16)

        self._assert_cookie_is_only_an_opaque_id(self._session_cookie())
        with self.client.session_transaction() as sess:
            self.assertNotIn("access_token", sess)
        self.assertFalse(self._session_files_contain(ACCESS_TOKEN))

        stored = grader.app.report_store.get(report_id)
        self.assertIsNotNone(stored)
        self.assertNotIn("access_token", stored)
        self.assertNotIn("account_id", stored)
        raw_report = Path(grader.app.report_store.path).read_bytes()
        self.assertNotIn(ACCESS_TOKEN.encode(), raw_report)
        self.assertNotIn(ACCOUNT_ID.encode(), raw_report)
        self.assertIn(b"Spring Sale", raw_report)

        report = self.client.get("/report/" + report_id)
        self.assertEqual(report.status_code, 200)
        body = report.get_data(as_text=True)
        self.assertEqual(report.headers.get("X-Robots-Tag"), "noindex, nofollow")
        self.assertIn('<meta name="robots" content="noindex, nofollow">', body)
        self.assertIn("Spring Sale", body)
        self.assertNotIn(ACCOUNT_ID, body)
        self.assertNotIn(ACCESS_TOKEN, body)
        self.assertEqual(body.count("fbq('init'"), 1)
        self.assertEqual(body.count(CTA_URL), 2)
        self.assertEqual(body.count("fbq('trackCustom', 'AuditCTAClick'"), 1)
        self.assertIn("gtag('event', 'audit_cta_click'", body)
        event_id = re.search(r'var eventId = "([^"]+)"', body).group(1)
        self.assertGreaterEqual(_opaque_nbytes(event_id), 16)
        self.assertIn("fbq('trackCustom', 'GraderGradeView', params, {eventID: eventId})", body)

        self._assert_cookie_is_only_an_opaque_id(self._session_cookie())

    def test_grade_view_capi_matches_the_browser_event_id(self):
        self._connect_and_callback()
        insights = _json_response(
            {"data": [{"campaign_name": "Spring Sale", "spend": "10", "impressions": "1", "clicks": "1", "ctr": "1"}]}
        )
        ads = _json_response({"data": []})
        with mock.patch.object(grader.requests, "get", side_effect=[insights, ads]):
            created = self.client.post("/audit", data={"account_id": ACCOUNT_ID})
        report_id = created.headers["Location"].rsplit("/", 1)[1]

        os.environ["META_CAPI_ACCESS_TOKEN"] = "unit-test-capi-token"
        captured = {}
        done = threading.Event()

        def capture(url, json=None, timeout=None):
            captured["url"] = url
            captured["json"] = json
            done.set()
            return mock.Mock(status_code=200)

        self.client.set_cookie("_fbp", "fb.1.1.fbpvalue")
        try:
            with mock.patch.object(capi.requests, "post", capture):
                response = self.client.get(
                    "/report/" + report_id,
                    headers={
                        "User-Agent": "GradeTest/1.0",
                        "X-Forwarded-For": "203.0.113.9, 10.0.0.4",
                        "CF-Connecting-IP": "198.51.100.14",
                    },
                )
                self.assertTrue(done.wait(2))
        finally:
            os.environ.pop("META_CAPI_ACCESS_TOKEN", None)

        body = response.get_data(as_text=True)
        event_id = re.search(r'var eventId = "([^"]+)"', body).group(1)
        self.assertNotIn("unit-test-capi-token", captured["url"])
        self.assertNotIn("unit-test-capi-token", body)
        posted = captured["json"]["data"][0]
        self.assertEqual(posted["event_id"], event_id)
        self.assertEqual(posted["event_name"], "GraderGradeView")
        self.assertEqual(posted["user_data"]["client_ip_address"], "198.51.100.14")
        self.assertEqual(
            posted["event_source_url"],
            "https://localhost/report/" + report_id,
        )
        self.assertNotIn("http://", posted["event_source_url"])
        self.assertEqual(posted["user_data"]["client_user_agent"], "GradeTest/1.0")
        self.assertEqual(posted["user_data"]["fbp"], "fb.1.1.fbpvalue")
        self.assertTrue(posted["user_data"]["fbc"].endswith(".click-1"), posted["user_data"]["fbc"])
        self.assertNotIn(ACCOUNT_ID, posted["event_source_url"])
        self.assertNotIn(ACCOUNT_ID, str(posted))
        self.assertNotIn("account_id", posted["custom_data"])
        self.assertNotIn("em", posted["user_data"])
        self.assertNotIn("access_token", posted)

        done.clear()
        with mock.patch.object(capi.requests, "post") as post:
            again = self.client.get("/report/" + report_id)
            time.sleep(0.05)
            post.assert_not_called()
        again_body = again.get_data(as_text=True)
        self.assertNotIn("GraderGradeView", again_body)
        self.assertEqual(again.headers.get("X-Robots-Tag"), "noindex, nofollow")
        self.assertIn('<meta name="robots" content="noindex, nofollow">', again_body)

    def test_capi_hashes_a_real_email_and_skips_jordan_test(self):
        self.assertIsNone(grader._email_hash_for_capi(""))
        self.assertIsNone(grader._email_hash_for_capi("Jordan+Test@example.com"))
        digest = grader._email_hash_for_capi("Buyer@Example.com")
        self.assertEqual(
            digest,
            __import__("hashlib").sha256(b"buyer@example.com").hexdigest(),
        )

        self._connect_and_callback()
        insights = _json_response({"data": []})
        ads = _json_response({"data": []})
        with mock.patch.object(grader.requests, "get", side_effect=[insights, ads]):
            created = self.client.post("/audit", data={"account_id": ACCOUNT_ID})
        report_id = created.headers["Location"].rsplit("/", 1)[1]
        with self.client.session_transaction() as sess:
            sess["email"] = "Jordan+Test@example.com"

        os.environ["META_CAPI_ACCESS_TOKEN"] = "unit-test-capi-token"
        captured = {}
        done = threading.Event()

        def capture(url, json=None, timeout=None):
            captured["json"] = json
            done.set()
            return mock.Mock(status_code=200)

        try:
            with mock.patch.object(capi.requests, "post", capture):
                response = self.client.get("/report/" + report_id)
                self.assertTrue(done.wait(2))
        finally:
            os.environ.pop("META_CAPI_ACCESS_TOKEN", None)

        posted = captured["json"]["data"][0]
        self.assertNotIn("em", posted["user_data"])
        self.assertNotIn("jordan+test", str(posted).lower())
        self.assertNotIn("Jordan+Test@example.com", response.get_data(as_text=True))

        other = grader.app.test_client()
        self.client = other
        self._connect_and_callback()
        insights = _json_response({"data": []})
        ads = _json_response({"data": []})
        with mock.patch.object(grader.requests, "get", side_effect=[insights, ads]):
            created = self.client.post("/audit", data={"account_id": ACCOUNT_ID})
        report_id = created.headers["Location"].rsplit("/", 1)[1]
        with self.client.session_transaction() as sess:
            sess["email"] = "Buyer@Example.com"
        os.environ["META_CAPI_ACCESS_TOKEN"] = "unit-test-capi-token"
        captured = {}
        done = threading.Event()
        try:
            with mock.patch.object(capi.requests, "post", capture):
                self.client.get("/report/" + report_id)
                self.assertTrue(done.wait(2))
        finally:
            os.environ.pop("META_CAPI_ACCESS_TOKEN", None)
        posted = captured["json"]["data"][0]
        self.assertEqual(posted["user_data"]["em"], digest)
        self.assertNotIn("buyer@example.com", str(posted))

    def test_report_response_does_not_wait_for_capi(self):
        self._connect_and_callback()
        insights = _json_response({"data": []})
        ads = _json_response({"data": []})
        with mock.patch.object(grader.requests, "get", side_effect=[insights, ads]):
            created = self.client.post("/audit", data={"account_id": ACCOUNT_ID})
        report_id = created.headers["Location"].rsplit("/", 1)[1]

        started = threading.Event()
        release = threading.Event()

        def slow_post(url, json=None, timeout=None):
            started.set()
            release.wait(3)
            return mock.Mock(status_code=200)

        os.environ["META_CAPI_ACCESS_TOKEN"] = "unit-test-capi-token"
        try:
            with mock.patch.object(capi.requests, "post", slow_post):
                began = time.time()
                response = self.client.get("/report/" + report_id)
                elapsed = time.time() - began
            self.assertEqual(response.status_code, 200)
            self.assertLess(elapsed, 1.0)
            self.assertTrue(started.wait(1))
        finally:
            release.set()
            os.environ.pop("META_CAPI_ACCESS_TOKEN", None)

    def test_legacy_audit_urls_do_not_leak_the_account_id(self):
        secret = "act_LEGACYSECRET"
        response = self.client.get("/audit/" + secret)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/report-unavailable")
        self.assertNotIn(secret, response.headers["Location"])
        self.assertNotIn(secret, response.get_data(as_text=True))
        self.assertEqual(response.headers.get("Referrer-Policy"), "no-referrer")
        self.assertEqual(response.headers.get("X-Robots-Tag"), "noindex, nofollow")

        page = self.client.get("/report-unavailable")
        self.assertEqual(page.status_code, 404)
        text = page.get_data(as_text=True)
        self.assertNotIn(secret, text)
        self.assertNotIn("act_", text)
        self.assertIn("no longer available", text)
        self.assertIn(
            "This report has expired. Re-run your grade in about 60 seconds.",
            text,
        )
        self.assertEqual(page.headers.get("X-Robots-Tag"), "noindex, nofollow")
        self.assertIn('<meta name="robots" content="noindex, nofollow">', text)
        self.assertEqual(text.count("fbq('init'"), 1)

    def test_creator_converts_once_and_a_shared_link_does_not(self):
        self._connect_and_callback()
        insights = _json_response({"data": []})
        ads = _json_response({"data": []})
        with mock.patch.object(grader.requests, "get", side_effect=[insights, ads]):
            created = self.client.post("/audit", data={"account_id": ACCOUNT_ID})
        report_id = created.headers["Location"].rsplit("/", 1)[1]
        with self.client.session_transaction() as sess:
            self.assertIn(report_id, sess.get("owned_report_ids"))

        os.environ["META_CAPI_ACCESS_TOKEN"] = "unit-test-capi-token"
        done = threading.Event()

        def capture(url, json=None, timeout=None):
            done.set()
            return mock.Mock(status_code=200)

        conversion = "AW-16946961660/PG0ECNaOt70dEPy5-JA_"
        try:
            with mock.patch.object(capi.requests, "post", capture):
                owner = self.client.get("/report/" + report_id)
                self.assertTrue(done.wait(2))
            owner_html = owner.get_data(as_text=True)
            self.assertIn("GraderGradeView", owner_html)
            self.assertIn(conversion, owner_html)

            stranger = grader.app.test_client()
            done.clear()
            with mock.patch.object(capi.requests, "post") as post:
                shared = stranger.get("/report/" + report_id)
                time.sleep(0.05)
                post.assert_not_called()
            shared_html = shared.get_data(as_text=True)
            self.assertEqual(shared.status_code, 200)
            self.assertIn('class="grade"', shared_html)
            self.assertNotIn("GraderGradeView", shared_html)
            self.assertNotIn(conversion, shared_html)
        finally:
            os.environ.pop("META_CAPI_ACCESS_TOKEN", None)

    def test_account_key_is_an_hmac_not_the_raw_id(self):
        key = grader._account_key(ACCOUNT_ID)
        self.assertEqual(key, grader._account_key(ACCOUNT_ID))
        self.assertNotEqual(key, hashlib.sha256(ACCOUNT_ID.encode("utf-8")).hexdigest())
        self.assertNotIn("act_", key)
        self.assertNotIn(ACCOUNT_ID, key)

    def test_session_disk_lifetime_is_about_two_hours(self):
        self.assertEqual(grader.app.config["PERMANENT_SESSION_LIFETIME"], timedelta(hours=2))

    def test_invalid_oauth_state_shows_start_again(self):
        response = self.client.get("/auth/meta/callback?code=abc&state=not-the-state")
        self.assertEqual(response.status_code, 400)
        body = response.get_data(as_text=True)
        self.assertIn("Start again", body)
        self.assertIn('href="/connect"', body)
        self.assertNotIn("Invalid state or missing code", body)

    def test_create_report_failure_shows_a_retry_page(self):
        self._connect_and_callback()
        insights = _json_response({"data": []})
        ads = _json_response({"data": []})
        with mock.patch.object(grader.requests, "get", side_effect=[insights, ads]):
            with mock.patch.object(
                grader.app.report_store, "save", side_effect=RuntimeError("disk full token")
            ):
                response = self.client.post("/audit", data={"account_id": ACCOUNT_ID})
        self.assertEqual(response.status_code, 503)
        body = response.get_data(as_text=True)
        self.assertIn("Start again", body)
        self.assertIn('href="/connect"', body)
        self.assertIn("The grade didn", body)
        self.assertIn("finish.", body)
        self.assertNotIn("UnboundLocalError", body)
        self.assertNotIn("disk full", body)
        self.assertNotIn(ACCESS_TOKEN, body)
        with self.client.session_transaction() as sess:
            self.assertNotIn("access_token", sess)

    def test_results_are_noindex_and_the_landing_page_is_not(self):
        home = self.client.get("/")
        self.assertIsNone(home.headers.get("X-Robots-Tag"))
        connect = self.client.get("/connect")
        self.assertIsNone(connect.headers.get("X-Robots-Tag"))
        for path in ("/robots.txt", "/sitemap.xml"):
            response = self.client.get(path)
            body = response.get_data(as_text=True).lower()
            self.assertNotIn("disallow: /report", body)
            self.assertNotIn("disallow: /audit", body)
            if response.status_code == 200:
                self.assertNotIn("/report/", body)
                self.assertNotIn("/audit/", body)

    def test_unknown_report_id_does_not_echo_the_path(self):
        response = self.client.get("/report/act_DOESNOTEXIST")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/report-unavailable")
        self.assertNotIn("act_DOESNOTEXIST", response.get_data(as_text=True))
        self.assertEqual(response.headers.get("X-Robots-Tag"), "noindex, nofollow")

    def test_store_refuses_access_tokens_and_strips_account_ids(self):
        store = storage.SqliteReportStore(
            os.path.join(tempfile.mkdtemp(), "reports.sqlite")
        )
        with self.assertRaises(ValueError):
            store.save({"grade": "A", "access_token": "secret-token"})
        report_id = store.save(
            {
                "grade": "C",
                "account_id": ACCOUNT_ID,
                "insights": [{"account_id": ACCOUNT_ID, "campaign_name": "Keep me"}],
            }
        )
        self.assertGreaterEqual(_opaque_nbytes(report_id), 16)
        loaded = store.get(report_id)
        self.assertEqual(loaded["grade"], "C")
        self.assertNotIn("account_id", loaded)
        self.assertEqual(loaded["insights"], [{"campaign_name": "Keep me"}])
        self.assertIsNone(store.get("../" + report_id))
        self.assertIsNone(store.get("short"))
        other = store.save({"grade": "D"})
        self.assertNotEqual(report_id, other)


if __name__ == "__main__":
    unittest.main()
