-- Flip Finder SQLite schema.
-- Three operational/training tables. Booleans are stored as INTEGER 0/1
-- (NULL allowed where a value is "not yet known"). Timestamps are ISO-8601 UTC
-- strings. Settings live in config/settings.json (onboarding), not here.

PRAGMA foreign_keys = ON;

-- Confirmed/candidate leads for the lead list (PLAN.md "Lead List Output Format").
CREATE TABLE IF NOT EXISTS leads (
    id                 TEXT PRIMARY KEY,
    created_at         TEXT NOT NULL,
    product_title      TEXT,
    asin               TEXT,
    amazon_url         TEXT,
    source_store       TEXT,
    source_url         TEXT,
    source_price       REAL,
    coupon_codes       TEXT,         -- comma-separated or JSON
    cashback_portal    TEXT,
    cashback_pct       REAL,
    amazon_sell_price  REAL,
    category           TEXT,
    bsr_90day          INTEGER,
    est_monthly_sales  INTEGER,
    net_profit         REAL,
    roi_pct            REAL,
    margin_pct         REAL,
    gating_status      TEXT,         -- ungated | gated | unknown
    hazmat             TEXT,         -- yes | no | unknown
    notes              TEXT,
    profit_breakdown   TEXT,         -- JSON line-item breakdown from profit_calc
    match_id           TEXT,         -- link to match_log row (operator verdict)
    notified           INTEGER NOT NULL DEFAULT 0,  -- 0/1 pushed to operator yet
    status             TEXT NOT NULL DEFAULT 'pending'  -- pending | confirmed | rejected
);
CREATE INDEX IF NOT EXISTS idx_leads_status     ON leads(status);
CREATE INDEX IF NOT EXISTS idx_leads_created_at ON leads(created_at);
CREATE INDEX IF NOT EXISTS idx_leads_asin       ON leads(asin);

-- Skill 1 training data: every product-match decision (TRAINING.md "match_log").
CREATE TABLE IF NOT EXISTS match_log (
    id                        TEXT PRIMARY KEY,
    timestamp                 TEXT NOT NULL,
    store                     TEXT,
    store_product_title       TEXT,
    store_product_url         TEXT,
    store_product_image_path  TEXT,
    store_price               REAL,
    amazon_asin               TEXT,
    amazon_product_title      TEXT,
    amazon_product_url        TEXT,
    amazon_product_image_path TEXT,
    amazon_price              REAL,
    match_method              TEXT,     -- 'upc' | 'vision'
    model_confidence          REAL,     -- NULL for UPC matches
    model_prediction          INTEGER,  -- 0/1, NULL for UPC matches
    operator_label            INTEGER,  -- 0/1, NULL if auto-confirmed
    operator_corrected        INTEGER NOT NULL DEFAULT 0,  -- 0/1
    is_match                  INTEGER   -- 0/1 final ground-truth label
);
CREATE INDEX IF NOT EXISTS idx_match_timestamp ON match_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_match_store     ON match_log(store);
CREATE INDEX IF NOT EXISTS idx_match_corrected ON match_log(operator_corrected);

-- Skill 2 training data: every product-selection eval (TRAINING.md "selection_log").
CREATE TABLE IF NOT EXISTS selection_log (
    id                  TEXT PRIMARY KEY,
    timestamp           TEXT NOT NULL,
    store               TEXT,
    product_title       TEXT,
    product_image_path  TEXT,
    brand               TEXT,
    category_guess      TEXT,
    source_price        REAL,
    original_price      REAL,
    discount_pct        REAL,
    model_prediction    INTEGER,  -- 0/1, NULL pre-model
    model_confidence    REAL,
    was_sent_to_amazon  INTEGER NOT NULL DEFAULT 0,  -- 0/1
    had_amazon_match    INTEGER,  -- 0/1, NULL until checked
    was_profitable      INTEGER,  -- 0/1 ground-truth, NULL until checked
    actual_roi          REAL      -- NULL until checked
);
CREATE INDEX IF NOT EXISTS idx_selection_timestamp  ON selection_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_selection_store      ON selection_log(store);
CREATE INDEX IF NOT EXISTS idx_selection_profitable ON selection_log(was_profitable);

-- Amazon look-up cache: the Amazon-side result (ASIN/price/BSR/category) keyed by
-- UPC (else normalized query). Dedupes the same product across stores/days so we
-- skip re-searching Amazon — fewer look-ups (lower footprint) and faster sweeps.
CREATE TABLE IF NOT EXISTS amazon_cache (
    cache_key      TEXT PRIMARY KEY,   -- 'upc:<digits>' or 'q:<normalized name>'
    asin           TEXT,
    amazon_title   TEXT,
    price          REAL,
    bsr            INTEGER,
    category       TEXT,
    amazon_url     TEXT,
    match_score    REAL,
    match_method   TEXT,
    fetched_at     TEXT NOT NULL
);

-- Cross-reference progress ledger: EVERY candidate we've attempted (lead or not),
-- keyed by source identity. Lets a budgeted run SKIP recently-tried items and advance
-- to new ground, so the scheduler covers a huge sale catalogue over days instead of
-- re-checking the same deepest-discount items every run. (amazon_cache only stores
-- SUCCESSFUL matches; this also remembers the no-matches so they aren't retried.)
CREATE TABLE IF NOT EXISTS lookup_ledger (
    item_key   TEXT PRIMARY KEY,   -- 'url:<source url>' or 'q:<normalized name>'
    store      TEXT,
    outcome    TEXT,               -- 'lead' | 'match' | 'no-match' | <reason>
    tried_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ledger_store ON lookup_ledger(store);
