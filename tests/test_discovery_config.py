from jobmatch.discovery import jobspy


def test_location_label_accepts_wizard_location_without_label():
    assert jobspy._location_label({"location": "Remote", "remote": True}) == "Remote"


def test_location_filters_accept_nested_example_config():
    accept, reject = jobspy._load_location_config(
        {
            "location": {
                "accept_patterns": ["Remote", "London"],
                "reject_patterns": ["must relocate"],
            }
        }
    )

    assert accept == ["Remote", "London"]
    assert reject == ["must relocate"]


def test_title_exclusion_matches_phrases_not_substrings():
    patterns = ["intern", "entry-level", "UAE National"]

    assert jobspy._title_excluded("Product Manager Intern", patterns)
    assert jobspy._title_excluded("Entry Level Procurement Officer", patterns)
    assert jobspy._title_excluded("Procurement Officer - UAE National", patterns)
    assert not jobspy._title_excluded("International Trade Compliance Manager", patterns)
    assert not jobspy._title_excluded("Senior Procurement Manager", patterns)


def test_full_crawl_accepts_wizard_generated_location_without_label(monkeypatch):
    searches = []

    def fake_run_one_search(
        search,
        sites,
        results_per_site,
        hours_old,
        proxy_config,
        defaults,
        max_retries,
        accept_locs,
        reject_locs,
        glassdoor_map,
        country_indeed_map=None,
        exclude_titles=None,
    ):
        searches.append(search)
        return {"new": 0, "existing": 0, "errors": 0, "filtered": 0, "total": 0, "label": "test"}

    class FakeConn:
        def execute(self, _sql):
            return self

        def fetchone(self):
            return (0,)

    monkeypatch.setattr(jobspy, "_run_one_search", fake_run_one_search)
    monkeypatch.setattr(jobspy, "init_db", lambda: None)
    monkeypatch.setattr(jobspy, "get_connection", lambda: FakeConn())

    result = jobspy._full_crawl(
        {
            "queries": [{"query": "Software Engineer", "tier": 1}],
            "locations": [{"location": "Remote", "remote": True}],
            "defaults": {"results_per_site": 1, "hours_old": 24},
            "boards": ["linkedin"],
        },
        sites=["linkedin"],
        results_per_site=1,
        hours_old=24,
    )

    assert result["queries"] == 1
    assert searches == [
        {
            "query": "Software Engineer",
            "location": "Remote",
            "location_label": "Remote",
            "remote": True,
            "tier": 1,
        }
    ]
