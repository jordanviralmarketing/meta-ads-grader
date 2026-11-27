# Meta Ads Grader

Flask app that connects a Meta ad account and grades recent campaign performance.

## Environment variables

Configuration comes from the process environment. Copy `.env.example` if you want a local checklist. `.env` is gitignored. This app does not load `.env` for you: export the variables in the shell, or set them on the Render service.

`META_APP_SECRET` and `FLASK_SECRET_KEY` are required. If either is missing or blank, the app raises a `RuntimeError` as soon as it starts (including under gunicorn) and names that variable. Use a long random `FLASK_SECRET_KEY` and keep it stable across restarts and workers.

The session cookie holds only an opaque id. Session data is stored on disk (CacheLib files). Saved grades live in a SQLite file, keyed by an unguessable `/report/<id>` URL. Both use `DATA_DIR`, which defaults to a folder under the system temp directory. That directory is writable on Render without extra configuration, and it is wiped when the service restarts or redeploys. It is also local to one instance. To keep sessions and reports, attach a Render disk and set `DATA_DIR` to the mount path (for example `/var/data`). No disk is required for the app to boot.

| Variable | Required | Default when unset |
| --- | --- | --- |
| `META_APP_SECRET` | Yes | none |
| `FLASK_SECRET_KEY` | Yes | none |
| `META_APP_ID` | No | `997123502759841` |
| `REDIRECT_URI` | No | `https://meta-ads-grader.onrender.com/auth/meta/callback` |
| `ONRENDER_HOST_REDIRECT` | No | on. `0`, `false`, `no`, or `off` disables the redirect below |
| `STRATEGY_SESSION_URL` | No | the current strategy-call booking URL |
| `META_CAPI_ACCESS_TOKEN` | No | unset; the Conversions API call is skipped |
| `META_CAPI_TEST_EVENT_CODE` | No | unset; sent as `test_event_code` only when set |
| `DATA_DIR` | No | a directory under the system temp dir |
| `SESSION_TYPE` | No | `cachelib` (files on disk; `filesystem` is an alias) |
| `SESSION_FILE_DIR` | No | `$DATA_DIR/sessions` |
| `REPORT_STORE` | No | `sqlite` |
| `REPORT_SQLITE_PATH` | No | `$DATA_DIR/reports.sqlite` |
| `SESSION_COOKIE_SECURE` | No | on when `RENDER=true` (set automatically on Render), otherwise off |
| `DATABASE_URL` | No | unused until `SESSION_TYPE=sqlalchemy` |
| `SMTP_HOST` | No | `smtp.example.com` |
| `SMTP_PORT` | No | `587` |
| `SMTP_USER` | No | empty |
| `SMTP_PASSWORD` | No | empty (no password is stored in the repo) |
| `FROM_EMAIL` | No | `jordan@theviralmarketingcompany.com` |
| `ONRENDER_HOST_REDIRECT` | No | on. `0`, `false`, `no`, or `off` disables the redirect below |

SMTP settings are reserved for later email delivery. No current route reads them.

Requests whose `Host` is `meta-ads-grader.onrender.com` receive a 301 to `https://grader.theviralmarketingcompany.com` with the same path and query string. `/auth/meta/callback` is exempt, so a login that started on that host finishes there. Other hosts, including the custom domain and localhost, are unchanged. Render terminates TLS, so the redirect looks at the `Host` header only (not the internal `http` scheme, and not `X-Forwarded-Host`). Set `ONRENDER_HOST_REDIRECT=0` and restart to turn it off. This repo has no `render.yaml` and no health-check route; Render's default probe is TCP and does not hit the app.

The Meta dialog and the code exchange share one `redirect_uri`. On `meta-ads-grader.onrender.com` or `grader.theviralmarketingcompany.com` it is `https://` that host plus `/auth/meta/callback`. The host must match exactly. Any other host, including a suffix spoof or `X-Forwarded-Host`, uses `REDIRECT_URI`.

`SESSION_TYPE=sqlalchemy` is the switch for database-backed sessions once Postgres exists. It requires `DATABASE_URL` and the `flask-sqlalchemy` package (not installed by default). Reports stay on the `ReportStore` interface (`storage.py`); sqlite is the only backend today.

