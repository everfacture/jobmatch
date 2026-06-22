from jobmatch.scoring.rules import apply_configured_hard_caps, score_by_rules


PREFERENCES = {
    "scoring": {
        "target_roles": ["Backend Engineer", "Software Engineer"],
        "adjacent_roles": ["Implementation Engineer"],
        "reject_roles": ["Sales Development Representative"],
        "hard_caps": [
            {"name": "security_clearance", "patterns": ["security clearance required"], "max_score": 3},
            {"name": "onsite_only", "patterns": ["onsite only"], "max_score": 5},
        ],
        "dealbreakers": ["commission only"],
        "positive_signals": ["Python", "APIs", "PostgreSQL", "AWS"],
        "negative_signals": ["cold calling"],
    }
}


def test_rules_do_nothing_without_preferences():
    result = score_by_rules({
        "title": "Senior Backend Engineer",
        "company": "TechCo",
        "location": "Remote",
        "full_description": "Build Python APIs on AWS.",
    })

    assert result is None


def test_rules_score_configured_target_role():
    result = score_by_rules({
        "title": "Senior Backend Engineer",
        "company": "TechCo",
        "location": "Remote",
        "full_description": "Build Python APIs with PostgreSQL on AWS.",
    }, PREFERENCES)

    assert result is not None
    assert result["score"] >= 8
    assert result["scoring_method"] == "rule:target_role_match"
    assert "Backend Engineer" in result["keywords"]


def test_rules_reject_configured_reject_role():
    result = score_by_rules({
        "title": "Sales Development Representative",
        "company": "SalesCo",
        "location": "Remote",
        "full_description": "Cold outreach and pipeline generation.",
    }, PREFERENCES)

    assert result is not None
    assert result["score"] == 2
    assert result["scoring_method"] == "rule:reject_role"


def test_rules_apply_low_hard_cap_as_deterministic_result():
    result = score_by_rules({
        "title": "Backend Engineer",
        "company": "GovCo",
        "location": "Remote",
        "full_description": "Build Python APIs. Security clearance required.",
    }, PREFERENCES)

    assert result is not None
    assert result["score"] == 3
    assert result["scoring_method"] == "rule:hard_cap:security_clearance"


def test_apply_configured_hard_caps_caps_llm_result():
    result = apply_configured_hard_caps(
        {"score": 9, "fit": "Strong", "gap": "", "keywords": "Python", "error": None},
        {"title": "Backend Engineer", "full_description": "This is onsite only."},
        PREFERENCES,
    )

    assert result["score"] == 5
    assert "hard capped" in result["gap"]


def test_rules_leave_ambiguous_product_role_to_llm():
    result = score_by_rules({
        "title": "Product Manager",
        "company": "SaaSCo",
        "location": "Remote",
        "full_description": "Own roadmap, analytics, user interviews, launches, and product growth.",
    }, PREFERENCES)

    assert result is None
