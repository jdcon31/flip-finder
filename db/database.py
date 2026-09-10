"""SQLite access layer for Flip Finder.

Holds operational + training data: leads, match_log, selection_log. Settings
stay in config/settings.json (onboarding). The match_log/selection_log columns
mirror TRAINING.md exactly so the fine-tune export can read them directly.

Design notes:
- Booleans are stored as INTEGER 0/1; None passes through as NULL ("unknown").
- IDs are uuid4 hex; timestamps are ISO-8601 UTC.
- Each writer function uses an explicit, fixed column list (no dynamic column
  names from caller input) and parameterized values.
"""

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_THIS_DIR)

DB_PATH = os.environ.get("FF_DB_PATH") or os.path.join(_THIS_DIR, "flipfinder.db")
SCHEMA_PATH = os.path.join(_THIS_DIR, "schema.sql")
IMAGES_DIR = os.path.join(_ROOT, "training", "data", "images")


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def new_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _b(value):
    """Bool -> 0/1, leaving None as None (NULL = 'unknown')."""
    return None if value is None else int(bool(value))


def get_connection(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: str = DB_PATH) -> None:
    """Create tables (idempotent), run light migrations, ensure the image dir."""
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    os.makedirs(IMAGES_DIR, exist_ok=True)
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        schema = f.read()
    conn = get_connection(db_path)
    try:
        conn.executescript(schema)
        _migrate(conn)
        conn.commit()
    finally:
        conn.close()


def _migrate(conn) -> None:
    """Add columns missing from older databases (CREATE IF NOT EXISTS won't)."""
    have = {r["name"] for r in conn.execute("PRAGMA table_info(leads)").fetchall()}
    if "match_id" not in have:
        conn.execute("ALTER TABLE leads ADD COLUMN match_id TEXT")
    if "notified" not in have:
        conn.execute("ALTER TABLE leads ADD COLUMN notified INTEGER NOT NULL DEFAULT 0")


def save_image(prefix: str, identifier: str, data: bytes,
               images_dir: str = IMAGES_DIR, root: str = _ROOT) -> str:
    """Persist an image and return its path *relative to ``root``* (the repo root
    by default) — the portable form stored in *_image_path columns and used by
    the training export. Falls back to the absolute path if it can't be made
    relative to ``root`` (e.g. a different drive on Windows).
    """
    os.makedirs(images_dir, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in identifier)
    fname = f"{prefix}_{safe}.png"
    abs_path = os.path.join(images_dir, fname)
    with open(abs_path, "wb") as f:
        f.write(data)
    try:
        return os.path.relpath(abs_path, root).replace(os.sep, "/")
    except ValueError:
        return abs_path.replace(os.sep, "/")


# --------------------------------------------------------------------------- #
# leads
# --------------------------------------------------------------------------- #
_LEAD_COLUMNS = [
    "product_title", "asin", "amazon_url", "source_store", "source_url",
    "source_price", "coupon_codes", "cashback_portal", "cashback_pct",
    "amazon_sell_price", "category", "bsr_90day", "est_monthly_sales",
    "net_profit", "roi_pct", "margin_pct", "gating_status", "hazmat", "notes",
    "match_id",
]


def insert_lead(conn, lead: dict, status: str = "pending") -> str:
    """Insert a lead row. ``lead`` may also carry a ``profit_breakdown`` dict
    (stored as JSON). Unknown keys are ignored. Returns the new id.
    """
    lead_id = new_id()
    cols = ["id", "created_at"] + _LEAD_COLUMNS + ["profit_breakdown", "status"]
    breakdown = lead.get("profit_breakdown")
    values = [lead_id, utcnow()] + [lead.get(c) for c in _LEAD_COLUMNS] + [
        json.dumps(breakdown) if breakdown is not None else None,
        status,
    ]
    placeholders = ", ".join("?" for _ in cols)
    conn.execute(
        f"INSERT INTO leads ({', '.join(cols)}) VALUES ({placeholders})", values
    )
    conn.commit()
    return lead_id


def set_lead_status(conn, lead_id: str, status: str) -> None:
    conn.execute("UPDATE leads SET status = ? WHERE id = ?", (status, lead_id))
    conn.commit()


