# Seattle Restaurants MCP Server

An MCP server that lets an AI assistant answer: *what restaurants are in
Seattle, what do they serve, and what's today's special?*

## Tools

| Tool | What it does |
|---|---|
| `search_restaurants` | Find Seattle restaurants by cuisine group (`desi`, `american`, `western`, `asian`), free-form cuisine (`thai`, `ethiopian`, ...), or neighborhood |
| `get_restaurant_details` | Full listing for one place (address, website, phone, hours) |
| `top_restaurants` | Best-documented listings by cuisine group |
| `todays_specials` | Best-effort specials lookup from the restaurant's own website |
| `get_signature_dishes` | Famous / signature dishes for one restaurant, from a small curated list researched 2026-11-02 (menus, popular dishes, review consensus). No live dishes API exists, so unknown restaurants return "not in the list" instead of invented dishes. |

## Data source (v1 — free, no API key)

OpenStreetMap, through two providers:

- **Overpass API (primary)** — full cuisine-tagged scans of Seattle.
- **Nominatim search API (fallback)** — used automatically when Overpass
  returns 502/504/429 or times out. The public Overpass instance does this
  under load, so the fallback is part of the design.

Every result carries a `source` field saying which provider served it.

## Honest limits (by design, not bugs)

1. **No ratings.** OpenStreetMap has no ratings or reviews, so
   `top_restaurants` ranks by listing completeness (website + phone +
   hours + cuisine + address), NOT by quality. The tool says so in its
   own description.
2. **No specials API exists.** There is no public feed of daily specials.
   `todays_specials` finds the restaurant, scans its own website for
   "special" mentions, and quotes what it finds — with a freshness
   caveat. If the site says nothing, it says so instead of inventing a
   special. Calling the restaurant is always the ground truth.
3. **Coverage follows OSM.** A restaurant missing from OpenStreetMap
   won't appear. OSM coverage in Seattle is good but not complete.

## v2: real ratings with Yelp (done — needs your free key)

The Yelp provider is built in (`_yelp_search` in `server.py`). When the
`YELP_API_KEY` environment variable is set, `search_restaurants` and
`top_restaurants` use Yelp Fusion first, so results carry real ratings,
review counts, and price levels, and "top" ranks by rating (tiebreak:
review count). If Yelp fails or no key is set, the server falls back to
OpenStreetMap automatically.

Get a free key (no billing, ~5,000 calls/day):

1. Go to https://www.yelp.com/developers and sign in with a Yelp account
   (create one if needed).
2. Create an app — the API key is shown on the app dashboard.
3. Run the server with the key set:

```bash
YELP_API_KEY=your-key-here .venv/bin/python server.py
```

Never commit the key to git. A `.env` file or shell export is the usual
home for it.

## v3 ideas (not built yet)

- **Google Places API** — ratings + live open-now status. Needs a GCP
  project with billing enabled, so Yelp is the better first upgrade.
- **Review excerpts** — Yelp's `/businesses/{id}/reviews` endpoint returns
  up to 3 review snippets per business.

## Run it

```bash
python3 -m venv .venv
.venv/bin/pip install "mcp[cli]<2" httpx
.venv/bin/python server.py          # stdio MCP server
```

Connect it to an MCP client (Claude Desktop, Muse, etc.) as a stdio
command: `<project>/.venv/bin/python <project>/server.py`.

## Test it

```bash
.venv/bin/python test_smoke.py
```

Calls the tool functions directly (no MCP client needed) and prints real
Seattle results.

## Why this project

Built as a portfolio piece: MCP server design, multi-provider fallback
with retries, honest handling of data that doesn't exist (specials,
ratings) instead of hallucinating it, and a real public dataset
(OpenStreetMap, ODbL).
