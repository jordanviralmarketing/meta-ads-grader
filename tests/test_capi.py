import logging
import os
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

FAKE_TOKEN = "CAPI-TOKEN-DO-NOT-LOG-9f3a"

os.environ.setdefault("META_APP_SECRET", "unit-test-secret")
os.environ.setdefault("FLASK_SECRET_KEY", "unit-test-flask-key")

import capi  # noqa: E402


class ConversionsApiTests(unittest.TestCase):
    def setUp(self):
        self._previous = os.environ.get("META_CAPI_ACCESS_TOKEN")
        self._previous_code = os.environ.get("META_CAPI_TEST_EVENT_CODE")
        os.environ.pop("META_CAPI_ACCESS_TOKEN", None)
        os.environ.pop("META_CAPI_TEST_EVENT_CODE", None)

    def tearDown(self):
        if self._previous is None:
            os.environ.pop("META_CAPI_ACCESS_TOKEN", None)
        else:
            os.environ["META_CAPI_ACCESS_TOKEN"] = self._previous
        if self._previous_code is None:
            os.environ.pop("META_CAPI_TEST_EVENT_CODE", None)
        else:
            os.environ["META_CAPI_TEST_EVENT_CODE"] = self._previous_code

    def test_unset_token_is_a_noop(self):
        event = capi.build_grade_view_event(
            event_id="evt-1",
            event_source_url="https://example.com/report/abc",
            user_data={"client_ip_address": "203.0.113.5"},
            custom_data={"grade": "B"},
        )
        with mock.patch.object(capi.requests, "post") as post:
            self.assertFalse(capi.send_grade_view_event(event))
            self.assertFalse(capi.schedule_grade_view_event(event))
            post.assert_not_called()

    def test_blank_token_is_a_noop(self):
        os.environ["META_CAPI_ACCESS_TOKEN"] = "   "
        with mock.patch.object(capi.requests, "post") as post:
            self.assertFalse(capi.send_grade_view_event({"event_id": "evt-1"}))
            post.assert_not_called()

    def test_payload_includes_event_id_and_keeps_the_token_out_of_the_url(self):
        os.environ["META_CAPI_ACCESS_TOKEN"] = "unit-test-capi-token"
        event = capi.build_grade_view_event(
            event_id="evt-grade-1",
            event_source_url="https://meta-ads-grader.example/report/opaque",
            user_data={
                "client_ip_address": "203.0.113.5",
                "client_user_agent": "TestAgent",
                "fbp": "fb.1.1.fbp",
                "fbc": "fb.1.1.fbc",
                "account_id": "",
            },
            custom_data={"grade": "A", "score": 91, "qualified": True},
            event_time=1700000000,
        )
        captured = {}

        def capture(url, json=None, timeout=None):
            captured["url"] = url
            captured["json"] = json
            return mock.Mock(status_code=200)

        with mock.patch.object(capi.requests, "post", capture):
            self.assertTrue(capi.send_grade_view_event(event))

        self.assertNotIn("unit-test-capi-token", captured["url"])
        self.assertNotIn("access_token", captured["url"])
        self.assertEqual(captured["json"]["access_token"], "unit-test-capi-token")
        posted = captured["json"]["data"][0]
        self.assertEqual(posted["event_id"], "evt-grade-1")
        self.assertEqual(posted["event_name"], "GraderGradeView")
        self.assertEqual(posted["event_time"], 1700000000)
        self.assertNotIn("account_id", posted["user_data"])
        self.assertNotIn("act_", str(posted))

    def test_non_2xx_is_a_failure_and_logs_only_status_and_trace(self):
        os.environ["META_CAPI_ACCESS_TOKEN"] = "unit-test-capi-token"
        response = mock.Mock(status_code=400)
        response.json.return_value = {
            "error": {
                "message": "bad unit-test-capi-token leaked in the body",
                "fbtrace_id": "trace-123",
            }
        }
        response.text = "bad unit-test-capi-token leaked in the body"
        with mock.patch.object(capi.requests, "post", return_value=response):
            with self.assertLogs("capi", level="WARNING") as logs:
                self.assertFalse(capi.send_grade_view_event({"event_id": "evt-fail"}))
        text = "\n".join(logs.output)
        self.assertIn("status=400", text)
        self.assertIn("fbtrace_id=trace-123", text)
        self.assertNotIn("unit-test-capi-token", text)
        self.assertNotIn("leaked in the body", text)

    def test_non_json_error_does_not_log_the_body(self):
        os.environ["META_CAPI_ACCESS_TOKEN"] = "unit-test-capi-token"
        response = mock.Mock(status_code=500)
        response.json.side_effect = ValueError("not json")
        response.text = "raw body unit-test-capi-token"
        with mock.patch.object(capi.requests, "post", return_value=response):
            with self.assertLogs("capi", level="WARNING") as logs:
                self.assertFalse(capi.send_grade_view_event({"event_id": "evt-fail"}))
        text = "\n".join(logs.output)
        self.assertIn("status=500", text)
        self.assertIn("fbtrace_id=", text)
        self.assertNotIn("raw body", text)
        self.assertNotIn("unit-test-capi-token", text)

    def test_success_returns_true_without_a_warning(self):
        os.environ["META_CAPI_ACCESS_TOKEN"] = "unit-test-capi-token"
        response = mock.Mock(status_code=200)
        with mock.patch.object(capi.requests, "post", return_value=response):
            with self.assertNoLogs("capi", level=logging.WARNING):
                self.assertTrue(capi.send_grade_view_event({"event_id": "evt-ok"}))

    def test_test_event_code_is_sent_only_when_set(self):
        os.environ["META_CAPI_ACCESS_TOKEN"] = "unit-test-capi-token"
        captured = {}

        def capture(url, json=None, timeout=None):
            captured["json"] = json
            return mock.Mock(status_code=200)

        with mock.patch.object(capi.requests, "post", capture):
            capi.send_grade_view_event({"event_id": "evt-1"})
        self.assertNotIn("test_event_code", captured["json"])

        os.environ["META_CAPI_TEST_EVENT_CODE"] = "  TEST123  "
        with mock.patch.object(capi.requests, "post", capture):
            capi.send_grade_view_event({"event_id": "evt-1"})
        self.assertEqual(captured["json"]["test_event_code"], "TEST123")
        self.assertNotIn("TEST123", captured.get("url", ""))

    def test_graph_version_is_v24_or_later(self):
        import app as grader

        for version in (capi.GRAPH_API_VERSION, grader.GRAPH_API_VERSION):
            major = int(version[1:].split(".", 1)[0])
            self.assertGreaterEqual(major, 24)

    def test_schedule_does_not_block_on_the_request(self):
        os.environ["META_CAPI_ACCESS_TOKEN"] = "unit-test-capi-token"
        started = threading.Event()
        release = threading.Event()

        def slow(event):
            started.set()
            release.wait(3)

        try:
            with mock.patch.object(capi, "send_grade_view_event", slow):
                began = time.time()
                self.assertTrue(capi.schedule_grade_view_event({"event_id": "evt-2"}))
                elapsed = time.time() - began
            self.assertLess(elapsed, 0.5)
            self.assertTrue(started.wait(1))
        finally:
            release.set()


