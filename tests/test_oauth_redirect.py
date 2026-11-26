import os
import unittest
from unittest import mock

os.environ.setdefault("META_APP_SECRET", "unit-test-secret")
os.environ.setdefault("FLASK_SECRET_KEY", "unit-test-flask-key")

import app as grader  # noqa: E402

ALLOWLIST = (
    "meta-ads-grader.onrender.com",
    "grader.theviralmarketingcompany.com",
)
ENV_REDIRECT_URI = "https://env.example/auth/meta/callback"


def _dialog_redirect_uri(location):
    marker = "redirect_uri="
    start = location.index(marker) + len(marker)
    end = location.find("&", start)
    if end == -1:
        return location[start:]
    return location[start:end]


class OauthRedirectUriTests(unittest.TestCase):
    def test_allowlisted_hosts_use_the_same_uri_for_dialog_and_exchange(self):
        for host in ALLOWLIST:
            expected = "https://%s/auth/meta/callback" % host
            client = grader.app.test_client()
            # /connect on onrender is 301'd before the view. Turn that off so
            # this request is the one that actually starts OAuth on that host.
            with mock.patch.dict(os.environ, {"ONRENDER_HOST_REDIRECT": "0"}):
                dialog = client.get("/connect", base_url="https://" + host)
            self.assertEqual(dialog.status_code, 302, host)
            location = dialog.headers["Location"]
            self.assertIn("facebook.com", location)
            self.assertIn("scope=ads_read", location)
            self.assertNotIn("client_secret", location)
            self.assertEqual(_dialog_redirect_uri(location), expected, host)

            with client.session_transaction(base_url="https://" + host) as sess:
                state = sess["oauth_state"]
            token = mock.Mock(status_code=200)
            token.json.return_value = {"access_token": "tok"}
            accounts = mock.Mock(status_code=200)
            accounts.json.return_value = {
                "data": [{"id": "act_1", "name": "A", "account_status": 1}]
            }
            with mock.patch.object(grader.requests, "get", side_effect=[token, accounts]) as get:
                callback = client.get(
                    "/auth/meta/callback?code=abc&state=%s" % state,
                    base_url="https://" + host,
                )
            self.assertEqual(callback.status_code, 302, host)
            self.assertTrue(
                callback.headers["Location"].startswith("/select-account"),
                callback.headers["Location"],
            )
            _args, kwargs = get.call_args_list[0]
            self.assertEqual(kwargs["params"]["redirect_uri"], expected, host)

    def test_unknown_and_spoofed_hosts_fall_back_to_env(self):
        spoofs = (
            "http://localhost:5000",
            "http://evil.example",
            "http://meta-ads-grader.onrender.com.evil.example",
            "http://evil.meta-ads-grader.onrender.com",
            "http://grader.theviralmarketingcompany.com.evil.example",
            "http://not-grader.theviralmarketingcompany.com",
        )
        with mock.patch.object(grader, "REDIRECT_URI", ENV_REDIRECT_URI):
            for base in spoofs:
                client = grader.app.test_client()
                dialog = client.get(
                    "/connect",
                    base_url=base,
                    headers={"X-Forwarded-Host": "grader.theviralmarketingcompany.com"},
                )
                self.assertEqual(dialog.status_code, 302, base)
                self.assertIn("scope=ads_read", dialog.headers["Location"])
                self.assertEqual(
                    _dialog_redirect_uri(dialog.headers["Location"]),
                    ENV_REDIRECT_URI,
                    base,
                )
                with client.session_transaction(base_url=base) as sess:
                    state = sess["oauth_state"]
                token = mock.Mock(status_code=200)
                token.json.return_value = {"access_token": "tok"}
                accounts = mock.Mock(status_code=200)
                accounts.json.return_value = {"data": []}
                with mock.patch.object(
                    grader.requests, "get", side_effect=[token, accounts]
                ) as get:
                    client.get(
                        "/auth/meta/callback?code=abc&state=%s" % state,
                        base_url=base,
                        headers={"X-Forwarded-Host": "meta-ads-grader.onrender.com"},
                    )
                _args, kwargs = get.call_args_list[0]
                self.assertEqual(kwargs["params"]["redirect_uri"], ENV_REDIRECT_URI, base)

    def test_forwarded_host_does_not_change_an_allowlisted_host(self):
        client = grader.app.test_client()
        dialog = client.get(
            "/connect",
            base_url="https://grader.theviralmarketingcompany.com",
            headers={"X-Forwarded-Host": "meta-ads-grader.onrender.com.evil.example"},
        )
        self.assertEqual(
            _dialog_redirect_uri(dialog.headers["Location"]),
            "https://grader.theviralmarketingcompany.com/auth/meta/callback",
        )


if __name__ == "__main__":
    unittest.main()
