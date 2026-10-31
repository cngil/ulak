# Contributing to Ulak

Keep developer-facing code, comments and diagnostics in English. Put reader-facing translations in locale catalogs, not conditionals in Python. Keep example profiles generic and personal settings in ignored local files.

## Add a language

1. Copy `locales/en.json` to `locales/<code>.json`. Codes accept two/three lowercase letters and an optional region, such as `pt-BR`.
2. Set `code` to match the filename, `native_name` to the language's own name, and `language_name` to the model's target language name.
3. Translate every reader-facing field. Keep dictionary keys stable. Keep placeholders such as `{count}`, `{page}`, `{pages}`, `{topics}` and `{minutes}` unchanged.
4. Supply the empty zero month and twelve month names. Weather conditions stay ordered: clear, cloudy, fog, rain, snow, thunderstorm.
5. Localize anagram words, original short concept definitions, stopwords, and interest/section labels. Concepts are reference material, not breaking news or product advertising.
6. Optionally add `examples/<code>.json` with suitable feeds, keywords and timezone. Without one, `--init --language <code>` starts from the generic English profile and localized labels. It does not invent suitable feeds or translate interest keywords.
7. Run `./ulak --check-locales` and the tests below. The CLI discovers the locale without Python edits. Render both themes you expect readers to use; confirm embedded fonts cover the script and check line wrapping.

`en.json` defines the catalog contract. New UI fields must be added to every bundled catalog in the same change. Do not silently fall back to English for a missing translation. Technical CLI help/errors intentionally remain English; localization of arbitrary source text uses the configured model, not the UI catalog.

## Add or adjust a source

Use stable source IDs and explicit declared languages. Leave article extraction disabled unless deliberately enabled by the reader. `article_hosts` restricts optional article requests when the feed host differs from the publisher. Robots checks and redirect bounds must remain intact. `quality` and source preferences are editorial inputs, never guarantees of trustworthiness.

Do not commit downloaded articles, API keys, local profiles, SQLite databases, PDFs or raw Obsidian notes. New examples should contain public RSS URLs and generic interests only.

## Editorial changes

Model instructions live in `prompts/*.txt`: `ranking`, `brief`, `summary`, `repair`, and the shared `preferences` section. Keep these instructions in English; `$language` selects the generated newspaper's language from its locale. Use Python `string.Template` placeholders (`$language`, `$max_words`, `$editorial_preferences`, `$instructions`); escape a literal dollar sign as `$$`. Missing variables fail explicitly. Inserted reader preferences and source data are never evaluated as templates.

Edit prompt wording in these files, not Python string literals. Python retains request construction, JSON schemas, and validation rules. Prompt content participates in cache keys, so edits invalidate affected model results automatically. Preserve exact evidence, uncertainty, source attribution, and output contracts when changing instructions. Continue putting reader-facing UI translations in `locales/`.

Keep candidate IDs tied to actual source entries. Reject invented story IDs, malformed scores and unsupported update evidence. Preserve exact evidence quotes and URL mappings for structured model briefs. Publisher excerpts and heuristic ranking must remain labeled accurately. Never fill missing context or consequences with invented material. Semantic relevance and factual entailment are not guaranteed by a matching quote; documentation and UI should preserve that distinction.

New ranking/grouping behavior needs fixtures for duplicate events, distinct updates, source limits, discovery slots and repeat suppression. Model tests mock HTTP responses; do not use paid requests or private credentials in tests.

## Print and layout checks

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
./ulak --check-locales
.venv/bin/python -m unittest discover -s tests -v
./ulak --preview --no-print --output output/review.pdf
pdftoppm -png -scale-to 1400 output/review.pdf /tmp/ulak-review
```

Inspect rendered pages for overflow, clipped paragraphs, missing glyphs, QR legibility, source references and footer collisions. Exercise multiple page counts and reading budgets. The PDF compatibility test runs the installed CUPS filter when available. Do not restore ASCII85 compound streams: affected PDFio versions silently produce blank sheets.

Tests must never send physical jobs. Printer capability and submission tests use mocks. Use `--dry-run-print` only with a persistent PDF and the appropriate manual duplex pass; this may generate pass files and preflight reports but must not submit to CUPS.

Use Conventional Commits and focused changes. Document new configuration fields and include defaults in generic profiles. Preserve existing personal configuration values during migration.
