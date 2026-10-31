"""Editorial selection, event grouping, grounded briefs and reading budgets."""
from __future__ import annotations

from datetime import datetime
import json
import math
import re

from .feeds import contains, choose
from .summary import model_request, summarize, shorten
from .state import digest, similarity
from . import articles
from .i18n import get_locale
from .prompts import render_prompt, preferences, fingerprint


def prepare(raw,ranked,config,state,now,warnings):
    settings=config['editorial']
    locale=get_locale(config['language'])
    weights,hidden=state.preferences()
    candidates=[]
    known={item['url'] for item in ranked}
    discovery=[]
    if settings['discovery_count']:
        for item in raw:
            if item['url'] not in known and not any(contains(item['title']+' '+item['source_text'],word) for word in config['exclude']):
                discovery.append(dict(item,score=.5,interest_id='discovery',topic=locale['discovery'],preferred=False,discovery=True))
    source_quality={source['id']:source.get('quality',.5) for source in config['sources']}
    recent=state.recent(now)
    discovery.sort(key=lambda item:item['published'],reverse=True)
    reserve=min(len(discovery),max(1,settings['candidate_limit']//5)) if settings['discovery_count'] else 0
    pool=ranked[:max(1,settings['candidate_limit']-reserve)]
    for item in pool+discovery[:settings['candidate_limit']]:
        if item['url'] in hidden: continue
        status=state.classify(item,recent)
        if status=='repeat' and settings['mode']!='model': continue
        quality=source_quality.get(item['source_id'],.5)
        candidates.append(dict(item,editorial_score=(min(1,item.get('score',1)/10)*.7+quality*.3)*weights.get(item['interest_id'],1),
            source_quality=quality,status=status,candidate_id=digest(item['url'])[:12]))
    candidates=candidates[:settings['candidate_limit']]
    if settings['mode']=='model':
        if config['summary']['provider']=='publisher':
            warnings.append('Editorial model ranking requires a configured model; using explicit heuristic ranking')
        elif candidates:
            try:
                request=[dict(id=i['candidate_id'],title=i['title'],abstract=i['source_text'][:1200],topic=i['topic'],quality=i['source_quality'],
                    previous_coverage=[dict(title=old['title'],abstract=old['body'][:800]) for old in recent if old['url']==i['url'] or similarity(i['title'],old['title'])>=.55][:2]) for i in candidates]
                prompt=json.dumps(dict(interests=[dict(name=i['name'],keywords=i['keywords'],weight=i['weight']) for i in config['interests']],stories=request),ensure_ascii=False)
                system=render_prompt('ranking')
                key=digest('rank-v4'+system+prompt+json.dumps({k:v for k,v in config['summary'].items() if k!='api_key_env'},sort_keys=True))
                result=state.get_cache(key) if settings['cache'] else None
                if result is None:
                    properties=dict(event={'type':'string','minLength':1,'maxLength':100},
                        status={'type':'string','enum':['new','repeat','update']},update_quote={'type':'string'})
                    properties.update({field:{'type':'number','minimum':0,'maximum':1} for field in ('relevance','significance','novelty')})
                    row_schema=dict(type='object',properties=properties,required=list(properties),additionalProperties=False)
                    ids=[item['candidate_id'] for item in candidates]
                    schema=dict(type='object',properties={'stories':dict(type='object',
                        properties={candidate_id:row_schema for candidate_id in ids},required=ids,additionalProperties=False)},required=['stories'],additionalProperties=False)
                    result=model_request(config['summary'],system,prompt,schema=schema)
                by_id=result.get('stories') if isinstance(result,dict) else None
                if not isinstance(by_id,dict) or set(by_id)!={item['candidate_id'] for item in candidates}:
                    raise ValueError('Ranking must map every supplied candidate ID exactly once')
                if any(not isinstance(row,dict) for row in by_id.values()): raise ValueError('Invalid ranking entry')
                rows=[dict(row,id=candidate_id) for candidate_id,row in by_id.items()]
                for row in rows:
                    if not isinstance(row.get('event'),str) or not 1<=len(row['event'])<=100: raise ValueError('Invalid event label')
                    for key_name in ('relevance','significance','novelty'):
                        value=row.get(key_name)
                        if type(value) not in (int,float) or not math.isfinite(value) or not 0<=value<=1: raise ValueError('Invalid editorial score')
                    if row.get('status') not in ('new','repeat','update'): raise ValueError('Invalid novelty status')
                    if row['status']=='update':
                        source=next(item for item in candidates if item['candidate_id']==row['id'])
                        previous=next(item['previous_coverage'] for item in request if item['id']==row['id'])
                        quote=row.get('update_quote')
                        if not previous or not isinstance(quote,str) or len(quote)<8 or quote not in source['source_text'] or any(quote in old['abstract'] for old in previous):
                            raise ValueError('Update needs a new, exact source quote and prior coverage')
                for item in candidates:
                    row=by_id[item['candidate_id']]
                    item['model_event']=row['event']
                    item['editorial_score']=(.4*row['relevance']+.3*row['significance']+.2*row['novelty']+.1*item['source_quality'])*weights.get(item['interest_id'],1)
                    item['ranking_kind']='model'
                    item['status']=row['status']
                if settings['cache']: state.put_cache(key,result)
            except Exception as exc:
                warnings.append(f'Editorial ranking failed; heuristic fallback: {type(exc).__name__}: {exc}')
    candidates=[item for item in candidates if item['status']!='repeat']
    candidates.sort(key=lambda item:item['editorial_score'],reverse=True)
    groups=[]
    for item in candidates:
        group=next((group for group in groups if any(
            item['url']==other['url'] or (item.get('model_event') and item.get('model_event')==other.get('model_event')) or
            similarity(item['title'],other['title'])>=settings['cluster_threshold'] for other in group)),None)
        if group is None: groups.append([item])
        else: group.append(item)
    events=[]
    for group in groups:
        primary=dict(group[0])
        primary['event_id']=digest(primary['url'])[:16]
        primary['coverage']=[dict(source=i['source'],source_id=i['source_id'],url=i['url'],title=i['title'],source_text=i['source_text']) for i in group]
        primary['corroborating_sources']=len({i['source_id'] for i in group})
        primary['ranking_kind']=primary.get('ranking_kind','heuristic')
        events.append(primary)
    return events


def select(events,config):
    discovery=[item for item in events if item.get('discovery')]
    regular=[item for item in events if not item.get('discovery')]
    quota=min(config['editorial']['discovery_count'],len(discovery),max(0,config['max_articles']-1))
    pool=discovery[:quota]+regular
    if not pool: pool=discovery[:1]
    selected=choose(pool,config) if pool else []
    selected.sort(key=lambda item:item.get('editorial_score',0),reverse=True)
    # Estimate actual reading from headline + summary; no promises of exact timing.
    budget=config['editorial']['reading_minutes']*config['editorial']['words_per_minute']
    kept=[]
    used=0
    for index,item in enumerate(selected):
        words=min(config['summary']['max_words'],90 if index==0 else 55 if item.get('editorial_score',0)>.45 else 30)
        cost=words+len(item['title'].split())
        if kept and used+cost>budget: continue
        kept.append(dict(item,word_budget=words,format='lead' if not kept else 'story' if words>=50 else 'brief'))
        used+=cost
    return kept


def evidence_brief(item,config):
    sources=item.get('coverage') or [dict(source_id=item['source_id'],title=item['title'],source_text=item['source_text'],url=item['url'])]
    supplied=[dict(source_id=f'S{index}',publisher=s['source_id'],url=s['url'],title=s.get('title',''),text=s['source_text']) for index,s in enumerate(sources,1)]
    # Offer verbatim spans so small models can select evidence without transcribing it.
    for source in supplied:
        source['quote_options']=[span for span in re.split(r'(?<=[.!?])\s+',source['text']) if len(span)>=8]
        if len(source['title'])>=8: source['quote_options'].insert(0,source['title'])
        if not source['quote_options']: source['quote_options']=[source['text']]
    locale=get_locale(config['language'])
    system=render_prompt('brief',language=locale['language_name'],
        max_words=item.get('word_budget',config['summary']['max_words']),
        editorial_preferences=preferences(config['summary']['instructions']))
    schema=dict(type='object',properties={field:{'type':'string'} for field in ('title','what_happened','context','why_it_matters')},required=['title','what_happened','context','why_it_matters','evidence'],additionalProperties=False)
    schema['properties']['evidence']=dict(type='array',minItems=2,items=dict(type='object',properties={
        'field':{'type':'string','enum':['title','what_happened','context','why_it_matters']},
        'source_id':{'type':'string','enum':[source['source_id'] for source in supplied]},
        'quote':{'type':'string','minLength':8,'enum':list(dict.fromkeys(quote for source in supplied for quote in source['quote_options']))}},required=['field','source_id','quote'],additionalProperties=False))
    request=dict(title=item['title'],sources=supplied)
    for attempt in range(config['editorial'].get('brief_retries',1)+1):
        result=model_request(config['summary'],system,json.dumps(request,ensure_ascii=False),schema=schema)
        try:
            return validate_brief(result,item,config,supplied,locale)
        except ValueError as exc:
            if attempt==config['editorial'].get('brief_retries',1): raise
            # Keep the original evidence authoritative; repair never relaxes validation.
            request=dict(title=item['title'],sources=supplied,previous_response=result,
                         validation_error=str(exc),instruction=render_prompt('repair'))


def validate_brief(result,item,config,supplied,locale):
    fields=('title','what_happened','context','why_it_matters')
    if not isinstance(result,dict) or any(not isinstance(result.get(field),str) for field in fields): raise ValueError('Brief must provide all four text fields')
    if not result['title'] or len(result['title'])>300 or len(result['what_happened'].split())<8: raise ValueError('Insufficient grounded brief')
    rows=result.get('evidence')
    if not isinstance(rows,list): raise ValueError('Brief has no evidence references')
    source_map={source['source_id']:source.get('title','')+'\n'+source['text'] for source in supplied}
    supported=set()
    for row in rows:
        if not isinstance(row,dict) or row.get('field') not in fields: raise ValueError('Invalid evidence field')
        quote=row.get('quote')
        if not isinstance(quote,str) or len(quote)<8 or quote not in source_map.get(row.get('source_id'),''): raise ValueError('Evidence quote is not in the supplied source')
        supported.add(row['field'])
    for field in fields:
        if result[field] and field not in supported: raise ValueError(f'Unsupported brief field: {field}')
    numbers=set(re.findall(r'\d+(?:[.,]\d+)?',' '.join(source_map.values())))
    if set(re.findall(r'\d+(?:[.,]\d+)?',' '.join(result[f] for f in fields)))-numbers: raise ValueError('Brief introduced an unsupported number')
    labels=(locale['context'],locale['why_it_matters'])
    body=result['what_happened']
    for field,label in zip(('context','why_it_matters'),labels):
        if result[field]: body+=f' {label}: '+result[field]
    return dict(item,title=result['title'],original_title=item['title'],summary=shorten(body,item.get('word_budget',config['summary']['max_words'])),summary_kind='model_summary',brief=result,
                evidence_sources=[{k:v for k,v in source.items() if k not in ('text','quote_options')} for source in supplied])


def write(item,config,state,warnings,robots_cache):
    try:
        item=articles.enrich(item,config,robots_cache)
        if item.get('coverage'):
            item['coverage']=[dict(item['coverage'][0],source_text=item['source_text'])]+item['coverage'][1:]
    except Exception as exc:
        warnings.append(f'Article extraction {item["source_id"]}: retaining publisher abstract ({type(exc).__name__})')
    if config['summary']['provider']=='publisher':
        result=summarize(item,config)
        result['summary']=shorten(result['summary'],item.get('word_budget',config['summary']['max_words']))
        return result
    prompt_version=fingerprint('brief','repair','preferences') if config['editorial']['structured_briefs'] else fingerprint('summary','preferences')
    key=digest('brief-v5'+prompt_version+str(config['editorial']['structured_briefs'])+json.dumps({k:v for k,v in item.items() if not k.startswith('_')},sort_keys=True)+json.dumps({k:v for k,v in config['summary'].items() if k!='api_key_env'},sort_keys=True)+config['language'])
    cached=state.get_cache(key) if config['editorial']['cache'] else None
    if cached: return dict(item,**cached)
    result=evidence_brief(item,config) if config['editorial']['structured_briefs'] else summarize(item,config)
    if config['editorial']['cache']:
        state.put_cache(key,{key:value for key,value in result.items() if key not in item or value!=item.get(key)})
    return result
