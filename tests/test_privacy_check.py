"""The privacy gate must catch personal data and stay quiet on placeholders."""

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "privacy_check", Path(__file__).resolve().parents[1] / "scripts" / "privacy_check.py"
)
assert _SPEC and _SPEC.loader
pc = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(pc)


def _rules(text, deny=None):
    return [rule for _, rule in pc.scan_text(text, deny)]


def test_flags_real_contact_data():
    assert _rules("send cv to hr" + "@" + "realhotel.co.id") == ["email"]
    assert _rules("chat -100" + "1234567890") == ["telegram-chat-id"]
    assert _rules("token 1234567890" + ":" + "AAbbCCddEEffGGhhIIjjKKllMMnnOOppQQ") == ["bot-token"]
    assert _rules("key sk" + "-abcdefghijklmnopqrstuv") == ["api-key"]
    assert _rules("call +6" + "2 812 3456 7890") == ["phone"]


def test_placeholders_pass():
    assert _rules("hr@example.com, x@y.example, a@b.test") == []
    assert _rules("re.findall(pattern, text)") == []
    assert _rules("released 2026-09-29T00:00:00, id 33694") == []


def test_denylist_matches_whole_words_case_insensitive():
    deny = pc._denylist_regex({"Jane Placeholder", "Placeholder"})
    assert _rules("profile of jane placeholder", deny) == ["denylist"]
    assert _rules("PlaceholderCity", deny) == []


def test_identity_terms_from_profile_fields():
    assert pc._identity_terms("Jane Q Placeholder", "full_name") >= {"Jane Q Placeholder", "Jane", "Placeholder"}
    assert pc._identity_terms("+6" + "2 812-3456-7890", "phone") == {"34567890"}
    assert pc._identity_terms("https://linkedin.com/in/jane-p-123/", "linkedin_url") == {"jane-p-123"}


def test_denylist_loads_from_runtime_dir_only(monkeypatch, tmp_path):
    (tmp_path / "profiles" / "a").mkdir(parents=True)
    (tmp_path / "profiles" / "a" / "profile.json").write_text('{"personal": {"full_name": "Jane Placeholder"}}')
    (tmp_path / "profiles" / "a" / ".env").write_text("TELEGRAM_CHAT_ID=-100" + "9876543210\n")
    (tmp_path / "privacy-denylist.txt").write_text("Somecity  # home\nab\n")
    monkeypatch.setenv("JOBMATCH_DIR", str(tmp_path))
    monkeypatch.setattr(pc, "_public_handles", lambda: set())

    terms, sources = pc.load_denylist()

    assert sources == 3
    assert {"Jane Placeholder", "Placeholder", "1009876543210", "Somecity"} <= terms
    assert "ab" not in terms
