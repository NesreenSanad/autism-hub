import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

DATA_TABLES = (
    "change_events, observations, contacts, provider_locations, provider_specialties, provider_services, "
    "warnings, external_ratings, provider_embeddings, source_records, ingest_runs, providers, facilities"
)


@pytest.fixture
def conn():
    """A database with supabase/migrations applied. Never point this at Supabase:
    every test empties the data tables."""
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    from collector import store

    with store.connect(url) as c:
        c.execute(f"truncate {DATA_TABLES} cascade")
        c.execute("delete from sources where name <> 'google_places'")
        yield c
