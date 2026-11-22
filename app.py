from flask import Flask, redirect, request, session, render_template, url_for, jsonify
import requests
import secrets
from config import META_APP_ID, META_APP_SECRET, REDIRECT_URI, STRATEGY_SESSION_URL

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)

GRAPH_API_VERSION = "v21.0"
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"

@app.route("/")
def index():
    return redirect(url_for("connect"), code=302)

@app.route("/connect")
def connect():
    state = secrets.token_urlsafe(16)
    session["oauth_state"] = state
    auth_url = (
        f"https://www.facebook.com/{GRAPH_API_VERSION}/dialog/oauth?"
        f"client_id={META_APP_ID}&"
        f"redirect_uri={REDIRECT_URI}&"
        f"state={state}&"
        f"scope=ads_read,business_management"
    )
    return redirect(auth_url)

@app.route("/auth/meta/callback")
def callback():
    error = request.args.get("error")
    if error:
        return f"Authorization failed: {error}", 400

    code = request.args.get("code")
    state = request.args.get("state")
    if not code or state != session.get("oauth_state"):
        return "Invalid state or missing code", 400

    # Exchange code for access token
    token_url = f"{GRAPH_BASE}/oauth/access_token"
    params = {
        "client_id": META_APP_ID,
        "client_secret": META_APP_SECRET,
        "redirect_uri": REDIRECT_URI,
        "code": code,
    }
    token_resp = requests.get(token_url, params=params)
    if token_resp.status_code != 200:
        return f"Token exchange failed: {token_resp.text}", 400

    token_data = token_resp.json()
    access_token = token_data.get("access_token")
    if not access_token:
        return "No access token received", 400

    session["access_token"] = access_token

    # Fetch ad accounts
    accounts_resp = requests.get(
        f"{GRAPH_BASE}/me/adaccounts",
        params={"access_token": access_token, "fields": "id,name,account_status"}
    )
    accounts = accounts_resp.json().get("data", []) if accounts_resp.status_code == 200 else []

    session["ad_accounts"] = accounts
    return redirect(url_for("select_account"))

@app.route("/select-account")
def select_account():
    accounts = session.get("ad_accounts", [])
    if not accounts:
        return "No ad accounts found or access denied.", 400
    return render_template("select_account.html", accounts=accounts)

@app.route("/audit/<account_id>")
def audit(account_id):
    access_token = session.get("access_token")
    if not access_token:
        return redirect(url_for("index"))

    # Fetch campaign insights (last 30 days)
    insights_url = f"{GRAPH_BASE}/{account_id}/insights"
    params = {
        "access_token": access_token,
        "date_preset": "last_30d",
        "level": "campaign",
        "fields": "campaign_name,spend,impressions,clicks,ctr,cpc,cpm,actions,reach,frequency",
        "limit": 100,
    }
    insights_resp = requests.get(insights_url, params=params)
    insights = insights_resp.json().get("data", []) if insights_resp.status_code == 200 else []

    # Fetch ad-level insights for creative volume / concentration checks
    ad_insights_url = f"{GRAPH_BASE}/{account_id}/insights"
    ad_params = {
        "access_token": access_token,
        "date_preset": "last_30d",
        "level": "ad",
        "fields": "ad_name,spend,impressions,clicks,ctr",
        "limit": 200,
    }
    ad_resp = requests.get(ad_insights_url, params=ad_params)
    ad_insights = ad_resp.json().get("data", []) if ad_resp.status_code == 200 else []

    # Score the first 4 categories
    categories = score_categories(insights, ad_insights)
    score = sum(c["score"] for c in categories) // max(len(categories), 1)
    grade = score_to_grade(score)

    total_spend = sum(float(c.get("spend", 0)) for c in insights)

    report = {
        "account_id": account_id,
        "grade": grade,
        "score": score,
        "insights": insights,
        "categories": categories,
        "summary": generate_summary(insights, score, categories),
        "total_spend": total_spend,
    }

    session["report"] = report
    return render_template("report.html", report=report, strategy_url=STRATEGY_SESSION_URL)

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