def get_lead(conn, lead_id: str):
    return conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()


# --------------------------------------------------------------------------- #
# Amazon look-up cache
# --------------------------------------------------------------------------- #
def put_amazon_cache(conn, cache_key: str, data: dict) -> None:
    """Store the Amazon-side result for a cache key (UPC or normalized query)."""
    conn.execute(
        "INSERT OR REPLACE INTO amazon_cache (cache_key, asin, amazon_title, price, "
        "bsr, category, amazon_url, match_score, match_method, fetched_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (cache_key, data.get("asin"), data.get("amazon_title"), data.get("price"),
         data.get("bsr"), data.get("category"), data.get("amazon_url"),
         data.get("match_score"), data.get("match_method"), utcnow()),
    )
    conn.commit()


def get_amazon_cache(conn, cache_key: str, ttl_hours: float = 12.0):
    """Return the cached Amazon result for ``cache_key`` if present and younger than
    ``ttl_hours`` (prices/BSR drift, so stale rows are ignored). Else None."""
    if not cache_key:
        return None
    row = conn.execute(
        "SELECT * FROM amazon_cache WHERE cache_key = ?", (cache_key,)).fetchone()
    if not row:
        return None
    try:
        fetched = datetime.fromisoformat(row["fetched_at"])
    except (TypeError, ValueError):
        return None
    age = (datetime.now(timezone.utc) - fetched).total_seconds()
    if age > ttl_hours * 3600:
        return None
    return dict(row)


# --------------------------------------------------------------------------- #
# Cross-reference progress ledger
# --------------------------------------------------------------------------- #
def record_lookup_attempt(conn, item_key: str, store: str, outcome: str) -> None:
    """Remember that we attempted ``item_key`` (lead or not), so a later budgeted run
    can skip it and advance to new ground."""
    if not item_key:
        return
    conn.execute(
        "INSERT OR REPLACE INTO lookup_ledger (item_key, store, outcome, tried_at) "
        "VALUES (?,?,?,?)", (item_key, store, outcome, utcnow()))
    conn.commit()


def was_recently_tried(conn, item_key: str, ttl_hours: float = 168.0) -> bool:
    """True if ``item_key`` was attempted within ``ttl_hours`` (default 7 days). Stale
    attempts return False so the item gets re-checked (prices drift / new deals)."""
    if not item_key:
        return False
    row = conn.execute(
        "SELECT tried_at FROM lookup_ledger WHERE item_key = ?", (item_key,)).fetchone()
    if not row:
        return False
    try:
        tried = datetime.fromisoformat(row["tried_at"])
    except (TypeError, ValueError):
        return False
    age = (datetime.now(timezone.utc) - tried).total_seconds()
    return age <= ttl_hours * 3600


def get_unnotified_pending(conn):
    """Pending leads not yet pushed to the operator (for auto-notify)."""
    return conn.execute(
        "SELECT * FROM leads WHERE status='pending' AND COALESCE(notified,0)=0 "
        "ORDER BY created_at"
    ).fetchall()


def mark_notified(conn, lead_id: str) -> None:
    conn.execute("UPDATE leads SET notified = 1 WHERE id = ?", (lead_id,))
    conn.commit()


def apply_lead_verdict(conn, lead_id: str, is_match: bool) -> str:
    """Apply the operator's review verdict on a lead.

    is_match True  -> lead 'confirmed'; False -> 'rejected'. Also records the
    verdict on the linked match_log row (closes the Skill-1 training loop).
    Returns the new lead status.
    """
    lead = get_lead(conn, lead_id)
    if lead is None:
        raise KeyError(f"lead not found: {lead_id}")
    status = "confirmed" if is_match else "rejected"
    set_lead_status(conn, lead_id, status)
    if lead["match_id"]:
        set_match_label(conn, lead["match_id"], operator_label=is_match)
    return status


def get_leads(conn, status: str = None, since: str = None):
    sql, params = "SELECT * FROM leads", []
    clauses = []
    if status is not None:
        clauses.append("status = ?"); params.append(status)
    if since is not None:
        clauses.append("created_at >= ?"); params.append(since)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY created_at DESC"
    return conn.execute(sql, params).fetchall()


