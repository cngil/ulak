#!/usr/bin/env python3
"""Ulak command-line entry point."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import datetime
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import hashlib
import math
from zoneinfo import ZoneInfo

from ulak_app.config import ConfigError, ROOT, default_path, example, load, load_theme, validate
from ulak_app.feeds import choose, collect, rank_candidates
from ulak_app.pdf import render
from ulak_app.summary import credentials, summarize
from ulak_app import extras, printing, storage, editorial, personalization
from ulak_app.state import State, digest
from ulak_app.i18n import available_languages, get_locale, DEFAULT_LANGUAGE


def send_to_printer(path, config, args, state=None):
    wanted = args.print_now or args.dry_run_print or (config["print"]["auto"] and not args.no_print)
    if not wanted:
        return []
    command = printing.print_command(path, config, args.print_pass, args.dry_run_print)
    print("Print command: " + shlex.join(command))
    if config['print']['preflight']:
        report=printing.preflight(path,config)
        print('Print preflight: '+json.dumps(report))
    if not args.dry_run_print:
        printing.validate_capabilities(command)
        key=digest(hashlib.sha256(Path(path).read_bytes()).hexdigest()+json.dumps(command[:-1])+str(args.print_pass))
        if getattr(args,'force_reprint',False):
            import uuid
            key+=uuid.uuid4().hex
        guarded=state is not None and state.enabled and config['print']['prevent_duplicates']
        if guarded: state.claim_print(key)
        # Preserve a pending claim on ambiguous failures; explicit force is needed to retry.
        receipt=printing.submit(command)
        if guarded: state.finish_print(key,receipt)
    if config["print"]["duplex"] == "manual":
        print("Manual duplex: reload paper and run --print-existing with the SAME PDF and --print-pass back. Test orientation with two pages first.")
        return [path.with_name(path.stem+suffix) for suffix in (".fronts.pdf", ".backs.pdf")]
    return []


def main(argv=None):
    argv=list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ('feedback','history','suggest-interests','preferences'):
        argv[0]='--'+argv[0]
    state=None
    parser = argparse.ArgumentParser(description="Generate and optionally print a personalized newspaper.")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--init", action="store_true", help="Write generic starter config without overwriting")
    parser.add_argument("--check-config", action="store_true")
    parser.add_argument("--check-model", action="store_true", help="Make one small model test request")
    parser.add_argument("--list-themes", action="store_true")
    parser.add_argument("--language", choices=available_languages())
    parser.add_argument('--list-languages',action='store_true')
    parser.add_argument('--check-locales',action='store_true')
    parser.add_argument("--theme", help="compact, classic, gazette, or custom JSON path")
    parser.add_argument("--hours", type=int)
    parser.add_argument("--news-count", type=int, help="Maximum number of news items")
    parser.add_argument("--pages", type=int, help="Maximum page count; no blank filler pages")
    parser.add_argument("--articles-per-page", type=int)
    parser.add_argument("--columns", type=int, choices=(1,2))
    parser.add_argument("--provider", choices=("publisher", "ollama", "openai_compatible"))
    parser.add_argument("--model")
    parser.add_argument("--model-url")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--open", action="store_true")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--print", dest="print_now", action="store_true")
    group.add_argument("--dry-run-print", action="store_true", help="Show CUPS command without submitting")
    group.add_argument("--no-print", action="store_true", help="Override configured automatic printing")
    parser.add_argument("--print-existing", type=Path, help="Use existing PDF instead of regenerating news")
    parser.add_argument("--print-pass", choices=("front", "back"))
    parser.add_argument("--printer")
    parser.add_argument("--copies", type=int)
    parser.add_argument("--cleanup", action="store_true", help="Apply configured retention without generating")
    parser.add_argument('--preview',action='store_true',help='Generate without advancing edition history or printing')
    parser.add_argument('--history',action='store_true')
    parser.add_argument('--preferences',action='store_true')
    parser.add_argument('--feedback',nargs=3,metavar=('ISSUE','STORY','ACTION'))
    parser.add_argument('--suggest-interests',type=Path,metavar='VAULT')
    parser.add_argument('--force-reprint',action='store_true',help='Intentionally bypass duplicate-job protection')
    parser.add_argument('--reading-minutes',type=int)
    args = parser.parse_args(argv)
    try:
        if args.list_languages or args.check_locales:
            for language in available_languages():
                locale=get_locale(language)
                print(language+' / '+locale['native_name'])
            return 0
        if args.list_themes:
            print("compact\nclassic\ngazette\nOr use a declarative theme JSON path.")
            return 0
        if args.init:
            path = args.config or ROOT/"config.local.json"
            config = example(args.language or DEFAULT_LANGUAGE)
            config["$schema"] = str(ROOT/"config.schema.json")
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("x") as handle:
                handle.write(json.dumps(config, ensure_ascii=False, indent=2)+"\n")
            print(f"Created {path.resolve()}. Edit this file to tailor your newspaper.")
            return 0
        config = load(args.config)
        if args.preview:
            if args.print_now or args.dry_run_print: raise ValueError('--preview cannot print')
            args.no_print=True
        if args.reading_minutes is not None: config['editorial']['reading_minutes']=args.reading_minutes
        for key in ("hours", "language", "theme"):
            if getattr(args, key) is not None:
                config[key] = getattr(args, key)
        if args.news_count is not None:
            config["max_articles"] = args.news_count
        for key in ("pages", "articles_per_page", "columns"):
            if getattr(args, key) is not None:
                config["layout"][key] = getattr(args, key)
        for key in ("provider", "model"):
            if getattr(args, key) is not None:
                config["summary"][key] = getattr(args, key)
        if args.model_url:
            config["summary"]["endpoint"] = args.model_url
        for key in ("printer", "copies"):
            if getattr(args, key) is not None:
                config["print"][key] = getattr(args, key)
        if args.no_print:
            config["print"]["auto"] = False
        validate(config)
        theme_name = config["theme"]
        if theme_name not in ("compact", "classic", "gazette") and not Path(theme_name).is_absolute():
            theme_name = str((args.config or default_path()).resolve().parent/theme_name)
        theme = load_theme(theme_name)
        if args.check_config:
            print(f'Valid config: {args.config or default_path()} ({config["language"]}, {theme["name"]})')
            return 0
        now = datetime.now(ZoneInfo(config["timezone"]))
        locale=get_locale(config['language'])
        if args.suggest_interests:
            print(json.dumps(personalization.suggest(args.suggest_interests,config['language']),ensure_ascii=False,indent=2))
            return 0
        state=State(config)
        if args.history:
            for row in state.history(): print(' | '.join(str(value) for value in row))
            return 0
        if args.preferences:
            weights,hidden=state.preferences()
            print(json.dumps(dict(topic_multipliers=weights,hidden_urls=sorted(hidden),feedback=state.feedback_history()),ensure_ascii=False,indent=2))
            return 0
        if args.feedback:
            issue,position,action=args.feedback
            if action not in ('more','less','hide','reset'): raise ValueError('Feedback action must be more, less, hide or reset')
            state.feedback(issue,int(position),action)
            print('Feedback saved locally; topic weights remain inspectable with --history and in the SQLite journal.')
            return 0
        if args.cleanup:
            removed = storage.cleanup(Path(config["storage"]["directory"]).expanduser(), config["storage"], now)
            print(f"Retention removed {len(removed)} managed files")
            return 0
        if args.print_existing:
            if not (args.print_now or args.dry_run_print):
                raise ValueError("--print-existing requires --print or --dry-run-print")
            printing_files = send_to_printer(args.print_existing.resolve(), config, args,state)
            if printing_files:
                storage.attach(args.print_existing.resolve(), printing_files)
            return 0
        if args.print_pass == "back":
            raise ValueError("Use --print-existing for the back pass; never regenerate between duplex passes")
        if config["summary"]["provider"] != "publisher":
            credentials(config["summary"])
        if args.check_model:
            if config["summary"]["provider"] == "publisher":
                print("Publisher mode needs no model. Set --provider and --model to test a model.")
            else:
                result = summarize(dict(title="A measurement study", source_text="Researchers measured temperatures at three observation stations. Their report describes the measurements and says additional data is needed before drawing a conclusion."), config)
                print("Model returned a valid structured brief: " + result["title"])
            return 0
        keep = config["storage"]["keep"]
        if not keep and (args.output or args.open or config["print"]["duplex"] == "manual"):
            raise ValueError("storage.keep=false cannot be combined with --output, --open, or manual duplex; those need persistent files")
        articles, warnings = collect(config, now)
        candidates = rank_candidates(articles, config)
        events=editorial.prepare(articles,candidates,config,state,now,warnings)
        items = []
        robots_cache={}
        for item in editorial.select(events,config):
            try:
                items.append(editorial.write(item,config,state,warnings,robots_cache))
            except Exception as exc:
                warnings.append(f'Summary {item["source_id"]}: {type(exc).__name__}: {exc}')
        if not items:
            for warning in warnings:
                print("Warning: " + warning, file=sys.stderr)
            print("No eligible articles with substantive summaries. Check sources/language/interests or use --hours 48. Existing files preserved.", file=sys.stderr)
            return 1
        extra = {}
        issue,number=state.reserve(now,args.preview)
        extra['issue_id'],extra['issue_number']=issue,number
        extra['preview']=args.preview
        extra['estimated_reading_minutes']=max(1,math.ceil(sum(len(item['title'].split())+len(item['summary'].split()) for item in items)/config['editorial']['words_per_minute']))
        if config['editorial']['introduction_every'] and (number-1)%config['editorial']['introduction_every']==0:
            topics=list(dict.fromkeys(item['topic'] for item in items))[:3]
            updates=sum(item.get('status')=='update' for item in items)
            extra['introduction']=locale['intro'].format(count=len(items),updates=updates,topics=', '.join(topics))
        try:
            extra["weather"] = extras.weather(config, now)
        except Exception as exc:
            warnings.append(f"Weather unavailable: {type(exc).__name__}: {exc}")
        if config["puzzle"]["enabled"]:
            extra["puzzle"] = extras.daily_puzzle(now,config)
        if config['editorial']['concept']: extra['concept']=extras.concept(now,config['language'])
        if config["visuals"]["mode"] != "none":
            count = 0
            for item in items:
                if count >= config["visuals"]["max_images"]:
                    break
                if not item.get("image_url"):
                    continue
                try:
                    image = extras.illustration(item["image_url"], config["visuals"]["grayscale"])
                    if config["visuals"]["mode"] == "ascii":
                        item["_ascii"] = extras.ascii_image(image)
                    else:
                        item["_image"] = image
                    count += 1
                except Exception as exc:
                    warnings.append(f'Illustration {item["source_id"]}: {type(exc).__name__}')
        missing = [interest["name"] for interest in config["interests"] if interest["id"] not in {item["interest_id"] for item in items}]
        for warning in warnings:
            print("Warning: " + warning, file=sys.stderr)
        with ExitStack() as stack:
            directory = Path(config["storage"]["directory"]).expanduser() if keep else Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="ulak-")))
            output = args.output or directory/f"ulak-{now:%Y-%m-%d-%H%M%S-%f}-{config['language']}.pdf"
            render(output, items, config, theme, now, warnings, extra)
            files = [output]
            manifest = dict(generator="ulak", issue_id=issue,generated=now.isoformat(), language=config["language"], hours=config["hours"],
                theme=theme["name"], summary_provider=config["summary"]["provider"],
                articles=[{k:v for k,v in item.items() if not k.startswith('_')} for item in items],
                warnings=warnings, unmatched_interests=missing, extras=extra)
            if config["storage"]["save_manifest"]:
                output.with_suffix(".json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+"\n")
                files.append(output.with_suffix(".json"))
            every=config['personalization']['suggest_every']
            if config['personalization']['vault_path'] and every and number%every==0:
                suggestions=output.with_suffix('.interests.json')
                suggestions.write_text(json.dumps(personalization.suggest(config['personalization']['vault_path'],config['language']),ensure_ascii=False,indent=2))
                files.append(suggestions)
            # Register before printing so a failed spool leaves a recoverable persistent edition.
            record = storage.register(output, files, now) if keep else None
            pass_files = send_to_printer(output, config, args,state)
            if not args.preview: state.record(issue,items,output)
            if pass_files and keep:
                record.unlink(missing_ok=True)
                storage.register(output, files+pass_files, now)
            if args.open:
                subprocess.run(["xdg-open", str(output.resolve())], check=True)
            if keep:
                removed = storage.cleanup(output.parent, config["storage"], now)
                if output.exists():
                    print(f"{output.resolve()} ({len(items)} stories)")
                else:
                    print("Edition generated; removed by configured retention limits.")
                if removed:
                    print(f"Retention removed {len(removed)} managed files")
            else:
                print(f"Generated {len(items)} stories; temporary edition discarded after processing.")
        if missing:
            print("No selected match: " + ", ".join(missing), file=sys.stderr)
        return 0
    except (ConfigError, OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    finally:
        if state: state.close()


if __name__ == "__main__":
    raise SystemExit(main())
