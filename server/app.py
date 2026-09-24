#!/usr/bin/env python3
"""Self-contained OWAR live-results server.  Uses only Python's standard library."""
import hashlib, json, os, re, sqlite3, threading, time
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from html.parser import HTMLParser

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "dist"
APP_VERSION = (ROOT / "VERSION").read_text().strip()
DB_FILE = Path(os.getenv("DATABASE_FILE", "/data/results.sqlite"))
POLL_SECONDS = max(1, int(os.getenv("POLL_SECONDS", "5")))
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")
RACETEC_URLS = [x.strip() for x in os.getenv("RACETEC_URLS", "").split("|") if x.strip()]

def db():
    con = sqlite3.connect(DB_FILE)
    con.row_factory = sqlite3.Row
    con.executescript("""
      create table if not exists events (id integer primary key, name text not null, visible integer not null default 1);
      create table if not exists athletes (id integer primary key, name text not null);
      create table if not exists results (event_id integer, athlete_id integer, bib text, position integer, time text, primary key(event_id,athlete_id));
      create table if not exists meta (key text primary key, value text not null);
      create table if not exists sources (url text primary key, active integer not null default 1, last_success text, last_error text);
    """)
    return con

def fields(line): return line.split(':', 1)[1].rstrip('\n').split('|')
def value(row, names, *candidates):
    for name in candidates:
        i = names.get(name)
        if i is not None and i < len(row): return row[i]
    return None

class RaceTecTable(HTMLParser):
    def __init__(self): super().__init__(); self.in_table=False; self.in_row=False; self.in_cell=False; self.row=[]; self.rows=[]
    def handle_starttag(self, tag, attrs):
        a=dict(attrs)
        if tag=='table' and a.get('id')=='ctl00_Content_Main_tblResults': self.in_table=True
        elif self.in_table and tag=='tr': self.in_row=True; self.row=[]
        elif self.in_row and tag in ('td','th'): self.in_cell=True; self.row.append('')
    def handle_data(self, data):
        if self.in_cell and self.row: self.row[-1]+=data.strip()
    def handle_endtag(self, tag):
        if tag in ('td','th'): self.in_cell=False
        elif tag=='tr' and self.in_row: self.in_row=False; self.rows.append(self.row)
        elif tag=='table' and self.in_table: self.in_table=False

def stable_id(value): return int(hashlib.sha256(value.encode()).hexdigest()[:15], 16) % 2000000000
def import_racetec(url):
    page=urlopen(Request(url,headers={'User-Agent':'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131 Safari/537.36','Accept':'text/html,application/xhtml+xml','Accept-Language':'en-GB,en;q=0.9'}),timeout=30).read().decode('utf-8','replace')
    parser=RaceTecTable(); parser.feed(page)
    if len(parser.rows)<2: raise ValueError('RaceTec returned no published result table')
    header=[x.upper() for x in parser.rows[0]]; pos=header.index('POS') if 'POS' in header else 1; name=header.index('NAME') if 'NAME' in header else 2; timing=header.index('TIME') if 'TIME' in header else 3
    event_id=stable_id(url); event_name=re.search(r'<title>\s*(.*?)\s*</title>',page,re.I|re.S); event_name=re.sub('<.*?>','',event_name.group(1)).strip() if event_name else 'RaceTec results'
    rows=[]
    for row in parser.rows[1:]:
        if len(row)<=max(pos,name,timing) or not row[pos].isdigit(): continue
        label=row[name]; bib=re.search(r'#(\S+)',label); rider=re.sub(r'\s*#\S+','',label).strip(); rows.append((event_id,stable_id(url+'|'+rider),bib.group(1) if bib else '',int(row[pos]),row[timing],rider))
    con = db()
    with con:
        con.execute("insert into events(id,name) values(?,?) on conflict(id) do update set name=excluded.name",(event_id,event_name))
        con.execute("delete from results where event_id=?",(event_id,))
        for eid,aid,bib,position,timing,rider in rows:
            con.execute("insert into athletes(id,name) values(?,?) on conflict(id) do update set name=excluded.name",(aid,rider))
            con.execute("insert into results(event_id,athlete_id,bib,position,time) values(?,?,?,?,?)",(eid,aid,bib,position,timing))
        con.execute("insert into meta(key,value) values('last_import',?) on conflict(key) do update set value=excluded.value",(datetime.now(timezone.utc).isoformat(),))
        con.execute("update sources set last_success=?,last_error=null where url=?",(datetime.now(timezone.utc).isoformat(),url))
        con.execute("insert into meta(key,value) values('status','Live') on conflict(key) do nothing")
    con.close()

