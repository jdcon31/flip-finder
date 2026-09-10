"""Tests for the structured-data product reader (pure, no network)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scrapers import structured_data as sd


JSONLD_PRODUCT = """
<html><head>
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "Product",
  "name": "Lenovo IdeaPad Slim 3i 15.6\\" Laptop",
  "brand": {"@type": "Brand", "name": "Lenovo"},
  "gtin13": "0196804534121",
  "mpn": "82RN004AUS",
  "sku": "6534839",
  "offers": {
    "@type": "Offer",
    "price": "329.99",
    "priceCurrency": "USD",
    "availability": "https://schema.org/InStock"
  },
  "image": "https://img/lenovo.jpg"
}
</script></head><body>...</body></html>
"""

GRAPH_AGGREGATE = """
<script type="application/ld+json">
{"@context":"https://schema.org","@graph":[
  {"@type":"BreadcrumbList","itemListElement":[]},
  {"@type":"Product","name":"Widget","brand":"Acme",
   "gtin12":"012345678905",
   "offers":{"@type":"AggregateOffer","lowPrice":"19.99","highPrice":"29.99","priceCurrency":"USD"}}
]}
</script>
"""

META_ONLY = """
<html><head>
<meta property="og:title" content="Acme Power Bank 20000mAh">
<meta property="product:price:amount" content="34.99">
<meta property="product:price:currency" content="USD">
<meta property="product:brand" content="Acme">
<meta property="og:image" content="https://img/pb.jpg">
<span itemprop="gtin13" content="0123456789012"></span>
</head></html>
"""


def test_jsonld_basic_fields():
    pd = sd.parse_jsonld(JSONLD_PRODUCT)
    assert pd.name.startswith("Lenovo IdeaPad")
    assert pd.brand == "Lenovo"
    assert pd.price == 329.99 and pd.currency == "USD"
    assert pd.upc == "0196804534121"
    assert pd.model == "82RN004AUS"           # mpn preferred over sku
    assert pd.availability == "InStock"
    assert pd.source == "jsonld" and pd.ok


def test_jsonld_graph_and_aggregate_offer():
    pd = sd.parse_jsonld(GRAPH_AGGREGATE)
    assert pd.name == "Widget" and pd.brand == "Acme"
    assert pd.price == 19.99                    # lowPrice from AggregateOffer
    assert pd.upc == "012345678905"             # gtin12


def test_price_from_offers_variants():
    assert sd.price_from_offers({"price": "$1,299.00"}) == (1299.0, None, None)
    assert sd.price_from_offers({"priceSpecification": {"price": 12.5,
                                "priceCurrency": "USD"}}) == (12.5, "USD", None)
    # list of offers -> cheapest
    p, _, _ = sd.price_from_offers([{"price": "40"}, {"price": "25"}])
    assert p == 25.0


def test_price_from_offers_pricespec_list_with_strikethrough():
    # a common shape: real price + a StrikethroughPrice, price at top-level null
    offers = {"@type": "Offer", "price": None, "priceCurrency": None,
              "priceSpecification": [
                  {"@type": "UnitPriceSpecification", "price": "15.64", "priceCurrency": "USD"},
                  {"@type": "UnitPriceSpecification",
                   "priceType": "https://schema.org/StrikethroughPrice", "price": "22.35"}]}
    price, currency, list_price = sd.price_from_offers(offers)
    assert price == 15.64 and currency == "USD" and list_price == 22.35


def test_to_price():
    assert sd.to_price("$1,299.00") == 1299.0
    assert sd.to_price("329.99") == 329.99
    assert sd.to_price(49) == 49.0
    assert sd.to_price("free") is None
    assert sd.to_price(None) is None


def test_meta_fallback():
    pd = sd.parse_meta(META_ONLY)
    assert pd.name == "Acme Power Bank 20000mAh"
    assert pd.price == 34.99 and pd.currency == "USD"
    assert pd.brand == "Acme"
    assert pd.upc == "0123456789012"
    assert pd.source == "meta"


def test_extract_product_prefers_jsonld_fills_from_meta():
    # JSON-LD has everything but the image; meta supplies a brand if jsonld lacked it
    html = JSONLD_PRODUCT + META_ONLY
    pd = sd.extract_product(html)
    assert pd.price == 329.99 and pd.upc == "0196804534121"   # jsonld wins
    assert pd.source == "jsonld"


def test_extract_product_meta_only_when_no_jsonld():
    pd = sd.extract_product(META_ONLY)
    assert pd.price == 34.99 and pd.brand == "Acme" and pd.source == "meta"


def test_no_structured_data_is_not_ok():
    pd = sd.extract_product("<html><body>just text, no data</body></html>")
    assert pd.ok is False and pd.price is None and pd.upc is None


def test_jsonld_type_as_list():
    html = ('<script type="application/ld+json">'
            '{"@type":["Product","IndividualProduct"],"name":"X",'
            '"offers":{"price":"9.99"}}</script>')
    pd = sd.parse_jsonld(html)
    assert pd.name == "X" and pd.price == 9.99


def test_jsonld_tolerates_trailing_comma():
    html = ('<script type="application/ld+json">'
            '{"@type":"Product","name":"Y","offers":{"price":"5.00",},}</script>')
    pd = sd.parse_jsonld(html)
    assert pd.name == "Y" and pd.price == 5.0


def test_to_weight_lb_units():
    assert sd.to_weight_lb({"value": "2.5", "unitText": "lb"}) == 2.5
    assert sd.to_weight_lb({"value": "2", "unitCode": "KGM"}) == round(2 * 2.20462, 3)
    assert sd.to_weight_lb({"value": "16", "unitText": "oz"}) == 1.0
    assert sd.to_weight_lb("900 g") == round(900 / 453.592, 3)
    assert sd.to_weight_lb("3.0") == 3.0          # unitless -> pounds
    assert sd.to_weight_lb(None) is None
    assert sd.to_weight_lb({"value": "0"}) is None


def test_jsonld_pricespec_list_reads_real_price_not_meta_junk():
    # reproduces a real misread: price in a priceSpecification LIST + a sloppy
    # itemprop="price" for a *related* product elsewhere on the page.
    html = ('<script type="application/ld+json">'
            '{"@type":"Product","name":"LactoBif 30 Probiotics","gtin13":"898220009657",'
            '"offers":{"@type":"Offer","price":null,'
            '"priceSpecification":[{"price":"15.64","priceCurrency":"USD"},'
            '{"priceType":"https://schema.org/StrikethroughPrice","price":"22.35"}]}}'
            '</script>'
            '<span itemprop="price" content="5.56"></span>')   # related product — must be ignored
    pd = sd.extract_product(html)
    assert pd.price == 15.64                # the REAL price, not the $5.56 junk
    assert pd.list_price == 22.35
    assert pd.upc == "898220009657"
    assert pd.price_confidence == "high"


def test_price_confidence_low_when_jsonld_and_meta_disagree():
    html = ('<script type="application/ld+json">'
            '{"@type":"Product","name":"X","offers":{"price":"15.64"}}</script>'
            '<meta property="product:price:amount" content="5.56">')
    pd = sd.extract_product(html)
    assert pd.price == 15.64 and pd.price_confidence == "low"   # disagree -> flag


def test_price_confidence_high_when_sources_agree():
    html = ('<script type="application/ld+json">'
            '{"@type":"Product","name":"X","offers":{"price":"15.64"}}</script>'
            '<meta property="og:price:amount" content="15.64">')
    assert sd.extract_product(html).price_confidence == "high"


def test_meta_no_longer_grabs_bare_itemprop_price():
    # bare itemprop=price (no og/product meta) must NOT become the price
    pd = sd.parse_meta('<span itemprop="price" content="5.56"></span>')
    assert pd.price is None


def test_jsonld_extracts_weight():
    html = ('<script type="application/ld+json">'
            '{"@type":"Product","name":"Toy","weight":{"value":"1.5","unitText":"lb"},'
            '"offers":{"price":"9.99"}}</script>')
    pd = sd.parse_jsonld(html)
    assert pd.weight_lb == 1.5
