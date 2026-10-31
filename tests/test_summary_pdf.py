from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest
import subprocess
from unittest.mock import patch

from pypdf import PdfReader

from daily import main
from ulak_app.config import example, load_theme
from ulak_app.pdf import render
from ulak_app.summary import model_request, shorten, summarize

NOW = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
BODY = "Researchers compared the new measurements with previous observations. The study describes how the telescope was calibrated and explains several limitations in the data. More observations are needed before the interpretation can be confirmed."


def article(index=0):
    return dict(title=f"Özgür yazılım ve bilimde yeni gelişme {index}", source="Source", source_id="test",
                published=NOW.isoformat(), topic="Bilim & keşif", interest_id="science",
                url=f"https://example.org/{index}", source_text=BODY,
                summary=BODY, summary_kind="publisher_excerpt")


class SummaryTests(unittest.TestCase):
    def test_ollama_thinking_and_schema_are_top_level_controls(self):
        settings = example()['summary']
        settings.update(provider='ollama', model='test-model', think=False)
        schema = {'type': 'object', 'required': ['title']}
        response = io.BytesIO(json.dumps({'done': True, 'done_reason': 'stop', 'response': '{"title":"Test"}'}).encode())
        with patch('ulak_app.summary.urlopen', return_value=response) as call:
            self.assertEqual(model_request(settings, 'System', 'Prompt', schema=schema), {'title': 'Test'})
        payload = json.loads(call.call_args.args[0].data)
        self.assertIs(payload['think'], False)
        self.assertNotIn('think', payload['options'])
        self.assertEqual(payload['format'], schema)

    def test_ollama_token_limit_rejects_even_parseable_json(self):
        settings = example()['summary']
        settings.update(provider='ollama', model='test-model')
        response = io.BytesIO(json.dumps({'done': True, 'done_reason': 'length', 'response': '{"title":"Test"}'}).encode())
        with patch('ulak_app.summary.urlopen', return_value=response), self.assertRaisesRegex(ValueError, 'output budget'):
            model_request(settings, 'System', 'Prompt')

    def test_excerpt_is_bounded_and_not_invented(self):
        config = example()
        config["summary"]["max_words"] = 20
        result = summarize(article(), config)
        self.assertLessEqual(len(result["summary"].split()), 20)
        self.assertTrue(BODY.startswith(result["summary"].removesuffix('...')))
        self.assertEqual(result["summary_kind"], "publisher_excerpt")

    def test_model_receives_abstract_and_requested_language(self):
        config = example("tr")
        config["summary"].update(provider="ollama", model="test-model")
        output = {'done': True, 'response': json.dumps({'title': 'Translated title', 'summary': BODY})}
        response = io.BytesIO(json.dumps(output).encode())
        with patch('ulak_app.summary.urlopen', return_value=response) as call:
            result = summarize(article(), config)
        payload = json.loads(call.call_args.args[0].data)
        self.assertIn('Turkish', payload['system'])
        self.assertEqual(json.loads(payload['prompt'])['abstract'], BODY)
        self.assertFalse(payload['stream'])
        self.assertEqual(result['summary_kind'], 'model_summary')

    def test_model_invalid_output_fails_instead_of_headline_fallback(self):
        config = example()
        config['summary'].update(provider='ollama', model='test-model')
        response = io.BytesIO(json.dumps({'done': True, 'response': '{"title":"Only title"}'}).encode())
        with patch('ulak_app.summary.urlopen', return_value=response), self.assertRaises(ValueError):
            summarize(article(), config)

    def test_all_empty_run_preserves_existing_pdf(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'issue.pdf'
            path.write_bytes(b'existing issue')
            with patch('daily.collect', return_value=([], ['offline'])), redirect_stderr(io.StringIO()):
                self.assertEqual(main(['--config', 'examples/tr.json', '--output', str(path)]), 1)
            self.assertEqual(path.read_bytes(), b'existing issue')


class PdfTests(unittest.TestCase):
    def test_qr_references_and_adaptive_rows(self):
        from ulak_app.printing import check_pdf
        config=example('en');config['references']['qr']=True
        items=[article(i) for i in range(4)]
        for item in items[:2]:item['summary']=BODY*8
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/'adaptive.pdf'
            render(output,items,config,load_theme('compact'),NOW,[])
            pdf=PdfReader(output);check_pdf(output)
            self.assertEqual(len(pdf.pages),1)
            self.assertIn('example.org',pdf.pages[0].extract_text())
            self.assertTrue(any(item.get('qr_printed') for item in items))
            self.assertGreater(items[0]['layout_box']['height'],items[2]['layout_box']['height'])
    def test_generated_text_and_photos_use_single_stream_filters(self):
        from PIL import Image
        from ulak_app.printing import check_pdf
        items = [article()]
        items[0]['_image'] = Image.new('RGB', (50,50), 'gray')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'compatible.pdf'
            render(path, items, example(), load_theme('compact'), NOW, [])
            check_pdf(path)
            filters = PdfReader(path).pages[0]['/Contents'].get_object().get('/Filter')
            self.assertNotIn('/ASCII85Decode', filters or [])

    @unittest.skipUnless(Path('/usr/lib/cups/filter/pdftopdf').exists(), 'CUPS PDF filter is not installed')
    def test_cups_filter_preserves_real_page_content(self):
        import os
        with tempfile.TemporaryDirectory() as directory:
            path, filtered = Path(directory)/'source.pdf', Path(directory)/'filtered.pdf'
            render(path, [article()], example(), load_theme('compact'), NOW, [])
            environment = dict(os.environ)
            environment.pop('PPD',None)
            with filtered.open('wb') as output:
                result = subprocess.run(['/usr/lib/cups/filter/pdftopdf','999','test','Ulak filter regression','1','media=A4 sides=one-sided',str(path)],stdout=output,stderr=subprocess.PIPE,env=environment,check=True)
            self.assertNotIn(b'Unsupported compound stream filter', result.stderr)
            self.assertIn('Researchers compared',PdfReader(filtered).pages[0].extract_text())

    def test_maximum_density_both_languages_and_themes(self):
        with tempfile.TemporaryDirectory() as directory:
            for language in ('tr', 'en'):
                for theme in ('compact', 'classic'):
                    with self.subTest(language=language, theme=theme):
                        config = example(language)
                        items = [article(i) for i in range(8)]
                        path = Path(directory)/f'{language}-{theme}.pdf'
                        render(path, items, config, load_theme(theme), NOW, [])
                        pdf = PdfReader(path)
                        self.assertEqual(len(pdf.pages), 1)
                        self.assertAlmostEqual(float(pdf.pages[0].mediabox.width), 595.276, places=2)
                        self.assertEqual(len(pdf.pages[0]['/Annots']), 16)
                        text = pdf.pages[0].extract_text()
                        self.assertIn('Özgür yazılım', text)
                        self.assertIn('PERSONAL NEWS BRIEF' if language=='en' else 'KİŞİSEL HABER BÜLTENİ', text)
                        self.assertIn('Researchers compared', text)

    def test_long_content_is_visibly_shortened_not_clipped(self):
        config = example()
        items = [article(i) for i in range(8)]
        for item in items:
            item['title'] = 'Very long headline with Turkish characters çğıöşü ' * 8
            item['summary'] = BODY * 10
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'long.pdf'
            render(path, items, config, load_theme('compact'), NOW, [])
            self.assertEqual(len(PdfReader(path).pages), 1)
            self.assertTrue(all(i['display_summary'].endswith('...') for i in items))
