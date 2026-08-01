from source_discovery.bachtrack.compile_seed import compile_rows, validate_review_rows


def review(**overrides: str) -> dict[str, str]:
    row = {
        "candidate_id": "BT0001",
        "input_url": "https://tickets.example/",
        "decision": "include",
        "canonical_url": "https://orchestra.example/concerts/",
        "country_code": "CZ",
        "classification": "concert_organization",
        "confidence": "high",
        "evidence_url": "https://orchestra.example/concerts",
        "notes": "Official orchestra calendar",
    }
    row.update(overrides)
    return row


def test_validate_and_compile_high_confidence_include() -> None:
    candidates = [{"candidate_id": "BT0001", "url": "https://tickets.example/"}]
    reviews = [review()]

    validate_review_rows(candidates, reviews)
    seeds, remaining = compile_rows(reviews, set())

    assert remaining == []
    assert seeds[0]["url"] == "https://orchestra.example/"
    assert seeds[0]["crawler_path"] == "crawlers/cz/orchestra_example"


def test_compile_leaves_medium_confidence_for_review() -> None:
    seeds, remaining = compile_rows([review(confidence="medium")], set())

    assert seeds == []
    assert remaining[0]["compile_status"] == "medium_confidence"


def test_compile_can_include_human_approved_medium_confidence() -> None:
    seeds, remaining = compile_rows(
        [review(confidence="medium")], set(), include_medium_confidence=True
    )

    assert len(seeds) == 1
    assert remaining == []
    assert "review_confidence=medium" in seeds[0]["notes"]
