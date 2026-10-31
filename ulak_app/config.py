from __future__ import annotations

import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from jsonschema import Draft202012Validator, FormatChecker
from .i18n import get_locale, DEFAULT_LANGUAGE

ROOT = Path(__file__).resolve().parent.parent


class ConfigError(ValueError):
    pass


def example(language=DEFAULT_LANGUAGE):
    locale=get_locale(language)
    path=ROOT/'examples'/f'{language}.json'
    config=json.loads((path if path.exists() else ROOT/'examples/en.json').read_text())
    config['language']=language
    if not path.exists():
        config['timezone']=locale['default_timezone']
        for interest in config['interests']:
            interest['name']=locale['interest_names'].get(interest['id'],interest['name'])
    return config


def default_path():
    local = ROOT / "config.local.json"
    user = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "ulak/config.json"
    return local if local.exists() else user if user.exists() else ROOT / 'examples' / f'{DEFAULT_LANGUAGE}.json'


def validate(config):
    schema = json.loads((ROOT / "config.schema.json").read_text())
    errors = sorted(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(config),
                    key=lambda error: str(list(error.path)))
    if errors:
        error = errors[0]
        raise ConfigError(f'{".".join(map(str, error.path)) or "config"}: {error.message}')
    try: get_locale(config['language'])
    except ValueError as exc: raise ConfigError(str(exc)) from exc
    try:
        ZoneInfo(config["timezone"])
    except ZoneInfoNotFoundError as exc:
        raise ConfigError(f'Unknown timezone: {config["timezone"]}') from exc
    for key in ("sources", "interests"):
        ids = [item["id"] for item in config[key]]
        if len(ids) != len(set(ids)):
            raise ConfigError(f"{key}: ids must be unique")
    if not any(source["enabled"] for source in config["sources"]):
        raise ConfigError("sources: enable at least one source")
    layout = config["layout"]
    if config["max_articles"] > layout["pages"] * layout["articles_per_page"]:
        raise ConfigError("max_articles exceeds page capacity; increase layout.pages or articles_per_page")
    if layout["articles_per_page"] > 4*layout["columns"]:
        raise ConfigError("layout.articles_per_page: allow at most four rows per page; increase columns or pages")
    if config["theme"] == "gazette" and config["puzzle"]["enabled"] and layout["articles_per_page"] > 5:
        raise ConfigError("Gazette with a puzzle supports at most 5 articles per page; increase pages instead")
    if config["print"]["auto"] and config["print"]["duplex"] == "manual":
        raise ConfigError("Automatic printing cannot do manual duplex; use explicit front/back passes")
    from urllib.parse import urlparse
    for source in config["sources"]:
        if urlparse(source["url"]).scheme not in ("http", "https"):
            raise ConfigError(f'sources.{source["id"]}.url: use http or https')
    if config["summary"]["provider"] != "publisher":
        endpoint = urlparse(config["summary"]["endpoint"])
        if endpoint.scheme not in ("http", "https") or not endpoint.hostname:
            raise ConfigError("summary.endpoint: use an HTTP(S) model endpoint")
        if endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
            raise ConfigError("summary.endpoint: credentials and query parameters are not allowed; use api_key_env")
        if endpoint.scheme != "https" and endpoint.hostname not in ("localhost", "127.0.0.1", "::1"):
            raise ConfigError("summary.endpoint: use HTTPS for remote model servers")
        if not config["summary"].get("model"):
            raise ConfigError("summary.model: required for model summaries")
        reserved = {"model", "messages", "prompt", "system", "stream", "format", "response_format"}
        if reserved.intersection(config["summary"]["parameters"]):
            raise ConfigError("summary.parameters cannot override the request model, prompt, or response format")
    return config


def load(path=None):
    path = path or default_path()
    try:
        config = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Cannot read config {path}: {exc}") from exc
    if not isinstance(config,dict): raise ConfigError('Configuration must be a JSON object')
    if not isinstance(config.get('language',DEFAULT_LANGUAGE),str): raise ConfigError('language must be a locale code string')
    # Add new optional feature sections without changing existing reader choices.
    defaults=example(config.get('language',DEFAULT_LANGUAGE))
    for section in ('editorial','memory','references','personalization'):
        config.setdefault(section,defaults[section])
        if isinstance(config[section],dict):
            for key,value in defaults[section].items(): config[section].setdefault(key,value)
    for section,keys in (('layout',('adaptive',)),('print',('preflight','prevent_duplicates'))):
        if isinstance(config.get(section),dict):
            for key in keys: config[section].setdefault(key,defaults[section][key])
    return validate(config)


def load_theme(name):
    # Files are declarative data, never executable template code.
    path = ROOT / "themes" / f"{name}.json" if name in ("compact", "classic", "gazette") else Path(name)
    try:
        theme = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Cannot read theme {name}: {exc}") from exc
    import re
    if not isinstance(theme, dict) or set(theme) != {"name", "accent", "heading_font", "body_font", "body_size", "gap", "margin", "layout"}:
        raise ConfigError("Theme must define name, accent, heading_font, body_font, body_size, gap, margin, layout")
    if theme["heading_font"] not in ("NewsBold", "UIBold"):
        raise ConfigError("Theme heading_font must be NewsBold or UIBold")
    if theme["body_font"] not in ("News", "UI") or theme["layout"] not in ("grid", "newspaper"):
        raise ConfigError("Theme body_font must be News or UI; layout must be grid or newspaper")
    if not isinstance(theme["accent"], str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", theme["accent"]):
        raise ConfigError("Theme accent must be a #RRGGBB color")
    for field, low, high in (("body_size", 8.5, 11), ("gap", 12, 28), ("margin", 28, 42)):
        if type(theme[field]) not in (int, float) or not low <= theme[field] <= high:
            raise ConfigError(f"Theme {field} must be between {low} and {high}")
    return theme
