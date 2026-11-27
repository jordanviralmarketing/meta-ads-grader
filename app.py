from flask import Flask, redirect, request, session, render_template, url_for, make_response
import hashlib
import hmac
import logging
import re
import requests
import secrets
import time
from urllib.parse import urlencode
from config import (
    FLASK_SECRET_KEY,
    META_APP_ID,
    META_APP_SECRET,
    REDIRECT_URI,
    STRATEGY_SESSION_URL,
    onrender_host_redirect_enabled,
)
import capi
import sessions
import storage

app = Flask(__name__)
app.secret_key = FLASK_SECRET_KEY
# gunicorn leaves the root logger at WARNING, which would hide capi's INFO lines.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


class _DropQueryStringFilter(logging.Filter):
    """Keep emails out of access logs, including gunicorn's atom dict.

    Gunicorn logs with a SafeAtoms dict, not a tuple. That dict must be
    updated in place: replacing it drops the missing-key behavior, and a
    header atom such as %({x-forwarded-for}i)s then raises KeyError. The
    logging error dump prints the whole atom dict, query and session cookie
    included.     q and {query_string}e have no leading "?", and {raw_uri}e
    carries the path plus the query, so those atoms are blanked by name.
    {cookie}i and {set-cookie}o are blanked too: a broken format makes
    logging print every atom to stderr, which would leak the session id.
    Request-line atoms that still contain "?..." are stripped as before.
    """

    _query = re.compile(r"\?[^\s\"']*")
    _query_bytes = re.compile(br"\?[^\s\"']*")
    _blank_atoms = frozenset({
        "q",
        "{query_string}e",
        "{raw_uri}e",
        "{cookie}i",
        "{set-cookie}o",
    })

    def filter(self, record):
        record.msg = self._scrub_text(record.msg)
        args = record.args
        if isinstance(args, dict):
            for key in list(args.keys()):
                args[key] = self._scrub_atom(key, args[key])
        elif isinstance(args, tuple):
            record.args = tuple(self._scrub_text(arg) for arg in args)
        else:
            record.args = self._scrub_text(args)
        return True

    def _scrub_atom(self, key, value):
        if isinstance(key, str) and key.lower() in self._blank_atoms:
            if isinstance(value, (bytes, bytearray)):
                return b""
            return ""
        return self._scrub_text(value)

    def _scrub_text(self, value):
        if isinstance(value, str):
            return self._query.sub("", value)
        if isinstance(value, (bytes, bytearray)):
            return self._query_bytes.sub(b"", value)
        return value


_query_filter = _DropQueryStringFilter()
for _handler in logging.getLogger().handlers:
    _handler.addFilter(_query_filter)
for _logger_name in ("werkzeug", "werkzeug.serving", "gunicorn.access", "gunicorn.error"):
    logging.getLogger(_logger_name).addFilter(_query_filter)

capi.log_startup()
sessions.init_server_session(app)
app.report_store = storage.open_report_store()

# Public Render hostname. Requests here 301 to the custom domain.
# Any other Host, including the custom domain and localhost, is unchanged.
ONRENDER_HOST = "meta-ads-grader.onrender.com"
CANONICAL_ORIGIN = "https://grader.theviralmarketingcompany.com"

# No render.yaml and no health route in this app. Render's default check is a
# TCP connect to the web port, which never reaches Flask. If the dashboard
# healthCheckPath is set, add that path here so a probe whose Host is still
# the onrender.com name gets the route's own status (200) instead of a 301.
HEALTH_CHECK_PATHS = frozenset()

# Hosts that may complete Meta OAuth on themselves. Compared exactly, after
# port and trailing-dot are removed. A suffix or X-Forwarded-Host does not match.
OAUTH_REDIRECT_HOSTS = frozenset({
    "meta-ads-grader.onrender.com",
    "grader.theviralmarketingcompany.com",
})
OAUTH_CALLBACK_PATH = "/auth/meta/callback"


