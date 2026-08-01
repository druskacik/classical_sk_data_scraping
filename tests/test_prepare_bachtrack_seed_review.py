from source_discovery.bachtrack.prepare_review import prepare_rows


def source(url: str, *, count: str = "1", registered_domain: str = "example.org") -> dict[str, str]:
    return {
        "url": url,
        "registered_domain": registered_domain,
        "listing_count": count,
        "customer_names": "Example Orchestra",
        "categories": "concerts",
        "statuses": "200",
        "example_target_url": f"{url}events/1",
        "example_bachtrack_event_url": "https://bachtrack.com/concert-event/example/1",
    }


def test_prepare_rows_merges_www_aliases() -> None:
    rows = prepare_rows(
        [source("https://example.org/", count="2"), source("https://www.example.org/", count="3")],
        set(),
    )

    assert len(rows) == 1
    assert rows[0]["listing_count"] == "5"
    assert rows[0]["candidate_id"] == "BT0001"
    assert "Merged hostname aliases" in rows[0]["deterministic_note"]


def test_prepare_rows_excludes_internal_and_existing_urls() -> None:
    rows = prepare_rows(
        [source("https://bachtrack.com/"), source("https://existing.example/")],
        {"https://existing.example/"},
    )

    assert [row["deterministic_status"] for row in rows] == ["internal", "existing_seed"]
    assert all(not row["candidate_id"] for row in rows)
