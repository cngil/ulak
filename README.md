# Ulak

A configurable, print-first personal newspaper. RSS/Atom sources become a compact PDF with headlines, substantive summaries, dates and clickable sources. Choose your language, interests, model, page budget, newspaper style and printer. Runs on Python 3.11+ and Linux/CUPS.

## Quick start

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
./ulak --init
# Edit config.local.json.
./ulak --check-config
./ulak --no-print --open
```

English is the default. Use `--init --language tr` for the Turkish starter. Initialization never overwrites an existing profile. Without any local configuration, Ulak uses the generic English example. Install fontconfig (`fc-match`) and Liberation Sans/Serif fonts. Generated PDFs embed fonts and support Turkish characters. No API key is needed in publisher-summary mode.

```bash
./ulak --config examples/en.json --theme classic --no-print
./ulak --config examples/tr.json --theme gazette --pages 2 --news-count 12 --articles-per-page 6 --no-print
./ulak --list-themes
./ulak --check-model       # One small request; requires a configured model provider.
```

## Configuration

Settings resolve in this order: explicit `--config`, project `config.local.json`, `$XDG_CONFIG_HOME/ulak/config.json` (default `~/.config/ulak/config.json`), then `examples/en.json`. Local profiles are Git ignored. Examples are generic and contain no reader identity or private notes. JSON Schema validation catches unknown fields, invalid types, duplicate IDs and impossible page budgets before network requests. The schema also provides editor completion.

| Section | Controls |
| --- | --- |
| `language`, `timezone`, `hours` | Locale-driven output (bundled Turkish/English), time display, last 1-168 hours of published news |
| `name`, `branding` | Optional reader name, masthead, subtitle, tagline, footer |
| `sources` | RSS/Atom URL, display name, language, enabled state and stable ID |
| `interests` | Topic name, keywords, weight and required keyword groups |
| `exclude`, `preferred_publishers` | Unwanted phrases and source-name preferences |
| `max_articles`, `publisher_limit` | Total news count and source diversity |
| `layout` | Page budget, articles per page, columns and A4/Letter paper |
| `summary` | Publisher excerpts, local Ollama or OpenAI-compatible models; model, endpoint, parameters, word limit, editorial instructions, retries and timeout |
| `theme` | Compact, classic, gazette, or declarative theme file |
| `print` | Automatic submission, preferred printers, duplex, copies, color and quality |
| `storage` | Keep/discard, output directory, manifest and retention limits |
| `visuals` | No images, publisher photographs or ASCII renderings; grayscale and image count |
| `weather`, `puzzle` | Optional location weather and a daily Sudoku |

CLI overrides include `--language`, `--hours`, `--theme`, `--pages`, `--news-count`, `--articles-per-page`, `--columns`, `--provider`, `--model`, `--model-url`, `--printer`, `--copies`, and `--output`. The examples show every configuration field. Do not put API keys in JSON; use the named environment variable.

### News and summaries

Starter interests cover world affairs, technology, science, culture, business and the environment with equal weights. The English profile includes BBC sections, The Guardian and ScienceDaily; the Turkish profile includes BBC Türkçe and DW Türkçe. These are editable examples, not a personal reading profile or an endorsement. Add, disable or remove feeds in `sources`. Entries require a publication timestamp, an HTTP(S) article link and a meaningful abstract. Old, future-dated, undated and headline-only entries are skipped. Updated timestamps do not make old articles fresh. Google News aggregation lists are not article summaries.

Publisher mode uses the feed's own abstract, cleaned of markup and shortened at a sentence/word boundary. It is explicitly labeled as a publisher excerpt, not an independent full-article analysis. Feeds may contain mistakes, old content with new dates or promotional material; configure sources and exclusions accordingly. Article fetching is opt-in per source and bounded by publisher hosts and robots rules. Paywalls and login restrictions are never bypassed.

In publisher mode only feeds whose declared language matches the edition are fetched. Each starter lists feeds in its own language. `--language` changes labels, dates and source filtering; it does not translate user-defined interest names or keywords. Use the matching starter or multilingual keywords. Model mode translates titles and summaries to the edition language. Both starters use UTC, an empty reader name, disabled weather with no preset city, and no preferred printer or model. Set your timezone and location explicitly in your ignored local profile.

Interests are lexical preferences, not a psychological profile:

```json
{
  "id": "environment",
  "name": "Environment & research",
  "keywords": ["climate", "environment", "conservation", "research"],
  "weight": 1.5,
  "required_groups": [["climate", "environment"], ["policy", "research"]]
}
```

Keywords match headlines and abstracts, with more weight on headline matches. Every required group must match at least one member. Short acronyms use complete-word matches. Longer keywords match word prefixes, permitting suffixes while avoiding accidental interior matches. Selection promotes topic diversity, limits repeated publishers and removes similar headlines. This lexical stage is not semantic inference; optional model event grouping/ranking is described below. Neither mode is independent fact checking. Unmatched interests are reported in the JSON and terminal instead of filled with unrelated news.

### Model integration

`summary.provider` supports:

- `publisher`: no model/network inference; uses publisher abstracts.
- `ollama`: local model via [`POST /api/generate`](https://docs.ollama.com/api/generate).
- `openai_compatible`: hosted or local Chat Completions API via [`POST /chat/completions`](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create).

Example summary configuration for a hosted provider (merge into your profile):

```json
{
  "provider": "openai_compatible",
  "endpoint": "https://api.openai.com/v1",
  "model": "YOUR_MODEL_ID",
  "api_key_env": "OPENAI_API_KEY",
  "max_words": 80,
  "timeout_seconds": 90,
  "retries": 1,
  "parameters": {},
  "instructions": "Use concise newspaper prose. Preserve uncertainty and attribution."
}
```

Ollama uses an endpoint such as `http://127.0.0.1:11434`, an installed model name, and an empty `api_key_env` unless authentication is needed. Local OpenAI-compatible servers typically use `http://127.0.0.1:PORT/v1`. Remote endpoints must use HTTPS. No model is downloaded automatically. API keys are read from the environment only and never written to generated metadata. `parameters` passes provider-specific generation controls; reserved request fields cannot be overridden. Providers/models must support JSON output. Models that refuse, truncate or return invalid content are skipped with warnings. Rate limits and transient server failures use bounded retries.

