"""Tests for the title<->URL consistency guard that catches extractor mis-pairing.

These are the EXACT real-world failures that minted wrong-item leads (an Aeropostale
jean title on a jogger URL; a running-shoe title on a shorts URL),
plus correct pairings that must survive.

Run:  python -m pytest tests/test_prefilter.py -v
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scrapers import prefilter


# --------------------------------------------------------------------------- #
# title_matches_url — the mis-pairing detector
# --------------------------------------------------------------------------- #
def test_real_mispair_aeropostale_jean_on_jogger_url():
    title = "Aeropostale Women's Aeropostale Womens Juniors High Rise Loose Fit Jean"
    url = "https://www.example-store.com/product/prd-7536923/juniors-so-foldover-fleece-jogger-pants.jsp"
    assert prefilter.title_matches_url(title, url) is False


def test_real_mispair_shoes_on_shorts_url():
    title = "Nike Women's Revolution 8 Road Running Shoes"
    url = "https://www.example-store.com/p/nike-womens-pro-3-shorts-20nikwpr3shrtxxxxapb/"
    assert prefilter.title_matches_url(title, url) is False


def test_correct_pairing_blanket_survives():
    title = ("The Big One Throw Blanket Plush Super Soft Warm Cozy for Living Room "
             "60 x 72 inches Oversized (Gray Chevron)")
    url = "https://www.example-store.com/product/prd-1944597/the-big-one-super-soft-plush-throw.jsp"
    assert prefilter.title_matches_url(title, url) is True


def test_correct_pairing_exact_match():
    assert prefilter.title_matches_url(
        "Nike Women's Pro 3 Shorts",
        "https://www.example-store.com/p/nike-womens-pro-3-shorts-abc/") is True


def test_verbose_amazon_style_title_still_matches():
    # long marketing title, short slug — slug-coverage keeps it (robust to verbosity)
    title = ("CeraVe Moisturizing Cream Daily Face and Body Moisturizer for Dry Skin "
             "with Hyaluronic Acid and Ceramides Fragrance Free 19 Oz")
    url = "https://www.example-store.com/p/cerave-moisturizing-cream/-/A-12345"
    assert prefilter.title_matches_url(title, url) is True


def test_cannot_judge_id_only_url_keeps():
    # no usable slug (id-only / dp-ASIN) -> don't drop on missing evidence
    assert prefilter.title_matches_url("Anything At All", "https://x.com/p/9981726") is True
    assert prefilter.title_matches_url("Sony WH-1000XM5", "https://www.amazon.com/dp/B09XS7JWHH") is True


def test_brand_only_overlap_is_not_enough():
    # shares only the brand 'nike' -> mismatch
    assert prefilter.title_matches_url(
        "Nike Air Zoom Pegasus Running Shoe",
        "https://store.com/p/nike-dri-fit-baseball-cap-hat/") is False


def test_empty_inputs_keep():
    assert prefilter.title_matches_url("", "https://x.com/p/foo-bar-baz") is True
    assert prefilter.title_matches_url("Some Title", "") is True


# --------------------------------------------------------------------------- #
# pre_filter integration — a mis-pair is rejected before the Amazon look-up
# --------------------------------------------------------------------------- #
def test_pre_filter_drops_mispaired_product():
    product = {
        "name": "Nike Women's Revolution 8 Road Running Shoes",
        "sale_price": 16.75, "discount_pct": 70.0,
        "url": "https://www.example-store.com/p/nike-womens-pro-3-shorts-xyz/",
    }
    res = prefilter.pre_filter(product)
    assert res["worth_checking"] is False
    assert "mismatch" in res["reason"]


def test_pre_filter_keeps_consistent_product():
    product = {
        "name": "The Big One Super Soft Plush Throw",
        "sale_price": 8.99, "discount_pct": 40.0,
        "url": "https://www.example-store.com/product/prd-1944597/the-big-one-super-soft-plush-throw.jsp",
    }
    res = prefilter.pre_filter(product)
    assert res["worth_checking"] is True


def test_pre_filter_no_url_unaffected():
    # missing URL -> the guard can't run, product still evaluated on other rules
    product = {"name": "Some Product Name", "sale_price": 20.0, "discount_pct": 30.0, "url": None}
    assert prefilter.pre_filter(product)["worth_checking"] is True
