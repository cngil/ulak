"""Publisher abstracts by default; optional local model for rewriting/translation."""
from __future__ import annotations

from difflib import SequenceMatcher
import json
import os
import re
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, HTTPRedirectHandler, build_opener

from .feeds import clean, normalized
from .i18n import get_locale
from .prompts import render_prompt, preferences


class NoModelRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        # Never forward a bearer token to a different destination through redirects.
        return None


def urlopen(request, timeout):
    return build_opener(NoModelRedirect()).open(request, timeout=timeout)


def credentials(settings):
    variable = settings["api_key_env"]
    if variable and not os.environ.get(variable):
        raise ValueError(f"Set the {variable} environment variable for the configured model")
    return os.environ.get(variable, "") if variable else ""


def model_request(settings, system, prompt, schema=None):
    headers = {"Content-Type": "application/json"}
    key = credentials(settings)
    if key:
        headers["Authorization"] = "Bearer " + key
    if settings["provider"] == "ollama":
        payload = dict(model=settings["model"], system=system, prompt=prompt,
                       stream=False, format=schema or "json", options=settings["parameters"])
        if settings.get('think') is not None:
            payload['think']=settings['think']
        endpoint = settings["endpoint"].rstrip("/") + "/api/generate"
    else:
        payload = dict(model=settings["model"], messages=[{"role": "system", "content": system},
                       {"role": "user", "content": prompt}], stream=False,
                       response_format={"type": "json_object"}, **settings["parameters"])
        endpoint = settings["endpoint"].rstrip("/") + "/chat/completions"
    request = Request(endpoint, data=json.dumps(payload).encode(), headers=headers)
    for attempt in range(settings["retries"]+1):
        try:
            with urlopen(request, timeout=settings["timeout_seconds"]) as response:
                raw = response.read(100_001)
            break
        except HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == settings["retries"]:
                # Do not echo provider response bodies or authentication headers.
                raise ValueError(f"Model HTTP {exc.code}; check model, endpoint, credentials and quota") from None
        except (URLError, TimeoutError):
            if attempt == settings["retries"]:
                raise ValueError("Model endpoint unavailable or timed out") from None
        time.sleep(min(2**attempt, 4))
    if len(raw) > 100_000:
        raise ValueError("Model response exceeds size limit")
    outer = json.loads(raw)
    if settings["provider"] == "ollama":
        if outer.get("done") is not True:
            raise ValueError("Model did not finish generating")
        if outer.get('done_reason') not in (None,'stop'):
            raise ValueError('Model stopped before completing its response; increase output budget')
        content = outer.get("response")
    else:
        choices = outer.get("choices", [])
        if not choices or choices[0].get("finish_reason") != "stop":
            raise ValueError("Model refused, truncated, or did not finish generating")
        content = choices[0].get("message", {}).get("content")
    if not isinstance(content, str):
        raise ValueError("Model returned no JSON content")
    return json.loads(content)


def shorten(text, words):
    parts = text.split()
    if len(parts) <= words:
        return text
    clipped = " ".join(parts[:words])
    sentences = re.split(r"(?<=[.!?])\s+", clipped)
    complete = " ".join(sentences[:-1])
    if len(complete.split()) >= words * .6:
        return complete
    return clipped.rstrip(".,;:") + "..."


def summarize(item, config):
    settings = config["summary"]
    if settings["provider"] == "publisher":
        return dict(item, summary=shorten(item["source_text"], settings["max_words"]),
                    summary_kind="publisher_excerpt")
    language = get_locale(config['language'])['language_name']
    system = render_prompt('summary', language=language, max_words=settings['max_words'],
                           editorial_preferences=preferences(settings['instructions']))
    result = model_request(settings, system, json.dumps(
        {"title": item["title"], "abstract": item["source_text"]}, ensure_ascii=False))
    if not isinstance(result, dict) or any(not isinstance(result.get(k), str) for k in ("title", "summary")):
        raise ValueError("Model must return title and summary strings")
    title, summary = clean(result["title"]), clean(result["summary"])
    if not title or len(title) > 300 or len(summary.split()) < 12:
        raise ValueError("Model returned empty/insufficient text")
    if SequenceMatcher(None, normalized(title), normalized(summary)).ratio() > .85:
        raise ValueError("Model repeated the title instead of summarizing")
    return dict(item, original_title=item["title"], title=title,
                summary=shorten(summary, settings["max_words"]), summary_kind="model_summary")
