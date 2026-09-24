import pytest

from scripts.local_database_guard import local_database_refusal


@pytest.mark.parametrize(
    "engine_url",
    [
        "sqlite:///coinpilotx.db",
        "sqlite:////tmp/scratch.db",
        "postgresql+psycopg2://app:pw@localhost:5432/app",
        "postgresql+psycopg2://app:pw@127.0.0.1:5432/app",
        "postgresql+psycopg2://app:pw@[::1]:5432/app",
        "postgresql:///app",
    ],
)
def test_local_targets_are_allowed(engine_url):
    assert local_database_refusal(engine_url) is None


@pytest.mark.parametrize(
    "engine_url",
    [
        "postgresql+psycopg2://u:pw@postgres.railway.internal:5432/railway",
        "postgresql+psycopg2://u:pw@monorail.proxy.rlwy.net:37421/railway",
        "postgres://u:pw@10.0.0.4:5432/railway",
        "mysql://u:pw@localhost/app",
        "postgresql+psycopg2://u:pw@[bad:/app",
        "",
        "   ",
    ],
)
def test_remote_and_unparseable_targets_are_refused(engine_url):
    assert local_database_refusal(engine_url) is not None


def test_refusal_names_the_offending_host():
    reason = local_database_refusal("postgresql://u:pw@db.example.com:5432/app")
    assert "db.example.com" in reason


def test_localhost_is_not_matched_as_a_substring():
    assert local_database_refusal("postgresql://u:pw@localhost.evil.com:5432/app") is not None
