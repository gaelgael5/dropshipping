from __future__ import annotations

import pytest

from atelier.candidates.url_normalizer import (
    InvalidUrlError,
    candidate_domains,
    normalize_purchase_url,
)


def test_normalize_strips_tracking_params_and_keeps_article_params() -> None:
    url = normalize_purchase_url(
        "https://fr.aliexpress.com/item/1005.html?spm=a2g0o.home&color=red&utm_source=x&gclid=1"
    )
    assert url.normalized == "https://fr.aliexpress.com/item/1005.html?color=red"


def test_normalize_urls_differing_only_by_tracking_give_same_key() -> None:
    a = normalize_purchase_url(
        "https://www.temu.com/p/42.html?_x_sessn_id=abc&refer_page_name=home"
    )
    b = normalize_purchase_url("http://temu.com/p/42.html?_x_ads_channel=fb#reviews")
    assert a.normalized == b.normalized == "https://temu.com/p/42.html"


def test_normalize_sorts_remaining_params_for_stable_key() -> None:
    a = normalize_purchase_url("https://shop.example/p?size=M&color=red")
    b = normalize_purchase_url("https://shop.example/p?color=red&size=M")
    assert a.normalized == b.normalized


def test_normalize_keeps_original_and_lowercases_host() -> None:
    url = normalize_purchase_url("  https://WWW.Shein.COM/Robe-p-9.html?utm_medium=s  ")
    assert url.original == "https://WWW.Shein.COM/Robe-p-9.html?utm_medium=s"
    assert url.host == "www.shein.com"
    assert url.normalized == "https://shein.com/Robe-p-9.html"


def test_normalize_empty_path_becomes_root() -> None:
    assert normalize_purchase_url("https://shop.example").normalized == "https://shop.example/"


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "pas une url",
        "ftp://shop.example/p",
        "javascript:alert(1)",
        "https://",
        "https://localhost/p",
        "https://exa mple.com/p",
        "https://" + "a" * 2100 + ".com",
    ],
)
def test_normalize_rejects_non_urls_with_message(raw: str) -> None:
    with pytest.raises(InvalidUrlError, match="Ce n'est pas une URL"):
        normalize_purchase_url(raw)


def test_candidate_domains_lists_parents_without_bare_tld() -> None:
    assert candidate_domains("fr.m.aliexpress.com") == [
        "fr.m.aliexpress.com",
        "m.aliexpress.com",
        "aliexpress.com",
    ]
