from datetime import datetime,timedelta,timezone
from pathlib import Path
import copy
import json
import shutil
import tempfile
import unittest
from unittest.mock import patch

from ulak_app.config import example
from ulak_app.state import State
from ulak_app import editorial,articles,personalization,prompts
from daily import main
from daily import send_to_printer
from argparse import Namespace
from pypdf import PdfWriter
from jsonschema import Draft202012Validator

NOW=datetime(2026,10,31,13,tzinfo=timezone.utc)
BODY='Researchers measured temperatures at three stations. The report explains the methods and says further observations are needed to confirm the findings.'


def story(index=1,title='Researchers publish a temperature study'):
    return dict(title=title,source='Test',source_id='test',source_text=BODY,url=f'https://example.org/{index}',published=NOW.isoformat(),language='en',interest_id='science',topic='Science',score=4,preferred=False)


class EditorialTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.config=example('en');self.config['memory'].update(enabled=True,directory=self.tmp.name)
        self.state=State(self.config)

    def tearDown(self):
        self.state.close();self.tmp.cleanup()

    def record(self,item):
        issue,number=self.state.reserve(NOW)
        self.state.record(issue,[item],'issue.pdf')
        return issue

    def test_event_coverage_groups_multiple_publishers(self):
        first=story();second=dict(story(2),source_id='other',source='Other')
        grouped=editorial.prepare([first,second],[first,second],self.config,self.state,NOW,[])
        self.assertEqual(len(grouped),1)
        self.assertEqual(grouped[0]['corroborating_sources'],2)
        self.assertEqual(len(grouped[0]['coverage']),2)

    def test_identical_story_with_new_timestamp_is_repeat(self):
        item=story();self.record(item)
        newer=dict(item,published=(NOW+timedelta(hours=1)).isoformat())
        self.assertEqual(self.state.classify(newer,self.state.recent(NOW)),'repeat')
        self.assertEqual(editorial.prepare([newer],[newer],self.config,self.state,NOW,[]),[])

    def test_substantive_development_is_update(self):
        item=story();self.record(item)
        newer=dict(item,published=(NOW+timedelta(hours=1)).isoformat(),source_text=BODY+' A fourth station recorded 28 degrees after the independent sensor replacement confirmed a previously undetected equipment problem.')
        self.assertEqual(self.state.classify(newer,self.state.recent(NOW)),'update')

    def test_feedback_weights_hide_and_reset(self):
        item=story();issue=self.record(item)
        self.state.feedback(issue,1,'more')
        self.assertGreater(self.state.preferences()[0]['science'],1)
        self.state.feedback(issue,1,'hide')
        self.assertIn(item['url'],self.state.preferences()[1])
        self.state.feedback(issue,1,'reset')
        self.assertEqual(self.state.preferences(),({},set()))

    def test_print_claim_survives_another_connection(self):
        self.state.claim_print('job-key')
        other=State(self.config)
        try:
            with self.assertRaisesRegex(ValueError,'already submitted or pending'): other.claim_print('job-key')
            self.state.finish_print('job-key','request-id-1')
            with self.assertRaises(ValueError): other.claim_print('job-key')
        finally:other.close()

    def test_actual_print_path_deduplicates_and_requires_explicit_force(self):
        output=Path(self.tmp.name)/'print.pdf';writer=PdfWriter();writer.add_blank_page(width=595,height=842)
        with output.open('wb') as handle:writer.write(handle)
        args=Namespace(print_now=True,dry_run_print=False,no_print=False,print_pass=None,force_reprint=False)
        with patch('ulak_app.printing.validate_capabilities'),patch('ulak_app.printing.preflight',return_value={'pages':1}),patch('ulak_app.printing.submit',return_value='test-job') as submit:
            send_to_printer(output,self.config,args,self.state)
            with self.assertRaisesRegex(ValueError,'already submitted or pending'):send_to_printer(output,self.config,args,self.state)
            self.assertEqual(submit.call_count,1)
            args.force_reprint=True
            send_to_printer(output,self.config,args,self.state)
            self.assertEqual(submit.call_count,2)

    def test_preview_does_not_consume_issue_number(self):
        preview=self.state.reserve(NOW,preview=True)
        self.assertEqual(self.state.reserve(NOW),preview)

    def test_discovery_reserves_space_without_excluded_promotions(self):
        science=story();discovery=story(2,'New archaeological findings reveal ancient settlement')
        ad=story(3,'Casino coupon promotion')
        events=editorial.prepare([science,discovery,ad],[science],self.config,self.state,NOW,[])
        chosen=editorial.select(events,self.config)
        self.assertTrue(any(item.get('discovery') for item in chosen))
        self.assertFalse(any('Casino' in item['title'] for item in chosen))

    def test_discovery_respects_the_same_publisher_limit(self):
        self.config['publisher_limit']=1
        items=[dict(story(1),editorial_score=.9),dict(story(2,'Archaeology discovery'),editorial_score=.2,discovery=True,interest_id='discovery')]
        self.assertEqual(len(editorial.select(items,self.config)),1)

    def test_model_rankings_only_accept_supplied_ids(self):
        self.config['editorial']['mode']='model';self.config['summary'].update(provider='ollama',model='test')
        warnings=[]
        with patch('ulak_app.editorial.model_request',return_value={'stories':{'invented':{'event':'event','relevance':1,'significance':1,'novelty':1,'status':'new'}}}):
            events=editorial.prepare([story()],[story()],self.config,self.state,NOW,warnings)
        self.assertEqual(events[0]['ranking_kind'],'heuristic')
        self.assertTrue(warnings)

    def test_semantic_event_groups_and_scores_are_used(self):
        self.config['editorial']['mode']='model';self.config['summary'].update(provider='ollama',model='test')
        a=story();b=dict(story(2,'Independent station measurements confirm earlier research'),source_id='other')
        def rank(settings,system,prompt,schema=None):
            request=json.loads(prompt)
            story_schema=schema['properties']['stories']
            event_schema=story_schema['properties'][request['stories'][0]['id']]['properties']['event']
            self.assertEqual(event_schema['maxLength'],100)
            self.assertEqual(set(story_schema['required']),{s['id'] for s in request['stories']})
            return {'stories':{s['id']:dict(event='same-measurement',relevance=.8,significance=.9,novelty=.7,status='new',update_quote='') for s in request['stories']}}
        with patch('ulak_app.editorial.model_request',side_effect=rank):
            events=editorial.prepare([a,b],[a,b],self.config,self.state,NOW,[])
        self.assertEqual(len(events),1)
        self.assertEqual(events[0]['ranking_kind'],'model')

    def grounded_result(self):
        return dict(title='Station study reports measurements',what_happened='Researchers measured temperatures at three stations and explained the study methods.',context='',why_it_matters='',evidence=[dict(field='title',source_id='S1',quote='Researchers measured temperatures at three stations.'),dict(field='what_happened',source_id='S1',quote='The report explains the methods')])

    def test_large_ranking_requires_every_candidate_without_repeated_ids(self):
        self.config['editorial'].update(mode='model',candidate_limit=40,discovery_count=0)
        self.config['summary'].update(provider='ollama',model='test')
        def rank(settings,system,prompt,schema=None):
            rows=json.loads(prompt)['stories']
            self.assertEqual(len(rows),40)
            result={'stories':{row['id']:dict(event=row['id'],relevance=.8,significance=.9,novelty=.7,status='new',update_quote='') for row in rows}}
            validator=Draft202012Validator(schema)
            self.assertTrue(validator.is_valid(result))
            missing=copy.deepcopy(result)
            missing['stories'].pop(rows[-1]['id'])
            self.assertFalse(validator.is_valid(missing))
            repeated={'stories':[dict(id=rows[0]['id'],**result['stories'][rows[0]['id']]) for _ in rows]}
            self.assertFalse(validator.is_valid(repeated))
            return result
        items=[story(index) for index in range(40)]
        warnings=[]
        with patch('ulak_app.editorial.model_request',side_effect=rank):
            events=editorial.prepare(items,items,self.config,self.state,NOW,warnings)
        self.assertFalse(warnings)
        self.assertTrue(events)
        self.assertTrue(all(item['ranking_kind']=='model' for item in events))

    def test_brief_evidence_must_be_exact_and_numbers_supported(self):
        self.config['summary'].update(provider='ollama',model='test')
        result=self.grounded_result()
        with patch('ulak_app.editorial.model_request',return_value=result):
            self.assertEqual(editorial.evidence_brief(story(),self.config)['brief']['context'],'')
        result['evidence'][0]['quote']='This quote is invented.'
        with patch('ulak_app.editorial.model_request',return_value=result),self.assertRaises(ValueError):
            editorial.evidence_brief(story(),self.config)
        result=self.grounded_result();result['what_happened']+=' The measurement was 99 degrees.'
        with patch('ulak_app.editorial.model_request',return_value=result),self.assertRaisesRegex(ValueError,'number'):
            editorial.evidence_brief(story(),self.config)

    def test_missing_context_evidence_is_rejected(self):
        self.config['summary'].update(provider='ollama',model='test')
        result=self.grounded_result();result['context']='The discovery resolves a decade of debate.'
        with patch('ulak_app.editorial.model_request',return_value=result),self.assertRaisesRegex(ValueError,'Unsupported'):
            editorial.evidence_brief(story(),self.config)

    def test_invalid_brief_repaired_using_original_evidence(self):
        self.config['summary'].update(provider='ollama',model='test')
        invalid=self.grounded_result();invalid['what_happened']='Study published.'
        with patch('ulak_app.editorial.model_request',side_effect=[invalid,self.grounded_result()]) as model:
            result=editorial.evidence_brief(story(),self.config)
        self.assertEqual(result['summary_kind'],'model_summary')
        self.assertEqual(model.call_count,2)
        first,repair=(json.loads(call.args[2]) for call in model.call_args_list)
        self.assertEqual(first['sources'],repair['sources'])
        self.assertEqual(repair['previous_response'],invalid)
        self.assertIn('Insufficient',repair['validation_error'])
        self.assertIn('AT LEAST 8',model.call_args_list[0].args[1])

    def test_invalid_brief_repair_stays_bounded_and_fails_closed(self):
        self.config['summary'].update(provider='ollama',model='test')
        invalid=self.grounded_result();invalid['evidence']=invalid['evidence'][1:]
        for retries in (0,1,2):
            self.config['editorial']['brief_retries']=retries
            with patch('ulak_app.editorial.model_request',return_value=invalid) as model:
                with self.assertRaisesRegex(ValueError,'Unsupported brief field: title'):
                    editorial.evidence_brief(story(),self.config)
            self.assertEqual(model.call_count,retries+1)

    def test_quote_choices_preserve_source_punctuation(self):
        self.config['summary'].update(provider='ollama',model='test')
        item=story();item['source_text']='Intel\u2019s report describes measured performance improvements. Further independent testing is still needed.'
        result=self.grounded_result()
        with patch('ulak_app.editorial.model_request',return_value=result) as model,self.assertRaises(ValueError):
            editorial.evidence_brief(item,self.config)
        quotes=model.call_args.kwargs['schema']['properties']['evidence']['items']['properties']['quote']['enum']
        self.assertIn('Intel\u2019s report describes measured performance improvements.',quotes)
        self.assertNotIn("Intel's report describes measured performance improvements.",quotes)
        with patch('ulak_app.editorial.model_request',return_value=self.grounded_result()) as model:
            editorial.evidence_brief(story(),self.config)
        request=json.loads(model.call_args.args[2])
        quotes=model.call_args.kwargs['schema']['properties']['evidence']['items']['properties']['quote']['enum']
        self.assertEqual(quotes,request['sources'][0]['quote_options'])
        self.assertTrue(all(quote in BODY or quote==story()['title'] for quote in quotes))

    def test_source_headline_is_valid_evidence_for_product_numbers(self):
        self.config['summary'].update(provider='ollama',model='test')
        item=story(title='Zen 6 measurements published')
        result=self.grounded_result()
        result['title']=item['title']
        result['evidence'][0]['quote']=item['title']
        with patch('ulak_app.editorial.model_request',return_value=result):
            accepted=editorial.evidence_brief(item,self.config)
        self.assertEqual(accepted['title'],item['title'])
        result['title']='Zen 99 measurements published'
        with patch('ulak_app.editorial.model_request',return_value=result),self.assertRaisesRegex(ValueError,'number'):
            editorial.evidence_brief(item,self.config)

    def test_model_brief_cache_avoids_repeated_requests(self):
        self.config['summary'].update(provider='ollama',model='test')
        with patch('ulak_app.editorial.model_request',return_value=self.grounded_result()) as model:
            one=editorial.write(story(),self.config,self.state,[],{})
            two=editorial.write(story(),self.config,self.state,[],{})
        self.assertEqual(model.call_count,1)
        self.assertEqual(one['summary'],two['summary'])

    def test_prompt_edit_invalidates_cached_brief(self):
        prompt_dir=Path(self.tmp.name)/'prompts'
        shutil.copytree(prompts.PROMPT_DIR,prompt_dir)
        self.config['summary'].update(provider='ollama',model='test')
        with patch.object(prompts,'PROMPT_DIR',prompt_dir),patch('ulak_app.editorial.model_request',return_value=self.grounded_result()) as model:
            editorial.write(story(),self.config,self.state,[],{})
            editorial.write(story(),self.config,self.state,[],{})
            self.assertEqual(model.call_count,1)
            path=prompt_dir/'brief.txt'
            path.write_text(path.read_text()+'\nPreserve every source qualification.\n')
            editorial.write(story(),self.config,self.state,[],{})
            self.assertEqual(model.call_count,2)
            self.assertIn('Preserve every source qualification.',model.call_args.args[1])

    def test_reading_budget_changes_story_count(self):
        self.config['max_articles']=8;self.config['editorial'].update(reading_minutes=1,words_per_minute=80,discovery_count=0)
        events=[dict(story(i,f'Distinct science {i} subject'),editorial_score=.8) for i in range(8)]
        self.assertLess(len(editorial.select(events,self.config)),8)


