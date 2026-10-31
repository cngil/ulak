"""Load model instructions independently of application logic and UI locales."""
from hashlib import sha256
from pathlib import Path
from string import Template

PROMPT_DIR = Path(__file__).resolve().parent.parent / 'prompts'
NAMES = frozenset(('ranking', 'brief', 'summary', 'repair', 'preferences'))


def read_prompt(name):
    if name not in NAMES:
        raise ValueError(f'Unknown prompt: {name}')
    return (PROMPT_DIR / f'{name}.txt').read_text(encoding='utf-8').strip()


def render_prompt(name, **values):
    # Strict substitution exposes missing variables; values are never re-templated.
    return Template(read_prompt(name)).substitute(values)


def preferences(instructions):
    return render_prompt('preferences', instructions=instructions) if instructions else ''


def fingerprint(*names):
    content = '\0'.join(f'{name}\0{read_prompt(name)}' for name in names)
    return sha256(content.encode('utf-8')).hexdigest()
