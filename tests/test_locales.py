import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime,timezone

from pypdf import PdfReader
from ulak_app import i18n
from ulak_app.config import example,validate,load_theme
from ulak_app.pdf import render


class LocaleTests(unittest.TestCase):
    def tearDown(self):
        i18n.get_locale.cache_clear();i18n.all_stopwords.cache_clear()

    def test_all_catalogs_have_matching_keys_and_placeholders(self):
        for language in i18n.available_languages():
            self.assertEqual(i18n.get_locale(language)['code'],language)

    def test_missing_translation_and_broken_placeholder_fail(self):
        reference=i18n.get_locale('en')
        invalid=copy.deepcopy(reference);invalid.pop('intro')
        with self.assertRaisesRegex(ValueError,'keys differ'):i18n.validate_locale(invalid,reference)
        invalid=copy.deepcopy(reference);invalid['page']='Page {wrong}'
        with self.assertRaisesRegex(ValueError,'placeholders'):i18n.validate_locale(invalid,reference)
        invalid=copy.deepcopy(reference);invalid['anagram_hint']=''
        with self.assertRaisesRegex(ValueError,'empty'):i18n.validate_locale(invalid,reference)

    def test_new_language_is_discovered_and_rendered_without_python_branches(self):
        en=copy.deepcopy(i18n.get_locale('en'));third=copy.deepcopy(en)
        third.update(code='de',native_name='Deutsch',language_name='German',eyebrow='NEUE SPRACHE',gazette='NEUE ZEITUNG')
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'en.json').write_text(json.dumps(en));(root/'de.json').write_text(json.dumps(third))
            with patch('ulak_app.i18n.LOCALES',root):
                i18n.get_locale.cache_clear()
                self.assertIn('de',i18n.available_languages())
                config=example('de');validate(config)
                self.assertEqual(config['language'],'de')
                item=dict(title='A new locale works',summary='A contributor can add a complete language catalog without adding conditional language branches to application code.',topic='Science',source='Source',published='2026-10-31T10:00:00+00:00',url='https://example.org/story',summary_kind='publisher_excerpt')
                output=root/'third.pdf'
                render(output,[item],config,load_theme('compact'),datetime.now(timezone.utc),[])
                self.assertIn('NEUE SPRACHE',PdfReader(output).pages[0].extract_text())

    def test_production_python_contains_no_embedded_turkish_copy(self):
        import re
        root=Path(__file__).resolve().parent.parent
        for path in [root/'daily.py',*(root/'ulak_app').glob('*.py')]:
            self.assertIsNone(re.search('[çğıöşüÇĞİÖŞÜ]',path.read_text()),str(path))
