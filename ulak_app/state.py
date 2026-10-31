"""Local SQLite journal for issues, feedback, model caches and print claims."""
from __future__ import annotations

from datetime import datetime, timedelta
from difflib import SequenceMatcher
import hashlib
import json
from pathlib import Path
import sqlite3
import re
from .i18n import all_stopwords


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def tokens(text):
    stop = all_stopwords()
    return {word for word in re.findall(r'\w+',text.casefold()) if len(word)>2 and word not in stop}


def similarity(a,b):
    left,right=tokens(a),tokens(b)
    overlap=len(left&right)/max(1,len(left|right))
    return max(overlap,SequenceMatcher(None,a.casefold(),b.casefold()).ratio()*.8)


class State:
    def __init__(self, config):
        self.enabled=config['memory']['enabled']
        self.days=config['memory']['repeat_days']
        self.max_issues=config['memory']['max_issues']
        self.max_cache=config['memory']['max_cache_entries']
        self.db=None
        if not self.enabled:
            return
        root=Path(config['memory']['directory']).expanduser()
        root.mkdir(parents=True,exist_ok=True,mode=0o700)
        path=root/'ulak.sqlite3'
        self.db=sqlite3.connect(path,timeout=15)
        path.chmod(0o600)
        self.db.executescript('''
          PRAGMA journal_mode=WAL;
          CREATE TABLE IF NOT EXISTS issues (number INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE, created TEXT, status TEXT DEFAULT 'draft', path TEXT);
          CREATE TABLE IF NOT EXISTS stories (issue TEXT, position INTEGER, url TEXT, title TEXT, body TEXT, topic TEXT, published TEXT, event TEXT, PRIMARY KEY(issue,position));
          CREATE TABLE IF NOT EXISTS feedback (issue TEXT, position INTEGER, action TEXT, topic TEXT, url TEXT, PRIMARY KEY(issue,position));
          CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value TEXT);
          CREATE TABLE IF NOT EXISTS prints (key TEXT PRIMARY KEY, state TEXT, receipt TEXT);
        ''')

    def close(self):
        if self.db:
            self.db.close()

    def recent(self, now):
        if not self.db:
            return []
        rows=self.db.execute('SELECT s.url,s.title,s.body,s.topic,s.published,s.event FROM stories s JOIN issues i ON s.issue=i.id WHERE i.status=\'ready\' AND julianday(i.created)>=julianday(?) ORDER BY i.number DESC LIMIT 5000',((now-timedelta(days=self.days)).isoformat(),)).fetchall()
        return [dict(zip(('url','title','body','topic','published','event'),row)) for row in rows]

    def classify(self,item,recent):
        title=item.get('original_title',item['title'])
        body=item['source_text']
        matches=[old for old in recent if old['url']==item['url'] or similarity(title,old['title'])>=.55]
        if not matches:
            return 'new'
        old=max(matches,key=lambda row:row['published'])
        old_words,new_words=tokens(old['body']),tokens(body)
        new_numbers=set(re.findall(r'\d+(?:[.,]\d+)?',body))-set(re.findall(r'\d+(?:[.,]\d+)?',old['body']))
        # A new timestamp, paraphrase or punctuation alone is not an update.
        if item['published']>old['published'] and len(new_words-old_words)>=5 and (new_numbers or len(new_words-old_words)/max(1,len(new_words))>.35):
            return 'update'
        return 'repeat'

    def preferences(self):
        weights,hidden={},set()
        if self.db:
            for topic,action,url in self.db.execute('SELECT topic,action,url FROM feedback'):
                if action=='hide': hidden.add(url)
                else: weights[topic]=weights.get(topic,0)+(1 if action=='more' else -1)
        return {topic:max(.25,min(3,1+value*.25)) for topic,value in weights.items()},hidden

    def reserve(self,now,preview=False):
        number=(self.db.execute('SELECT COALESCE(MAX(number),0)+1 FROM issues').fetchone()[0] if self.db else 1)
        if self.db and not preview:
            cursor=self.db.execute('INSERT INTO issues(created) VALUES(?)',(now.isoformat(),))
            number=cursor.lastrowid
            issue=f'UL-{now:%Y%m%d}-{number:04d}'
            self.db.execute('UPDATE issues SET id=? WHERE number=?',(issue,number))
            self.db.commit()
        else:
            issue=f'UL-{now:%Y%m%d}-{number:04d}'
        return issue,number

    def record(self,issue,items,path):
        if not self.db: return
        with self.db:
            self.db.execute("UPDATE issues SET status='ready',path=? WHERE id=?",(str(path),issue))
            self.db.executemany('INSERT INTO stories VALUES(?,?,?,?,?,?,?,?)',[
                (issue,index,item['url'],item.get('original_title',item['title']),item['source_text'][:16000],item['interest_id'],item['published'],item.get('event_id','')) for index,item in enumerate(items,1)])
            old=self.db.execute("SELECT id FROM issues WHERE status='ready' ORDER BY number DESC LIMIT -1 OFFSET ?",(self.max_issues,)).fetchall()
            for (identifier,) in old:
                self.db.execute('DELETE FROM stories WHERE issue=?',(identifier,))
                self.db.execute('DELETE FROM issues WHERE id=?',(identifier,))

    def feedback(self,issue,position,action):
        if not self.db: raise ValueError('Enable memory to save feedback')
        if action not in ('more','less','hide','reset'): raise ValueError('Unknown feedback action')
        if action=='reset':
            with self.db:self.db.execute('DELETE FROM feedback WHERE issue=? AND position=?',(issue,position))
            return
        row=self.db.execute('SELECT topic,url FROM stories WHERE issue=? AND position=?',(issue,position)).fetchone()
        if not row: raise ValueError('Unknown issue/story number; use --history')
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO feedback VALUES(?,?,?,?,?)',(issue,position,action,*row))

    def feedback_history(self):
        if not self.db:return []
        return [dict(zip(('issue','story','action','topic'),row)) for row in self.db.execute('SELECT issue,position,action,topic FROM feedback')]

    def history(self):
        if not self.db: return []
        return self.db.execute("SELECT id,created,path FROM issues WHERE status='ready' ORDER BY number DESC LIMIT 20").fetchall()

    def get_cache(self,key):
        row=self.db.execute('SELECT value FROM cache WHERE key=?',(key,)).fetchone() if self.db else None
        return json.loads(row[0]) if row else None

    def put_cache(self,key,value):
        if self.db and self.max_cache:
            with self.db:
                self.db.execute('INSERT OR REPLACE INTO cache VALUES(?,?)',(key,json.dumps(value,ensure_ascii=False)))
                self.db.execute('DELETE FROM cache WHERE rowid NOT IN (SELECT rowid FROM cache ORDER BY rowid DESC LIMIT ?)',(self.max_cache,))

    def claim_print(self,key):
        if not self.db: return
        try:
            with self.db: self.db.execute("INSERT INTO prints VALUES(?,'pending','')",(key,))
        except sqlite3.IntegrityError:
            raise ValueError('Identical print already submitted or pending; use --force-reprint only for an intentional duplicate') from None

    def finish_print(self,key,receipt):
        if self.db:
            with self.db: self.db.execute("UPDATE prints SET state='submitted',receipt=? WHERE key=?",(receipt or '',key))

    def release_print(self,key):
        if self.db:
            with self.db: self.db.execute("DELETE FROM prints WHERE key=? AND state='pending'",(key,))
