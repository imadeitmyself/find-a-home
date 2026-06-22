# Price Tracker — Build Brief for Claude Code

> **Status:** Design complete, build not started. This document is the canonical,
> self-contained brief. A future Claude Code session should be able to read *only
> this file* and start building. It is written to be copied into a brand-new repo.
>
> **Origin:** Designed as a sibling to the `find-a-home` rental-alert bot, whose
> infrastructure this project reuses (see "Reuse map"). If you have access to the
> `find-a-home` repo, read the named modules directly; if not, this brief describes
> what they do well enough to reimplement.

---

## 0. Instructions to the future agent (how to use this doc)

1. **Before building**, resolve the items in §10 "Open decisions" with the user —
   they change the schema and cost profile.
2. **Build in the phase order in §9.** Each phase is shippable on its own.
3. **Keep this doc alive:** when a decision is made or scope changes, update the
   relevant section here in the same commit. This file is the source of truth.
4. **Default to completeness over cost** in v1 (the user's explicit priority).
   Do not add cost-optimizations (skipping resellers, throttling) unless asked.
5. When you reach a v2 feature (§8), it needs a **research spike first** — do not
   silently pick an LLM/vendor; present options with cost/accuracy to the user.

---

## 1. Purpose

Find the best price for a given product across all online specialist retailers,
track every retailer's price daily, and raise a persistent alert when a price
meets a target. Generalizes to **any type of sports gear** (kitesurf, ski,
climbing, bikes, etc.) — niches where the number of specialist resellers is
modest (tens, not thousands), so we can afford to track *all* of them.

Example: user enters "Core Kite Nexus 8m", target £500 (or -30% off reference).
The app discovers every reseller, pings each daily, and alerts when any reseller
hits the target — and keeps that alert standing.

## 2. Core principles

- **Completeness first.** Maintain the full list of resellers for a product and
  ping them all daily. Don't drop resellers to save cost (that's a later concern).
- **Persistent opportunities.** When a price meets target, it becomes a standing
  alert. If the price later rises (e.g. a promo ended), we **keep the reseller and
  keep pinging** — we never expire or forget it. Only the user dismisses.
- **Propose-then-verify.** LLMs propose (discovery); the deterministic scraper
  verifies. Nothing is trusted unverified.
- **Cheap-and-deterministic daily path.** The daily price poll uses no LLM. LLMs
  are reserved for discovery (infrequent) and v2 hard-extraction fallback.
- **Human-in-the-loop learning.** Preferences are an editable file; the app
  proposes additions, the user confirms.

## 3. Scope

**v1 (build this):**
- Add a product + target (absolute price and/or % off reference).
- Discover resellers (Gemini, search-grounded) → verify → store as tracked links.
- Daily poll of every tracked link via platform-aware deterministic extraction.
- Record price + stock history for every poll.
- Persistent opportunity alerts (never expire; §6).
- Dead-link detection + relink; **distinct flag** for JS-render parse failures (§5).
- Dashboard (Supabase + thin web UI): opportunities, tracked links, retailers,
  relink history, manual add/delete, preferences.
- `PREFERENCES.md` soft-preference learning loop.

**v2 (research spikes, §8):** LLM "view-as-user" crawler for JS-only pages;
landed cost (shipping + VAT/duty + FX); model-year identity resolution;
marketplace scope.

## 4. Reuse map (from `find-a-home`)

Lift these into a shared `core/` or reimplement from the description. They
transfer with little/no change because retail sites have the same fetch/bot/
health/scheduling needs as estate-agent sites.

| `find-a-home` module | What it does | Reuse |
|---|---|---|
| `browser.py` | Camoufox browser + Webshare proxy, tiered (proxy / headless), jittered sequential fetch | as-is |
| `tiers.py` | Maps a source name → fetch tier (1 proxy / 2 headless / 3 plain urllib) | concept; swap name lists for retailers |
| `http.py` (`RobotsCache`, `fetch_text`) | robots.txt-aware polite fetch | as-is |
| `health.py` | Per-source `consecutive_failures` / `consecutive_empty`, historic-max, recovery detection, outcome log. **This is the dead-link engine.** | ~as-is; add a JS-parse-fail outcome (§5) |
| `scheduler.py` | `fast` / `standard` / `stale` schedule groups driven by health | concept; here used to keep healthy links daily and back off dead ones |
| `notifiers.py` | Telegram + Mailgun + SMTP + Composite, chunking, instant-vs-digest channel split | as-is; instant = opportunity, digest = health |
| `extractor.py` | HTMLParser + JSON-LD walker + `__NEXT_DATA__` / inline-data-blob parsers | **toolkit only** — keep the parsing machinery, rewrite the "what is a hit" layer for products |
| `store.py` | SQLite store pattern (upsert/seen/alert_history) | pattern only — v1 uses Supabase Postgres instead |
| `config.py` + CSV/Supabase export | config + source-list loading, Supabase seed export | pattern |
| `runner.py` | fetch → extract → process → record-health loop, tiered by fetch method | reuse the loop *shape* |

