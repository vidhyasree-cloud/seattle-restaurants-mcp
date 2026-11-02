"""Seattle Restaurants MCP server.

Data: OpenStreetMap, via two free providers (no API key needed):
  - Overpass API (primary): full cuisine-filtered scans of Seattle.
  - Nominatim search API (fallback): used automatically when Overpass
    returns 502/504/429 or times out — the public instance does this
    under load, so the fallback is part of the design, not an afterthought.

Tools:
  - search_restaurants: by cuisine group / cuisine / neighborhood
  - get_restaurant_details: full listing for one place
  - top_restaurants: best-documented listings (see honesty note)
  - todays_specials: best-effort specials lookup from the restaurant's site

Honest limits, stated in the tool descriptions so any AI client relays them:
  - OpenStreetMap has NO ratings and NO daily specials. "Top" without a
    Yelp key means most complete listing, not highest rated. With
    YELP_API_KEY set, Yelp Fusion supplies real ratings, review counts,
    and price levels, and "top" ranks by rating.
  - Daily specials are only as fresh as the restaurant's own website; the
    tool says so and never invents a special.
"""

from __future__ import annotations

import os
import re
import time
import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("seattle-restaurants")

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
YELP_URL = "https://api.yelp.com/v3/businesses/search"
# Rough Seattle bounding box: south, west, north, east
SEATTLE_BBOX = "47.49,-122.44,47.73,-122.24"
TIMEOUT = 180.0

# Yelp category aliases per cuisine group. When YELP_API_KEY is set, Yelp
# becomes the primary provider for search/top (real ratings); OSM remains
# the fallback. Yelp has no daily specials, so todays_specials is unchanged.
YELP_CATEGORIES = {
    "desi": "indian,pakistani,himalayan",
    "american": "newamerican,tradamerican,burgers,steakhouses,bbq",
    "western": "italian,french,spanish,greek,mediterranean",
    "asian": "chinese,japanese,thai,vietnamese,korean",
}

# Signature / famous dishes, curated from web research on 2026-11-02
# (restaurant menus, Yelp popular dishes, and review highlights).
# HONEST LIMIT: this is a small curated list, not a live menu feed.
# There is no public API for "famous dishes" — Yelp Fusion does not
# return dishes, and menus change. For a restaurant not in this list,
# the tool says so instead of inventing a dish. Add entries only with
# a verified source (menu / popular-dishes list / review consensus).
SIGNATURE_DISHES = {
    "chai house": {
        "restaurant": "Chai House, Kirkland",
        "website": "https://thechaihouse.com/",
        "dishes": [
            "Kashmiri chai (pink tea)",
            "Dumm chai",
            "Chocolate sooper biscuit",
            "Plain sooper biscuits",
        ],
        "note": "A chai cafe first — its famous items are teas, not curries.",
    },
    "cafe ashiana": {
        "restaurant": "Cafe Ashiana, Seattle (Capitol Hill)",
        "website": "https://www.cafeashianawa.com/",
        "dishes": [
            "Chicken Seekh Kabab",
            "Afgani Tikka Boti with Rumali Roti",
            "Chicken Tikka / Malai Boti",
            "Mutton Curry",
            "Rumali Roti",
            "Samosas",
        ],
        "note": "Indian / Pakistani, Zabiha Halal, kababs made to order.",
    },
    "indian-nepali kitchen": {
        "restaurant": "Indian-Nepali Kitchen, Seattle (Aurora Ave N)",
        "website": "https://www.indiannepalikitchen.com/",
        "dishes": [
            "Jhol Momo (chicken)",
            "Tandoori Chicken Momos",
            "Combo Momo (steamed / fried / tandoori)",
            "Chicken Thukpa",
            "Butter Chicken Curry",
        ],
        "note": "Reviewers call it 'Best momos in Seattle'.",
    },
    "foody moody": {
        "restaurant": "Foody Moody, Bellevue (Bel-Red Rd, food truck)",
        "website": "https://foodyformoody.com/",
        "dishes": [
            "Lamb Korma with Pulao Rice",
            "Crispy Chicken Zinger Burger",
            "Malai Boti Paratha Roll",
            "Chicken Tikka Roll",
        ],
        "note": "Halal Indian-Pakistani street food truck.",
    },
    "naan n curry": {
        "restaurant": "Naan N Curry, Issaquah",
        "website": "http://www.naanncurry.com/",
        "dishes": [
            "Chicken Dumm Biryani (most popular dish)",
            "Lamb Dumm Biryani",
            "Chicken Tikka Masala",
            "Malai Boti",
            "Haleem",
            "Garlic Naan",
        ],
        "note": "Pakistani-style biryani slow-cooked in saffron.",
    },
    "momo king": {
        "restaurant": "Momo King, Seattle (E Madison St, food truck)",
        "website": "",
        "dishes": [
            "Steamed Chicken Momo",
            "Butter Chicken Momo",
            "Chili Chicken Momo",
            "Coco Momo (coconut curry sauce)",
            "Jhol Momo",
        ],
        "note": "Nepalese dumplings — butter, coco, and chili styles are the standouts.",
    },
    "himalayan bigfoot": {
        "restaurant": "Himalayan Bigfoot, Kent (food truck)",
        "website": "https://himalayanbigfoot.us/",
        "dishes": [
            "Chicken Momo",
            "Pan-Fried Chicken Mo-Mo",
            "Sukuti Chowmein",
            "Chow Mein",
            "Yakisoba",
        ],
        "note": "Authentic Nepali food truck.",
    },
    "maza grill": {
        "restaurant": "Maza Grill, Kent",
        "website": "https://mazagrill.co/",
        "dishes": [
            "Lamb Shahi Nihari (most celebrated dish)",
            "Namak Mandi Lamb Tikka",
            "Halal Aged Ribeye Steak",
            "Mix Grilled Kebab Platter",
            "Mardan Chapli Kebab",
        ],
        "note": "Halal Pakistani grill + steakhouse.",
    },
}