def _public_hostname():
    """Hostname the client used, from the Host header, without a port.

    Render terminates TLS and forwards plain HTTP. It sets Host to the public
    name (the onrender.com subdomain or a custom domain). The WSGI scheme is
    http for every request, and X-Forwarded-Host is not that public name, so
    neither is used. Redirecting because the scheme is http would loop.
    """
    raw = (request.environ.get("HTTP_HOST") or "").strip().lower().rstrip(".")
    if not raw:
        return ""
    if raw.startswith("["):
        end = raw.find("]")
        return raw[1:end] if end != -1 else raw
    return raw.split(":", 1)[0]


def _canonical_url():
    """Same path and query on the custom domain, always https."""
    path = request.script_root + (request.path or "/")
    if not path.startswith("/") or path.startswith("//") or "\\" in path:
        path = "/"
    query = request.query_string.decode("latin-1")
    if query:
        return "%s%s?%s" % (CANONICAL_ORIGIN, path, query)
    return CANONICAL_ORIGIN + path


def oauth_redirect_uri():
    """redirect_uri for this request.

    Allowlisted hosts always use https and /auth/meta/callback on that host,
    so the session cookie set at /connect is present when Facebook returns.
    Every other host uses REDIRECT_URI. Host comes from the Host header only.
    """
    host = _public_hostname()
    if host in OAUTH_REDIRECT_HOSTS:
        return "https://%s%s" % (host, OAUTH_CALLBACK_PATH)
    return REDIRECT_URI


@app.before_request
def redirect_onrender_host():
    if not onrender_host_redirect_enabled():
        return None
    # In-flight logins started on onrender must finish there. The state cookie
    # is host-only, and this path is not sent on to the custom domain.
    # /start is also exempt: its only redirect is the thanks page, which must
    # not carry the email. A host 301 would put ?email= in Location.
    if (
        request.path == OAUTH_CALLBACK_PATH
        or request.path == "/start"
        or request.path in HEALTH_CHECK_PATHS
    ):
        return None
    if _public_hostname() != ONRENDER_HOST:
        return None
    return redirect(_canonical_url(), code=301)


GRAPH_API_VERSION = "v24.0"
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"
_log = logging.getLogger(__name__)

@app.route("/")
def index():
    return redirect(url_for("connect"), code=302)

# Ad click IDs, the GA4 cross-domain linker (_gl) and UTMs passed from the VMC
# thank-you page. /connect redirects straight to Facebook, so no tag runs here; we
# keep them in the session and re-append them after OAuth so the Meta Pixel,
# Google Ads and GA4 tags on this domain can attribute the visit.
TRACKING_PARAMS = ("gclid", "gbraid", "wbraid", "fbclid", "_gl",
                   "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term")

THANKS_URL = "https://theviralmarketingcompany.com/meta-ads-grader-thanks"
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _normalize_email(raw):
    """Lowercased email, or None when the shape is wrong or it is too long."""
    if not isinstance(raw, str):
        return None
    email = raw.strip().lower()
    if not email or len(email) > 254 or not _EMAIL_RE.fullmatch(email):
        return None
    return email


@app.route("/start")
def start():
    """Store a GHL form email, then leave for the thanks page without it.

    The response is an empty 302. No template and no tracking tags. The email
    stays in the server-side session and is not logged or copied into Location.
    """
    email = _normalize_email(request.args.get("email"))
    if email:
        session["email"] = email
    forwarded = [
        (key, value)
        for key, value in request.args.items(multi=True)
        if value and (key == "fbclid" or key.startswith("utm_"))
    ]
    location = THANKS_URL
    if forwarded:
        location = "%s?%s" % (THANKS_URL, urlencode(forwarded))
    response = make_response("", 302)
    response.headers["Location"] = location
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response

@app.route("/connect")
def connect():
    state = secrets.token_urlsafe(16)
    session["oauth_state"] = state
    session["tracking_params"] = {k: request.args[k] for k in TRACKING_PARAMS if request.args.get(k)}
    redirect_uri = oauth_redirect_uri()
    auth_url = (
        f"https://www.facebook.com/{GRAPH_API_VERSION}/dialog/oauth?"
        f"client_id={META_APP_ID}&"
        f"redirect_uri={redirect_uri}&"
        f"state={state}&"
        f"scope=ads_read"
    )
    return redirect(auth_url)

