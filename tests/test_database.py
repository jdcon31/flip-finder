"""Tests for the SQLite access layer (leads, match_log, selection_log).

Run:  python -m pytest tests/test_database.py -v
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import database as db
from engines import profit_calc


@pytest.fixture
def conn(tmp_path):
    dbfile = str(tmp_path / "test.db")
    db.init_db(dbfile)
    c = db.get_connection(dbfile)
    yield c
    c.close()


def test_init_creates_tables(conn):
    names = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    assert {"leads", "match_log", "selection_log"} <= names


# --------------------------------------------------------------------------- #
# leads
# --------------------------------------------------------------------------- #
def test_insert_and_get_lead(conn):
    lead_id = db.insert_lead(conn, {
        "product_title": "Trail Runner Low", "asin": "B0TEST", "source_store": "store-a",
        "source_price": 85.97, "amazon_sell_price": 119.99, "net_profit": 18.11,
        "roi_pct": 25.83, "margin_pct": 15.09, "category": "shoes",
        "profit_breakdown": {"net_profit": 18.11, "referral_fee": 18.0},
    })
    rows = db.get_leads(conn)
    assert len(rows) == 1
    assert rows[0]["id"] == lead_id
    assert rows[0]["status"] == "pending"
    assert rows[0]["source_price"] == pytest.approx(85.97)
    assert '"referral_fee": 18.0' in rows[0]["profit_breakdown"]


def test_unnotified_pending_and_mark(conn):
    a = db.insert_lead(conn, {"product_title": "A"})
    db.insert_lead(conn, {"product_title": "B"})
    assert len(db.get_unnotified_pending(conn)) == 2
    db.mark_notified(conn, a)
    rem = db.get_unnotified_pending(conn)
    assert len(rem) == 1 and rem[0]["product_title"] == "B"


def test_lead_status_filter_and_update(conn):
    a = db.insert_lead(conn, {"product_title": "A"})
    db.insert_lead(conn, {"product_title": "B"})
    db.set_lead_status(conn, a, "confirmed")
    assert len(db.get_leads(conn, status="confirmed")) == 1
    assert len(db.get_leads(conn, status="pending")) == 1


def test_lead_from_profit_engine(conn):
    """A lead built from the profit engine round-trips correctly."""
    deal = profit_calc.Deal(sale_price=85.97, sell_price=119.99, category="shoes")
    result = profit_calc.calculate(deal, profit_calc.ProfitSettings(state="IL")).rounded()
    lead_id = db.insert_lead(conn, {
        "product_title": "Trail Runner Low", "source_store": "store-a",
        "source_price": deal.sale_price, "amazon_sell_price": deal.sell_price,
        "net_profit": result.net_profit, "roi_pct": result.roi_pct,
        "margin_pct": result.margin_pct, "notes": result.cashback_note,
        "profit_breakdown": result.breakdown,
    })
    row = db.get_leads(conn)[0]
    assert row["id"] == lead_id
    assert row["net_profit"] == pytest.approx(result.net_profit)
    assert "generic 2% card cashback" in row["notes"]


# --------------------------------------------------------------------------- #
# match_log (Skill 1)
# --------------------------------------------------------------------------- #
def test_upc_match_has_null_model_fields(conn):
    mid = db.log_match(conn, store="store-a", match_method="upc", is_match=True)
    row = conn.execute("SELECT * FROM match_log WHERE id = ?", (mid,)).fetchone()
    assert row["match_method"] == "upc"
    assert row["model_confidence"] is None
    assert row["model_prediction"] is None
    assert row["is_match"] == 1
    assert row["operator_corrected"] == 0


def test_operator_correction_is_flagged(conn):
    # Model predicted a match; operator says it's NOT -> corrected = 1.
    mid = db.log_match(conn, store="store-a", match_method="vision",
                       model_confidence=0.73, model_prediction=True)
    db.set_match_label(conn, mid, operator_label=False)
    row = conn.execute("SELECT * FROM match_log WHERE id = ?", (mid,)).fetchone()
    assert row["operator_label"] == 0
    assert row["operator_corrected"] == 1
    assert row["is_match"] == 0


def test_operator_agreement_not_flagged_corrected(conn):
    mid = db.log_match(conn, store="store-a", match_method="vision",
                       model_confidence=0.9, model_prediction=True)
    db.set_match_label(conn, mid, operator_label=True)
    row = conn.execute("SELECT * FROM match_log WHERE id = ?", (mid,)).fetchone()
    assert row["operator_corrected"] == 0
    assert row["is_match"] == 1


def test_set_match_label_unknown_id_raises(conn):
    with pytest.raises(KeyError):
        db.set_match_label(conn, "nope", operator_label=True)


# --------------------------------------------------------------------------- #
# selection_log (Skill 2)
# --------------------------------------------------------------------------- #
def test_selection_log_and_backfill(conn):
    sid = db.log_selection(conn, store="store-a", product_title="Performance Tee - Medium",
                           brand="ExampleBrand", category_guess="clothing", source_price=18.99,
                           original_price=35.0, discount_pct=45.7,
                           model_prediction=False, model_confidence=0.88,
                           was_sent_to_amazon=False)
    row = conn.execute("SELECT * FROM selection_log WHERE id = ?", (sid,)).fetchone()
    assert row["model_prediction"] == 0
    assert row["was_sent_to_amazon"] == 0
    assert row["was_profitable"] is None  # unknown until checked

    db.backfill_selection(conn, sid, was_sent_to_amazon=True, had_amazon_match=True,
                          was_profitable=True, actual_roi=31.2)
    row = conn.execute("SELECT * FROM selection_log WHERE id = ?", (sid,)).fetchone()
    assert row["was_sent_to_amazon"] == 1
    assert row["was_profitable"] == 1
    assert row["actual_roi"] == pytest.approx(31.2)


# --------------------------------------------------------------------------- #
# image storage
# --------------------------------------------------------------------------- #
def test_save_image_roundtrip(tmp_path):
    img_dir = str(tmp_path / "images")
    rel = db.save_image("store_a", "001/abc", b"\x89PNG\r\n\x1a\n",
                        images_dir=img_dir, root=str(tmp_path))
    assert rel == "images/store_a_001_abc.png"  # relative + unsafe chars sanitized
    assert not os.path.isabs(rel)
    assert os.path.exists(os.path.join(img_dir, "store_a_001_abc.png"))
