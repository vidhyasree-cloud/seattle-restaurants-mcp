"""Yelp provider test with a stubbed HTTP client (no real key, no network)."""
import os
import sys

sys.path.insert(0, ".")
import server

CANNED = {
    "businesses": [
        {
            "name": "B Closed Place", "is_closed": True, "rating": 5.0,
            "review_count": 900, "price": "$$",
            "categories": [{"title": "Indian"}],
            "location": {"display_address": ["1 Closed St", "Seattle, WA 98101"]},
            "display_phone": "(206) 000-0000", "url": "https://yelp.com/b-closed",
        },
        {
            "name": "A Top Spot", "is_closed": False, "rating": 4.5,
            "review_count": 1200, "price": "$$",
            "categories": [{"title": "Indian"}, {"title": "Pakistani"}],
            "location": {"display_address": ["10 Main St", "Seattle, WA 98101"]},
            "display_phone": "(206) 111-1111", "url": "https://yelp.com/a-top",
        },
        {
            "name": "C Quiet Gem", "is_closed": False, "rating": 4.5,
            "review_count": 300, "price": "$",
            "categories": [{"title": "Indian"}],
            "location": {"display_address": ["20 Pine St", "Seattle, WA 98101"]},
            "display_phone": "(206) 222-2222", "url": "https://yelp.com/c-quiet",
        },
    ]
}


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self):
        self.seen = {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, params=None, headers=None, timeout=None):
        self.seen = {"url": url, "params": params, "auth": headers.get("Authorization")}
        return FakeResponse(CANNED)


fake = FakeClient()
server._client = lambda: fake  # noqa: E731 — stub the transport
os.environ["YELP_API_KEY"] = "test-key-not-real"

places = server.search_restaurants(cuisine_group="desi", limit=5)
assert fake.seen["url"] == "https://api.yelp.com/v3/businesses/search", fake.seen
assert fake.seen["auth"] == "Bearer test-key-not-real", fake.seen
assert fake.seen["params"]["categories"] == "indian,pakistani,himalayan", fake.seen
assert fake.seen["params"]["location"] == "Seattle, WA", fake.seen
names = [p["name"] for p in places]
assert names == ["A Top Spot", "C Quiet Gem"], names  # closed filtered; rating desc, reviews tiebreak
assert places[0]["rating"] == 4.5 and places[0]["review_count"] == 1200
assert places[0]["price"] == "$$" and places[0]["source"] == "yelp-fusion"
assert "Indian" in places[0]["cuisine"] and "Pakistani" in places[0]["cuisine"]

top = server.top_restaurants(cuisine_group="desi", limit=2)
assert top["ranking_basis"] == "Yelp rating (tiebreak: review count)", top["ranking_basis"]

# No key -> Yelp skipped cleanly
del os.environ["YELP_API_KEY"]
assert server._yelp_search("desi", None, None, 5) is None
print("ALL YELP MOCK TESTS PASSED")