@app.route("/auth/meta/callback")
def callback():
    error = request.args.get("error")
    if error:
        # Do not echo error or error_description. Both are attacker-controlled.
        return _start_again("The sign-in didn't finish. Connect your account once more."), 400

    code = request.args.get("code")
    state = request.args.get("state")
    if not code or state != session.get("oauth_state"):
        return _start_again("The sign-in didn't finish. Connect your account once more."), 400

    # Exchange code for access token. Same redirect_uri the dialog used.
    token_url = f"{GRAPH_BASE}/oauth/access_token"
    redirect_uri = oauth_redirect_uri()
    params = {
        "client_id": META_APP_ID,
        "client_secret": META_APP_SECRET,
        "redirect_uri": redirect_uri,
        "code": code,
    }
    token_resp = requests.get(token_url, params=params, timeout=20)
    if token_resp.status_code != 200:
        _log_oauth_exchange_failure(token_resp)
        return _start_again("The sign-in didn't finish. Connect your account once more."), 400

    token_data = token_resp.json()
    access_token = token_data.get("access_token")
    if not access_token:
        return "No access token received", 400

    # Server-side only. The session cookie is an opaque id; this value is removed
    # once the report is saved.
    session["access_token"] = access_token
    session.pop("oauth_state", None)

    # Fetch ad accounts
    accounts_resp = requests.get(
        f"{GRAPH_BASE}/me/adaccounts",
        params={"access_token": access_token, "fields": "id,name,account_status"},
        timeout=20,
    )
    accounts = accounts_resp.json().get("data", []) if accounts_resp.status_code == 200 else []

    session["ad_accounts"] = accounts
    return redirect(url_for("select_account", **session.get("tracking_params", {})))

@app.route("/select-account")
def select_account():
    accounts = session.get("ad_accounts", [])
    if not accounts:
        return "No ad accounts found or access denied.", 400
    return render_template("select_account.html", accounts=accounts)

def _strip_account_ids(rows):
    cleaned = []
    for row in rows:
        if isinstance(row, dict):
            cleaned.append({key: value for key, value in row.items() if key != "account_id"})
        else:
            cleaned.append(row)
    return cleaned


def _graph_get(path, access_token, params):
    """GET Graph data. Failures become an empty list and are not logged (the
    access token is in the query string, and exception text includes the URL)."""
    query = dict(params)
    query["access_token"] = access_token
    try:
        response = requests.get(f"{GRAPH_BASE}/{path}", params=query, timeout=20)
    except requests.RequestException:
        return []
    if response.status_code != 200:
        return []
    try:
        payload = response.json()
    except ValueError:
        return []
    return _strip_account_ids(payload.get("data", []))


def _client_ip():
    cloudflare = (request.headers.get("CF-Connecting-IP") or "").strip()
    if cloudflare:
        return cloudflare
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    return request.remote_addr


def _event_source_url():
    """Public https URL of this report.

    Only the two OAuth hosts are trusted. Anything else, including a spoofed
    Host or X-Forwarded-Host, uses the custom domain. Render's WSGI scheme is http.
    """
    host = _public_hostname()
    if host not in OAUTH_REDIRECT_HOSTS:
        host = "grader.theviralmarketingcompany.com"
    path = request.path or "/"
    if not path.startswith("/") or path.startswith("//") or "\\" in path:
        path = "/"
    return "https://%s%s" % (host, path)


def _log_oauth_exchange_failure(response):
    """Log a token-exchange failure without the body, code, or secrets."""
    kind = ""
    trace = ""
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        err = payload.get("error")
        if isinstance(err, dict):
            candidate = err.get("type") or ""
            fbtrace = err.get("fbtrace_id") or ""
        elif isinstance(err, str):
            candidate = err
            fbtrace = payload.get("fbtrace_id") or ""
        else:
            candidate = ""
            fbtrace = ""
        if isinstance(candidate, str) and candidate.isalnum() and len(candidate) <= 64:
            kind = candidate
        if isinstance(fbtrace, str) and fbtrace.isalnum() and len(fbtrace) <= 64:
            trace = fbtrace
    _log.warning(
        "Meta token exchange failed status=%s error=%s fbtrace_id=%s",
        response.status_code,
        kind,
        trace,
    )


def _short_cookie(name):
    value = request.cookies.get(name) or ""
    if value and len(value) <= 512:
        return value
    return None


