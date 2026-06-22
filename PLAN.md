# Product Price Tracker — Plan

A sibling app to `find-a-home` that finds the best price for a given product
(e.g. "Core Kite Nexus 8m") across European specialist retailers, tracks prices
over time, and raises a persistent **opportunity alert** when a price meets your
target. It reuses `find-a-home`'s fetching/health/scheduling/notification infra
and adds product-specific discovery, extraction, and a web dashboard.

## Goals

1. Given a product reference, discover every European specialist retailer that
   sells it and maintain that list (dead-link pruning + relinking, refreshed
   daily/weekly).
2. Poll each tracked product page, record price + stock, and raise an
   **opportunity** when price ≤ target (absolute, e.g. £500) or ≤ -X% off
   reference price.
3. A dashboard to view/manage everything: open opportunities, tracked links,
   retailers, relink history, and manual add/delete.
4. Learn soft preferences via an editable `PREFERENCES.md`-style file.

Out of scope for v1 (see `BACKLOG.md`): landed cost, FX/VAT normalization,
model-year identity resolution, marketplace inclusion.

## Architecture

```
                    ┌──────────────────────────────┐
                    │  Supabase (Postgres)          │  ← single source of truth
                    │  products, tracked_links,     │
                    │  retailers, price_observations│
                    │  opportunities, relink_history│
                    └───────────▲──────────┬────────┘
        writes (observations,   │          │  reads/writes (CRUD)
        opportunities, health)  │          │
                    ┌───────────┴───┐  ┌───▼─────────────────┐
                    │  Bot (VPS)    │  │  Dashboard (web)    │
                    │  price_bot/   │  │  thin Next.js UI    │
                    └───────────────┘  └─────────────────────┘
                          │  uses
        ┌─────────────────┼──────────────────────────┐
        │ discovery (Gemini)   price extraction       │
        │ + verification       + opportunity logic    │
        └──────────────────────────────────────────────┘
```

- **Data store:** Supabase Postgres (chosen). Replaces the local SQLite as the
  shared truth so both the VPS bot and the dashboard read/write it. The repo
  already has a Supabase migration scaffold to build on.
- **Bot:** runs on the VPS, same host/deploy story as `find-a-home`. Polls
  tracked links, writes observations + opportunities, runs discovery on a slower
  cadence.
- **Dashboard:** thin Next.js/React app on top of Supabase (auth, API, realtime
  come free). Read-heavy; the only writes are manual CRUD (add/delete products
  and links, dismiss opportunities, edit preferences).

## Reuse from `find-a-home`

Lift the infra into a shared `core/` (or import directly) — these transfer with
little/no change:

| Module | Reuse | Notes |
|---|---|---|
| `browser.py` | as-is | Camoufox + Webshare proxy tiering; retailers have the same bot defenses |
| `tiers.py` | concept | swap agent lists for retailer lists |
| `health.py` | ~as-is | consecutive_failures/empty + recovery = the dead-link engine |
| `scheduler.py` | concept | fast/standard/stale groups → daily vs weekly cadence |
| `notifiers.py` | as-is | instant channel = opportunity alert; daily digest = tracker health |
| `http.py` (RobotsCache) | as-is | polite, robots-aware fetch |
| `config.py`, CSV/Supabase export | pattern | config + source-list loading |
| `runner.py` | shape | fetch→extract→process→record-health loop skeleton |
| `extractor.py` | toolkit only | keep HTMLParser, JSON-LD walker, `__NEXT_DATA__`/data-blob parsers; rewrite the "what is a hit" layer |

## New components

### 1. Product identity & variants
- Canonical product = `(brand, model, year)`; variant = size (e.g. 8m).
- Each variant carries its own target price / target % off.
- v1 matching: brand + model + size string match, plus GTIN/EAN/MPN from
  `schema.org/Product` when present. Full fuzzy/model-year resolution → backlog.

### 2. Discovery (Gemini as proposer, scraper as gatekeeper)
- **Propose:** Gemini 2.5 (Flash/Pro) with Google Search grounding + structured
  output (responseSchema) returns `[{retailer, url, country}]` with citations for
  "European specialist retailers selling <product>".
- **Verify:** the existing fetch+extract layer hits each candidate URL and
  confirms it actually sells the product/variant at a real price before it
  becomes a `tracked_link`. LLMs hallucinate URLs — nothing is trusted unverified.
