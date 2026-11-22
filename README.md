# Meta Ads Grader

Flask app that connects a Meta ad account and grades recent campaign performance.

## Environment variables

Configuration comes from the process environment. Copy `.env.example` if you want a local checklist. `.env` is gitignored. This app does not load `.env` for you: export the variables in the shell, or set them on the Render service.

`META_APP_SECRET` is required. If it is missing or blank, the app raises a `RuntimeError` as soon as it starts (including under gunicorn) and names that variable.

| Variable | Required | Default when unset |
| --- | --- | --- |
| `META_APP_SECRET` | Yes | none |
| `META_APP_ID` | No | `997123502759841` |
| `REDIRECT_URI` | No | `https://meta-ads-grader.onrender.com/auth/meta/callback` |
| `STRATEGY_SESSION_URL` | No | the current strategy-call booking URL |
| `SMTP_HOST` | No | `smtp.example.com` |
| `SMTP_PORT` | No | `587` |
| `SMTP_USER` | No | empty |
| `SMTP_PASSWORD` | No | empty (no password is stored in the repo) |
| `FROM_EMAIL` | No | `jordan@theviralmarketingcompany.com` |

SMTP settings are reserved for later email delivery. No current route reads them.

## Run locally

```bash
export META_APP_SECRET=your-meta-app-secret
python app.py
```

Set `META_APP_SECRET` on the Render service before deploying. The other variables only need to be set when you want to override the defaults above.
