"""Unit tests for the AI product filter — pure logic, model call mocked (no ollama)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scrapers import ai_extractor as ax

PRODUCTS = [
    {"name": "New Arrivals", "sale_price": 50.0, "url": "u/na"},          # 0 junk
    {"name": "Your cart is empty", "sale_price": 15.0, "url": "u/cart"},  # 1 junk
    {"name": "The IceFlow Tumbler", "sale_price": 18.0, "url": "u/ice"},  # 2 real
    {"name": "The Quencher Tumbler", "sale_price": 33.75, "url": "u/q"},  # 3 real
]


def test_ai_filter_keeps_only_model_selected(monkeypatch):
    monkeypatch.setattr(ax, "_call_ollama", lambda prompt, model: '{"keep":[2,3]}')
    out = ax.ai_filter_products(PRODUCTS)
    assert [p["name"] for p in out] == ["The IceFlow Tumbler", "The Quencher Tumbler"]
    # rows are the ORIGINAL dicts, untouched (prices/urls preserved, not renamed)
    assert out[0]["sale_price"] == 18.0 and out[0]["url"] == "u/ice"


def test_ai_filter_fails_open_on_model_error(monkeypatch):
    def boom(prompt, model):
        raise RuntimeError("ollama down")
    monkeypatch.setattr(ax, "_call_ollama", boom)
    assert ax.ai_filter_products(PRODUCTS) == PRODUCTS      # keep all, lose nothing


def test_ai_filter_fails_open_on_unparseable(monkeypatch):
    monkeypatch.setattr(ax, "_call_ollama", lambda p, m: "not json at all")
    assert ax.ai_filter_products(PRODUCTS) == PRODUCTS


def test_ai_filter_empty_input_noop(monkeypatch):
    called = []
    monkeypatch.setattr(ax, "_call_ollama", lambda p, m: called.append(1) or '{"keep":[]}')
    assert ax.ai_filter_products([]) == []
    assert not called                                       # no model call on empty input


def test_ai_filter_tolerates_json_embedded_in_prose(monkeypatch):
    monkeypatch.setattr(ax, "_call_ollama",
                        lambda p, m: 'Here you go: {"keep":[2]} hope that helps')
    out = ax.ai_filter_products(PRODUCTS)
    assert [p["name"] for p in out] == ["The IceFlow Tumbler"]


def test_parse_keep_drops_out_of_range_and_nonint():
    assert ax._parse_keep('{"keep":[0,2,9,"x",-1]}', n=4) == {0, 2}
    assert ax._parse_keep("garbage", n=4) is None          # None -> caller fails open
