import yaml

from jobmatch.wizard.init import build_preferences_yaml


def test_build_preferences_yaml_uses_profile_search_roles_and_skills():
    profile = {
        "experience": {
            "target_role": "Senior Backend Engineer",
            "current_title": "Software Engineer",
        },
        "skills_boundary": {
            "programming_languages": ["Python", "SQL"],
            "frameworks": ["FastAPI"],
            "tools": ["AWS", "Docker"],
        },
    }

    data = yaml.safe_load(build_preferences_yaml(
        profile,
        ["Backend Engineer", "Senior Backend Engineer"],
        headline="Python backend engineer",
        reject_roles=["Sales Development Representative"],
        negative_signals=["cold calling"],
    ))

    assert data["candidate"]["headline"] == "Python backend engineer"
    assert data["scoring"]["target_roles"] == ["Senior Backend Engineer", "Backend Engineer"]
    assert data["scoring"]["positive_signals"] == ["Python", "SQL", "FastAPI", "AWS", "Docker"]
    assert data["scoring"]["reject_roles"] == ["Sales Development Representative"]
    assert data["scoring"]["dealbreakers"] == ["unpaid", "commission only"]
    assert data["scoring"]["negative_signals"] == ["cold calling"]


def test_build_preferences_yaml_accepts_empty_optional_rules():
    data = yaml.safe_load(build_preferences_yaml({}, []))

    assert data["candidate"]["headline"] == "Candidate"
    assert data["scoring"]["target_roles"] == []
    assert data["scoring"]["adjacent_roles"] == []
    assert data["scoring"]["reject_roles"] == []