def watch():
    while True:
        try:
            con=db(); paused=con.execute("select value from meta where key='feed_paused'").fetchone(); con.close()
            if not paused or paused[0] != 'true':
                con=db(); urls=[r[0] for r in con.execute("select url from sources where active=1")]; con.close()
                for url in urls:
                    try: import_racetec(url)
                    except Exception as exc:
                        con=db(); con.execute("update sources set last_error=? where url=?",(str(exc)[:300],url)); con.commit(); con.close(); print(f"Import failed for {url}: {exc}",flush=True)
                if urls: print("Imported RaceTec results", flush=True)
        except Exception as exc: print(f"Import failed: {exc}", flush=True)
        time.sleep(POLL_SECONDS)

class App(SimpleHTTPRequestHandler):
    def translate_path(self, path):
        route = urlparse(path).path
        if route == "/": route = "/index.html"
        candidate = (STATIC / route.lstrip("/")).resolve()
        if STATIC.resolve() not in candidate.parents and candidate != STATIC.resolve():
            return str(STATIC / "index.html")
        return str(candidate)
    def json(self, body, code=200):
        data=json.dumps(body).encode(); self.send_response(code); self.send_header("Content-Type","application/json"); self.send_header("Cache-Control","no-store"); self.end_headers(); self.wfile.write(data)
    def authorized(self): return bool(ADMIN_TOKEN) and self.headers.get("Authorization") == f"Bearer {ADMIN_TOKEN}"
    def do_GET(self):
        path=urlparse(self.path).path
        if path == "/api/public/events":
            con=db(); status=con.execute("select value from meta where key='status'").fetchone(); rows=con.execute("select e.id,e.name,count(r.athlete_id) count from events e left join results r on r.event_id=e.id where e.visible=1 group by e.id having count(r.athlete_id)>0 order by e.id").fetchall(); con.close(); return self.json({"status":status[0] if status else "Live","events":[dict(x) for x in rows]})
        if path.startswith("/api/public/events/") and path.endswith("/results"):
            event_id=path.split("/")[4]; con=db(); rows=con.execute("select r.position,a.name,r.bib,r.time from results r join athletes a on a.id=r.athlete_id where r.event_id=? order by r.position",(event_id,)).fetchall(); con.close(); return self.json([dict(x) for x in rows])
        if path == "/api/admin/status":
            if not self.authorized(): return self.json({"error":"Unauthorized"},401)
            con=db(); meta={r[0]:r[1] for r in con.execute("select key,value from meta")}; events=[dict(r) for r in con.execute("select e.id,e.name,e.visible,count(r.athlete_id) count from events e left join results r on r.event_id=e.id group by e.id order by e.id")]; sources=[dict(r) for r in con.execute("select rowid,url,active,last_success,last_error from sources order by rowid")]; con.close(); return self.json({"version":APP_VERSION,"sources":sources,"pollSeconds":POLL_SECONDS,"meta":meta,"events":events})
        return super().do_GET()
    def do_POST(self):
        path=urlparse(self.path).path
        if not self.authorized(): return self.json({"error":"Unauthorized"},401)
        try: payload=json.loads(self.rfile.read(int(self.headers.get("Content-Length","0"))) or "{}")
        except json.JSONDecodeError: return self.json({"error":"Invalid JSON"},400)
        con=db()
        with con:
            if path == "/api/admin/status": con.execute("insert into meta(key,value) values('status',?) on conflict(key) do update set value=excluded.value",(payload.get("status","Live"),))
            elif path == "/api/admin/feed": con.execute("insert into meta(key,value) values('feed_paused',?) on conflict(key) do update set value=excluded.value",('false' if payload.get('running') else 'true',))
            elif path == "/api/admin/sources":
                url=payload.get('url','').strip()
                if not url.startswith('https://www.racetecresults.com/results.aspx?'): con.close(); return self.json({"error":"Enter a RaceTec results URL"},400)
                con.execute("insert into sources(url,active) values(?,1) on conflict(url) do update set active=1",(url,))
            elif path.startswith("/api/admin/sources/"):
                con.execute("update sources set active=? where rowid=?",(1 if payload.get('active') else 0,path.rsplit("/",1)[1]))
            elif path.startswith("/api/admin/events/"):
                con.execute("update events set visible=? where id=?",(1 if payload.get("visible") else 0,path.rsplit("/",1)[1]))
            else: con.close(); return self.json({"error":"Not found"},404)
        con.close(); return self.json({"ok":True})

if __name__ == "__main__":
    DB_FILE.parent.mkdir(parents=True,exist_ok=True)
    con=db()
    for url in RACETEC_URLS: con.execute("insert into sources(url,active) values(?,1) on conflict(url) do nothing",(url,))
    con.commit(); con.close()
    threading.Thread(target=watch,daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0",int(os.getenv("PORT","8080"))),App).serve_forever()