def _account_key(account_id):
    """HMAC of the ad account id. The raw id is not recoverable from this."""
    secret = app.secret_key
    if isinstance(secret, str):
        secret = secret.encode("utf-8")
    return hmac.new(secret, account_id.encode("utf-8"), hashlib.sha256).hexdigest()


def _email_hash_for_capi(email):
    """SHA-256 of a normalized email, or None when there is nothing to send.

    jordan+test addresses are skipped. The raw address is never returned.
    """
    if not isinstance(email, str):
        return None
    normalized = email.strip().lower()
    if not normalized or "@" not in normalized:
        return None
    if "jordan+test" in normalized:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _fbc_from_click_id():
    tracking = session.get("tracking_params") or {}
    fbclid = tracking.get("fbclid") if isinstance(tracking, dict) else None
    if not fbclid or len(fbclid) > 512:
        return None
    return "fb.1.%d.%s" % (int(time.time() * 1000), fbclid)


def _schedule_grade_view(report, event_id):
    qualified = bool((report.get("total_spend") or 0) >= 5000)
    user_data = {}
    ip = _client_ip()
    if ip:
        user_data["client_ip_address"] = ip
    user_agent = request.headers.get("User-Agent")
    if user_agent:
        user_data["client_user_agent"] = user_agent
    fbp = _short_cookie("_fbp")
    if fbp:
        user_data["fbp"] = fbp
    fbc = _short_cookie("_fbc") or _fbc_from_click_id()
    if fbc:
        user_data["fbc"] = fbc
    email_hash = _email_hash_for_capi(session.get("email") or "")
    if email_hash:
        user_data["em"] = email_hash
    event = capi.build_grade_view_event(
        event_id=event_id,
        event_source_url=_event_source_url(),
        user_data=user_data,
        custom_data={
            "content_name": "Meta Ads Grader",
            "grade": report.get("grade"),
            "score": report.get("score"),
            "qualified": qualified,
        },
    )
    capi.schedule_grade_view_event(event)


@app.after_request
def _noindex_private_reports(response):
    path = request.path or ""
    if path == "/audit" or path.startswith("/audit/") or path.startswith("/report"):
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


def _unavailable():
    return make_response(render_template("report_unavailable.html"), 404)


def _start_again(message):
    return make_response(render_template("start_again.html", message=message))


@app.route("/report-unavailable")
def report_unavailable():
    return _unavailable()


@app.route("/audit", methods=["POST"])
def create_report():
    access_token = session.get("access_token")
    if not access_token:
        return redirect(url_for("index"))

    account_id = (request.form.get("account_id") or "").strip()
    accounts = session.get("ad_accounts") or []
    allowed = {account.get("id") for account in accounts if isinstance(account, dict)}
    if account_id not in allowed:
        return "Choose an account from your list.", 400

    report_id = None
    try:
        insights = _graph_get(
            "%s/insights" % account_id,
            access_token,
            {
                "date_preset": "last_30d",
                "level": "campaign",
                "fields": "campaign_name,spend,impressions,clicks,ctr,cpc,cpm,actions,reach,frequency",
                "limit": 100,
            },
        )
        ad_insights = _graph_get(
            "%s/insights" % account_id,
            access_token,
            {
                "date_preset": "last_30d",
                "level": "ad",
                "fields": "ad_name,spend,impressions,clicks,ctr",
                "limit": 200,
            },
        )
        categories = score_categories(insights, ad_insights)
        score = sum(c["score"] for c in categories) // max(len(categories), 1)
        grade = score_to_grade(score)
        total_spend = sum(float(c.get("spend", 0) or 0) for c in insights)
        report = {
            "grade": grade,
            "score": score,
            "insights": insights,
            "categories": categories,
            "summary": generate_summary(insights, score, categories),
            "total_spend": total_spend,
            # Not the ad account id. Used to fire GraderGradeView once per account.
            "account_key": _account_key(account_id),
        }
        report_id = app.report_store.save(report)
        _remember_report(report_id)
    except Exception as exc:
        # The exception text can include a Graph URL, which carries the token.
        _log.warning("Could not save grade report: %s", type(exc).__name__)
        report_id = None
    finally:
        # Drop the Meta user token as soon as the report attempt finishes.
        # It is not part of the saved report and is not written to the cookie.
        access_token = None
        session.pop("access_token", None)

    if not report_id:
        return _start_again(
            "The grade didn't finish. Start again and it should only take about a minute."
        ), 503
    return redirect(url_for("view_report", report_id=report_id), code=303)


