from jobmatch.scoring.cover_letter import _build_cover_letter_prompt


PROFILE = {
    "personal": {"full_name": "Candidate Example", "preferred_name": "Candidate"},
    "skills_boundary": {
        "programming_languages": ["Python", "SQL"],
        "tools": ["FastAPI", "AWS"],
    },
    "resume_facts": {
        "preserved_projects": ["Revenue Automation Project"],
        "real_metrics": ["Reduced processing time by 40%"],
    },
}


def test_cover_letter_prompt_uses_preferences_headline():
    preferences = {
        "candidate": {
            "headline": "A product-minded implementation engineer who automates support workflows and ships measurable outcomes.",
        },
        "scoring": {
            "target_roles": ["Implementation Specialist"],
            "positive_signals": ["automation", "FastAPI"],
        },
    }

    prompt = _build_cover_letter_prompt(PROFILE, preferences)

    assert "A product-minded implementation engineer" in prompt
    assert "Implementation Specialist" in prompt
    assert "Revenue Automation Project" in prompt
    assert "Reduced processing time by 40%" in prompt
    assert "Candidate" in prompt


def test_cover_letter_prompt_does_not_hardcode_example_persona():
    prompt = _build_cover_letter_prompt(PROFILE, None)

    for hardcoded_story_fragment in [
        "LEGACY_DEFAULT_CANDIDATE",
        "private employer acronym",
        "specific hometown",
        "real customer name",
        "one-person-only career lane",
        "hardcoded resume narrative",
    ]:
        assert hardcoded_story_fragment not in prompt
