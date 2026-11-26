import os
import re
import unittest
from unittest import mock

os.environ.setdefault("META_APP_SECRET", "unit-test-secret")
os.environ.setdefault("FLASK_SECRET_KEY", "unit-test-flask-key")

import app as grader  # noqa: E402
from flask import render_template  # noqa: E402

PIXEL_ID = "3803790859932149"
GADS_ID = "AW-16946961660"
GA4_ID = "G-S0JYBC8F8L"


def _report(spend):
    return {
        "grade": "B",
        "score": 81,
        "insights": [],
        "categories": [],
        "summary": "x",
        "total_spend": spend,
    }


def _render_report(spend):
    with grader.app.test_request_context("/"):
        return render_template(
            "report.html",
            report=_report(spend),
            strategy_url="x",
            event_id="evt-test",
            report_id="opaque-report-id",
        )


class TrackingTagTests(unittest.TestCase):
    def test_every_page_loads_pixel_and_google_tag(self):
        with grader.app.test_request_context("/"):
            pages = {
                "index": render_template("index.html"),
                "select_account": render_template(
                    "select_account.html",
                    accounts=[{"id": "act_1", "name": "A", "account_status": 1}],
                ),
                "report": _render_report(100),
                "thank_you": render_template("thank_you.html", strategy_url="x"),
            }
        for name, html in pages.items():
            self.assertIn("fbq('init', '%s')" % PIXEL_ID, html, name)
            self.assertIn("gtag('config', '%s')" % GADS_ID, html, name)
            self.assertIn(GA4_ID, html, name)

    def test_ga4_ignores_facebook_oauth_referrer(self):
        html = _render_report(100)
        self.assertIn("ignore_referrer: true", html)
        self.assertIn("facebook", html)

    def test_grade_view_event_fires_with_qualified_flag(self):
        html = _render_report(6000)
        self.assertIn("fbq('trackCustom', 'GraderGradeView', params, {eventID: eventId})", html)
        self.assertIn('var eventId = "evt-test";', html)
        self.assertEqual(html.count("fbq('init'"), 1)
        self.assertNotIn("act_", html)
        self.assertIn("AW-16946961660/PG0ECNaOt70dEPy5-JA_", html)
        self.assertIn("var qualified = true;", html)
        self.assertIn("var qualified = false;", _render_report(100))

    def test_audit_cta_tracks_pixel_and_ga4_without_changing_the_link(self):
        html = _render_report(100)
        self.assertEqual(html.count("fbq('trackCustom', 'AuditCTAClick'"), 1)
        self.assertIn("{eventID: newEventId()}", html)
        self.assertEqual(html.count("newEventId()"), 2)
        self.assertIn("gtag('event', 'audit_cta_click'", html)
        self.assertEqual(html.count("Buy an audit today"), 2)
        self.assertEqual(
            html.count(
                "https://audit.theviralmarketingcompany.com/?utm_source=grader&amp;utm_medium=cta&amp;utm_campaign=grade_screen"
            ),
            2,
        )

    def test_review_button_only_for_5k_spend(self):
        self.assertIn('id="grader-review-btn"', _render_report(5000))
        self.assertNotIn('id="grader-review-btn"', _render_report(4999.99))

    def test_review_click_is_tracked(self):
        html = _render_report(6000)
        self.assertIn("fbq('trackCustom', 'GraderReviewRequestClick', params)", html)
        self.assertIn("AW-16946961660/wM9WCNmOt70dEPy5-JA_", html)

    def test_review_link_has_no_utm_parameters(self):
        html = _render_report(6000)
        link = re.search(r'id="grader-review-btn" href="([^"]+)"', html).group(1)
        self.assertEqual(
            link,
            "https://theviralmarketingcompany.com/meta-ads-services-the-viral-marketing-company#review",
        )
        self.assertNotIn("utm_", link)


class ClickIdRestoreTests(unittest.TestCase):
    def test_click_ids_survive_facebook_oauth(self):
        client = grader.app.test_client()
        response = client.get(
            "/connect?gclid=G1&fbclid=F1&_gl=1*abc&utm_source=meta&utm_campaign=grader&junk=x"
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("facebook.com", response.headers["Location"])
        with client.session_transaction() as sess:
            state = sess["oauth_state"]
            self.assertEqual(
                sess["tracking_params"],
                {"gclid": "G1", "fbclid": "F1", "_gl": "1*abc", "utm_source": "meta", "utm_campaign": "grader"},
            )

        token = mock.Mock(status_code=200)
        token.json.return_value = {"access_token": "tok"}
        accounts = mock.Mock(status_code=200)
        accounts.json.return_value = {"data": [{"id": "act_1", "name": "A", "account_status": 1}]}
        with mock.patch.object(grader.requests, "get", side_effect=[token, accounts]):
            callback = client.get("/auth/meta/callback?code=abc&state=%s" % state)

        self.assertEqual(callback.status_code, 302)
        location = callback.headers["Location"]
        self.assertTrue(location.startswith("/select-account?"), location)
        for part in ("gclid=G1", "fbclid=F1", "_gl=1*abc", "utm_source=meta", "utm_campaign=grader"):
            self.assertIn(part, location)
        self.assertNotIn("junk", location)

    def test_connect_without_click_ids_redirects_cleanly(self):
        client = grader.app.test_client()
        client.get("/connect")
        with client.session_transaction() as sess:
            self.assertEqual(sess["tracking_params"], {})


if __name__ == "__main__":
    unittest.main()
