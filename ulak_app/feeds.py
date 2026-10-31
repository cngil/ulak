from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from html import unescape
from html.parser import HTMLParser
import re
import unicodedata
from urllib.parse import urlparse, urljoin
from urllib.request import Request, urlopen

import feedparser


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self.hidden, self.images = [], 0, []

    def handle_starttag(self, tag, attrs):
        if tag == "img":
            self.images.append(dict(attrs).get("src", ""))
        if tag in ("script", "style"):
            self.hidden += 1
        elif tag in ("p", "div", "br", "li"):
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.hidden = max(0, self.hidden - 1)
        elif tag in ("p", "div", "li"):
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def clean(value):
    parser = PlainText()
    parser.feed(value or "")
    return re.sub(r"\s+", " ", unescape("".join(parser.parts))).strip()


def normalized(value):
    return re.sub(r"[^\w ]", "", value.casefold())


def contains(text, keyword):
    fold = lambda value: ''.join(c for c in unicodedata.normalize('NFKD', value.casefold()) if not unicodedata.combining(c))
    text, keyword = fold(text), fold(keyword)
    # Short acronyms such as AI must not match "said" or "daily".
    if len(keyword) <= 3:
        return re.search(r"(?<!\w)" + re.escape(keyword) + r"(?!\w)", text) is not None
    # Permit suffixes (oyun -> oyuncular) but not words such as kamuoyuna.
    return re.search(r"(?<!\w)" + re.escape(keyword), text) is not None


def read_url(url, timeout=18, limit=2_000_000):
    request = Request(url, headers={"User-Agent": "Ulak/0.2 (personal RSS reader)"})
    with urlopen(request, timeout=timeout) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Response exceeds size limit")
    return data


def parse_feed(data, source, now, hours):
    feed = feedparser.parse(data)
    if not feed.entries and feed.get("bozo"):
        raise ValueError("Invalid RSS/Atom feed")
    items = []
    for entry in feed.entries:
        # Updated timestamps can turn old stories into today's news; use publication only.
        stamp = entry.get("published_parsed") or entry.get("created_parsed")
        if not stamp:
            continue
        try:
            published = datetime(*stamp[:6], tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
        if not now - timedelta(hours=hours) <= published <= now:
            continue
        title = clean(entry.get("title", ""))
        link = urljoin(source["url"], entry.get("link", ""))
        if not title or not entry.get("link") or urlparse(link).scheme not in ("http", "https"):
            continue
        body = clean(entry.get("summary", ""))
        images = entry.get("media_thumbnail", []) or entry.get("media_content", [])
        image_url = images[0].get("url", "") if images else ""
        if not image_url:
            html = PlainText()
            html.feed(entry.get("summary", ""))
            image_url = next((u for u in html.images if u), "")
        # Avoid Google News lists masquerading as article summaries.
        if urlparse(source["url"]).hostname == "news.google.com":
            body = ""
        title_key = normalized(title)
        if normalized(body).startswith(title_key):
            body = body[len(title):].lstrip(" .:-")
        if SequenceMatcher(None, normalized(body), title_key).ratio() > .8:
            body = ""
        # No headline-only filler when a publisher provides no meaningful abstract.
        if len(body.split()) < 12:
            continue
        items.append(dict(title=title, source=source["name"], source_id=source["id"],
                          language=source["language"], url=link, published=published.isoformat(),
                          source_text=body[:8000], source_text_kind="publisher_feed_summary",
                          image_url=urljoin(source["url"], image_url) if image_url else ""))
    return items


def collect(config, now):
    sources = [s for s in config["sources"] if s["enabled"]]
    warnings = []
    if config["summary"]["provider"] == "publisher":
        sources = [s for s in sources if s["language"] == config["language"]]
        if not sources:
            warnings.append("No enabled sources match the edition language; configure sources or use Ollama translation")

    def fetch(source):
        try:
            return parse_feed(read_url(source["url"]), source, now, config["hours"]), None
        except Exception as exc:
            return [], f'{source["id"]}: {type(exc).__name__}: {exc}'

    items = []
    with ThreadPoolExecutor(max_workers=5) as pool:
        for batch, error in pool.map(fetch, sources):
            items.extend(batch)
            if error:
                warnings.append(error)
    return items, warnings


def rank_candidates(items, config):
    preferred = {s.casefold() for s in config["preferred_publishers"]}
    candidates = []
    for item in items:
        headline = item["title"].casefold()
        text = (headline + " " + item["source_text"].casefold())
        if any(contains(text, word) for word in config["exclude"]):
            continue
        matches = []
        for interest in config["interests"]:
            if any(not any(contains(text, word) for word in group)
                   for group in interest.get("required_groups", [])):
                continue
            score = sum(3 if contains(headline, word) else 1
                        for word in interest["keywords"] if contains(text, word))
            if score:
                matches.append((score * interest["weight"], interest))
        if not matches:
            continue
        score, interest = max(matches, key=lambda match: match[0])
        candidates.append(dict(item, score=score, interest_id=interest["id"], topic=interest["name"],
                               preferred=item["source"].casefold() in preferred))
    return sorted(candidates, key=lambda item: (item["preferred"], item["score"], item["published"]), reverse=True)


def choose(candidates, config):
    selected, publishers, topics = [], {}, {}
    # Round robin passes encourage coverage without reserving empty topic boxes.
    for per_topic in range(1, config["max_articles"] + 1):
        for item in candidates:
            if topics.get(item["interest_id"], 0) >= per_topic:
                continue
            if publishers.get(item["source_id"], 0) >= config["publisher_limit"]:
                continue
            if any(item["url"] == old["url"] or SequenceMatcher(
                None, normalized(item["title"]), normalized(old["title"])).ratio() > .68
                   for old in selected):
                continue
            selected.append(item)
            publishers[item["source_id"]] = publishers.get(item["source_id"], 0) + 1
            topics[item["interest_id"]] = topics.get(item["interest_id"], 0) + 1
            if len(selected) == config["max_articles"]:
                return selected
    return selected
