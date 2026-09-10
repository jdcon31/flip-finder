# FlipFinder

**A product-extraction and unit-economics engine for retail arbitrage. Paused, and this
repo is a partial excerpt — see both notes below.**

The problem: given a retailer's sale page, decide which items are real products, read
their prices reliably, and compute whether reselling them on Amazon actually nets a
profit after fees, shipping, and tax.

## The interesting part: models instead of selectors

Most scrapers bind to CSS selectors, so every layout change breaks them and every store
needs its own hand-maintained config. This uses two local models instead:

**Keep/drop classification** (`scrapers/ai_extractor.py`) — a structural heuristic finds
anything on the page with a price, a link, and a title, which is high-recall but low
precision: category tiles, promo banners, and "Your cart is empty" all come back as
products. A local `llama3.1:8b` then decides per row whether it's a single real product.

The design constraint that makes it trustworthy: **the model only classifies, never
rewrites.** Every name, price, and URL stays exactly what the DOM gave. An 8B model was
tried on title cleanup and made titles worse. It also fails open — any model or parse
error keeps all candidates, so a bad model call degrades precision rather than silently
dropping inventory.

**Vision price backstop** (`scrapers/price_vision.py`) — structured data (`JSON-LD`, Open
Graph) gives a canonical price for most product pages, and cross-validating the two
sources yields a confidence score. But a single-source page has nothing to check against,
which is exactly where the expensive false leads came from — a financing rate or an
accessory price read as the real one. So on low-confidence pages only, a local
`qwen2.5-vl:7b` screenshots the buy-box region and reads the price a customer actually
pays. It acts as a validator, not a silent primary: it can confirm, it can flag a
conflict, and it can supply a price where there was none, but it never overwrites a
high-confidence structured price.

The result is a reader that adapts to layout changes with no per-store selectors to
maintain, using free local inference rather than a paid extraction API.

## The rest

- `engines/` — FBM profit math: Amazon referral and closing fees by category, shipping
  estimates, per-state tax tables, and a gating/hazmat risk assessment for whether a
  brand or category is likely restricted. Pure functions, heavily tested.
- `scrapers/structured_data.py` — JSON-LD and meta-tag reader producing price, UPC/EAN,
  model, brand, and a cross-validated confidence score. No dependencies, pure regex.
- `scrapers/prefilter.py` — cheap rules that reject candidates before an expensive
  marketplace lookup, including a title-vs-URL agreement check that catches mis-paired
  scrapes.
- `db/` — SQLite schema and accessors for leads, match logs, and outcome backfill.

133 tests, no network or GPU required to run them: `pip install -r requirements.txt && pytest`

## What this excerpt leaves out

The full project also contained the marketplace cross-reference, a Telegram operations
bot, a scheduler, newsletter-based deal discovery, and per-store configuration. Those are
not included here — this repo is the extraction and economics core, which is the part
with a reusable idea in it.

Page loading here is plain Playwright with default settings and an identifying user
agent. The original had a more aggressive fetching layer; it is deliberately not part of
this excerpt.

## Why it's paused

The engine works. The business didn't, for two reasons that were knowable up front:

1. **Sourcing is an adversarial problem, permanently.** The retailers with the most
   resellable inventory are the ones that invest most in keeping automated clients out.
   That's a maintenance treadmill with a full-time team on the other side of it, and no
   amount of cleverness in the extraction layer changes the economics of that race.
2. **The data that makes a lead trustworthy costs money.** Reliable Amazon figures —
   price history, sales rank, fee and gating accuracy — come from a seller account or a
   paid tool. A lead list's entire value is accuracy, and buyers verify leads with the
   same paid tools you'd be trying to avoid. Competing with an established service at
   $60–100/month, solo and without the data budget, doesn't work.

**What I'd do differently:** spend a day testing those two constraints before building
the pipeline. Both were researchable in advance. Concluding it in days rather than months
was the right call, but it could have been hours.

## Stack

Python 3.11, Playwright, Ollama (`llama3.1:8b`, `qwen2.5-vl:7b`), SQLite, pytest.