# This VM's egress proxy does TLS interception with a CA that lives in the
# system bundle but not in certifi's. Use the system bundle, and pass the
# proxy explicitly: httpx chokes parsing this shell's no_proxy list.
_SYSTEM_CA = "/etc/ssl/certs/ca-certificates.crt"


def _client() -> httpx.Client:
    return httpx.Client(
        proxy=os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY"),
        trust_env=False,
        verify=_SYSTEM_CA if os.path.exists(_SYSTEM_CA) else True,
        # Overpass rejects requests without an identifying User-Agent (406);
        # Nominatim requires one by policy too.
        headers={"User-Agent": "SeattleRestaurantsMCP/1.0 (portfolio project)"},
        timeout=TIMEOUT,
    )


CUISINE_GROUPS = {
    "desi": ["indian", "pakistani", "bangladeshi", "nepali", "sri_lankan"],
    "american": ["american", "burger", "steak_house", "barbecue", "diner"],
    "western": ["italian", "french", "spanish", "greek", "mediterranean", "european"],
    "asian": ["chinese", "japanese", "thai", "vietnamese", "korean"],
}
# Single keyword per group for the Nominatim fallback (text search, not tags).
GROUP_KEYWORD = {"desi": "indian", "american": "american", "western": "european", "asian": "asian"}

SPECIAL_KEYWORDS = re.compile(r"special|today'?s|chef'?s pick|deal of the day", re.I)


def _cuisine_regex(cuisine_group: str | None, cuisine: str | None) -> str | None:
    if cuisine:
        return re.escape(cuisine.lower())
    if cuisine_group:
        values = CUISINE_GROUPS.get(cuisine_group.lower())
        if values:
            return "|".join(values)
    return None


def _overpass_post(query: str) -> list[dict]:
    """POST a query to Overpass with retries on transient server errors."""
    last_exc: Exception | None = None
    for attempt in range(4):
        try:
            with _client() as client:
                resp = client.post(OVERPASS_URL, data={"data": query}, timeout=TIMEOUT)
                resp.raise_for_status()
                return resp.json().get("elements", [])
        except (httpx.HTTPStatusError, httpx.TimeoutException) as exc:
            status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            if (status in (429, 502, 503, 504) or status is None) and attempt < 3:
                last_exc = exc
                time.sleep(5 * (attempt + 1))
                continue
            raise
    raise last_exc  # unreachable


def _overpass_query(cuisine_regex: str | None) -> list[dict]:
    cuisine_filter = f'["cuisine"~"{cuisine_regex}",i]' if cuisine_regex else ""
    query = f"""
    [out:json][timeout:120];
    (
      node["amenity"="restaurant"]{cuisine_filter}({SEATTLE_BBOX});
      way["amenity"="restaurant"]{cuisine_filter}({SEATTLE_BBOX});
    );
    out tags 300;
    """
    return _overpass_post(query)


