# Meta Ads Grader

Flask app that connects a Meta ad account and grades recent campaign performance.

## Environment variables

Configuration comes from the process environment. Copy `.env.example` if you want a local checklist. `.env` is gitignored. This app does not load `.env` for you: export the variables in the shell, or set them on the Render service.

`META_APP_SECRET` and `FLASK_SECRET_KEY` are required. If either is missing or blank, the app raises a `RuntimeError` as soon as it starts (including under gunicorn) and names that variable. `FLASK_SECRET_KEY` signs the session cookie, so it must stay the same across restarts and workers; use a long random value.

| Variable | Required | Default when unset |
| --- | --- | --- |
| `META_APP_SECRET` | Yes | none |
| `FLASK_SECRET_KEY` | Yes | none |
| `META_APP_ID` | No | `997123502759841` |
| `REDIRECT_URI` | No | `https://meta-ads-grader.onrender.com/auth/meta/callback` |
| `STRATEGY_SESSION_URL` | No | the current strategy-call booking URL |
| `SMTP_HOST` | No | `smtp.example.com` |
| `SMTP_PORT` | No | `587` |
| `SMTP_USER` | No | empty |
| `SMTP_PASSWORD` | No | empty (no password is stored in the repo) |
| `FROM_EMAIL` | No | `jordan@theviralmarketingcompany.com` |
| `ONRENDER_HOST_REDIRECT` | No | on. `0`, `false`, `no`, or `off` disables the redirect below |

SMTP settings are reserved for later email delivery. No current route reads them.

Requests whose `Host` is `meta-ads-grader.onrender.com` receive a 301 to `https://grader.theviralmarketingcompany.com` with the same path and query string. `/auth/meta/callback` is exempt, so a login that started on that host finishes there. Other hosts, including the custom domain and localhost, are unchanged. Render terminates TLS, so the redirect looks at the `Host` header only (not the internal `http` scheme, and not `X-Forwarded-Host`). Set `ONRENDER_HOST_REDIRECT=0` and restart to turn it off. This repo has no `render.yaml` and no health-check route; Render's default probe is TCP and does not hit the app.

The Meta dialog and the code exchange share one `redirect_uri`. On `meta-ads-grader.onrender.com` or `grader.theviralmarketingcompany.com` it is `https://` that host plus `/auth/meta/callback`. The host must match exactly. Any other host, including a suffix spoof or `X-Forwarded-Host`, uses `REDIRECT_URI`.

## Run locally

```bash
export META_APP_SECRET=your-meta-app-secret
export FLASK_SECRET_KEY=any-long-random-string
python app.py
```

Set `META_APP_SECRET` and `FLASK_SECRET_KEY` on the Render service before deploying. The other variables only need to be set when you want to override the defaults above.

## Conversion tracking

`templates/_tracking.html` loads the Meta Pixel (3803790859932149) and the Google tag (Google Ads AW-16946961660, GA4 G-S0JYBC8F8L) on every page.

| Event | Where | Meta | Google Ads |
| --- | --- | --- | --- |
| Grade viewed | `report.html`, once per account per browser session | `GraderGradeView` (grade, score, qualified) | Meta Ads Grader - Grade Viewed |
| Free review click | `report.html`, $5k+ spend only | `GraderReviewRequestClick` | Meta Ads Grader - Free Review Request Click |

`/connect` stores `gclid`, `gbraid`, `wbraid`, `fbclid`, `_gl` and UTM parameters in the session, and the OAuth callback re-appends them to `/select-account` so attribution survives the Facebook redirect. Form submit and Connect-click events fire from GTM on theviralmarketingcompany.com, not from this app.

Known limitation: the Meta access token and full report are stored in the Flask cookie session, which can exceed the ~4KB cookie limit on large accounts. This will move to a database.
