import os
import unittest
from unittest import mock

os.environ.setdefault("META_APP_SECRET", "unit-test-secret")
os.environ.setdefault("FLASK_SECRET_KEY", "unit-test-flask-key")

import app as grader  # noqa: E402

ONRENDER = "http://meta-ads-grader.onrender.com"
CUSTOM = "http://grader.theviralmarketingcompany.com"
CANONICAL = "https://grader.theviralmarketingcompany.com"


class OnrenderHostRedirectTests(unittest.TestCase):
    def setUp(self):
        os.environ.pop("ONRENDER_HOST_REDIRECT", None)
        self.client = grader.app.test_client()

    def test_onrender_host_redirects_with_path_and_query(self):
        response = self.client.get("/some/path?x=1", base_url=ONRENDER)
        self.assertEqual(response.status_code, 301)
        self.assertEqual(
            response.headers["Location"],
            CANONICAL + "/some/path?x=1",
        )

    def test_onrender_host_preserves_query_order_and_omits_empty_query(self):
        with_query = self.client.get("/audit/act_1?b=2&a=1", base_url=ONRENDER)
        self.assertEqual(
            with_query.headers["Location"],
            CANONICAL + "/audit/act_1?b=2&a=1",
        )
        bare = self.client.get("/some/path", base_url=ONRENDER)
        self.assertEqual(bare.headers["Location"], CANONICAL + "/some/path")
        self.assertNotIn("?", bare.headers["Location"])

    def test_oauth_callback_on_onrender_host_redirects_with_query(self):
        response = self.client.get(
            "/auth/meta/callback?code=abc&state=xyz",
            base_url=ONRENDER,
        )
        self.assertEqual(response.status_code, 301)
        self.assertEqual(
            response.headers["Location"],
            CANONICAL + "/auth/meta/callback?code=abc&state=xyz",
        )

    def test_root_on_onrender_host_keeps_the_root_path(self):
        response = self.client.get("/", base_url=ONRENDER)
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response.headers["Location"], CANONICAL + "/")

    def test_head_on_onrender_host_redirects(self):
        response = self.client.head("/some/path?x=1", base_url=ONRENDER)
        self.assertEqual(response.status_code, 301)
        self.assertEqual(
            response.headers["Location"],
            CANONICAL + "/some/path?x=1",
        )

    def test_host_match_ignores_case_port_and_trailing_dot(self):
        for base in (
            "http://Meta-Ads-Grader.onrender.com",
            "http://meta-ads-grader.onrender.com:443",
            "http://meta-ads-grader.onrender.com.",
        ):
            response = self.client.get("/some/path?x=1", base_url=base)
            self.assertEqual(response.status_code, 301, base)
            self.assertEqual(
                response.headers["Location"],
                CANONICAL + "/some/path?x=1",
                base,
            )

    def test_custom_host_is_not_redirected(self):
        response = self.client.get("/some/path?x=1", base_url=CUSTOM)
        self.assertEqual(response.status_code, 404)
        self.assertNotIn("Location", response.headers)

        home = self.client.get("/", base_url="https://grader.theviralmarketingcompany.com")
        self.assertEqual(home.status_code, 302)
        self.assertTrue(home.headers["Location"].endswith("/connect"), home.headers["Location"])

        thank_you = self.client.get("/thank-you", base_url=CUSTOM)
        self.assertEqual(thank_you.status_code, 200)

    def test_localhost_and_other_hosts_are_not_redirected(self):
        for base in (
            "http://localhost",
            "http://localhost:5000",
            "http://127.0.0.1:5000",
            "http://other.onrender.com",
            "http://meta-ads-grader.onrender.com.evil.example",
        ):
            response = self.client.get("/some/path?x=1", base_url=base)
            self.assertEqual(response.status_code, 404, base)
            self.assertNotIn("Location", response.headers, base)

    def test_forwarded_host_and_proto_do_not_redirect_or_loop(self):
        # Custom domain behind Render: TLS is already terminated, so the app
        # sees http. A spoofed or stale X-Forwarded-Host must not 301 this.
        custom = self.client.get(
            "/some/path?x=1",
            base_url=CUSTOM,
            headers={
                "X-Forwarded-Host": "meta-ads-grader.onrender.com",
                "X-Forwarded-Proto": "http",
            },
        )
        self.assertEqual(custom.status_code, 404)
        self.assertNotIn("Location", custom.headers)

        # onrender.com behind the same proxy still goes to https on the
        # custom domain, never back to the onrender host.
        onrender = self.client.get(
            "/some/path?x=1",
            base_url=ONRENDER,
            headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": "grader.theviralmarketingcompany.com"},
        )
        self.assertEqual(onrender.status_code, 301)
        self.assertEqual(onrender.headers["Location"], CANONICAL + "/some/path?x=1")
        self.assertNotIn("onrender.com", onrender.headers["Location"])

    def test_env_var_disables_redirect(self):
        for off in ("0", "false", "no", "off", " FALSE "):
            with mock.patch.dict(os.environ, {"ONRENDER_HOST_REDIRECT": off}):
                response = self.client.get("/some/path?x=1", base_url=ONRENDER)
            self.assertEqual(response.status_code, 404, off)
            self.assertNotIn("Location", response.headers, off)

        for on in ("", "1", "true", "yes"):
            with mock.patch.dict(os.environ, {"ONRENDER_HOST_REDIRECT": on}):
                response = self.client.get("/some/path?x=1", base_url=ONRENDER)
            self.assertEqual(response.status_code, 301, repr(on))

    def test_health_check_path_is_not_redirected(self):
        # Nothing in the repo is a health route, so the production set is empty.
        # The skip must leave that path's own status in place on the onrender host.
        self.assertEqual(grader.HEALTH_CHECK_PATHS, frozenset())
        with mock.patch.object(grader, "HEALTH_CHECK_PATHS", frozenset({"/healthz"})):
            health = self.client.get("/healthz", base_url=ONRENDER)
            other = self.client.get("/some/path?x=1", base_url=ONRENDER)
        self.assertNotEqual(health.status_code, 301)
        self.assertNotIn("Location", health.headers)
        self.assertEqual(other.status_code, 301)
        self.assertEqual(other.headers["Location"], CANONICAL + "/some/path?x=1")


if __name__ == "__main__":
    unittest.main()