def _fmt_overpass(element: dict) -> dict:
    tags = element.get("tags", {})
    addr_parts = [
        tags.get("addr:housenumber", ""),
        tags.get("addr:street", ""),
        tags.get("addr:suburb", "") or tags.get("addr:neighbourhood", ""),
        tags.get("addr:city", ""),
        tags.get("addr:postcode", ""),
    ]
    return {
        "osm_id": f"{element['type']}/{element['id']}",
        "name": tags.get("name", "Unnamed"),
        "cuisine": tags.get("cuisine", ""),
        "address": ", ".join(p for p in addr_parts if p),
        "neighborhood": tags.get("addr:suburb", "") or tags.get("addr:neighbourhood", ""),
        "website": tags.get("website", "") or tags.get("contact:website", ""),
        "phone": tags.get("phone", "") or tags.get("contact:phone", ""),
        "opening_hours": tags.get("opening_hours", ""),
        "source": "openstreetmap-overpass",
    }


def _nominatim_search(
    cuisine_group: str | None, cuisine: str | None, neighborhood: str | None, limit: int
) -> list[dict]:
    """Fallback provider: Nominatim text search over OSM data."""
    term = cuisine or GROUP_KEYWORD.get((cuisine_group or "").lower(), "")
    q = " ".join(p for p in [term, "restaurant", neighborhood, "Seattle"] if p)
    params = {"format": "json", "extratags": 1, "limit": max(limit, 10), "q": q}
    with _client() as client:
        resp = client.get(NOMINATIM_URL, params=params, timeout=60.0)
        resp.raise_for_status()
        results = resp.json()
    places = []
    for r in results:
        if r.get("class") != "amenity" or r.get("type") != "restaurant":
            continue
        extra = r.get("extratags", {}) or {}
        places.append({
            "osm_id": f"{r.get('osm_type')}/{r.get('osm_id')}",
            "name": r.get("name", "Unnamed"),
            "cuisine": extra.get("cuisine", ""),
            "address": r.get("display_name", ""),
            "neighborhood": "",
            "website": extra.get("website", "") or extra.get("contact:website", ""),
            "phone": extra.get("phone", "") or extra.get("contact:phone", ""),
            "opening_hours": extra.get("opening_hours", ""),
            "source": "openstreetmap-nominatim",
        })
    return places[:limit]


def _fmt_yelp(business: dict) -> dict:
    loc = business.get("location", {}) or {}
    return {
        "osm_id": "",
        "name": business.get("name", "Unnamed"),
        "cuisine": ", ".join(c.get("title", "") for c in business.get("categories", [])),
        "address": ", ".join(loc.get("display_address", [])),
        "neighborhood": "",
        "website": business.get("url", ""),
        "phone": business.get("display_phone", ""),
        "opening_hours": "",
        "rating": business.get("rating"),
        "review_count": business.get("review_count", 0),
        "price": business.get("price", ""),
        "yelp_url": business.get("url", ""),
        "source": "yelp-fusion",
    }


def _yelp_search(
    cuisine_group: str | None, cuisine: str | None, neighborhood: str | None, limit: int
) -> list[dict] | None:
    """Yelp Fusion business search. Returns None when no key is configured."""
    key = os.environ.get("YELP_API_KEY")
    if not key:
        return None
    params: dict = {
        "location": f"{neighborhood}, Seattle, WA" if neighborhood else "Seattle, WA",
        "sort_by": "rating",
        "limit": min(max(limit, 1), 50),
    }
    if cuisine_group and cuisine_group.lower() in YELP_CATEGORIES:
        params["categories"] = YELP_CATEGORIES[cuisine_group.lower()]
    elif cuisine:
        params["term"] = f"{cuisine} restaurant"
    with _client() as client:
        resp = client.get(
            YELP_URL, params=params, headers={"Authorization": f"Bearer {key}"}, timeout=60.0
        )
        resp.raise_for_status()
        businesses = resp.json().get("businesses", [])
    places = [_fmt_yelp(b) for b in businesses if not b.get("is_closed")]
    places.sort(key=lambda p: (p.get("rating") or 0, p.get("review_count") or 0), reverse=True)
    return places[:limit]


