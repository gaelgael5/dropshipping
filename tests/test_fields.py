from __future__ import annotations

from decimal import Decimal

import pytest

from atelier.candidates.fields import (
    CANDIDATE_FIELDS,
    LINK_FIELDS,
    FieldValueError,
    parse_currency,
    parse_price,
    parse_sizes,
    parse_tristate,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("12,50", Decimal("12.50")),
        (" 7.5 ", Decimal("7.5")),
        ("0", Decimal(0)),
        ("19 €", Decimal(19)),
    ],
)
def test_parse_price_accepts_decimals(raw: str, expected: Decimal) -> None:
    assert parse_price(raw) == expected


def test_parse_price_empty_stays_empty() -> None:
    assert parse_price("   ") is None


@pytest.mark.parametrize("raw", ["-1", "-0,01", "abc", "1.234", "NaN", "Infinity", "1e12"])
def test_parse_price_refuses_negative_or_invalid(raw: str) -> None:
    with pytest.raises(FieldValueError):
        parse_price(raw)


def test_parse_currency_uppercases_and_validates() -> None:
    assert parse_currency(" usd ") == "USD"
    assert parse_currency("") is None
    with pytest.raises(FieldValueError):
        parse_currency("euro")


@pytest.mark.parametrize(("raw", "expected"), [("oui", True), ("NON", False), ("inconnu", None)])
def test_parse_tristate(raw: str, expected: bool | None) -> None:
    assert parse_tristate(raw) is expected


def test_parse_tristate_refuses_other_values() -> None:
    with pytest.raises(FieldValueError):
        parse_tristate("peut-être")


def test_parse_sizes_many_none_and_dedup() -> None:
    assert parse_sizes("S, M ;L\nM") == ["S", "M", "L"]
    assert parse_sizes("  ") == []


def test_text_field_empty_stays_empty_and_required_refuses() -> None:
    assert CANDIDATE_FIELDS["brand"].parse("  ") is None
    with pytest.raises(FieldValueError):
        CANDIDATE_FIELDS["status"].parse(" ")
    with pytest.raises(FieldValueError):
        CANDIDATE_FIELDS["title"].parse("x" * 201)


def test_display_round_trips_values() -> None:
    assert CANDIDATE_FIELDS["sale_price"].display(Decimal("12.5")) == "12,50"
    assert LINK_FIELDS["ships_from_europe"].display(None) == "inconnu"
    assert LINK_FIELDS["available_sizes"].display(["S", "M"]) == "S, M"
    assert CANDIDATE_FIELDS["title"].display(None) == ""
