"""Retention applies only to Ulak-owned edition bundles in the chosen directory."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import uuid


def register(path, files, now):
    root = Path(path).resolve().parent
    names = []
    for file in files:
        file = Path(file)
        if file.is_file() and not file.is_symlink() and file.resolve().parent == root:
            if file.name not in names:
                names.append(file.name)
    # A deliberate --output reuse must not leave an older record owning the new files.
    for old in root.glob(".ulak-edition-*.json"):
        try:
            if old.is_symlink():
                continue
            data = json.loads(old.read_text())
            if data.get("generator") != "ulak" or not isinstance(data.get("files"), list):
                continue
            remaining = [name for name in data["files"] if name not in names]
            if not remaining:
                old.unlink()
            elif remaining != data["files"]:
                data["files"] = remaining
                old.write_text(json.dumps(data))
        except (OSError, ValueError, TypeError):
            continue
    record = root / f".ulak-edition-{uuid.uuid4().hex}.json"
    record.write_text(json.dumps(dict(generator="ulak", created=now.isoformat(), files=names)))
    return record


def cleanup(directory, settings, now):
    root = Path(directory).resolve()
    editions = []
    for record in root.glob(".ulak-edition-*.json"):
        try:
            if record.is_symlink():
                continue
            data = json.loads(record.read_text())
            if data.get("generator") != "ulak":
                continue
            created = datetime.fromisoformat(data["created"])
            if created.tzinfo is None or not isinstance(data["files"], list):
                continue
            files = []
            for name in data["files"]:
                if not isinstance(name, str) or Path(name).name != name or name in (".", ".."):
                    raise ValueError("Invalid managed filename")
                file = root/name
                if file.is_symlink():
                    raise ValueError("Managed file became a symlink")
                if file.is_file():
                    files.append(file)
            editions.append((created, record, files))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    editions.sort(key=lambda edition: (edition[0], edition[1].name), reverse=True)
    count, file_count, size, removed = 0, 0, 0, []
    for created, record, files in editions:
        total = sum(file.stat().st_size for file in files)
        expired = settings["max_age_hours"] and created < now-timedelta(hours=settings["max_age_hours"])
        excess_count = settings["max_editions"] and count >= settings["max_editions"]
        excess_size = settings["max_total_mb"] and size+total > settings["max_total_mb"]*1024*1024
        excess_files = settings.get("max_files", 0) and file_count+len(files) > settings["max_files"]
        if expired or excess_count or excess_size or excess_files:
            for file in files:
                file.unlink(missing_ok=True)
                removed.append(str(file))
            record.unlink(missing_ok=True)
        else:
            count += 1
            file_count += len(files)
            size += total
    return removed


def attach(path, files):
    """Add duplex pass files only when the original edition is already managed."""
    path = Path(path).resolve()
    for record in path.parent.glob(".ulak-edition-*.json"):
        try:
            if record.is_symlink():
                continue
            data = json.loads(record.read_text())
            if data.get("generator") != "ulak" or path.name not in data.get("files", []):
                continue
            if any(not isinstance(name, str) or Path(name).name != name for name in data["files"]):
                continue
            original = [path.parent/name for name in data["files"]]
            register(path, original+files, datetime.fromisoformat(data["created"]))
            return
        except (OSError, ValueError, KeyError, TypeError):
            continue