**Key semantic difference from `find-a-home`:** that app polls *search/listing*
pages and windows many listings out of each. This app polls *one product page per
(reseller, product)* and extracts *one price + stock*. Extraction is simpler;
the new hard problems are discovery and product identity.

## 5. Architecture

```
                    ┌──────────────────────────────────────┐
                    │  Supabase (Postgres)  — source of truth│
                    │  products, retailers, tracked_links,   │
                    │  price_observations, opportunities,    │
                    │  relink_history, source_health         │
                    └──────────▲─────────────────┬───────────┘
       writes (prices,         │                 │  reads/writes (CRUD)
       opportunities, health)  │                 │
              ┌────────────────┴───┐      ┌──────▼────────────────┐
              │  Bot (VPS)          │      │  Dashboard (web)      │
              │  price_tracker pkg  │      │  thin Next.js + auth  │
              └─────────┬───────────┘      └───────────────────────┘
                        │ uses
   ┌────────────────────┼─────────────────────────────────────┐
   │ discovery (Gemini, weekly/on-add)   platform-aware extract │
   │   → verify with scraper             opportunity engine     │
   │ relink-on-death                     parse-health flagging  │
   └────────────────────────────────────────────────────────────┘
```

- **Data store:** Supabase Postgres, shared by the VPS bot (writes) and dashboard
  (reads + CRUD). Chosen for free auth/API/realtime and multi-device access.
- **Bot:** runs on the VPS like `find-a-home`. Daily poll loop + slower discovery.
- **Dashboard:** thin Next.js/React app on Supabase. Read-heavy; writes are manual
  CRUD (add/delete products & links, dismiss opportunities, edit preferences).

### Platform-aware extraction (try cheapest/most-reliable first)
1. **Shopify** — `/products/<slug>.js` / `.json`: all variants + price as clean
   JSON, no render. (Large share of sports retailers run Shopify.)
2. **WooCommerce** — Store API / REST.
3. **`schema.org/Product` + `Offer`** — price + `availability` from JSON-LD.
4. **Rendered HTML** via Camoufox — last deterministic resort.
5. **Parse-health flag (NEW, per user):** if a page is JS-rendered and none of
   1–4 yield a price, do **not** silently mark it "empty." Flag it distinctly as
   `needs_llm_crawler` on the tracked link and surface it in the dashboard. The
   actual LLM crawler that resolves these is a **v2 feature** (§8.1).

Use conditional GET (ETag/Last-Modified) where possible to keep daily polling
cheap and polite.

## 6. Opportunity engine (persistent — never expires)

Replaces `find-a-home`'s seen-once dedup. Per `(product, tracked_link)`:

```
   price ≤ target  ──▶  OPPORTUNITY OPEN  (alert once; standing on dashboard)
                              │
                              ├─ price stays ≤ target ──▶ stays OPEN (no re-spam)
                              ├─ new lower low ─────────▶ stays OPEN, optional re-ping
                              ├─ price RISES > target ──▶ stays OPEN + flagged
                              │      "price went up (promo may have ended)";
                              │      reseller stays tracked, keep pinging daily
                              └─ user dismisses ───────▶ DISMISSED (only way to close)
```

- **No EXPIRED state.** A price rise does not close anything — it may be a promo
  ending, which is exactly what the user wants to keep watching.
- Every poll writes a `price_observation` regardless of opportunity state.
- Alert (instant channel) fires when an opportunity first opens; optionally on a
  new lower low. Standing opportunities live on the dashboard, not in repeated
  pings.
- Dismissal is user-only and may feed preference learning (§7).

## 7. Preferences (`PREFERENCES.md`) — two layers

- **Hard criteria** → structured columns (target price, target % off, size/variant,
  in-stock-only). Deterministic; never LLM-judged.
