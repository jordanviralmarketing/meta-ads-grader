"""Server-side GraderGradeView via the Meta Conversions API.

META_CAPI_ACCESS_TOKEN is optional. When it is unset or blank, every send is a
no-op and no request is made. The token is read at call time and is never
logged, printed, or put in the request URL (a requests exception includes the
URL). It is sent only in the JSON body.
"""

import logging
import os
import threading
import time

import requests

PIXEL_ID = "3803790859932149"
GRAPH_API_VERSION = "v24.0"
_EVENTS_URL = "https://graph.facebook.com/%s/%s/events" % (GRAPH_API_VERSION, PIXEL_ID)
_log = logging.getLogger(__name__)


def access_token():
    value = os.environ.get("META_CAPI_ACCESS_TOKEN")
    if value is None:
        return ""
    return value.strip()


def build_grade_view_event(event_id, event_source_url, user_data, custom_data, event_time=None):
    event = {
        "event_name": "GraderGradeView",
        "event_time": int(time.time() if event_time is None else event_time),
        "event_id": event_id,
        "action_source": "website",
        "user_data": {key: value for key, value in user_data.items() if value},
        "custom_data": custom_data,
    }
    if event_source_url:
        event["event_source_url"] = event_source_url
    return event


def _test_event_code():
    value = os.environ.get("META_CAPI_TEST_EVENT_CODE")
    if value is None:
        return ""
    return value.strip()


def _fbtrace_id(response):
    """Pull fbtrace_id off an error body. Never returns the rest of the body."""
    try:
        payload = response.json()
    except ValueError:
        return ""
    if not isinstance(payload, dict):
        return ""
    error = payload.get("error")
    if isinstance(error, dict):
        trace = error.get("fbtrace_id") or ""
    else:
        trace = payload.get("fbtrace_id") or ""
    if not isinstance(trace, str):
        return ""
    return trace


def send_grade_view_event(event):
    """POST one event. Returns False without calling Meta when the token is unset.

    A non-2xx response is a failure. The log line is only the status code and
    fbtrace_id. The access token and the response body are not logged.
    """
    token = access_token()
    if not token:
        return False
    body = {"data": [event]}
    test_code = _test_event_code()
    if test_code:
        body["test_event_code"] = test_code
    body["access_token"] = token
    try:
        response = requests.post(_EVENTS_URL, json=body, timeout=4)
    except Exception:
        return False
    if 200 <= response.status_code < 300:
        return True
    _log.warning(
        "Meta CAPI grade view failed status=%s fbtrace_id=%s",
        response.status_code,
        _fbtrace_id(response),
    )
    return False


def schedule_grade_view_event(event):
    """Fire the CAPI call on a daemon thread so rendering does not wait on Meta."""
    if not access_token():
        return False
    threading.Thread(
        target=send_grade_view_event,
        args=(event,),
        daemon=True,
    ).start()
    return True
