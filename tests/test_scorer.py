from jobmatch.scoring.scorer import build_score_prompt, score_job


PROFILE = {
    "personal": {"full_name": "Candidate Example", "preferred_name": "Candidate"},
    "experience": {
        "target_role": "Backend Engineer",
        "current_title": "Software Engineer",
        "years_of_experience_total": "5",
        "education_level": "Bachelor's",
    },
    "skills_boundary": {
        "programming_languages": ["Python", "SQL"],
        "frameworks": ["FastAPI"],
        "tools": ["AWS", "Docker"],
    },
    "resume_facts": {
        "preserved_companies": ["ExampleCo"],
        "real_metrics": ["Reduced runtime by 40%"],
    },
}

PREFERENCES = {
    "scoring": {
        "target_roles": ["Backend Engineer"],
        "adjacent_roles": ["Implementation Engineer"],
        "reject_roles": ["Sales Development Representative"],
        "hard_caps": [{"name": "clearance", "patterns": ["security clearance"], "max_score": 3}],
        "positive_signals": ["Python", "FastAPI", "AWS"],
        "negative_signals": ["cold calling"],
    }
}


def test_build_score_prompt_is_candidate_neutral():
    prompt = build_score_prompt(PROFILE, PREFERENCES)

    assert "Candidate" in prompt
    assert "Backend Engineer" in prompt
    assert "LEGACY_DEFAULT_CANDIDATE" not in prompt.upper()
    assert "one-person-only career lane" not in prompt.lower()
    assert "RESPOND IN EXACTLY THIS FORMAT" in prompt


def test_score_job_uses_rule_gate_without_llm():
    result = score_job(
        "Python backend engineer resume",
        {
            "title": "Senior Backend Engineer",
            "company": "TechCo",
            "location": "Remote",
            "full_description": "Build Python APIs with FastAPI on AWS.",
            "url": "https://example.test/job",
        },
        profile=PROFILE,
        preferences=PREFERENCES,
    )

    assert result["score"] >= 8
    assert result["url"] == "https://example.test/job"
    assert result["scoring_method"] == "rule:target_role_match"


def test_score_job_applies_adjustments_to_rule_scored_result():
    preferences = {
        "scoring": {
            "target_roles": ["Administrative Assistant"],
            "positive_signals": ["administration", "filing", "customer service"],
            "required_signal_caps": [
                {
                    "name": "hands-on creative work",
                    "patterns": ["florist", "packing", "membungkus", "display produk"],
                    "max_score": 6,
                }
            ],
        }
    }
    job = {
        "title": "Administrative Assistant",
        "company": "Generic Co",
        "location": "Jakarta, Indonesia",
        "full_description": "Administration, filing, and customer service support.",
        "url": "https://example.test/admin",
    }

    result = score_job("Administrative resume", job, profile={}, preferences=preferences)

    assert result["scoring_method"] == "rule:target_role_match"
    assert result["score"] == 6
    assert "lacks 'hands-on creative work' evidence" in result["gap"]
