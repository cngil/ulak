from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from pypdf import PdfReader, PdfWriter

from daily import main
from ulak_app.config import example, load_theme, validate, ConfigError
from ulak_app import extras, printing, storage
from ulak_app.pdf import render
from ulak_app.summary import summarize

NOW = datetime(2026, 10, 31, 12, tzinfo=timezone.utc)
BODY = 'Researchers compared the new measurements with previous observations. Their report describes how the instrument was calibrated and explains several limitations that require further investigation.'


def make_pdf(path, count=3):
    writer = PdfWriter()
    for i in range(count):
        writer.add_blank_page(width=595+i, height=842)
    with path.open('wb') as output:
        writer.write(output)


def item(i):
    return dict(title=f'New science and software developments {i}', source='Test', source_id='test',
        published=NOW.isoformat(), topic='Science', interest_id='science', url=f'https://example.org/{i}',
        source_text=BODY, summary=BODY, summary_kind='publisher_excerpt')


class PrintTests(unittest.TestCase):
    def test_unsupported_duplex_color_and_paper_are_rejected(self):
        result=subprocess.CompletedProcess([],0,stdout='PageSize/Media Size: *Letter\nColorModel/Color Mode: *Gray\n')
        base=['lp','-d','Test','-o','media=Letter','-o','sides=one-sided','-o','print-color-mode=monochrome','--','issue.pdf']
        for old,new in [('sides=one-sided','sides=two-sided-long-edge'),('media=Letter','media=A4'),('print-color-mode=monochrome','print-color-mode=color')]:
            command=[new if part==old else part for part in base]
            with patch('ulak_app.printing.subprocess.run',return_value=result),self.assertRaises(ValueError):
                printing.validate_capabilities(command)
    def test_legacy_compound_pdf_is_rejected_before_submission(self):
        from reportlab import rl_config
        from reportlab.pdfgen.canvas import Canvas
        previous = rl_config.useA85
        try:
            rl_config.useA85 = 1
            with tempfile.TemporaryDirectory() as directory:
                path=Path(directory)/'legacy.pdf'
                c=Canvas(str(path),pageCompression=1)
                c.drawString(40,750,'Legacy encoded content')
                c.save()
                with self.assertRaisesRegex(ValueError,'compound stream'):
                    printing.print_command(path,example(),dry_run=True)
        finally:
            rl_config.useA85 = previous

    def test_odd_duplex_keeps_blank_back_and_reverse_order(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'edition.pdf'
            make_pdf(path, 3)
            front, back = printing.duplex_files(path, reverse_backs=True)
            self.assertEqual([float(p.mediabox.width) for p in PdfReader(front).pages], [595,597])
            # Blank back of final sheet precedes page 2 when reverse ordering is requested.
            self.assertEqual([float(p.mediabox.width) for p in PdfReader(back).pages], [597,596])

    def test_print_settings_map_to_cups_without_submitting(self):
        config = example()
        config['print'].update(printers=['first','second'], copies=3, duplex='long-edge', quality='efficient', color='color')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'edition.pdf'
            make_pdf(path)
            with patch('ulak_app.printing.subprocess.run') as submit:
                command = printing.print_command(path, config, dry_run=True)
            submit.assert_not_called()
            self.assertIn('first', command)
            self.assertIn('two-sided-long-edge', ' '.join(command))
            self.assertIn('print-color-mode=monochrome', command)
            self.assertIn('print-quality=3', command)
            self.assertEqual(command[command.index('-n')+1], '3')

    def test_preferred_printer_fallback_happens_before_submission(self):
        config = example()
        config['print']['printers'] = ['missing','online']
        result = subprocess.CompletedProcess([], 0, stdout='online accepting requests\n')
        with patch('ulak_app.printing.subprocess.run', return_value=result):
            self.assertEqual(printing.select_printer(config['print']), 'online')

    def test_manual_duplex_needs_explicit_pass(self):
        config = example()
        config['print']['duplex'] = 'manual'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'edition.pdf'
            make_pdf(path)
            with self.assertRaises(ValueError):
                printing.print_command(path, config, dry_run=True)
            command = printing.print_command(path, config, 'front', dry_run=True)
            self.assertTrue(command[-1].endswith('.fronts.pdf'))

    def test_back_pass_cannot_regenerate_news(self):
        with patch('daily.collect') as collect:
            self.assertEqual(main(['--config','examples/en.json','--print-pass','back']), 2)
            collect.assert_not_called()


class StorageTests(unittest.TestCase):
    def settings(self, **kwargs):
        return dict(max_editions=0,max_age_hours=0,max_total_mb=0,**kwargs)

    def test_count_retention_preserves_unmanaged_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            unrelated = root/'unrelated.pdf'
            unrelated.write_bytes(b'important')
            for i in range(3):
                path = root/f'edition-{i}.pdf'
                path.write_bytes(b'pdf')
                storage.register(path, [path], NOW+timedelta(hours=i))
            storage.cleanup(root, dict(max_editions=1,max_age_hours=0,max_total_mb=0), NOW+timedelta(hours=3))
            self.assertTrue((root/'edition-2.pdf').exists())
            self.assertFalse((root/'edition-0.pdf').exists())
            self.assertEqual(unrelated.read_bytes(), b'important')

    def test_age_and_size_limits(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            old=root/'old.pdf'; old.write_bytes(b'a'*200)
            new=root/'new.pdf'; new.write_bytes(b'a'*200)
            storage.register(old,[old],NOW-timedelta(hours=10))
            storage.register(new,[new],NOW)
            storage.cleanup(root,dict(max_editions=0,max_age_hours=2,max_total_mb=0),NOW)
            self.assertFalse(old.exists())
            self.assertTrue(new.exists())
            storage.cleanup(root,dict(max_editions=0,max_age_hours=0,max_total_mb=.0001),NOW)
            self.assertFalse(new.exists())

    def test_physical_file_limit_keeps_complete_bundles(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for i in range(2):
                pdf=root/f'{i}.pdf';sidecar=root/f'{i}.json'
                pdf.write_bytes(b'pdf');sidecar.write_text('{}')
                storage.register(pdf,[pdf,sidecar],NOW+timedelta(hours=i))
            storage.cleanup(root,dict(max_editions=0,max_files=2,max_age_hours=0,max_total_mb=0),NOW+timedelta(hours=2))
            self.assertTrue((root/'1.pdf').exists())
            self.assertTrue((root/'1.json').exists())
            self.assertFalse((root/'0.pdf').exists())
            self.assertFalse((root/'0.json').exists())

    def test_overwrite_does_not_let_old_record_delete_current_output(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'issue.pdf';path.write_bytes(b'new')
            storage.register(path,[path],NOW-timedelta(days=2))
            storage.register(path,[path],NOW)
            storage.cleanup(path.parent,dict(max_editions=1,max_age_hours=24,max_total_mb=0),NOW)
            self.assertTrue(path.exists())

    def test_symlinks_and_parent_paths_are_not_deleted(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);outside=root/'important';outside.write_text('keep')
            managed=root/'managed';managed.mkdir()
            (managed/'link.pdf').symlink_to(outside)
            for i,name in enumerate(['link.pdf','../important']):
                (managed/f'.ulak-edition-{i}.json').write_text(json.dumps(dict(generator='ulak',created=(NOW-timedelta(days=9)).isoformat(),files=[name])))
            storage.cleanup(managed,dict(max_editions=1,max_age_hours=1,max_total_mb=1),NOW)
            self.assertEqual(outside.read_text(),'keep')

    def test_duplex_passes_attach_only_to_managed_editions(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);path=root/'edition.pdf';make_pdf(path)
            storage.register(path,[path],NOW)
            files=list(printing.duplex_files(path))
            storage.attach(path,files)
            storage.attach(path,files)
            record=json.loads(next(root.glob('.ulak-edition-*.json')).read_text())
            self.assertEqual(len(record['files']),3)


class ExtrasModelLayoutTests(unittest.TestCase):
    def test_automatic_print_and_no_print_override(self):
        candidate=item(0);candidate.update(score=1,preferred=False)
        for suppressed in (False,True):
            config=example('en');config['storage']['keep']=False;config['print']['auto']=True
            with patch('daily.load',return_value=config),patch('daily.collect',return_value=([candidate],[])),patch('daily.rank_candidates',return_value=[candidate]),patch('daily.printing.validate_capabilities'),patch('daily.printing.submit') as submit:
                self.assertEqual(main(['--no-print'] if suppressed else []),0)
            self.assertEqual(submit.call_count,0 if suppressed else 1)

    def test_daily_sudoku_has_unique_solution_and_is_reproducible(self):
        first=extras.puzzle(NOW)
        self.assertEqual(first,extras.puzzle(NOW))
        self.assertEqual(extras.solutions([row[:] for row in first['grid']]),1)
        for row in first['solution']:
            self.assertEqual(set(row),{1,2,3,4})

    def test_ascii_conversion_has_fixed_dimensions(self):
        rows=extras.ascii_image(Image.new('RGB',(80,50),'white'))
        self.assertEqual(len(rows),13)
        self.assertTrue(all(len(row)==40 for row in rows))

    def test_weather_uses_model_timestamp_and_rejects_stale_data(self):
        config=example();config['weather']['enabled']=True
        data=dict(current=dict(time=NOW.isoformat(),temperature_2m=20,wind_speed_10m=8,weather_code=0))
        with patch('ulak_app.extras.read_url',return_value=json.dumps(data).encode()):
            self.assertEqual(extras.weather(config,NOW)['condition'],'Clear')
            with self.assertRaises(ValueError):
                extras.weather(config,NOW+timedelta(hours=5))

    def test_hosted_model_auth_and_structured_response(self):
        config=example('en');config['summary'].update(provider='openai_compatible',endpoint='https://example.org/v1',model='user-model',api_key_env='ULAK_TEST_KEY')
        response=io.BytesIO(json.dumps({'choices':[{'finish_reason':'stop','message':{'content':json.dumps(dict(title='Source-grounded title',summary=BODY))}}]}).encode())
        with patch.dict('os.environ',{'ULAK_TEST_KEY':'test-only-key'}),patch('ulak_app.summary.urlopen',return_value=response) as call:
            result=summarize(item(0),config)
        request=call.call_args.args[0]
        self.assertEqual(request.full_url,'https://example.org/v1/chat/completions')
        self.assertEqual(request.get_header('Authorization'),'Bearer test-only-key')
        self.assertEqual(result['summary_kind'],'model_summary')

    def test_multi_page_gazette_puzzle_branding_and_ascii(self):
        config=example();config['theme']='gazette';config['layout'].update(pages=2,articles_per_page=5);config['max_articles']=10
        config['branding']['title']='THE TEST PRESS';config['puzzle']['enabled']=True
        validate(config)
        items=[item(i) for i in range(10)];items[0]['_ascii']=extras.ascii_image(Image.new('RGB',(50,50),'gray'))
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'gazette.pdf'
            render(path,items,config,load_theme('gazette'),NOW,[],{'puzzle':extras.puzzle(NOW)})
            pdf=PdfReader(path)
            self.assertEqual(len(pdf.pages),2)
            self.assertIn('THE TEST PRESS',pdf.pages[0].extract_text())
            self.assertIn('SUDOKU',pdf.pages[1].extract_text())
            self.assertEqual({item['page'] for item in items},{1,2})

    def test_auto_manual_print_is_invalid(self):
        config=example();config['print'].update(auto=True,duplex='manual')
        with self.assertRaises(ConfigError):validate(config)

    def test_no_keep_discards_temporary_edition_and_never_prints_by_default(self):
        config=example('en');config['storage']['keep']=False
        candidate=item(0);candidate.update(score=1,preferred=False)
        with patch('daily.load',return_value=config),patch('daily.collect',return_value=([candidate],[])),patch('daily.rank_candidates',return_value=[candidate]),patch('daily.printing.submit') as submit:
            self.assertEqual(main([]),0)
        submit.assert_not_called()
