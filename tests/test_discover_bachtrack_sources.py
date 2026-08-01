from source_discovery.bachtrack.discover import (
    BachtrackSource,
    summarize_websites,
    website_url,
)


def make_source(**overrides: str) -> BachtrackSource:
    values = {
        "listing_id": "1",
        "category": "concerts",
        "customer_id": "10",
        "customer_name": "Example Orchestra",
        "ticket_url": "https://bachtrack.com/handler/listing/click/1/Search",
        "source_url": "https://www.example.org/events/concert?from=bachtrack",
        "source_status": "200",
        "source_domain": "www.example.org",
        "source_registered_domain": "example.org",
        "event_url": "https://bachtrack.com/concert-event/example/1",
        "event_title": "Example concert",
        "venue": "Example Hall",
        "city": "Prague",
    }
    values.update(overrides)
    return BachtrackSource(**values)


def test_website_url_reduces_target_to_origin() -> None:
    assert (
        website_url("HTTPS://Tickets.Example.org/path?a=1#fragment")
        == "https://tickets.example.org/"
    )
    assert website_url("") == ""


def test_summarize_websites_deduplicates_origins_and_keeps_evidence() -> None:
    sources = [
        make_source(),
        make_source(
            listing_id="2",
            customer_id="11",
            customer_name="Example Festival",
            source_url="https://www.example.org/another-event",
            event_url="https://bachtrack.com/concert-event/another/2",
        ),
        make_source(
            listing_id="3",
            source_url="",
            source_status="TooManyRedirects",
            source_domain="",
            source_registered_domain="",
        ),
    ]

    websites = summarize_websites(sources)

    assert len(websites) == 1
    assert websites[0].url == "https://www.example.org/"
    assert websites[0].listing_count == 2
    assert websites[0].customer_ids == "10 | 11"
    assert websites[0].customer_names == "Example Festival | Example Orchestra"
    assert websites[0].example_target_url.startswith("https://www.example.org/events/")
