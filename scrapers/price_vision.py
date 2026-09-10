"""Vision price backstop — read the rendered BUY BOX when structured data is weak.

Structured-data extraction (scrapers/structured_data) gives the canonical price for
most product pages and cross-validates JSON-LD vs meta into a ``price_confidence``.
But cross-validation needs TWO sources: a single-source JSON-LD error, or a page with
no structured data at all, slips through with nothing to check it against. THAT is the
gap that produced the early false leads (a financing / accessory / related-product
price read as the real one).

The backstop: screenshot the product page's price region and have Qwen2.5-VL read the
price the customer actually pays — an INDEPENDENT second source that materialises
exactly where cross-validation can't help. Vision's strength is our failure mode: it
sees the big, bold price next to "Add to Cart" that structured data flattens.

Used surgically:
  * GATED on price_confidence — only fires for low / medium / none. High-confidence
    JSON-LD (the majority) skips it, so sweeps stay fast.
  * VALIDATOR, never a silent primary. ``reconcile`` keeps the structured price
    authoritative when vision confirms it, lowers confidence + flags when vision
    contradicts (vision is approximate — we surface the conflict, we don't blindly
    swap), and only SUPPLIES a price when structured had none.

The reconcile() decision and the JSON parsing are pure and unit-tested (no GPU/
network). The screenshot + ollama call are I/O, exercised via the CLI:

    python -m scrapers.price_vision <product-page-url>
"""

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass

from scrapers.structured_data import to_price

# Local Ollama vision model for the price backstop. Override with FF_VISION_MODEL.
VISION_MODEL = os.environ.get("FF_VISION_MODEL", "qwen2.5vl:7b")
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spike_out")

PRICE_PROMPT = (
    "This is a cropped screenshot of the BUY BOX on an online store PRODUCT page. Report "
    "the ONE-TIME purchase price — what the customer pays right now to buy this item "
    "once. IGNORE all of these:\n"
    "- 'Subscribe & Save' / subscription / auto-ship / auto-delivery prices (often the "
    "larger, highlighted number) — if BOTH a subscription price and a one-time price are "
    "shown, report the ONE-TIME (regular) price, NOT the subscription price;\n"
    "- strikethrough / 'was' / list / original prices;\n"
    "- per-unit prices like '$0.50/oz' or '$0.07/serving';\n"
    "- monthly financing like '$13/mo'; shipping and tax; and prices for related or "
    "recommended products.\n"
    "Return ONLY compact JSON, no prose:\n"
    '{"price": <one-time price number or null>, "currency": "<3-letter code or null>", '
    '"list_price": <strikethrough/original price or null>}'
)


@dataclass
class VisionPrice:
    price: float = None
    currency: str = None
    list_price: float = None
    raw: str = None
    error: str = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.price is not None


def _slug(s: str, maxlen: int = 50) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(s or "item").lower()).strip("-")
    return (s or "item")[:maxlen]


# --------------------------------------------------------------------------- #
# pure parsing
# --------------------------------------------------------------------------- #
def parse_price_json(raw: str) -> dict:
    """Pull the JSON object out of the model's reply (tolerant of fences/prose)."""
    if not raw:
        return {}
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except (json.JSONDecodeError, ValueError):
        try:
            return json.loads(re.sub(r",\s*([}\]])", r"\1", m.group(0)))
        except (json.JSONDecodeError, ValueError):
            return {}


def vision_price_from_json(data: dict, raw: str = None) -> VisionPrice:
    """Map the model JSON into a VisionPrice (pure)."""
    price = to_price(data.get("price"))
    listp = to_price(data.get("list_price"))
    cur = data.get("currency") or None
    if isinstance(cur, str):
        cur = cur.strip().upper() or None
    # a list price that's <= the price isn't a strikethrough; drop it
    if listp is not None and price is not None and listp <= price:
        listp = None
    return VisionPrice(price=price, currency=cur, list_price=listp, raw=raw)