def _remember_report(report_id):
    """Mark this report as one this session created, so only that browser converts."""
    owned = session.get("owned_report_ids")
    if not isinstance(owned, list):
        owned = []
    if report_id not in owned:
        owned.append(report_id)
    session["owned_report_ids"] = owned


@app.route("/audit/<path:legacy_id>")
def legacy_audit(legacy_id):
    # Old links put the ad account id in the path. Redirect before any pixel
    # runs, and do not send that URL onward as the next page's referrer.
    response = redirect(url_for("report_unavailable"), code=302)
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def _owns_report(report_id):
    owned = session.get("owned_report_ids")
    return isinstance(owned, list) and report_id in owned


def _claim_grade_view(report, report_id):
    """Event id for the creator's first view of this account. Shared links get none."""
    if not _owns_report(report_id):
        return None
    account_key = report.get("account_key") or ""
    if not account_key:
        return None
    seen = session.get("grade_view_ids")
    if not isinstance(seen, dict):
        seen = {}
    if account_key in seen:
        return None
    event_id = storage.new_opaque_id(16)
    seen[account_key] = event_id
    session["grade_view_ids"] = seen
    _schedule_grade_view(report, event_id)
    return event_id


@app.route("/report/<report_id>")
def view_report(report_id):
    report = app.report_store.get(report_id)
    if not report:
        return redirect(url_for("report_unavailable"), code=302)
    event_id = _claim_grade_view(report, report_id)
    return make_response(
        render_template(
            "report.html",
            report=report,
            strategy_url=STRATEGY_SESSION_URL,
            event_id=event_id,
            report_id=report_id,
        )
    )