class ArticleAndSuggestionTests(unittest.TestCase):
    def test_article_extraction_is_opt_in_and_robots_checked(self):
        config=example('en');item=story();config['sources']=[dict(id='test',url='https://example.org/rss',allow_article_fetch=False)]
        with patch('ulak_app.articles.read') as read:
            self.assertEqual(articles.enrich(item,config,{}),item)
            read.assert_not_called()
        config['sources'][0]['allow_article_fetch']=True
        with patch('ulak_app.articles.read',return_value=b'User-agent: *\nDisallow: /') as read:
            self.assertEqual(articles.enrich(item,config,{}),item)
            self.assertEqual(read.call_count,1)

    def test_permitted_article_has_explicit_provenance(self):
        config=example('en');config['sources']=[dict(id='test',url='https://example.org/rss',allow_article_fetch=True)]
        with patch('ulak_app.articles.read',side_effect=[b'User-agent: *\nAllow: /',b'<html>article</html>']),patch('ulak_app.articles.trafilatura.extract',return_value='evidence '*100):
            result=articles.enrich(story(),config,{})
        self.assertEqual(result['source_text_kind'],'publisher_article')
        self.assertEqual(result['feed_abstract'],BODY)

    def test_interest_suggestions_do_not_read_private_notes(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'Notes/Technology').mkdir(parents=True);(root/'Health').mkdir()
            (root/'Notes/Technology/Linux.md').write_text('# Linux networking\n')
            (root/'Health/private.md').write_text('private medical entry')
            suggestions=personalization.suggest(root,'en')
        self.assertTrue(suggestions['review_required'])
        self.assertTrue(any(item['id']=='open-source' for item in suggestions['suggestions']))
        self.assertNotIn('medical',json.dumps(suggestions))


