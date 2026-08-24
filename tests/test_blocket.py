import datetime
import unittest

import blocket
import monitor

FIXTURE = """
<html><body>
<section aria-labelledby="results-heading">
<div class="grid">
<article class="relative isolate sf-search-ad card">
  <a class="sf-search-ad-link" href="https://www.blocket.se/recommerce/forsale/item/25883058" id="25883058"></a>
  <div><img class="sf-ad-carousel-desktop-item--active" src="https://images.blocketcdn.se/dynamic/default/item/25883058/abc.jpg" alt=""/></div>
  <div class="m-8 mt-4 mb-8 sm:mb-16">
    <div>
      <div class="flex justify-between"><span>1 500 kr</span></div>
      <h2 class="h4" id="search-ad-25883058-1">TaylorMade SIM2 Max 3-wood golfklubba</h2>
    </div>
    <div class="flex flex-col">
      <div class="text-xs s-text-subtle flex justify-between flex-wrap mt-4 sm:mt-8">
        <span class="whitespace-nowrap truncate mr-8">Stockholm</span><span class="whitespace-nowrap">1 dag</span>
      </div>
    </div>
  </div>
</article>
<article class="relative isolate sf-search-ad card">
  <a class="sf-search-ad-link" href="https://www.blocket.se/recommerce/forsale/item/25879930" id="25879930"></a>
  <div><img src="https://images.blocketcdn.se/dynamic/default/item/25879930/def.jpg" alt=""/></div>
  <div>
    <div><span>500 kr</span></div>
    <h2>Trasig putter reservdelar</h2>
    <div><span class="whitespace-nowrap truncate mr-8">Göteborg</span><span class="whitespace-nowrap">25 min</span></div>
  </div>
</article>
</div>
</section>
</body></html>
"""


class TestBuildUrl(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(
            blocket.build_search_url("Cobra King F8"),
            "https://www.blocket.se/recommerce/forsale/search?q=Cobra+King+F8",
        )

    def test_with_price(self):
        url = blocket.build_search_url("golfklubba", max_price=1000)
        self.assertIn("price_to=1000", url)
        self.assertIn("q=golfklubba", url)


class TestParse(unittest.TestCase):
    def test_parses_cards(self):
        listings = blocket.parse_listings(FIXTURE)
        self.assertEqual(len(listings), 2)

        first = listings[0]
        self.assertEqual(first["ad_id"], "25883058")
        self.assertEqual(first["title"], "TaylorMade SIM2 Max 3-wood golfklubba")
        self.assertEqual(first["price"], 1500)
        self.assertEqual(first["location"], "Stockholm")
        self.assertEqual(first["published_text"], "1 dag")
        self.assertTrue(first["image_url"].startswith("https://images.blocketcdn.se"))
        self.assertIn("25883058", first["url"])

    def test_raises_when_no_cards(self):
        with self.assertRaises(blocket.ParseError):
            blocket.parse_listings("<html><body>helt annan sida</body></html>")


class TestTime(unittest.TestCase):
    def test_relative(self):
        now = datetime.datetime(2026, 8, 20, 12, 0, 0)
        self.assertEqual(blocket.parse_published("25 min", now), now - datetime.timedelta(minutes=25))
        self.assertEqual(blocket.parse_published("4 tim", now), now - datetime.timedelta(hours=4))
        self.assertEqual(blocket.parse_published("2 dagar", now), now - datetime.timedelta(days=2))
        self.assertEqual(blocket.parse_published("1 vecka", now), now - datetime.timedelta(weeks=1))

    def test_date(self):
        now = datetime.datetime(2026, 8, 20, 12, 0, 0)
        self.assertEqual(
            blocket.parse_published("18 aug", now),
            datetime.datetime(2026, 8, 18, 0, 0, 0),
        )


class TestFilters(unittest.TestCase):
    def setUp(self):
        self.search = {
            "exclude_words": ["trasig", "reservdelar"],
            "location": "stockholm",
            "max_price": 1000,
        }

    def test_exclude_word(self):
        listing = {"title": "Trasig golfklubba", "location": "Stockholm", "price": 500}
        self.assertFalse(monitor.filter_listing(listing, self.search))

    def test_exclude_prefix_variant(self):
        listing = {"title": "Golfklubba sökes", "location": "Stockholm", "price": 500}
        search = {**self.search, "exclude_words": ["sökes"]}
        self.assertFalse(monitor.filter_listing(listing, search))

    def test_exclude_word_boundary(self):
        listing = {"title": "Mobiltelefon", "location": "Stockholm", "price": 500}
        search = {**self.search, "exclude_words": ["bil"]}
        self.assertTrue(monitor.filter_listing(listing, search))

    def test_location_contains(self):
        listing = {"title": "Golfklubba", "location": "Stockholms län", "price": 500}
        self.assertTrue(monitor.filter_listing(listing, self.search))

    def test_location_mismatch(self):
        listing = {"title": "Golfklubba", "location": "Malmö", "price": 500}
        self.assertFalse(monitor.filter_listing(listing, self.search))

    def test_price_above_max(self):
        listing = {"title": "Golfklubba", "location": "Stockholm", "price": 1500}
        self.assertFalse(monitor.filter_listing(listing, self.search))

    def test_passes(self):
        listing = {"title": "Cobra golfklubba", "location": "Stockholm", "price": 900}
        self.assertTrue(monitor.filter_listing(listing, self.search))


if __name__ == "__main__":
    unittest.main()