# --------------------------------------------------------------------------- #
# the decision brain (pure, unit-tested) — how vision reconciles with structured
# --------------------------------------------------------------------------- #
def reconcile(structured_price, structured_confidence, vision_price, tol: float = 0.05) -> dict:
    """Decide the final price + confidence from the structured read and the vision read.

    Vision is APPROXIMATE, so we never blindly overwrite a structured price with it — we
    use it to confirm, to flag the DANGEROUS kind of disagreement, or to fill a gap.
    ``tol`` (default 5%) absorbs vision's minor imprecision.

    The disagreement handling is ASYMMETRIC, by design:
      * vision reads HIGHER than structured -> the structured price may be reading TOO
        LOW (a financing/accessory/related-product slip), which inflates ROI and creates
        a FALSE LEAD — the exact bug class this backstop exists to catch. Flag it loudly.
      * vision reads LOWER than structured -> almost always a Subscribe & Save / member /
        coupon price (often styled largest on the page) that we intentionally DON'T
        baseline; the structured public one-time price is the conservative-correct one.
        Keep it quietly, no alarm. (Worst case structured slightly over-reads -> a
        conservative lead price, never a false lead.)

    Returns {price, confidence, source, note, changed}:
      * both present, AGREE        -> keep structured, 'high', confirm note
      * vision HIGHER (dangerous)  -> keep structured, 'low', conflict note (both numbers)
      * vision LOWER (subscribe…)  -> keep structured at its confidence, no alarm
      * structured missing         -> use vision, 'medium', supplied note
      * vision missing/failed       -> keep structured at its confidence, no note
      * neither                    -> price None
    """
    sp, vp = structured_price, vision_price
    if sp is not None and vp is not None:
        if abs(sp - vp) <= tol * max(sp, vp):
            return {"price": sp, "confidence": "high", "source": "structured+vision",
                    "note": "✓ price confirmed by buy-box vision", "changed": False}
        if vp > sp * (1.0 + tol):                     # vision higher -> dangerous direction
            return {"price": sp, "confidence": "low", "source": "structured",
                    "note": f"⚠ buy-box vision read ${vp:.2f} vs page ${sp:.2f} — verify price",
                    "changed": False}
        # vision lower -> subscription/member/coupon price we don't baseline; keep public
        return {"price": sp, "confidence": structured_confidence or "medium",
                "source": "structured", "note": "", "changed": False}
    if sp is None and vp is not None:
        return {"price": vp, "confidence": "medium", "source": "vision",
                "note": f"price read from buy-box image (${vp:.2f}) — no structured data",
                "changed": True}
    if sp is not None:
        return {"price": sp, "confidence": structured_confidence or "medium",
                "source": "structured", "note": "", "changed": False}
    return {"price": None, "confidence": "none", "source": None, "note": "", "changed": False}


# --------------------------------------------------------------------------- #
# I/O: capture + read (exercised via CLI, not unit-tested)
# --------------------------------------------------------------------------- #
# Cookie/consent/newsletter overlays sit on top of the buy box and blind the model
# (live finding: one store's price was fully occluded by a popup). Best-effort
# dismiss before the shot. Interstitial overlays can't be
# clicked away — there the read just fails and we keep the structured price.
_DISMISS_SELECTORS = [
    "#onetrust-accept-btn-handler", "#truste-consent-button",
    "button:has-text('Accept All')", "button:has-text('Accept all')",
    "button:has-text('Accept')", "button:has-text('I Accept')",
    "button:has-text('Got it')", "button:has-text('No thanks')",
    "button:has-text('No Thanks')", "button:has-text('Continue')",
    "[aria-label='Close']", "[aria-label='close']", "button[aria-label='Close']",
    ".close-button", ".modal-close", ".onetrust-close-btn-handler",
]


def _dismiss_overlays(page) -> None:
    """Best-effort: click common cookie/newsletter close buttons, then press Escape."""
    for sel in _DISMISS_SELECTORS:
        try:
            page.click(sel, timeout=600)
        except Exception:
            continue
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass


