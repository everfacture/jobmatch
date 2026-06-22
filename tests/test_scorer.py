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