# --------------------------------------------------------------------------- #
# match_log (Skill 1)
# --------------------------------------------------------------------------- #
def log_match(conn, *, store=None, store_product_title=None, store_product_url=None,
              store_product_image_path=None, store_price=None, amazon_asin=None,
              amazon_product_title=None, amazon_product_url=None,
              amazon_product_image_path=None, amazon_price=None, match_method=None,
              model_confidence=None, model_prediction=None, operator_label=None,
              operator_corrected=False, is_match=None) -> str:
    """Log one match decision (UPC auto-confirm, vision, or operator)."""
    match_id = new_id()
    conn.execute(
        """INSERT INTO match_log (
            id, timestamp, store, store_product_title, store_product_url,
            store_product_image_path, store_price, amazon_asin, amazon_product_title,
            amazon_product_url, amazon_product_image_path, amazon_price, match_method,
            model_confidence, model_prediction, operator_label, operator_corrected, is_match
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (match_id, utcnow(), store, store_product_title, store_product_url,
         store_product_image_path, store_price, amazon_asin, amazon_product_title,
         amazon_product_url, amazon_product_image_path, amazon_price, match_method,
         model_confidence, _b(model_prediction), _b(operator_label),
         _b(operator_corrected), _b(is_match)),
    )
    conn.commit()
    return match_id


def set_match_label(conn, match_id: str, operator_label: bool, is_match: bool = None) -> None:
    """Record the operator's verdict; flags operator_corrected when it disagrees
    with the model's original prediction. is_match defaults to operator_label.
    """
    row = conn.execute(
        "SELECT model_prediction FROM match_log WHERE id = ?", (match_id,)
    ).fetchone()
    if row is None:
        raise KeyError(f"match_log id not found: {match_id}")
    pred = row["model_prediction"]
    corrected = pred is not None and _b(operator_label) != pred
    final = operator_label if is_match is None else is_match
    conn.execute(
        "UPDATE match_log SET operator_label = ?, operator_corrected = ?, is_match = ? WHERE id = ?",
        (_b(operator_label), _b(corrected), _b(final), match_id),
    )
    conn.commit()


# --------------------------------------------------------------------------- #
# selection_log (Skill 2)
# --------------------------------------------------------------------------- #
def log_selection(conn, *, store=None, product_title=None, product_image_path=None,
                  brand=None, category_guess=None, source_price=None, original_price=None,
                  discount_pct=None, model_prediction=None, model_confidence=None,
                  was_sent_to_amazon=False, had_amazon_match=None, was_profitable=None,
                  actual_roi=None) -> str:
    """Log one product-selection evaluation (every product seen on a sale page)."""
    sel_id = new_id()
    conn.execute(
        """INSERT INTO selection_log (
            id, timestamp, store, product_title, product_image_path, brand,
            category_guess, source_price, original_price, discount_pct,
            model_prediction, model_confidence, was_sent_to_amazon, had_amazon_match,
            was_profitable, actual_roi
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (sel_id, utcnow(), store, product_title, product_image_path, brand,
         category_guess, source_price, original_price, discount_pct,
         _b(model_prediction), model_confidence, _b(was_sent_to_amazon),
         _b(had_amazon_match), _b(was_profitable), actual_roi),
    )
    conn.commit()
    return sel_id


def backfill_selection(conn, sel_id: str, *, was_sent_to_amazon=True,
                       had_amazon_match=None, was_profitable=None, actual_roi=None) -> None:
    """Fill in the Amazon-side outcome for a product that was checked."""
    conn.execute(
        """UPDATE selection_log
           SET was_sent_to_amazon = ?, had_amazon_match = ?, was_profitable = ?, actual_roi = ?
           WHERE id = ?""",
        (_b(was_sent_to_amazon), _b(had_amazon_match), _b(was_profitable), actual_roi, sel_id),
    )
    conn.commit()


if __name__ == "__main__":
    init_db()
    print(f"Initialized database at {DB_PATH}")
    print(f"Image store at {IMAGES_DIR}")