# Find the price ELEMENT and screenshot its container directly (Playwright auto-scrolls
# it into frame — no viewport/scroll guesswork, which was unreliable across layouts:
# the price was below a short viewport, and centering it scrolled inconsistently). The
# main selling price is almost always the largest-font "$NN.NN" leaf on a product page
# (related/strikethrough prices are smaller); we climb to a modest container so the shot
# includes a little context (currency, /unit) but never the whole page. Returns the
# container handle, or null.
_PRICE_CONTAINER_JS = r"""
() => {
  const re = /\$\s?\d{1,4}(?:[.,]\d{2})/;
  // The subscribe/autoship price is often styled LARGEST, so "biggest price" grabs it
  // (the buyer-specific subscription price, not the public one-time price). Exclude any
  // price inside a subscription-named container; fall back to including them if that
  // leaves nothing, so we never come back empty.
  function inSub(el) {
    let n = el;
    for (let i = 0; i < 6 && n; i++) {
      const tag = (String(n.id || '') + ' ' + String(n.className || '')).toLowerCase();
      if (/subscri|autoship|auto-?ship|auto-?deliver/.test(tag)) return true;
      n = n.parentElement;
    }
    return false;
  }
  function pick(skipSub) {
    let best = null, bestSize = 0;
    for (const el of document.querySelectorAll('body *')) {
      if (el.children.length) continue;               // leaf text nodes only
      const txt = (el.textContent || '').trim();
      if (txt.length > 18 || !re.test(txt)) continue;
      const r = el.getBoundingClientRect();
      if (!r.width || !r.height) continue;
      const s = getComputedStyle(el);
      if (s.visibility === 'hidden' || s.display === 'none' || +s.opacity === 0) continue;
      if (skipSub && inSub(el)) continue;
      const size = parseFloat(s.fontSize) || 0;
      if (size > bestSize) { bestSize = size; best = el; }
    }
    return best;
  }
  const best = pick(true) || pick(false);
  if (!best) return null;
  // Climb to the buy-box column so the crop carries a little context (currency, the
  // strikethrough list price), but never the whole page.
  let node = best;
  for (let i = 0; i < 5; i++) {
    const p = node.parentElement;
    if (!p) break;
    const r = p.getBoundingClientRect();
    if (r.height > 760 || r.width > 760) break;
    node = p;
  }
  return node;
}
"""


def capture_price_region(page, out_dir: str = OUT_DIR, name: str = "item") -> str:
    """Screenshot the buy-box price. Dismisses overlays, locates the most-prominent
    price element and screenshots its container directly (auto-scrolled into frame) for
    a tight, easy-to-read crop. Falls back to a top-of-page viewport shot if the price
    element can't be located. Returns the image path, or None."""
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"buybox_{_slug(name)}.png")
    _dismiss_overlays(page)
    # primary: element screenshot of the price container (no scroll/viewport guesswork)
    try:
        handle = page.evaluate_handle(_PRICE_CONTAINER_JS)
        el = handle.as_element() if handle else None
        if el:
            try:
                el.screenshot(path=path, timeout=5000, animations="disabled")
                return path
            except Exception:
                pass
    except Exception:
        pass
    # fallback: plain top-of-page viewport shot
    try:
        page.evaluate("window.scrollTo(0, 0)")
    except Exception:
        pass
    try:
        page.screenshot(path=path)
        return path
    except Exception:
        return None


def read_price_vision(image_path: str, model: str = VISION_MODEL) -> str:
    """Run the vision model on the buy-box image; returns its raw text reply."""
    import ollama
    r = ollama.chat(model=model,
                    messages=[{"role": "user", "content": PRICE_PROMPT,
                               "images": [image_path]}])
    return r["message"]["content"]


def vision_price(page, name: str = "item", model: str = VISION_MODEL,
                 out_dir: str = OUT_DIR) -> VisionPrice:
    """Screenshot the (already-loaded) product page's buy box and read its price."""
    if page is None:
        return VisionPrice(error="no page")
    try:
        img = capture_price_region(page, out_dir=out_dir, name=name)
    except Exception as e:
        return VisionPrice(error=f"capture: {type(e).__name__}: {e}")
    if not img:
        return VisionPrice(error="no screenshot")
    try:
        raw = read_price_vision(img, model=model)
    except Exception as e:
        return VisionPrice(error=f"vision: {type(e).__name__}: {e}")
    return vision_price_from_json(parse_price_json(raw), raw=raw)


# --------------------------------------------------------------------------- #
# CLI — validate the buy-box read on any product URL (HANDOFF: price validation)
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="Read a product page's buy-box price with vision.")
    ap.add_argument("url")
    ap.add_argument("--headless", action="store_true")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except Exception:
        pass

    from scrapers import browser_session
    from scrapers import structured_data as sd
    session = browser_session.BrowserSession(headless=args.headless)
    session.start()
    page = session.new_page()
    try:
        page.goto(args.url, wait_until="domcontentloaded", timeout=45000)
        time.sleep(2)
        pd = sd.extract_product(page.content())
        vp = vision_price(page, name=_slug(args.url))
    finally:
        try:
            page.close()
        except Exception:
            pass
        session.close()

    print(f"\n=== buy-box price read ===\n  url: {args.url}")
    print(f"  structured: price={pd.price} conf={pd.price_confidence} src={pd.source}")
    print(f"  vision:     price={vp.price} currency={vp.currency} list={vp.list_price} "
          f"ok={vp.ok} error={vp.error}")
    decision = reconcile(pd.price, pd.price_confidence, vp.price)
    print(f"  reconciled: {decision}")
    if vp.raw:
        print(f"  raw: {vp.raw[:200]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