def score_categories(campaign_insights, ad_insights):
    """Score the first 4 categories based on blog principles."""
    categories = []

    # 1. Budget Capacity & Structure
    total_spend = sum(float(c.get("spend", 0)) for c in campaign_insights)
    num_campaigns = len(campaign_insights)
    capacity_score = 70
    capacity_notes = []
    if num_campaigns > 5 and total_spend < 2000:
        capacity_score = 45
        capacity_notes.append("Account appears over-split for its budget. Multiple campaigns/ad sets with limited spend reduce learning.")
    elif num_campaigns <= 2 and total_spend > 500:
        capacity_score = 85
        capacity_notes.append("Structure looks concentrated enough for the budget to generate meaningful signal.")
    else:
        capacity_notes.append("Structure is reasonable relative to spend.")
    categories.append({
        "name": "Budget Capacity & Structure",
        "score": capacity_score,
        "notes": capacity_notes,
    })

    # 2. Creative Volume & Variety
    total_ad_spend = sum(float(a.get("spend", 0)) for a in ad_insights)
    num_ads = len(ad_insights)
    creative_score = 65
    creative_notes = []
    if num_ads == 0:
        creative_score = 40
        creative_notes.append("No ad-level data found.")
    else:
        # Concentration check
        if ad_insights:
            top_ad_spend = max(float(a.get("spend", 0)) for a in ad_insights)
            concentration = top_ad_spend / total_ad_spend if total_ad_spend > 0 else 0
            if concentration > 0.8 and num_ads > 3:
                creative_score = 50
                creative_notes.append(f"One ad is receiving {concentration:.0%} of spend. This can be fine if results are strong, but it limits learning from other creatives.")
            elif num_ads >= 3 and num_ads <= 8:
                creative_score = 80
                creative_notes.append("Creative volume looks reasonable for testing without overwhelming the budget.")
            else:
                creative_notes.append(f"{num_ads} ads detected. Review whether they represent meaningfully different angles.")
    categories.append({
        "name": "Creative Volume & Variety",
        "score": creative_score,
        "notes": creative_notes,
    })

    # 3. Creative Fatigue & Stability
    fatigue_score = 75
    fatigue_notes = []
    if campaign_insights:
        avg_ctr = sum(float(c.get("ctr", 0)) for c in campaign_insights) / len(campaign_insights)
        avg_freq = sum(float(c.get("frequency", 0)) for c in campaign_insights) / len(campaign_insights)
        if avg_ctr < 0.8 and avg_freq > 3:
            fatigue_score = 45
            fatigue_notes.append("Low CTR combined with higher frequency suggests possible creative fatigue or weak resonance.")
        elif avg_ctr > 1.5:
            fatigue_score = 85
            fatigue_notes.append("CTR looks healthy. Creative appears to be resonating.")
        else:
            fatigue_notes.append("CTR and frequency are in a normal range. Monitor trends.")
    else:
        fatigue_notes.append("Insufficient data for fatigue assessment.")
    categories.append({
        "name": "Creative Fatigue & Stability",
        "score": fatigue_score,
        "notes": fatigue_notes,
    })

    # 4. Enhancement Risk (placeholder — full check needs creative-level data)
    enhancement_score = 70
    enhancement_notes = ["Enhancement risk requires creative-level review. Check for high-risk Advantage+ features (text rewriting, summaries, overlays) especially on lead-gen campaigns."]
    categories.append({
        "name": "Enhancement Risk",
        "score": enhancement_score,
        "notes": enhancement_notes,
    })

    # 5. Signal Quality & Optimization Alignment
    signal_score = 65
    signal_notes = []
    if campaign_insights:
        # Look for actions to infer optimization event quality
        has_actions = any(c.get("actions") for c in campaign_insights)
        total_clicks = sum(float(c.get("clicks", 0)) for c in campaign_insights)
        if has_actions and total_clicks > 50:
            signal_score = 80
            signal_notes.append("Account is generating measurable actions. Confirm the optimization event aligns with actual business outcomes (qualified leads, purchases, etc.).")
        elif total_clicks > 20:
            signal_score = 60
            signal_notes.append("Click volume exists but limited conversion signal. Consider whether the optimization event is too high-funnel or tracking is incomplete.")
        else:
            signal_score = 45
            signal_notes.append("Limited conversion signal. Review tracking setup and whether the optimization event can generate enough data.")
    else:
        signal_notes.append("Insufficient data for signal quality assessment.")
    categories.append({
        "name": "Signal Quality & Optimization Alignment",
        "score": signal_score,
        "notes": signal_notes,
    })

    # 6. Overall System Alignment
    # Derived from the other categories — coherence check
    avg_other = sum(c["score"] for c in categories) / len(categories)
    alignment_score = int(avg_other)
    alignment_notes = []
    low_categories = [c["name"] for c in categories if c["score"] < 60]
    if len(low_categories) >= 2:
        alignment_score = max(alignment_score - 10, 40)
        alignment_notes.append("Multiple weak areas suggest structural misalignment. Prioritize budget capacity and creative variety before tactical changes.")
    elif alignment_score >= 75:
        alignment_notes.append("Account shows reasonable coherence across structure, creative, and signal. Focus on refinement rather than major restructuring.")
    else:
        alignment_notes.append("Some misalignment present. Review the lower-scoring categories first.")
    categories.append({
        "name": "Overall System Alignment",
        "score": alignment_score,
        "notes": alignment_notes,
    })

    return categories

def score_to_grade(score):
    if score >= 90:
        return "A"
    elif score >= 80:
        return "B"
    elif score >= 70:
        return "C"
    elif score >= 60:
        return "D"
    else:
        return "F"

def generate_summary(insights, score, categories):
    if not insights:
        return "No recent campaign data found. Connect an active ad account for a full audit."
    weak = [c["name"] for c in categories if c["score"] < 60]
    if weak:
        return f"Analyzed {len(insights)} campaigns. Overall score: {score}/100. Primary areas to review: {', '.join(weak)}."
    return f"Analyzed {len(insights)} campaigns. Overall score: {score}/100. Account structure and creative look reasonably aligned with budget."

@app.route("/thank-you")
def thank_you():
    return render_template("thank_you.html", strategy_url=STRATEGY_SESSION_URL)

if __name__ == "__main__":
    app.run(debug=True, port=5000)