- **Soft preferences** → editable `PREFERENCES.md` free text ("no used gear",
  "prefer EU retailers with free returns", "ignore marketplace listings",
  "prior-year model OK if -40%"). The bot loads it; an LLM normalizes it into
  scoring/filter hints.
- **Learning loop:** when the user dismisses an opportunity, the app proposes a
  one-line append to `PREFERENCES.md` for confirmation. Never a silent rewrite.

## 8. v2 features (each needs a research spike before building)

### 8.1 LLM "view-as-user" crawler (for JS-only pages)
- **Trigger:** tracked links flagged `needs_llm_crawler` in §5.
- **Idea:** render the page as a user would (Camoufox), capture DOM/text and/or a
  screenshot, hand to an LLM to read out `price`, `variant`, `in_stock`.
- **This is a whole feature in itself.** Research spike must compare options and
  report **token cost vs. accuracy** before committing:
  - Vision model on a screenshot vs. text/DOM extraction.
  - Browser-agent approaches (computer-use / browser-use style) vs. single-shot.
  - Per-page cost at daily cadence × number of flagged links.
  - Accuracy on a labeled sample of real flagged pages.
- Keep it **off the daily deterministic path**; only run on flagged links, and
  decide cadence (it may not need to be daily).

### 8.2 Landed cost ("true best price")
Shipping to user's country + VAT differences + import VAT/duty + FX (monthly avg
for alerting vs. spot+card fee at purchase). v1 compares sticker prices only.

### 8.3 Model-year identity resolution
Sports gear refreshes yearly; prior-year stock drops hard exactly when "-30%"
fires. Make `year` first-class; fuzzy-match across retailers; use GTIN/EAN/MPN.

### 8.4 Marketplace scope
Decide whether Amazon/eBay/grey-market are included; flag distinctly; let
preferences exclude.

## 9. Build phases (suggested order)

1. **Schema + Supabase migrations** (§ data model below).
2. **Bot skeleton** reusing `browser`/`http`/`health`/`scheduler`/`notifiers`;
   platform detector + deterministic price extractor; write `price_observations`.
   Add the `needs_llm_crawler` parse-health flag.
3. **Opportunity engine** (persistent, §6) + instant/daily notifications.
4. **Discovery** (Gemini propose → scraper verify) + relink-on-death.
5. **Dashboard** (Next.js + Supabase): read views first, then CRUD + preferences.
6. **Preference loading + learning loop** (§7).
7. **v2 research spikes** (§8), starting with the LLM crawler if JS-only pages are
   common in practice.

## 10. Open decisions (resolve with user before/while building)

- **Reference price for "-X% off":** brand MSRP vs. median/max across retailers?
- **Countries / currencies in scope** for v1 (display only, no FX yet)?
- **Gemini model tier:** Flash (cost) vs. Pro (hard discovery)? API key source?
- **Dashboard hosting** (e.g. Vercel) and **auth** (single-user vs. Supabase Auth)?
- **Product identity granularity for v1:** is `(brand, model, year, size)` enough,
  or do we need GTIN matching from day one?
- **Repo:** new standalone repo, or a package alongside `find-a-home` sharing a
  `core/`?

## 11. Data model (Supabase / Postgres)

- `products` — id, brand, model, year, variant (size), target_price,
  target_pct_off, reference_price, currency, in_stock_only, created_at
- `retailers` — id, name, country, currency, platform (shopify|woo|schema|other),
  fetch_tier
- `tracked_links` — id, product_id, retailer_id, url, status
  (active|dead|relinked|needs_llm_crawler), source (discovered|manual),
  last_checked_at, health_state
- `price_observations` — id, tracked_link_id, price, currency, in_stock,
  observed_at  (full time series; written every poll)
- `opportunities` — id, product_id, tracked_link_id, status (open|dismissed),
  trigger (abs_target|pct_off), opened_at, low_price, last_alerted_price,
  price_risen_flag, dismissed_at   ← **no expired state**
- `relink_history` — id, tracked_link_id, old_url, new_url, reason, changed_at
- `source_health` / `source_outcome_log` — ported from `find-a-home/health.py`,
  with an added `needs_llm_crawler` outcome type

## 12. Notifications (reuse `find-a-home/notifiers.py`)
- **Instant** (Mailgun/SMTP): an opportunity opened (and optional new-lower-low).
- **Daily digest** (Telegram): tracker health — dead links, relinks, links flagged
  `needs_llm_crawler`, resellers returning no price.
