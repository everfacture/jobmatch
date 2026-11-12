import sqlite3

from jobmatch.discovery.smartextract import _dealls_query_relevant, _store_jobs_filtered


def test_dealls_relevance_rejects_unrelated_popular_jobs():
    job = {
        "title": "Android Mobile Developer",
        "company": "PT INDOSAKU DIGITAL TEKNOLOGI",
        "description": "Build mobile banking applications with Kotlin.",
    }

    assert _dealls_query_relevant(job, "hampers") is False
    assert _dealls_query_relevant(job, "florist") is False


def test_dealls_relevance_rejects_generic_customer_service_match_for_ecommerce_query():
    job = {
        "title": "Customer Service - Jakarta Selatan",
        "company": "FIT HUB",
        "description": "Handle member questions and complaints.",
    }

    assert _dealls_query_relevant(job, "e-commerce customer service") is False


def test_dealls_relevance_accepts_hampers_synonyms():
    job = {
        "title": "Kru Packing",
        "company": "Oentukmu",
        "description": "Melakukan proses packing hampers dan paket hadiah, mengikat pita, dan menyusun tampilan akhir hampers.",
    }

    assert _dealls_query_relevant(job, "hampers packing") is True
    assert _dealls_query_relevant(job, "gift packaging") is True


def test_dealls_relevance_accepts_florist_synonyms():
    job = {
        "title": "Perangkai Bunga",
        "company": "Cally Florist",
        "description": "Merangkai buket bunga fresh, artificial, dan dried dalam bentuk box dan vas.",
    }

    assert _dealls_query_relevant(job, "florist") is True
    assert _dealls_query_relevant(job, "buket bunga") is True


def test_dealls_relevance_accepts_visual_display_terms():
    job = {
        "title": "Visual Merchandiser",
        "company": "Facetology",
        "description": "Create 3D display and POSM design for store and event booth.",
    }

    assert _dealls_query_relevant(job, "POSM display") is True
    assert _dealls_query_relevant(job, "booth display") is True


def test_store_jobs_filtered_preserves_company(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        create table jobs (
            url text primary key,
            title text,
            company text,
            salary text,
            description text,
            location text,
            site text,
            strategy text,
            discovered_at text,
            discovered_run_id integer
        )
        """
    )
    monkeypatch.setattr("jobmatch.discovery.smartextract.current_run_id", lambda: 42)

    new, existing = _store_jobs_filtered(
        conn,
        [{
            "url": "https://dealls.example/job",
            "title": "Marketplace Admin",
            "company": "Gift Brand",
            "salary": "4jt-5jt IDR",
            "description": "Manage Shopee orders and product listings.",
            "location": "Jakarta",
        }],
        "Dealls",
        "api_extractor",
        ["Jakarta"],
        [],
    )

    row = conn.execute("select company, discovered_run_id from jobs").fetchone()
    assert (new, existing) == (1, 0)
    assert row == ("Gift Brand", 42)
