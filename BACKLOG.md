# Product Price Tracker — Backlog

Deferred items, captured during planning. See `PLAN.md` for the v1 scope and
`PRICE_TRACKER_BRIEF.md` for the full self-contained build brief.

## v2 — LLM "view-as-user" crawler (a feature in itself)

v1 flags any JS-rendered page where no price can be parsed (no Shopify/Woo JSON,
no schema.org) as `needs_llm_crawler` and surfaces it on the dashboard — but does
not resolve it. This feature resolves those:

- Render the page as a user would (Camoufox), capture DOM/text and/or screenshot,
  and have an LLM read out `price`, `variant`, `in_stock`.
- **Requires a research spike first** — compare options and report token cost vs.
  accuracy before committing:
  - vision-on-screenshot vs. text/DOM extraction,
  - browser-agent (computer-use / browser-use style) vs. single-shot,
  - per-page cost at the chosen cadence × number of flagged links,
  - accuracy on a labeled sample of real flagged pages.
- Keep it off the daily deterministic path; run only on flagged links, cadence TBD.

## v2 — true "best price" (landed cost)

The v1 alert compares sticker prices only. To rank retailers by what you'd
actually pay:

- **Shipping to your country** per retailer (flat / threshold / weight-based).
- **VAT differences** across EU countries; some B2B EU sites show ex-VAT, EU
  consumer sites show VAT-inclusive, US shows pre-tax — normalize before compare.
- **Import VAT + duty** (post-Brexit UK ↔ EU) on the landed total.
- **FX**: you proposed a monthly FX average for the alert threshold; note that
  spot rate + card FX fee applies at actual purchase — decide alert-rate vs
  purchase-rate.
- Then "best price" = landed cost in your currency, not the lowest sticker.

## v2 — model-year identity resolution

Kite/watersports gear refreshes yearly and prior-year stock drops hard exactly
when "-30%" would fire — so comparing a 2024 8m against a 2025 8m is a real
false-positive risk.

- Make `year` a first-class part of product identity.
- Fuzzy matching across retailers (different titles, languages, SKU naming).
- Use GTIN/EAN/MPN when present to disambiguate variants.

## v2 — marketplace scope

- Decide whether Amazon / eBay / grey-market listings are included. They often
  dominate "cheapest" but differ on trust, warranty, and returns.
- If included, flag them distinctly in the dashboard and let preferences exclude.

## Nice-to-have / later

- Conditional GET (ETag/Last-Modified) everywhere to minimize bandwidth and be
  polite under daily polling. (May land in v1 if cheap.)
- Affiliate-network data sources (Awin, Skimlinks) or Google Shopping API as a
  cleaner — possibly paid — feed for some retailers, vs. scraping.
- Price-history charts per product/variant on the dashboard.
- Multi-user / sharing.
- Alert channels beyond email/Telegram (push, webhook).