Session cookies are `HttpOnly` and `SameSite=Lax` (Lax so the Facebook OAuth callback still sends the cookie). `Secure` is on in production. The Meta user access token is kept in the server-side session only until the grade is saved, then removed. It is not stored with the report.

## Run locally

```bash
export META_APP_SECRET=your-meta-app-secret
export FLASK_SECRET_KEY=any-long-random-string
python app.py
```

```bash
python -m unittest discover -s tests
```

Set `META_APP_SECRET` and `FLASK_SECRET_KEY` on the Render service before deploying. The other variables only need to be set when you want to override the defaults above. `META_CAPI_ACCESS_TOKEN` is optional: set it to send server-side grade views, and leave it unset to skip that call. Do not put a real token in the repo.

OAuth still requests only `ads_read`. Account listing uses `/me/adaccounts`.

## Conversion tracking

`templates/_tracking.html` loads the Meta Pixel (3803790859932149) and the Google tag (Google Ads AW-16946961660, GA4 G-S0JYBC8F8L) once on every page. Report pages do not load a second pixel.

| Event | Where | Meta | Google |
| --- | --- | --- | --- |
| Grade viewed | `report.html`, once per ad account, and only in the session that created the report | Pixel `GraderGradeView` with `eventID`, plus Conversions API `GraderGradeView` with the same `event_id` | Google Ads "Meta Ads Grader - Grade Viewed" and GA4 `grader_grade_view`, on that same first view |
| Free review click | `report.html`, $5k+ spend only | `GraderReviewRequestClick` | Meta Ads Grader - Free Review Request Click |
| $247 audit CTA | both "Buy an audit today" buttons | Pixel `AuditCTAClick`, a new `eventID` on each click | GA4 `audit_cta_click` |

The audit CTA still links to `https://audit.theviralmarketingcompany.com/?utm_source=grader&utm_medium=cta&utm_campaign=grade_screen`.

The Conversions API call uses `META_CAPI_ACCESS_TOKEN`. If that variable is missing or blank, the call is skipped. `META_CAPI_TEST_EVENT_CODE`, when set, is sent as `test_event_code`. The request is sent in the background so the report page does not wait on Meta. A non-2xx response is a failure; the log is only the status code and `fbtrace_id`. Match fields, when present, are client IP (`CF-Connecting-IP` first, otherwise the first `X-Forwarded-For` hop), user agent, `_fbp`, `_fbc` (or an `fbc` built from the stored `fbclid`), and `https://<host><path>`. An email is sent only when the session has one, as a SHA-256 hash, and `jordan+test` addresses are left out. The ad account id is not included. The audit CTA is pixel-only.

Only the browser session that created the report fires the grade view, and only once per account. Opening a shared `/report/<id>` link does not fire the pixel event, the Google Ads conversion, or the Conversions API call. Server-side session files expire after about two hours.

`GET /start` accepts `email`, `utm_*`, and `fbclid` from the GHL form. A valid address is stored lowercased in the server-side session and later hashed as `em` on `GraderGradeView`. The response is an empty 302 to `https://theviralmarketingcompany.com/meta-ads-grader-thanks` with only `utm_*` and `fbclid`. The email is not in that URL, the body, or the logs. The session cookie is host-only and `SameSite=Lax`, so the thanks page's Connect link must return to the same grader host (`/connect` on that host) for the address to survive Meta OAuth.

`/connect` stores `gclid`, `gbraid`, `wbraid`, `fbclid`, `_gl` and UTM parameters in the server-side session, and the OAuth callback re-appends them to `/select-account` so attribution survives the Facebook redirect. Form submit and Connect-click events fire from GTM on theviralmarketingcompany.com, not from this app.

Grades are served at `/report/<opaque id>`. Old `/audit/<act_id>` links redirect to `/report-unavailable` before any tag runs, and that redirect does not send the old URL as the next page's referrer. Report responses send `X-Robots-Tag: noindex, nofollow` and a matching robots meta tag. `/report` and `/audit` are not blocked in robots.txt, and there is no sitemap entry for them. The landing redirect (`/`) stays indexable.
