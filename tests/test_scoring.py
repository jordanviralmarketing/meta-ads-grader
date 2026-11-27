import os
import unittest

os.environ.setdefault("META_APP_SECRET", "unit-test-secret")
os.environ.setdefault("FLASK_SECRET_KEY", "unit-test-flask-key")

import app as grader  # noqa: E402
from flask import render_template  # noqa: E402

# One campaign, concentrated enough to score well, with enough clicks and
# actions for the signal category. Four evenly funded ads.
CAMPAIGNS = [
    {
        "campaign_name": "Prospecting",
        "spend": "1000",
        "ctr": "2.0",
        "frequency": "1.2",
        "clicks": "80",
        "actions": [{"action_type": "link_click", "value": "80"}],
    }
]
ADS = [
    {"ad_name": "A", "spend": "100"},
    {"ad_name": "B", "spend": "100"},
    {"ad_name": "C", "spend": "100"},
    {"ad_name": "D", "spend": "100"},
]


def _overall(categories):
    return sum(category["score"] for category in categories) // len(categories)


def score_categories_before_2026_11_27(campaign_insights, ad_insights):
    """Scoring as it was on main at 7f542a5, including Enhancement Risk.

    Kept only so this test can show the grade shift. The app no longer calls it.
    """
    categories = []

    total_spend = sum(float(c.get("spend", 0)) for c in campaign_insights)
    num_campaigns = len(campaign_insights)
    capacity_score = 70
    capacity_notes = []
    if num_campaigns > 5 and total_spend < 2000:
        capacity_score = 45
        capacity_notes.append("over-split")
    elif num_campaigns <= 2 and total_spend > 500:
        capacity_score = 85
        capacity_notes.append("concentrated")
    else:
        capacity_notes.append("reasonable")
    categories.append({
        "name": "Budget Capacity & Structure",
        "score": capacity_score,
        "notes": capacity_notes,
    })

    total_ad_spend = sum(float(a.get("spend", 0)) for a in ad_insights)
    num_ads = len(ad_insights)
    creative_score = 65
    creative_notes = []
    if num_ads == 0:
        creative_score = 40
        creative_notes.append("none")
    else:
        if ad_insights:
            top_ad_spend = max(float(a.get("spend", 0)) for a in ad_insights)
            concentration = top_ad_spend / total_ad_spend if total_ad_spend > 0 else 0
            if concentration > 0.8 and num_ads > 3:
                creative_score = 50
                creative_notes.append("concentrated")
            elif num_ads >= 3 and num_ads <= 8:
                creative_score = 80
                creative_notes.append("reasonable")
            else:
                creative_notes.append("review")
    categories.append({
        "name": "Creative Volume & Variety",
        "score": creative_score,
        "notes": creative_notes,
    })

    fatigue_score = 75
    fatigue_notes = []
    if campaign_insights:
        avg_ctr = sum(float(c.get("ctr", 0)) for c in campaign_insights) / len(campaign_insights)
        avg_freq = sum(float(c.get("frequency", 0)) for c in campaign_insights) / len(campaign_insights)
        if avg_ctr < 0.8 and avg_freq > 3:
            fatigue_score = 45
            fatigue_notes.append("fatigue")
        elif avg_ctr > 1.5:
            fatigue_score = 85
            fatigue_notes.append("healthy")
        else:
            fatigue_notes.append("normal")
    else:
        fatigue_notes.append("insufficient")
    categories.append({
        "name": "Creative Fatigue & Stability",
        "score": fatigue_score,
        "notes": fatigue_notes,
    })

    categories.append({
        "name": "Enhancement Risk",
        "score": 70,
        "notes": ["placeholder"],
    })

    signal_score = 65
    signal_notes = []
    if campaign_insights:
        has_actions = any(c.get("actions") for c in campaign_insights)
        total_clicks = sum(float(c.get("clicks", 0)) for c in campaign_insights)
        if has_actions and total_clicks > 50:
            signal_score = 80
            signal_notes.append("actions")
        elif total_clicks > 20:
            signal_score = 60
            signal_notes.append("clicks")
        else:
            signal_score = 45
            signal_notes.append("limited")
    else:
        signal_notes.append("insufficient")
    categories.append({
        "name": "Signal Quality & Optimization Alignment",
        "score": signal_score,
        "notes": signal_notes,
    })

    avg_other = sum(c["score"] for c in categories) / len(categories)
    alignment_score = int(avg_other)
    alignment_notes = []
    low_categories = [c["name"] for c in categories if c["score"] < 60]
    if len(low_categories) >= 2:
        alignment_score = max(alignment_score - 10, 40)
        alignment_notes.append("misaligned")
    elif alignment_score >= 75:
        alignment_notes.append("coherent")
    else:
        alignment_notes.append("some")
    categories.append({
        "name": "Overall System Alignment",
        "score": alignment_score,
        "notes": alignment_notes,
    })
    return categories


class EnhancementRiskRemovalTests(unittest.TestCase):
    def test_no_category_is_named_enhancement_risk(self):
        categories = grader.score_categories(CAMPAIGNS, ADS)
        names = [category["name"] for category in categories]
        self.assertNotIn("Enhancement Risk", names)
        self.assertEqual(
            names,
            [
                "Budget Capacity & Structure",
                "Creative Volume & Variety",
                "Creative Fatigue & Stability",
                "Signal Quality & Optimization Alignment",
                "Overall System Alignment",
            ],
        )
        source_path = os.path.join(os.path.dirname(__file__), "..", "app.py")
        with open(source_path) as source_file:
            source = source_file.read()
        self.assertNotIn("Enhancement Risk", source)
        self.assertNotIn("enhancement_score", source)
        with grader.app.test_request_context("/"):
            html = render_template(
                "report.html",
                report={
                    "grade": "B",
                    "score": 82,
                    "insights": [],
                    "categories": categories,
                    "summary": "x",
                    "total_spend": 1000,
                },
                strategy_url="x",
                event_id=None,
                report_id="opaque",
            )
        self.assertNotIn("Enhancement", html)

    def test_same_insights_score_higher_without_the_fixed_seventy(self):
        before = score_categories_before_2026_11_27(CAMPAIGNS, ADS)
        after = grader.score_categories(CAMPAIGNS, ADS)

        before_scores = [category["score"] for category in before]
        after_scores = [category["score"] for category in after]
        # Budget 85, creative 80, fatigue 85, enhancement 70, signal 80, alignment 80.
        self.assertEqual(before_scores, [85, 80, 85, 70, 80, 80])
        self.assertEqual(len(before), 6)
        before_overall = _overall(before)
        self.assertEqual(before_overall, 80)
        self.assertEqual(grader.score_to_grade(before_overall), "B")

        # Alignment is the integer average of the four categories, then the
        # overall score is the integer average of those four plus Alignment.
        self.assertEqual(after_scores[:4], [85, 80, 85, 80])
        self.assertEqual(after_scores[4], int(sum(after_scores[:4]) / 4))
        self.assertEqual(after_scores, [85, 80, 85, 80, 82])
        self.assertEqual(len(after), 5)
        after_overall = _overall(after)
        self.assertEqual(after_overall, sum(after_scores) // 5)
        self.assertEqual(after_overall, 82)
        self.assertEqual(grader.score_to_grade(after_overall), "B")
        self.assertGreater(after_overall, before_overall)


if __name__ == "__main__":
    unittest.main()