def _completeness_score(place: dict) -> int:
    return sum(bool(place[k]) for k in ("website", "phone", "opening_hours", "cuisine", "address"))


@mcp.tool()
def search_restaurants(
    cuisine_group: str | None = None,
    cuisine: str | None = None,
    neighborhood: str | None = None,
    limit: int = 10,
) -> list[dict]:
    """Find restaurants in Seattle.

    cuisine_group: one of desi, american, western, asian — or leave blank
    and pass a free-form cuisine (e.g. 'thai', 'ethiopian', 'mexican').
    neighborhood: e.g. 'Capitol Hill', 'Ballard', 'Bellevue' (matched
    against the listing's address).
    Returns name, cuisine, address, website, phone, opening hours.
    Falls back to a second OpenStreetMap source automatically if the
    primary one is overloaded. When a Yelp API key is configured
    (YELP_API_KEY), Yelp is tried first so results carry real ratings.
    """
    try:
        yelp_places = _yelp_search(cuisine_group, cuisine, neighborhood, limit)
    except Exception:
        yelp_places = None
    if yelp_places:
        if neighborhood:
            needle = neighborhood.lower()
            yelp_places = [p for p in yelp_places if needle in p["address"].lower()]
        return yelp_places[:limit]
    try:
        elements = _overpass_query(_cuisine_regex(cuisine_group, cuisine))
        places = [_fmt_overpass(e) for e in elements if e.get("tags", {}).get("name")]
        if neighborhood:
            needle = neighborhood.lower()
            places = [p for p in places if needle in p["neighborhood"].lower() or needle in p["address"].lower()]
    except Exception:
        places = _nominatim_search(cuisine_group, cuisine, neighborhood, limit)
        if neighborhood:
            needle = neighborhood.lower()
            places = [p for p in places if needle in p["address"].lower()]
    places.sort(key=_completeness_score, reverse=True)
    return places[:limit]


@mcp.tool()
def get_restaurant_details(osm_id: str) -> dict:
    """Full listing for one restaurant, by its osm_id (e.g. 'node/123456789')."""
    kind, _, oid = osm_id.partition("/")
    elements = _overpass_post(f"[out:json][timeout:60];\n{kind}({oid});\nout tags;")
    if not elements:
        return {"error": f"No restaurant found for {osm_id}"}
    return _fmt_overpass(elements[0])


@mcp.tool()
def top_restaurants(cuisine_group: str | None = None, limit: int = 10) -> dict:
    """Best-documented Seattle restaurants, optionally by cuisine group.

    HONEST LIMIT: OpenStreetMap carries no ratings or reviews, so without
    a Yelp key this ranks by listing completeness (website + phone +
    hours + cuisine + address), NOT by quality. With a Yelp key configured,
    it ranks by Yelp rating (tiebreak: review count) instead.
    """
    places = search_restaurants(cuisine_group=cuisine_group, limit=limit)
    if places and places[0].get("source") == "yelp-fusion":
        basis = "Yelp rating (tiebreak: review count)"
    else:
        basis = "listing completeness (website, phone, hours, cuisine, address) — NOT ratings"
    return {
        "ranking_basis": basis,
        "restaurants": places,
    }


@mcp.tool()
def todays_specials(restaurant_name: str) -> dict:
    """Best-effort lookup of a restaurant's specials.

    HONEST LIMIT: there is no public API for daily specials. This tool finds
    the restaurant, then scans its own website for 'special' mentions.
    Anything returned is quoted from the site with the caveat that it may
    be stale — if the site says nothing, the tool says so instead of
    inventing a special.
    """
    try:
        places = _nominatim_search(None, restaurant_name, None, 5)
    except Exception:
        places = []
    if not places:
        # Fallback: Overpass name search across the Seattle bbox.
        name_regex = re.escape(restaurant_name)
        query = f"""
        [out:json][timeout:60];
        (
          node["amenity"="restaurant"]["name"~"{name_regex}",i]({SEATTLE_BBOX});
          way["amenity"="restaurant"]["name"~"{name_regex}",i]({SEATTLE_BBOX});
        );
        out tags 20;
        """
        try:
            places = [_fmt_overpass(e) for e in _overpass_post(query) if e.get("tags", {}).get("name")]
        except Exception:
            places = []
    if not places:
        return {"error": f"No Seattle restaurant found matching '{restaurant_name}'"}
    place = sorted(places, key=_completeness_score, reverse=True)[0]
    result: dict = {"restaurant": place, "specials": [], "caveat": None}
    if not place["website"]:
        result["caveat"] = "No website listed for this restaurant, so specials cannot be checked. Call the restaurant for today's special."
        return result
    try:
        with _client() as client:
            resp = client.get(place["website"], timeout=20.0, follow_redirects=True)
        text = re.sub(r"<[^>]+>", " ", resp.text)
        lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines()]
        hits = [ln for ln in lines if SPECIAL_KEYWORDS.search(ln) and 20 < len(ln) < 300][:5]
        result["specials"] = hits
        result["caveat"] = (
            "Quoted from the restaurant's own website; it may be out of date. "
            "Confirm before visiting." if hits else
            "The restaurant's website mentions no specials — call them for today's special."
        )
    except Exception as exc:  # network failures must not break the tool
        result["caveat"] = f"Could not load the restaurant's website ({exc}). Call them for today's special."
    return result