class PipelineTests(unittest.TestCase):
    def test_issue_feedback_repeat_suppression_and_update_end_to_end(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);config=example('en')
            config['memory'].update(enabled=True,directory=str(root/'state'))
            path=root/'config.json';path.write_text(json.dumps(config))
            output=root/'first.pdf';article=dict(story(),source_id='bbc-tech')
            with patch('daily.collect',return_value=([article],[])),patch('daily.printing.submit') as printer:
                self.assertEqual(main(['--config',str(path),'--no-print','--output',str(output)]),0)
                self.assertEqual(main(['--config',str(path),'--no-print','--output',str(root/'repeat.pdf')]),1)
                printer.assert_not_called()
            manifest=json.loads(output.with_suffix('.json').read_text())
            self.assertEqual(main(['feedback',manifest['issue_id'],'1','more','--config',str(path)]),0)
            state=State(config)
            try:self.assertTrue(state.preferences()[0])
            finally:state.close()
            updated=dict(article,published=(NOW+timedelta(hours=1)).isoformat(),source_text=BODY+' New instruments at a fourth station recorded 28 degrees and confirmed a previously unknown equipment fault.')
            with patch('daily.collect',return_value=([updated],[])):
                self.assertEqual(main(['--config',str(path),'--no-print','--output',str(root/'update.pdf')]),0)
            self.assertEqual(json.loads((root/'update.json').read_text())['articles'][0]['status'],'update')