- **Cadence:** discovery runs weekly / on-demand when a product is added, NOT on
  the daily price poll. Daily polling stays deterministic and free.
- **Fallback extraction:** same LLM can extract price/variant/stock from a page
  when deterministic parsing fails (a tier below Shopify-JSON / schema.org).

### 3. Platform-aware price extraction
Try cheapest/most-reliable first:
1. **Shopify** — `/products/<slug>.js|.json` exposes every variant + price as
   clean JSON (large share of watersports retailers run Shopify). No render.
2. **WooCommerce** — REST / Store API.
3. **`schema.org/Product` + `Offer`** — price + `availability` from JSON-LD.
4. **Rendered HTML** (Camoufox) — last resort.
5. **LLM extraction** — fallback when all above are empty.

Extract per poll: `price`, `currency`, `in_stock` (bool / availability), and the
matched variant. Use conditional GET (ETag/Last-Modified) to keep daily polling
cheap and polite.

### 4. Opportunity state machine (persistent)
Replaces `find-a-home`'s "seen once" dedup.

```
        price ≤ target detected
   (none) ─────────────────────────▶ OPEN  ──(re-confirmed, price holds)──▶ stays OPEN
                                       │  └─(new lower low)─▶ re-ping, stays OPEN
                                       ├─(price rises above target / OOS)─▶ EXPIRED
                                       └─(user dismisses in dashboard)────▶ DISMISSED
```

- Alert fires once when an opportunity OPENS (and optionally on a new lower low).
- OPEN opportunities persist on the dashboard until expired or dismissed.
- DISMISSED can feed preference learning (see below).

### 5. Preferences (`PREFERENCES.md`) — two layers
- **Hard criteria** → structured columns (target price, size, in-stock-only).
  Deterministic, never LLM-judged.
- **Soft preferences** → editable `PREFERENCES.md` ("no used gear", "prefer EU
  retailers with free returns", "ignore Amazon marketplace"). The bot loads it;
  an LLM normalizes it into scoring/filter hints.
- **Learning loop:** when you dismiss an opportunity, the app proposes a one-line
  append to `PREFERENCES.md` for you to confirm — human-in-the-loop, never a
  silent rewrite.

## Data model (Supabase / Postgres)

- `products` — id, brand, model, year, variant (size), target_price, target_pct_off,
  reference_price, currency, in_stock_only, created_at
- `retailers` — id, name, country, currency, platform (shopify|woo|other), tier
- `tracked_links` — id, product_id, retailer_id, url, status (active|dead|relinked),
  last_checked_at, health_state, source (discovered|manual)
- `price_observations` — id, tracked_link_id, price, currency, in_stock,
  observed_at  (the time series)
- `opportunities` — id, product_id, tracked_link_id, status (open|expired|dismissed),
  trigger (abs_target|pct_off), opened_at, low_price, last_alerted_price, closed_at
- `relink_history` — id, tracked_link_id, old_url, new_url, reason, changed_at
- `source_health` / `source_outcome_log` — ported from `find-a-home` health schema

## Dashboard surfaces

- **Opportunities** (home): open opportunities, price vs target, retailer, link.
- **Tracked links:** filter by product/retailer/status; manual add + delete; see
  health and relink history.
- **Retailers:** list, country, platform, tier.
- **Products:** add a product (kicks off discovery), set target price / % off.
- **Preferences:** view/edit `PREFERENCES.md`; review proposed learned additions.

## Notifications (reuse `notifiers.py`)
- **Instant** (Mailgun/SMTP): an opportunity opened.
- **Daily digest** (Telegram): tracker health — dead links, relinks, sources
  returning no price.

## Build phases

1. **Schema + Supabase migrations** for the tables above.
2. **Bot skeleton** reusing `browser/http/health/scheduler/notifiers`; platform
   detector + price extractor; write observations.
3. **Opportunity engine** + instant/daily notifications.
4. **Discovery** (Gemini propose → scraper verify) + relink-on-death.
5. **Dashboard** (Next.js + Supabase) — read views first, then CRUD + preferences.
6. **Preference loading + learning loop.**

## Open questions to resolve before/while building
- Reference price for "-X% off": brand MSRP vs median/max across retailers?
- Which countries / currencies in scope for v1 (display only, no FX yet)?
- Gemini API key + model tier (Flash for cost, Pro for hard discovery)?
- Dashboard hosting (Vercel) and auth (single-user vs Supabase Auth)?