Brief writing receives public source evidence: titles, feed abstracts and explicitly permitted extracted article text. Editorial ranking also receives configured interest keywords/weights and relevant previous public coverage. The prompt forbids inventing context and treats source text as untrusted data, but this does not guarantee factual accuracy. PDFs label model-written summaries. `--check-model` makes a small live request and can incur provider costs. Normal tests mock model HTTP requests; a passing test does not establish live provider access or summary quality. No hosted model requests are made until a model profile is configured and a generation/test command is run.

For Ollama, `summary.think` controls the top-level [thinking setting](https://docs.ollama.com/capabilities/thinking). Leave it `null` to use the model default, or set `false` for a supported non-thinking mode. Check the installed model's capabilities before choosing a value. This setting belongs beside `parameters`, not inside it. Structured briefs also send an explicit JSON schema to Ollama.

### Page count and newspaper style

```json
"layout": {"pages": 2, "articles_per_page": 5, "columns": 2, "paper_size": "A4"},
"max_articles": 10,
"theme": "gazette"
```

`pages` is a maximum, not a request for blank filler. Articles spread across up to that many pages; fewer eligible stories may produce fewer pages. Total news count must fit the configured page capacity. Up to 200 stories and 50 pages are allowed, with at most eight stories/four rows per page. One-column pages allow up to four stories. Gazette pages with a puzzle allow up to five stories per page. Use a larger page budget for longer summaries rather than squeezing more text into unreadable type.

- `compact`: small masthead, sans-serif headings and a restrained green accent.
- `classic`: serif headings with grayscale rules.
- `gazette`: black-and-white newspaper aesthetic, centered masthead, double rules, serif text, lead headline and column dividers.

Custom themes are declarative JSON. Copy a file from `themes/`, edit colors, fonts, margin, gap, body size and `layout` (`grid` or `newspaper`), then set its path relative to the config file. Theme files never execute code. Font names and dimensions are validated. Long text is visibly shortened rather than clipped; full and displayed summaries remain in the optional manifest.

### Printing

Printing uses [CUPS standard options](https://www.cups.org/doc/options.html). **Defaults never submit a print job.** Set `print.auto=true` to print whenever the generator runs, or pass `--print` explicitly. `--no-print` overrides automatic submission; `--dry-run-print` shows the command and never submits it. Ulak does not install a scheduler; use your preferred scheduler to invoke it periodically if needed.

Generated PDFs disable ASCII85 wrapping to avoid a [PDFio/libcupsfilters compound-stream regression](https://github.com/OpenPrinting/libcupsfilters/issues/301) that can otherwise produce blank sheets while reporting success. Existing PDFs with compound content/image filters are rejected before submission; regenerate older Ulak editions. When the CUPS PDF filter is installed, tests also verify that its output retains the page text.

```json
"print": {
  "auto": false,
  "printer": "",
  "printers": ["HomePrinter", "OfficePrinter"],
  "duplex": "manual",
  "copies": 1,
  "color": "monochrome",
  "quality": "normal",
  "reverse_backs": false
}
```

`printer` overrides the preference list. Otherwise Ulak picks the first preferred queue available in CUPS, or uses the CUPS default when no preferences are set. It does not broadcast to every printer or retry an uncertain submission on another printer. Copies are collated. `color` is `color` or `monochrome`; `quality` is `draft`, `normal`, `high`, or `efficient`. Efficient mode requests draft monochrome output. Printer/driver support determines whether requested quality, color and automatic duplex options are honored.

Duplex choices: `off`, `long-edge`, `short-edge`, or `manual`. Choose automatic duplex only when the selected printer advertises it; otherwise use manual passes or one-sided printing.

Manual duplex is two explicit passes over the **same saved edition**:

```bash
./ulak --no-print --output output/issue.pdf
./ulak --print-existing output/issue.pdf --print-pass front --dry-run-print
# When ready, replace --dry-run-print with --print.
# Reload the paper, then print the back pass without regenerating news:
./ulak --print-existing output/issue.pdf --print-pass back --print
```

The generated `.fronts.pdf` and `.backs.pdf` preserve sheet pairing, including a blank back for an odd final page. `reverse_backs` reverses back-pass sheet order. Feed orientation and order depend on the printer; test two pages first. Manual duplex requires persistent storage and one copy per pass. Repeat both passes for more copies. Automatic printing with manual duplex is rejected because a person must reload the paper.

### Storage and retention

```json
"storage": {
  "keep": true,
  "directory": "output/pdf",
  "save_manifest": true,
  "max_editions": 30,
  "max_files": 0,
  "max_age_hours": 720,
  "max_total_mb": 100
}
```

Directory paths are relative to the command's working directory unless absolute; `~` is expanded. Default filenames include date, time and language, so repeated editions are separate. `--output` supplies an explicit destination and can intentionally overwrite it.

Retention is applied to registered Ulak editions after successful processing, or using `./ulak --cleanup`. An edition is a bundle of the PDF, optional JSON and manual-duplex pass PDFs. Limits combine: newest bundles are kept only while age, edition count, physical file count and total size budgets permit. Zero disables a limit. Bundles are removed together, never partially. Index files are not counted toward the size/file budgets. If a single edition exceeds the configured budget it may be removed too; the command reports that outcome. Unmanaged files, old exports without registration, symlinks and paths outside the managed directory are not removed.

`keep=false` uses a temporary directory and discards it after processing, including after CUPS has accepted a job. It cannot be used with `--output`, `--open` or manual duplex, since those need a persistent file. A failed persistent print submission leaves the generated edition available for retry. CUPS may retain spool/job history independently of Ulak's file retention.

### Images, ASCII, weather, puzzles and branding

- `visuals.mode`: `none`, `images`, or `ascii`. Only selected publisher image URLs are downloaded, with size/time limits. `grayscale` and `max_images` control print-friendly illustrations. ASCII uses the actual image, not model-invented artwork. Missing/blocked images produce a warning, not a failed edition. No image requests are made in `none` mode.
- `weather`: opt in with a place label and latitude/longitude. A timestamped [Open-Meteo](https://open-meteo.com/en/docs) current-weather estimate is shown with attribution. Stale/unavailable reports are omitted. Weather sends configured coordinates to Open-Meteo; use city-level coordinates if precision is unnecessary. Check the provider's terms before commercial use.
- `puzzle`: enable `sudoku4` for a date-seeded 4×4 Sudoku with a unique solution. It appears on the final page; the optional manifest includes the solution. It requires no model or network request.
- `branding`: customize `title`, `subtitle`, `tagline`, and `footer`; empty optional fields use localized defaults. Branding is text-based, separate from publisher illustrations.

## Privacy and upgrading

Vault reading is disabled in starter profiles. Explicit interest suggestions read local note titles/headings; setting `personalization.vault_path` can also enable periodic suggestions. Raw notes are not sent to models. Feed requests go to configured publishers; optional images contact their hosting sites. Weather contacts Open-Meteo. Model requests send public evidence and, for editorial ranking, configured interest keywords/weights and relevant previous public coverage to the configured endpoint. Generated manifests include abstracts and source metadata, not credentials or the entire configuration.

Public history starts from a cleaned release snapshot with generic examples. Keep personal preferences in ignored local profiles; generated editions, model logs, SQLite journals and vault notes should remain outside version control. Git ignore rules do not remove files already committed, so review tracked files before publishing your own fork. There is no automatic publication or account connection.

## Development

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests -v
```

Tests cover configuration, RSS/Atom parsing, source/language/freshness filters, keyword matching, summary adapters, model failure behavior, printer command construction, manual duplex pairing, retention boundaries, weather freshness, Sudoku uniqueness and PDF page/layout behavior. Physical jobs are never submitted by tests. Runtime libraries are in `requirements.txt`; no API/model SDK is required.


## Editorial editions

The pipeline now collects candidates, groups coverage of an event, ranks it, applies reading/history preferences, writes a brief, allocates page space, and preflights print jobs. `editorial.mode` selects `heuristic` or `model`. Heuristic mode is deterministic and uses keyword relevance, configured source quality, and headline similarity. Model mode uses the configured summary model for semantic grouping and scores for relevance, significance and novelty. Unknown candidate IDs, malformed scores and unsupported update claims are rejected; ranking failures produce an explicit heuristic fallback warning.

```json
"editorial": {
  "mode": "model",
  "candidate_limit": 40,
  "discovery_count": 1,
  "reading_minutes": 5,
  "words_per_minute": 180,
  "cluster_threshold": 0.55,
  "structured_briefs": true,
  "brief_retries": 1,
  "introduction_every": 3,
  "concept": true,
  "cache": true
}
```

Model ranking needs a model provider; `publisher` mode cannot perform semantic inference. `quality` is a per-source preference between zero and one, not a certification of accuracy. Event grouping stores alternate coverage in the manifest and counts distinct publishers. A small discovery quota can include an unexpected story outside the configured keywords; exclusions, publisher limits and history still apply. Page capacity and estimated reading time both cap selection. Reading estimates are approximate, based on words and configured speed.

Structured model briefs contain `what_happened`, optional `context` and optional `why_it_matters`. Every non-empty field, including the headline, requires an exact supporting quote from the supplied evidence. Invented quotes, missing references and new numeric claims are rejected. Unsupported context/consequences should be left empty. Document IDs keep multiple articles from the same publisher distinct. The manifest records the evidence and source URLs. Quote checking establishes traceability, not logical entailment or independent fact verification; a model can still misinterpret its evidence. Publisher mode remains an explicitly labeled source excerpt.

Brief prompts offer original source headlines and verbatim source sentences as quote choices; Ollama's schema constrains quotes to those choices. Numeric validation includes both the supplied headline and body, so a product version stated only in a headline remains valid evidence. `editorial.brief_retries` permits zero to two repair attempts after content validation fails (default one), using the original evidence and validation error. Repairs never relax evidence validation. Network retries remain separately controlled by `summary.retries`.

Set a source's `allow_article_fetch=true` only when you want article extraction. By default only its feed host is allowed; `article_hosts` can explicitly list publisher domains when feeds and articles use different hosts. Robots denial, missing robots data, extraction failures, cross-host redirects or insufficient text retain the feed abstract with a warning where appropriate. No authentication/cookie bypass is attempted. Original evidence is bounded and never includes private notes.

### Memory, feedback and reviewable interests

```json
"memory": {
  "enabled": true,
  "directory": ".state",
  "repeat_days": 7,
  "max_issues": 100,
  "max_cache_entries": 500
}
```

Memory is local SQLite, enabled in the original reader's local profile and opt-in in generic starter profiles. It is separate from PDF retention: `storage.keep=false` discards edition files but does not erase an explicitly enabled history database. Disable memory too for stateless operation. Model caches are bounded and keyed by evidence, language, model and generation settings. The database retains at most the configured ready issue count; explicit feedback and print fingerprints persist independently.

Repeats with only a new timestamp or paraphrase are suppressed. Substantial later developments can appear as a labeled update. In model mode, previous evidence is included for comparison, and updates require a new quote from the current abstract. Heuristic update detection can make mistakes; inspect the manifest when tuning thresholds.

```bash
./ulak --preview --no-print --open   # Render without consuming an issue number or recording seen stories
./ulak history
./ulak feedback UL-20261031-0001 3 more
./ulak feedback UL-20261031-0001 3 less
./ulak feedback UL-20261031-0001 3 hide
./ulak feedback UL-20261031-0001 3 reset
./ulak preferences
```

Issue IDs and story numbers are printed on paper. More/less apply bounded, inspectable topic multipliers; hide suppresses the selected article URL; reset removes that vote. Preview files are labeled and cannot be printed through the preview command. Their IDs are provisional; feedback applies to recorded issues. Failed drafts can leave gaps in the issue sequence. History describes generated editions, not proof that a physical sheet reached the reader.

`./ulak suggest-interests /path/to/vault` creates a reviewable proposal from allowed note titles/headings. It does not modify the profile or send notes to a model. Sensitive categories and paths outside the allowed technology/media/education folders are excluded. Optional `personalization.vault_path` plus `suggest_every` writes a proposal sidecar periodically, which participates in edition retention. Its suggestion labels come from the locale catalog; keywords are editable data in `data/interest_topics.json`.

### Page allocation and paper references

`layout.adaptive=true` assigns row heights using headline length, brief length and editorial priority; lead stories and short briefs receive different word budgets. It retains readable minimum type sizes and explicit ellipses when a requested density exceeds the available text area. Stable interest labels act as section names. Issue numbers, occasional factual count/topic introductions, localized daily concepts and date-seeded rotating Sudoku/anagram puzzles give editions a consistent identity. Extras yield to crowded news pages instead of squeezing the article text.

`references.domains=true` prints numbered references and readable publisher domains. Optional `references.qr=true` prints a source-article QR code where the box has enough room. QR codes link directly to public article URLs; no private archive is published. Source/code mappings and alternate coverage remain in the manifest. QR codes may replace illustrations in a constrained box; test scanability on your printer.

### Print preflight and duplicate protection

`print.preflight=true` runs the installed CUPS PDF filter without submitting a job, checks page/text preservation, and rasterizes each page when Poppler is available to detect blank output. The report includes sheet count, approximate dark-pixel coverage per page and the darkest quadrant. These are print-density proxies, not measured ink consumption. Missing CUPS/Poppler tools are reported through unavailable fields rather than falsely claiming a full filter test. Dry-run printing performs this preflight too.

Actual submissions verify advertised duplex, paper and color capabilities before calling `lp`. With memory enabled and `print.prevent_duplicates=true`, a durable claim prevents repeating the same PDF/options/pass, including an uncertain failed submission. `--force-reprint` is the explicit escape hatch for an intentional extra copy. Pending claims are retained after ambiguous failures to avoid duplicate sheets. This does not monitor the physical output or detect empty ink tanks. Test paper orientation for manual duplex as before.

## Languages and contributions

Reader-facing localization is stored in `locales/<code>.json`; application Python has no Turkish/English UI branches. Catalogs cover PDF copy, dates, weather, puzzle words, concepts, section labels and model target-language metadata. Locale files are discovered automatically:

```bash
./ulak --list-languages
./ulak --check-locales
```

Missing keys, empty translations and broken format placeholders fail validation instead of mixing languages silently. Technical CLI diagnostics and developer instructions remain English. User-defined interests, branding and publisher content are data, not automatically translated UI. A language-specific example profile is optional; if absent, initialization uses the generic English configuration with translated section labels, ready for the contributor to configure appropriate feeds/keywords. Fonts must cover the target script.

See [CONTRIBUTING.md](CONTRIBUTING.md) for adding a locale or source, and for the editorial/print test workflow.
