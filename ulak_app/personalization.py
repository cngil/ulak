"""Reviewable local interest suggestions; no model receives vault notes."""
from collections import defaultdict
from pathlib import Path
import re


import json
from .i18n import get_locale

TOPICS=json.loads((Path(__file__).resolve().parent.parent/'data/interest_topics.json').read_text())


def suggest(vault,language):
    locale=get_locale(language)
    root=Path(vault).expanduser().resolve()
    if not root.is_dir(): raise ValueError('Obsidian vault directory does not exist')
    evidence=defaultdict(list)
    allowed=('Notes/Technology','Media','Notes/Gaming','Education/International Education')
    count=0
    for folder in allowed:
        for path in sorted((root/folder).rglob('*.md')):
            if path.is_symlink() or not path.resolve().is_relative_to(root): continue
            relative=path.relative_to(root).as_posix()
            if re.search(r'passport|identity|private|financial|health|journal|visa|evidence|contact|credential|password|secret|token',relative,re.I): continue
            count+=1
            if count>500: break
            # Filenames and tags/headings only, not free-form personal entries.
            with path.open(errors='replace') as handle:
                header=handle.read(4000)
            headings=' '.join(line for line in header.splitlines() if line.startswith('#'))
            text=(relative+' '+headings).casefold().replace('-',' ')
            for key,topic in TOPICS.items():
                keywords=topic['keywords']
                if any(keyword in text for keyword in keywords): evidence[key].append(relative)
    return dict(review_required=True,method='Local note titles/headings; no notes sent to models',
        suggestions=[dict(id=key,name=locale['suggested_interest_names'][key],keywords=TOPICS[key]['keywords'],weight=1,evidence=paths[:5])
                     for key,paths in sorted(evidence.items(),key=lambda pair:len(pair[1]),reverse=True)])