@mcp.tool()
def get_signature_dishes(restaurant_name: str) -> dict:
    """Famous / signature dishes for one restaurant.

    HONEST LIMIT: this is a small curated list researched on 2026-11-02
    from menus, popular-dishes lists, and review consensus — it is NOT
    a live menu feed, and there is no public API for famous dishes.
    If the restaurant is not in the curated list, this tool says so
    instead of inventing dishes. Try a short name, e.g. 'ashiana',
    'chai house', 'naan n curry', 'momo king'.
    """
    entry = _lookup_signature(restaurant_name)
    if entry:
        return {"restaurant": entry["restaurant"], "signature_dishes": entry["dishes"], "note": entry["note"], "menu_url": entry.get("website", "") or None, "source": "curated-list-2026-11-02"}
    return {
        "error": f"No curated signature dishes for '{restaurant_name}' yet.",
        "available_restaurants": sorted(e["restaurant"] for e in SIGNATURE_DISHES.values()),
        "note": "Check the restaurant's own menu / Yelp popular dishes instead of guessing.",
    }


def _lookup_signature(restaurant_name: str) -> dict | None:
    """Curated signature-dish entry for a name, or None. Exact key
    first, then substring match in either direction, so 'ashiana',
    'chai house', 'naan n curry', 'momo king' all work."""
    query = restaurant_name.lower().strip()
    if query in SIGNATURE_DISHES:
        return SIGNATURE_DISHES[query]
    for key, entry in SIGNATURE_DISHES.items():
        if query in key or key in query:
            return entry
    return None


@mcp.tool()
def top_restaurants_with_signature_dishes(cuisine_group: str | None = "desi", limit: int = 10) -> dict:
    """Top restaurants, ratings, signature dishes, AND menu link — all at once.

    One call instead of three: it runs top_restaurants (Yelp rating +
    review count + price when a Yelp key is set, otherwise listing
    completeness), then attaches, for each place, its curated signature
    dishes and its menu link (the restaurant's own website when known,
    otherwise its Yelp page). For places not in the curated list,
    signature_dishes is empty and the tool says so — it never invents
    dishes, and it never invents a full menu. There is no public API
    for full live menus, so the menu link is the honest way to show
    the full menu: open it for current items and prices.
    """
    top = top_restaurants(cuisine_group=cuisine_group, limit=limit)
    combined = []
    for place in top.get("restaurants", []):
        entry = _lookup_signature(place.get("name", ""))
        menu_url = (entry.get("website") if entry else "") or place.get("yelp_url", "") or place.get("website", "")
        combined.append({
            "name": place.get("name"),
            "rating": place.get("rating"),
            "review_count": place.get("review_count"),
            "price": place.get("price", ""),
            "address": place.get("address", ""),
            "cuisine": place.get("cuisine", ""),
            "menu_url": menu_url or None,
            "signature_dishes": entry["dishes"] if entry else [],
            "signature_note": entry["note"] if entry else "Not in the curated signature list yet — check the menu link, do not guess.",
            "source": place.get("source", ""),
        })
    return {
        "ranking_basis": top.get("ranking_basis"),
        "menu_note": "Full menus are not available via any public API — signature dishes above are curated highlights, and menu_url opens the restaurant's own menu / Yelp page for the full, current menu.",
        "restaurants": combined,
    }


if __name__ == "__main__":
    mcp.run()