class ConversionsApiLogTests(unittest.TestCase):
    """Logs may say whether the token is set. They must never contain its value."""

    def setUp(self):
        self._previous = os.environ.get("META_CAPI_ACCESS_TOKEN")
        self._previous_code = os.environ.get("META_CAPI_TEST_EVENT_CODE")
        os.environ.pop("META_CAPI_ACCESS_TOKEN", None)
        os.environ.pop("META_CAPI_TEST_EVENT_CODE", None)

    def tearDown(self):
        if self._previous is None:
            os.environ.pop("META_CAPI_ACCESS_TOKEN", None)
        else:
            os.environ["META_CAPI_ACCESS_TOKEN"] = self._previous
        if self._previous_code is None:
            os.environ.pop("META_CAPI_TEST_EVENT_CODE", None)
        else:
            os.environ["META_CAPI_TEST_EVENT_CODE"] = self._previous_code

    def _assert_token_absent(self, text):
        self.assertNotIn(FAKE_TOKEN, text)

    def test_capi_logger_level_is_info_after_app_import(self):
        import app as grader  # noqa: F401

        self.assertEqual(logging.getLogger("capi").getEffectiveLevel(), logging.INFO)

    def test_request_exception_logs_only_the_exception_type(self):
        os.environ["META_CAPI_ACCESS_TOKEN"] = FAKE_TOKEN
        leaked_url = "https://graph.facebook.com/v24.0/1/events?access_token=" + FAKE_TOKEN

        def boom(*args, **kwargs):
            raise capi.requests.Timeout("timed out " + leaked_url)

        with mock.patch.object(capi.requests, "post", side_effect=boom):
            with self.assertLogs("capi", level="WARNING") as logs:
                self.assertFalse(capi.send_grade_view_event({"event_id": "evt-timeout"}))
        text = "\n".join(logs.output)
        self.assertIn("error=Timeout", text)
        self.assertNotIn(FAKE_TOKEN, text)
        self.assertNotIn("graph.facebook.com", text)
        self.assertNotIn(leaked_url, text)
        self.assertNotIn("evt-timeout", text)

    def test_startup_log_says_yes_or_no_and_hides_the_token(self):
        os.environ["META_CAPI_ACCESS_TOKEN"] = FAKE_TOKEN
        with self.assertLogs("capi", level="INFO") as logs:
            capi.log_startup()
        text = "\n".join(logs.output)
        self.assertIn("META_CAPI_ACCESS_TOKEN is set: yes", text)
        self._assert_token_absent(text)

        os.environ["META_CAPI_ACCESS_TOKEN"] = "   "
        with self.assertLogs("capi", level="INFO") as logs:
            capi.log_startup()
        text = "\n".join(logs.output)
        self.assertIn("META_CAPI_ACCESS_TOKEN is set: no", text)
        self._assert_token_absent(text)
        app_source = Path(__file__).resolve().parents[1].joinpath("app.py").read_text()
        self.assertEqual(app_source.count("capi.log_startup()"), 1)

    def test_success_log_is_status_count_and_test_flag_only(self):
        os.environ["META_CAPI_ACCESS_TOKEN"] = FAKE_TOKEN
        os.environ["META_CAPI_TEST_EVENT_CODE"] = "TEST-CODE-DO-NOT-LOG"
        response = mock.Mock(status_code=200)
        response.json.return_value = {
            "events_received": 1,
            "fbtrace_id": "do-not-log-trace",
            "messages": [FAKE_TOKEN],
        }
        response.text = FAKE_TOKEN
        with mock.patch.object(capi.requests, "post", return_value=response):
            with self.assertLogs("capi", level="INFO") as logs:
                self.assertTrue(
                    capi.send_grade_view_event(
                        {"event_id": "evt-ok", "secret": FAKE_TOKEN}
                    )
                )
        text = "\n".join(logs.output)
        self.assertIn("status=200", text)
        self.assertIn("events_received=1", text)
        self.assertIn("test_event_code=true", text)
        self.assertNotIn("TEST-CODE-DO-NOT-LOG", text)
        self.assertNotIn("do-not-log-trace", text)
        self.assertNotIn("evt-ok", text)
        self._assert_token_absent(text)

        os.environ.pop("META_CAPI_TEST_EVENT_CODE", None)
        response.json.return_value = {"events_received": 2, "access_token": FAKE_TOKEN}
        with mock.patch.object(capi.requests, "post", return_value=response):
            with self.assertLogs("capi", level="INFO") as logs:
                self.assertTrue(capi.send_grade_view_event({"event_id": "evt-2"}))
        text = "\n".join(logs.output)
        self.assertIn("events_received=2", text)
        self.assertIn("test_event_code=false", text)
        self._assert_token_absent(text)

    def test_failure_log_still_hides_the_token(self):
        os.environ["META_CAPI_ACCESS_TOKEN"] = FAKE_TOKEN
        response = mock.Mock(status_code=400)
        response.json.return_value = {
            "error": {
                "message": "rejected " + FAKE_TOKEN,
                "fbtrace_id": "trace-123",
            }
        }
        response.text = FAKE_TOKEN
        with mock.patch.object(capi.requests, "post", return_value=response):
            with self.assertLogs("capi", level="INFO") as logs:
                self.assertFalse(capi.send_grade_view_event({"event_id": FAKE_TOKEN}))
        text = "\n".join(logs.output)
        self.assertIn("status=400", text)
        self.assertIn("fbtrace_id=trace-123", text)
        self._assert_token_absent(text)

    def test_skip_logs_one_line_and_not_the_payload(self):
        event = {"event_id": "evt-skip", "marker": FAKE_TOKEN}
        with mock.patch.object(capi.requests, "post") as post:
            with self.assertLogs("capi", level="INFO") as logs:
                self.assertFalse(capi.send_grade_view_event(event))
        post.assert_not_called()
        text = "\n".join(logs.output)
        self.assertEqual(
            text.count("Meta CAPI grade view skipped: META_CAPI_ACCESS_TOKEN is not set"),
            1,
        )
        self.assertNotIn("evt-skip", text)
        self._assert_token_absent(text)

        with mock.patch.object(capi.requests, "post") as post:
            with self.assertLogs("capi", level="INFO") as logs:
                self.assertFalse(capi.schedule_grade_view_event(event))
        post.assert_not_called()
        text = "\n".join(logs.output)
        self.assertEqual(
            text.count("Meta CAPI grade view skipped: META_CAPI_ACCESS_TOKEN is not set"),
            1,
        )
        self.assertNotIn("evt-skip", text)
        self._assert_token_absent(text)


if __name__ == "__main__":
    unittest.main()
