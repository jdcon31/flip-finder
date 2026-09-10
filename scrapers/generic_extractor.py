"""Generic product extractor — works on ANY store with no per-store selectors.

Instead of hand-coded CSS selectors, it finds product "cards" by structure: an
element that contains a price ($N), a link, and a title. This is the scalable
path to many stores (hand-coded configs stay only for the highest-volume ones). A vision
fallback (Qwen2.5-VL on a screenshot) handles layouts the heuristic can't.

CLI (test on one or more sale-page URLs):
    python -m scrapers.generic_extractor <url> [<url> ...]
    python -m scrapers.generic_extractor --headless <url>
"""

import argparse
import sys
import time

from scrapers import browser_session

# Browser-side heuristic. Returns raw product dicts. Kept as one JS blob so all
# DOM work happens in-page (fast, full access).
_JS_EXTRACT = r"""
() => {
  const reTok = /\$\s?\d[\d,]*(?:\.\d{1,2})?/g;
  const bad = /add to cart|see price|in cart|rated|review|free shipping|^deal$|^sale$|^new$|sponsored|compare|quick view|out of stock|shop now|details|view all|24\/7|customer service|help center|track order|gift card|sign in|create account|my account|rewards/i;

  // 1) price-bearing leaf-ish elements
  const priceLeaves = [];
  for (const e of document.querySelectorAll('body *')) {
    if (e.childElementCount > 2) continue;
    const t = (e.textContent || '').trim();
    if (t.length <= 28 && /\$\s?\d/.test(t)) priceLeaves.push(e);
  }

  // 2) for each price, climb to the nearest ancestor that also has a link
  const cardSet = new Set();
  for (const pe of priceLeaves) {
    let c = pe, d = 0;
    while (c && d < 6) {
      if (c.querySelector && c.querySelector('a[href]')) { cardSet.add(c); break; }
      c = c.parentElement; d++;
    }
  }

  const out = [], seen = new Set();
  for (const card of cardSet) {
    const text = card.textContent || '';
    const prices = [...text.matchAll(reTok)]
      .map(m => parseFloat(m[0].replace(/[$,\s]/g, '')))
      .filter(v => v >= 1 && v <= 3000);
    if (!prices.length) continue;
    const sale = Math.min(...prices);
    const orig = Math.max(...prices);

    // product link (prefer real product paths)
    const norm = (h) => { try { return new URL(h, location.href).href; } catch (e) { return h; } };
    const anchors = [...card.querySelectorAll('a[href]')]
      .filter(a => { const h = a.getAttribute('href') || '';
                     return h && !h.startsWith('#') && !h.startsWith('javascript'); });
    const productAnchor = anchors.find(a =>
      /\/(p|dp|product|products|prod|shop|item)\//i.test(a.getAttribute('href'))) || anchors[0] || null;
    let href = productAnchor ? norm(productAnchor.getAttribute('href')) : null;

    // title: BIND it to the chosen product link so we never grab a neighbouring
    // card's title (the root cause of wrong-item leads). Gather text only from the
    // anchor(s) that point to THIS product; fall back to card headings ONLY if the
    // product link is image-only (no usable text of its own).
    const clean = s => (s || '').replace(/\s+/g, ' ').trim();
    const good = s => s.length > 4 && s.length < 160 && !s.includes('$') && !bad.test(s);
    const collect = (els, out) => {
      for (const el of els) {
        const aria = el.getAttribute && el.getAttribute('aria-label'); if (aria) out.push(aria);
        const ttl = el.getAttribute && el.getAttribute('title'); if (ttl) out.push(ttl);
        if (el.tagName === 'IMG') { out.push(el.getAttribute('alt') || ''); }
        else {
          out.push(el.textContent || '');
          for (const im of el.querySelectorAll('img[alt]')) out.push(im.getAttribute('alt') || '');
        }
      }
    };
    const sameProductAnchors = href
      ? anchors.filter(a => norm(a.getAttribute('href')) === href)
      : [];
    const cands = [];
    collect(sameProductAnchors, cands);
    let title = cands.map(clean).filter(good).sort((a, b) => b.length - a.length)[0] || null;
    if (!title) {                          // image-only link → fall back to card text
      collect(card.querySelectorAll('h1, h2, h3, h4, img[alt], [aria-label]'), cands);
      title = cands.map(clean).filter(good).sort((a, b) => b.length - a.length)[0] || null;
    }
    if (!title) continue;

    const key = href || title;
    if (seen.has(key)) continue;
    seen.add(key);
    const img = card.querySelector('img');
    out.push({
      name: title.slice(0, 140),
      sale_price: sale,
      original_price: orig > sale ? orig : null,
      url: href,
      image_url: img ? (img.getAttribute('src') || img.getAttribute('data-src')) : null,
    });
  }
  return out;
}
"""


def extract_generic(page, max_products: int = 150) -> list:
    """Run the heuristic extractor on the current page; returns product dicts."""
    try:
        products = page.evaluate(_JS_EXTRACT)
    except Exception:
        return []
    return products[:max_products]


def scan_url(url: str, session=None, headless: bool = False, scroll_rounds: int = 8,
             pause: float = 1.2, max_products: int = 150) -> dict:
    """Navigate to any sale-page URL and extract products generically."""
    own = session is None
    if own:
        session = browser_session.BrowserSession(headless=headless)
        session.start()
    page = None
    title = ""
    try:
        page = session.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        time.sleep(5)
        browser_session.polite_pause(1.0)
        for _ in range(scroll_rounds):
            try:
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            except Exception:
                break
            time.sleep(pause)
        products = extract_generic(page, max_products=max_products)
        title = page.title()
    finally:
        # Always close the page we opened (this function creates a fresh page each
        # call) so probing many sale-paths doesn't leak hundreds of tabs.
        if page is not None:
            try:
                page.close()
            except Exception:
                pass
        if own:
            session.close()
    return {"url": url, "page_title": title, "count": len(products), "products": products}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("urls", nargs="+")
    ap.add_argument("--headless", action="store_true")
    args = ap.parse_args()
    session = browser_session.BrowserSession(headless=args.headless)
    session.start()
    try:
        for url in args.urls:
            r = scan_url(url, session=session)
            print(f"\n=== {url}")
            print(f"    title={r['page_title']!r:50} products={r['count']}")
            for p in r["products"][:8]:
                d = ""
                if p["original_price"] and p["sale_price"]:
                    off = round((p["original_price"] - p["sale_price"]) / p["original_price"] * 100)
                    d = f" ({off}% off)"
                print(f"    - {p['name'][:50]!r:52} ${p['sale_price']}{d}")
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
