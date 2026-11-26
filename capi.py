"""Server-side GraderGradeView via the Meta Conversions API.

META_CAPI_ACCESS_TOKEN is optional. When it is unset or blank, every send is a
no-op and no request is made. The token is read at call time and is never
logged, printed, or put in the request URL (a requests exception includes the
URL). It is sent only in the JSON body.
"""

import os
import threading
import time

import requests

PIXEL_ID = "3803790859932149"
GRAPH_API_VERSION = "v21.0"
_EVENTS_URL = "https://graph.facebook.com/%s/%s/events" % (GRAPH_API_VERSION, PIXEL_ID)


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


def send_grade_view_event(event):
    """POST one event. Returns False without calling Meta when the token is unset."""
    token = access_token()
    if not token:
        return False
    body = {"data": [event], "access_token": token}
    try:
        requests.post(_EVENTS_URL, json=body, timeout=4)
    except Exception:
        return False
    return True


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
