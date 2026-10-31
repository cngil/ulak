"""Opt-in article extraction honoring publisher scope and robots rules."""
from urllib.parse import urlparse, urljoin
from urllib.robotparser import RobotFileParser
from urllib.request import Request, HTTPRedirectHandler, build_opener

import trafilatura


class SameSiteRedirect(HTTPRedirectHandler):
    def redirect_request(self,request,response,code,message,headers,new_url):
        if urlparse(new_url).hostname != urlparse(request.full_url).hostname:
            return None
        return super().redirect_request(request,response,code,message,headers,new_url)


def read(url):
    request=Request(url,headers={'User-Agent':'Ulak/0.3 (personal newspaper)'})
    with build_opener(SameSiteRedirect()).open(request,timeout=12) as response:
        data=response.read(2_000_001)
    if len(data)>2_000_000: raise ValueError('Article exceeds size limit')
    return data


def enrich(item,config,robots_cache):
    source=next((s for s in config['sources'] if s['id']==item['source_id']),{})
    if not source.get('allow_article_fetch',False): return item
    url=item['url']
    parsed=urlparse(url)
    allowed={urlparse(source['url']).hostname,*source.get('article_hosts',[])}
    if parsed.scheme not in ('http','https') or parsed.hostname not in allowed:
        return item
    origin=f'{parsed.scheme}://{parsed.netloc}'
    if origin not in robots_cache:
        parser=RobotFileParser()
        # Missing/unavailable robots is conservative: retain the feed abstract.
        parser.parse(read(urljoin(origin,'/robots.txt')).decode('utf-8',errors='replace').splitlines())
        robots_cache[origin]=parser
    if not robots_cache[origin].can_fetch('Ulak',url): return item
    html=read(url)
    extracted=trafilatura.extract(html,url=url,include_comments=False,include_tables=False,favor_precision=True)
    if extracted and len(extracted.split())>=80:
        return dict(item,source_text=' '.join(extracted.split())[:16000],source_text_kind='publisher_article',feed_abstract=item['source_text'])
    return item
