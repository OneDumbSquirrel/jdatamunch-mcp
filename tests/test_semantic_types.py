"""Semantic column type detection (A6)."""

from jdatamunch_mcp.profiler.semantic_types import detect_semantic_type


def test_email_detected():
    samples = ["alice@example.com", "bob@test.org", "carol+filter@gmail.com"]
    t, c = detect_semantic_type("string", samples, "email")
    assert t == "email"
    assert c >= 0.85


def test_url_detected():
    samples = ["https://example.com/x", "http://foo.bar/baz", "https://a.test/"]
    t, c = detect_semantic_type("string", samples, "homepage_url")
    assert t == "url"


def test_uuid_detected():
    samples = [
        "550e8400-e29b-41d4-a716-446655440000",
        "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
        "00000000-0000-0000-0000-000000000000",
    ]
    t, _ = detect_semantic_type("string", samples, "session_uuid")
    assert t == "uuid"


def test_iso_currency_requires_name_hint():
    samples = ["USD", "EUR", "JPY"]
    # Without name hint: 3-letter codes are too ambiguous → not flagged.
    t, _ = detect_semantic_type("string", samples, "code")
    assert t is None
    # With hint: confident classification.
    t, _ = detect_semantic_type("string", samples, "iso_currency")
    assert t == "iso_currency"


def test_lat_requires_numeric_and_hint():
    samples = ["37.7749", "40.7128", "-33.8688"]
    t, _ = detect_semantic_type("float", samples, "latitude")
    assert t == "lat"


def test_lon_requires_numeric_and_hint():
    samples = ["-122.4194", "-74.0060", "151.2093"]
    t, _ = detect_semantic_type("float", samples, "lon")
    assert t == "lon"


def test_zip_us():
    samples = ["94016", "10001", "60601-1234"]
    t, _ = detect_semantic_type("string", samples, "postal_code")
    assert t == "zip_us"


def test_no_detection_with_too_few_samples():
    t, c = detect_semantic_type("string", ["alice@x.com"], "email")
    assert t is None
    assert c == 0.0


def test_boolean_text():
    samples = ["true", "false", "true", "false", "true"]
    t, _ = detect_semantic_type("string", samples, "is_active")
    assert t == "boolean_text"


def test_low_match_rate_rejects():
    samples = ["alice@x.com", "bob", "carol", "dan"]  # only 25% emails
    t, _ = detect_semantic_type("string", samples, "user")
    assert t is None
