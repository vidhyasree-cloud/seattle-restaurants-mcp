"""Smoke test: call the tool functions directly (no MCP client needed)."""
import sys
sys.path.insert(0, ".")
from server import search_restaurants, top_restaurants, todays_specials

print("== desi search ==")
desi = search_restaurants(cuisine_group="desi", limit=5)
for p in desi:
    print(f"- {p['name']} | {p['cuisine']} | {p['address']} | {p['website'][:60]}")
assert len(desi) > 0, "expected at least one desi restaurant"
assert all("indian" in (p["name"] + " " + p["cuisine"]).lower() for p in desi), "results not desi-relevant"

print("\n== top american ==")
top = top_restaurants(cuisine_group="american", limit=3)
print("ranking_basis:", top["ranking_basis"])
for p in top["restaurants"]:
    print(f"- {p['name']} | {p['address']}")

print("\n== specials (best-effort) ==")
first = desi[0]["name"]
spec = todays_specials(first)
print(f"restaurant: {spec['restaurant']['name']}")
print(f"caveat: {spec['caveat']}")
print(f"specials lines: {len(spec['specials'])}")
print("\nALL SMOKE TESTS PASSED")
