from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from ulak_app.config import example
from ulak_app.feeds import choose, clean, collect, contains, parse_feed, rank_candidates

NOW = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
BODY = "Researchers published a detailed new space study. The telescope measured distant stars over several nights and compared the results with previous observations."
SOURCE = dict(id="test", name="Test", url="https://example.org/feed", language="en", enabled=True)


def rss(rows):
    return ('<rss><channel>' + ''.join(
        f'<item><title>{title}</title><pubDate>{stamp}</pubDate><link>{url}</link><description>{body}</description></item>'
        for title, stamp, url, body in rows) + '</channel></rss>').encode()


class FeedTests(unittest.TestCase):
    def test_short_acronyms_do_not_match_inside_words(self):
        self.assertFalse(contains('daily news said today', 'AI'))
        self.assertTrue(contains('new ai software', 'AI'))
        self.assertFalse(contains('kamuoyuna açıklama yapıldı', 'oyun'))
        self.assertTrue(contains('oyuncular için yeni oyunlar', 'oyun'))
        self.assertTrue(contains('İNTEL işlemci üretiyor', 'intel'))

    def test_freshness_links_and_no_headline_only_filler(self):
        data = rss([
            ("New study", "Fri, 30 Oct 2026 10:00:00 GMT", "https://example.org/a", BODY),
            ("Old", "Wed, 28 Oct 2026 10:00:00 GMT", "https://example.org/b", BODY),
            ("Future", "Sat, 31 Oct 2026 10:00:00 GMT", "https://example.org/c", BODY),
            ("Unsafe", "Fri, 30 Oct 2026 10:00:00 GMT", "javascript:alert(1)", BODY),
            ("Title only", "Fri, 30 Oct 2026 10:00:00 GMT", "https://example.org/d", "Title only"),
            ("Undated", "", "https://example.org/e", BODY),
        ])
        self.assertEqual([i["title"] for i in parse_feed(data, SOURCE, NOW, 24)], ["New study"])

    def test_atom_content_and_publication_date(self):
        data = f'''<feed xmlns="http://www.w3.org/2005/Atom"><title>Feed</title>
          <entry><title>Study</title><link href="https://example.org/a"/>
          <published>2026-10-30T10:00:00Z</published><summary>{BODY}</summary></entry></feed>'''
        self.assertEqual(parse_feed(data, SOURCE, NOW, 24)[0]["source_text"], BODY)

    def test_google_aggregator_descriptions_rejected(self):
        source = dict(SOURCE, url="https://news.google.com/rss/search?q=test")
        data = rss([("A story", "Fri, 30 Oct 2026 10:00:00 GMT", "https://example.org/a", BODY)])
        self.assertEqual(parse_feed(data, source, NOW, 24), [])

    def test_clean_removes_active_markup_and_preserves_words(self):
        self.assertEqual(clean('<p>Hello</p><p>world</p><script>secret()</script> &amp; all'), 'Hello world & all')

    def test_required_groups_and_keyword_score(self):
        config = example("en")
        config["interests"] = [dict(id="study", name="Study", keywords=["grant"], weight=2,
                                    required_groups=[["europe"], ["university"]])]
        base = dict(source="Test", source_id="test", published=NOW.isoformat(), source_text=BODY)
        items = [dict(base, title="Europe grant crisis", url="https://a/1"),
                 dict(base, title="Europe university grant", url="https://a/2")]
        self.assertEqual([i["url"] for i in rank_candidates(items, config)], ["https://a/2"])

    def test_diversity_duplicates_and_publisher_limit(self):
        config = dict(max_articles=3, publisher_limit=1)
        a = dict(title="Linux kernel release announced", url="https://a/1", interest_id="open", source_id="a")
        duplicate = dict(a, title="Linux kernel release announced!", url="https://b/1", source_id="b")
        other = dict(title="A telescope discovers distant stars", url="https://c/1", interest_id="science", source_id="c")
        capped = dict(title="A new privacy law", url="https://a/2", interest_id="open", source_id="a")
        self.assertEqual(choose([a, duplicate, other, capped], config), [a, other])

    def test_source_failure_does_not_discard_healthy_feed(self):
        config = example("en")
        config["sources"] = [SOURCE, dict(SOURCE, id="bad", url="https://bad.invalid/feed")]
        data = rss([("Study", "Fri, 30 Oct 2026 10:00:00 GMT", "https://example.org/a", BODY)])
        def read(url):
            if 'bad.invalid' in url:
                raise OSError("offline")
            return data
        with patch('ulak_app.feeds.read_url', side_effect=read):
            items, warnings = collect(config, NOW)
        self.assertEqual(len(items), 1)
        self.assertEqual(len(warnings), 1)

    def test_publisher_mode_filters_foreign_language_without_fetching(self):
        config = example("tr")
        config["sources"] = [SOURCE]
        with patch('ulak_app.feeds.read_url') as fetch:
            items, warnings = collect(config, NOW)
        fetch.assert_not_called()
        self.assertEqual(items, [])
        self.assertTrue(warnings)
