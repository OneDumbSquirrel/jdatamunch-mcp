"""Unit tests for the cell-level redaction module."""

import re

import pytest

from jdatamunch_mcp.redact import (
    DEFAULT_ENABLED,
    KNOWN_PATTERN_NAMES,
    merge_summary,
    redact_rows,
    redact_scalar_list,
    redact_value_distribution,
    redaction_meta,
)


# --------------------------------------------------------------------------- #
# redact_rows                                                                 #
# --------------------------------------------------------------------------- #

class TestRedactRows:
    def test_email_redacted_by_default(self):
        rows = [{"email": "alice@example.com", "name": "Alice"}]
        out, summary = redact_rows(rows)
        assert out[0]["email"] == "[REDACTED:email]"
        assert out[0]["name"] == "Alice"
        assert summary["cells_redacted"] == 1
        assert summary["patterns_matched"] == {"email": 1}

    def test_no_redaction_when_no_match(self):
        rows = [{"id": 1, "city": "Hollywood"}]
        out, summary = redact_rows(rows)
        assert out == rows
        assert summary["cells_redacted"] == 0
        assert summary["patterns_matched"] == {}

    def test_numeric_cells_untouched(self):
        # Integer 123456789012 would match credit-card by digit count but
        # we don't stringify numeric columns — agents rarely treat numbers as PII.
        rows = [{"id": 1, "ssn_like_int": 123456789}]
        out, _ = redact_rows(rows)
        assert out[0]["ssn_like_int"] == 123456789

    def test_ssn_redacted(self):
        rows = [{"id": 1, "ssn": "123-45-6789"}]
        out, summary = redact_rows(rows)
        assert out[0]["ssn"] == "[REDACTED:ssn]"
        assert summary["patterns_matched"] == {"ssn": 1}

    def test_invalid_ssn_not_redacted(self):
        # 000 / 666 / 9xx area numbers are invalid SSNs per SSA rules.
        rows = [{"ssn": "000-12-3456"}]
        out, _ = redact_rows(rows)
        assert out[0]["ssn"] == "000-12-3456"

    def test_credit_card_luhn_valid_redacted(self):
        # 4111-1111-1111-1111 is the canonical Luhn-valid Visa test card.
        rows = [{"card": "4111-1111-1111-1111"}]
        out, summary = redact_rows(rows)
        assert out[0]["card"] == "[REDACTED:credit_card]"
        assert summary["patterns_matched"].get("credit_card") == 1

    def test_credit_card_luhn_invalid_not_redacted(self):
        # 1234-5678-9012-3456 fails Luhn — must pass through untouched.
        rows = [{"order_id": "1234-5678-9012-3456"}]
        out, _ = redact_rows(rows)
        assert out[0]["order_id"] == "1234-5678-9012-3456"

    def test_jwt_redacted(self):
        rows = [{"token": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.abc123"}]
        out, summary = redact_rows(rows)
        assert out[0]["token"] == "[REDACTED:jwt]"
        assert summary["patterns_matched"].get("jwt") == 1

    def test_aws_access_key_redacted(self):
        rows = [{"key": "AKIAIOSFODNN7EXAMPLE"}]
        out, summary = redact_rows(rows)
        assert out[0]["key"] == "[REDACTED:aws_access_key]"

    def test_github_pat_redacted(self):
        rows = [{"token": "ghp_" + "a" * 40}]
        out, _ = redact_rows(rows)
        assert "[REDACTED:github_pat]" in out[0]["token"]

    def test_private_key_block_redacted(self):
        pem = (
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIEpAIBAAKCAQEAwz6X...truncated\n"
            "-----END RSA PRIVATE KEY-----"
        )
        rows = [{"key": pem}]
        out, summary = redact_rows(rows)
        assert out[0]["key"] == "[REDACTED:private_key]"

    def test_skip_columns_exempts_field(self):
        rows = [{"hashed_email": "alice@example.com", "raw_email": "bob@example.com"}]
        out, summary = redact_rows(rows, skip_columns=["hashed_email"])
        assert out[0]["hashed_email"] == "alice@example.com"
        assert out[0]["raw_email"] == "[REDACTED:email]"
        assert summary["cells_redacted"] == 1

    def test_custom_pattern_added(self):
        rows = [{"id_card": "ABC-12345"}]
        out, summary = redact_rows(
            rows, custom_patterns=[r"\bABC-\d+\b"]
        )
        assert out[0]["id_card"] == "[REDACTED:custom_0]"
        assert summary["patterns_matched"].get("custom_0") == 1

    def test_invalid_custom_pattern_reported(self):
        rows = [{"x": "hello"}]
        out, summary = redact_rows(
            rows, custom_patterns=[r"(unclosed"]
        )
        assert "invalid_custom_patterns" in summary
        assert summary["invalid_custom_patterns"] == [0]

    def test_enabled_patterns_can_disable_email(self):
        rows = [{"email": "alice@example.com"}]
        out, summary = redact_rows(
            rows, enabled_patterns={"ssn", "credit_card"}
        )
        assert out[0]["email"] == "alice@example.com"
        assert summary["cells_redacted"] == 0

    def test_none_values_untouched(self):
        rows = [{"email": None, "missing": None}]
        out, _ = redact_rows(rows)
        assert out[0]["email"] is None

    def test_multiple_rows_summary_aggregates(self):
        rows = [
            {"email": "a@a.com"},
            {"email": "b@b.com"},
            {"email": "no email here"},
        ]
        _, summary = redact_rows(rows)
        assert summary["cells_redacted"] == 2
        assert summary["patterns_matched"]["email"] == 2


# --------------------------------------------------------------------------- #
# redact_value_distribution                                                   #
# --------------------------------------------------------------------------- #

class TestRedactValueDistribution:
    def test_value_field_redacted_count_pct_preserved(self):
        items = [
            {"value": "alice@example.com", "count": 10, "pct": 50.0},
            {"value": "Hollywood", "count": 10, "pct": 50.0},
        ]
        out, summary = redact_value_distribution(items)
        assert out[0]["value"] == "[REDACTED:email]"
        assert out[0]["count"] == 10
        assert out[0]["pct"] == 50.0
        assert out[1]["value"] == "Hollywood"
        assert summary["cells_redacted"] == 1


# --------------------------------------------------------------------------- #
# redact_scalar_list                                                          #
# --------------------------------------------------------------------------- #

class TestRedactScalarList:
    def test_flat_list_redacted(self):
        sample = ["alice@example.com", "Hollywood", None, 42]
        out, summary = redact_scalar_list(sample)
        assert out[0] == "[REDACTED:email]"
        assert out[1] == "Hollywood"
        assert out[2] is None
        assert out[3] == 42
        assert summary["cells_redacted"] == 1


# --------------------------------------------------------------------------- #
# merge_summary + redaction_meta                                              #
# --------------------------------------------------------------------------- #

class TestMergeAndMeta:
    def test_merge_summary_adds_counts(self):
        a = {"cells_redacted": 2, "patterns_matched": {"email": 2}}
        b = {"cells_redacted": 3, "patterns_matched": {"email": 1, "ssn": 2}}
        merged = merge_summary(a, b)
        assert merged["cells_redacted"] == 5
        assert merged["patterns_matched"] == {"email": 3, "ssn": 2}

    def test_redaction_meta_applied_true(self):
        meta = redaction_meta(
            applied=True,
            summary={"cells_redacted": 1, "patterns_matched": {"email": 1}},
        )
        assert meta["applied"] is True
        assert meta["cells_redacted"] == 1
        assert meta["patterns_matched"] == {"email": 1}

    def test_redaction_meta_applied_false(self):
        meta = redaction_meta(applied=False)
        assert meta == {"applied": False}

    def test_redaction_meta_includes_custom_count(self):
        meta = redaction_meta(
            applied=True,
            summary={"cells_redacted": 0, "patterns_matched": {}},
            custom_patterns=[r"a", r"b"],
        )
        assert meta["custom_patterns_count"] == 2


# --------------------------------------------------------------------------- #
# Default policy invariants                                                   #
# --------------------------------------------------------------------------- #

class TestDefaultPolicy:
    def test_default_enabled_is_subset_of_known(self):
        assert DEFAULT_ENABLED <= KNOWN_PATTERN_NAMES

    def test_default_policy_covers_high_value_kinds(self):
        # Sanity: these must ship enabled by default.
        for k in ("email", "ssn", "credit_card", "jwt", "private_key", "aws_access_key"):
            assert k in DEFAULT_ENABLED
