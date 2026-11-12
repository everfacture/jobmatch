from jobmatch.scoring.rules import (
    apply_configured_hard_caps,
    apply_required_signal_caps,
    apply_score_adjustments,
    score_by_rules,
)


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


def test_title_scoped_cap_ignores_other_jobs_named_in_the_body():
    preferences = {
        "scoring": {
            "target_roles": ["Guest Service Agent"],
            "positive_signals": ["guest service", "front desk", "hotel"],
            "hard_caps": [{
                "name": "hotel_back_of_house_or_manual_labour",
                "scope": "title",
                "patterns": ["waiter"],
                "max_score": 4,
            }],
        }
    }
    kept = score_by_rules({
        "title": "Guest Service Agent (DW)",
        "company": "Hotel",
        "full_description": "Also hiring waiter and bartender.",
    }, preferences)
    killed = score_by_rules({
        "title": "Waiter Daily Worker",
        "company": "Hotel",
        "full_description": "Serve tables.",
    }, preferences)

    assert kept is not None and kept["score"] >= 8
    assert killed is not None and killed["score"] == 4


def test_apply_configured_hard_caps_caps_llm_result():
    result = apply_configured_hard_caps(
        {"score": 9, "fit": "Strong", "gap": "", "keywords": "Python", "error": None},
        {"title": "Backend Engineer", "full_description": "This is onsite only."},
        PREFERENCES,
    )

    assert result["score"] == 5
    assert "hard capped" in result["gap"]


def test_required_signal_cap_blocks_generic_admin_from_specialised_lane():
    preferences = {
        "scoring": {
            "required_signal_caps": [
                {
                    "name": "hands-on creative work",
                    "patterns": ["florist", "packing", "membungkus", "display produk"],
                    "max_score": 6,
                }
            ]
        }
    }

    result = apply_required_signal_caps(
        {"score": 8, "fit": "Strong general admin fit", "gap": "", "keywords": "admin", "error": None},
        {"title": "Administrative Assistant", "full_description": "Filing, email, and calendar support."},
        preferences,
    )

    assert result["score"] == 6
    assert "lacks 'hands-on creative work' evidence" in result["gap"]


def test_required_signal_cap_keeps_real_packing_role_and_allows_location_boost():
    preferences = {
        "scoring": {
            "required_signal_caps": [
                {
                    "name": "hands-on creative work",
                    "patterns": ["florist", "packing", "membungkus", "display produk"],
                    "max_score": 6,
                }
            ],
            "location_boosts": [
                {
                    "name": "west_java_priority",
                    "patterns": ["Bandung"],
                    "points": 1,
                    "min_base_score": 8,
                    "max_score": 10,
                }
            ],
        }
    }

    result = apply_score_adjustments(
        {"score": 8, "fit": "Good packing fit", "gap": "", "keywords": "packing", "error": None},
        {
            "title": "Packer",
            "location": "Bandung, West Java, Indonesia",
            "full_description": "Membungkus barang pesanan dan menyiapkan paket.",
        },
        preferences,
    )

    assert result["score"] == 9
    assert "location boost" in result["fit"]


def test_rules_leave_ambiguous_product_role_to_llm():
    result = score_by_rules({
        "title": "Product Manager",
        "company": "SaaSCo",
        "location": "Remote",
        "full_description": "Own roadmap, analytics, user interviews, launches, and product growth.",
    }, PREFERENCES)

    assert result is None


def test_apply_score_adjustments_boosts_configured_location():
    preferences = {
        "scoring": {
            "location_boosts": [
                {
                    "name": "west_java_priority",
                    "patterns": ["Bandung", "Cimahi", "West Java"],
                    "points": 1,
                    "min_base_score": 8,
                    "max_score": 10,
                }
            ]
        }
    }

    result = apply_score_adjustments(
        {"score": 8, "fit": "Strong admin fit", "gap": "", "keywords": "admin", "error": None},
        {"title": "Admin Staff", "location": "Bandung, West Java, Indonesia"},
        preferences,
    )

    assert result["score"] == 9
    assert "location boost" in result["fit"]
    assert "Bandung" in result["keywords"]


def test_apply_score_adjustments_does_not_boost_weak_location_match():
    preferences = {
        "scoring": {
            "location_boosts": [
                {"name": "west_java_priority", "patterns": ["Bandung"], "points": 1, "min_base_score": 8}
            ]
        }
    }

    result = apply_score_adjustments(
        {"score": 6, "fit": "Weak", "gap": "", "keywords": "", "error": None},
        {"title": "Admin Staff", "location": "Bandung, West Java, Indonesia"},
        preferences,
    )

    assert result["score"] == 6
