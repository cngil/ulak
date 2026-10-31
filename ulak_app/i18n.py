"""Locale discovery and strict catalog validation. All reader-facing copy is data."""
from functools import lru_cache
import json
from pathlib import Path
import re
from string import Formatter

LOCALES=Path(__file__).resolve().parent.parent/'locales'
DEFAULT_LANGUAGE='en'


def available_languages():
    return sorted(path.stem for path in LOCALES.glob('*.json') if re.fullmatch(r'[a-z]{2,3}(?:-[A-Z]{2})?',path.stem))


def validate_locale(data,reference,path='locale'):
    if isinstance(reference,dict):
        if not isinstance(data,dict) or set(data)!=set(reference):
            missing=set(reference)-set(data) if isinstance(data,dict) else set(reference)
            extra=set(data)-set(reference) if isinstance(data,dict) else set()
            raise ValueError(f'{path}: locale keys differ; missing={sorted(missing)}, extra={sorted(extra)}')
        for key,value in reference.items(): validate_locale(data[key],value,path+'.'+key)
    elif isinstance(reference,list):
        if not isinstance(data,list) or not data: raise ValueError(f'{path}: expected non-empty array')
        if path.endswith('.months') and len(data)!=13: raise ValueError(f'{path}: provide empty month zero and 12 month names')
        if path.endswith('.months') and data[0]!='': raise ValueError(f'{path}: month zero must be empty')
        if path.endswith('.weather_conditions') and len(data)!=6: raise ValueError(f'{path}: expected six weather conditions')
        for index,value in enumerate(data): validate_locale(value,reference[0],path+f'[{index}]')
    else:
        if not isinstance(data,str): raise ValueError(f'{path}: expected a string')
        if not data and not path.endswith('.months[0]'): raise ValueError(f'{path}: translation must not be empty')
        fields=lambda text:{field for _,field,_,_ in Formatter().parse(text) if field is not None}
        if fields(data)!=fields(reference): raise ValueError(f'{path}: preserve format placeholders {sorted(fields(reference))}')


@lru_cache(maxsize=64)
def get_locale(language):
    if not re.fullmatch(r'[a-z]{2,3}(?:-[A-Z]{2})?',language): raise ValueError('Invalid locale code')
    path=LOCALES/f'{language}.json'
    try:
        data=json.loads(path.read_text())
        reference=json.loads((LOCALES/'en.json').read_text())
    except (OSError,json.JSONDecodeError) as exc:
        raise ValueError(f'Cannot load locale {language}: {exc}') from exc
    validate_locale(data,reference,language)
    if data['code']!=language: raise ValueError(f'{path}: code must match filename')
    return data


class Catalog:
    def __getitem__(self,language): return get_locale(language)


TEXT=Catalog()


@lru_cache(maxsize=1)
def all_stopwords():
    return {word for language in available_languages() for word in get_locale(language)['stopwords']}
