#!/usr/bin/env python3
"""Private-folder RaceTec RDF results service; Python standard library only."""
import hashlib,json,os,sqlite3,threading,time
from datetime import datetime,timezone
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
ROOT=Path(__file__).resolve().parents[1]; STATIC=ROOT/"dist"
VERSION=(ROOT/"VERSION").read_text().strip(); DB_FILE=Path(os.getenv("DATABASE_FILE","/data/results.sqlite"))
POLL_SECONDS=5; ADMIN_TOKEN=os.getenv("ADMIN_TOKEN","")
EXPORT_DIR=Path(os.getenv("RACE_EXPORT_DIR","/race-export")); EXPORT_FILENAME=os.getenv("RACE_EXPORT_FILENAME","results.rdf"); EXPORT_FILE=EXPORT_DIR/EXPORT_FILENAME
EXPORT_HOST_DIR=os.getenv("RACE_EXPORT_HOST_DIR",str(EXPORT_DIR))
def now(): return datetime.now(timezone.utc).isoformat()
def db():
 c=sqlite3.connect(DB_FILE);c.row_factory=sqlite3.Row
 c.executescript("""create table if not exists events(id text primary key,name text not null,visible integer not null default 1,sort_order integer not null default 0);
 create table if not exists athletes(id text primary key,name text not null);
 create table if not exists results(event_id text,athlete_id text,bib text,position integer,time text,primary key(event_id,athlete_id));
 create table if not exists imports(id integer primary key,fingerprint text unique,imported_at text,source_file text,event_count integer,result_count integer);
 create table if not exists result_history(import_id integer,event_id text,athlete_id text,bib text,position integer,time text,primary key(import_id,event_id,athlete_id));
 create table if not exists meta(key text primary key,value text not null);""");return c
def clean(x):
 x=(x or "").strip();return "" if x.upper() in ("NULL","NONE","-") else x
def val(row,*ix):
 for i in ix:
  if i<len(row) and clean(row[i]): return clean(row[i])
 return ""
def rows(text,table):
 p="[DATA].["+table+"]:"
 for line in text.splitlines():
  if line.startswith(p): yield line[len(p):].split("|")
def parse(raw):
 text=raw.decode("utf-8-sig","replace")
 if "[DATA].[EventAthlete]:" not in text: raise ValueError("Not a RaceTec RDF export: EventAthlete data is missing")
 athletes={};events={};out={}
 for r in rows(text,"Athlete"):
  if val(r,0): athletes[val(r,0)]=" ".join(x for x in (val(r,1),val(r,2)) if x)
 for r in rows(text,"RaceEvent"):
  if val(r,1,0): events[val(r,1,0)]=val(r,2,1,3)
 for r in rows(text,"EventAthlete"):
  eid,aid=val(r,1),val(r,2)
  try: pos=int(val(r,24,26))
  except ValueError: continue
  if eid and aid: out.setdefault(eid,[]).append((aid,athletes.get(aid,"Rider "+aid),val(r,18),pos,val(r,21,22,23)))
 parsed=[]
 for order,(eid,standing) in enumerate(out.items()): parsed.append((eid,events.get(eid,"Event "+eid),order,sorted(standing,key=lambda x:(x[3],x[1].casefold()))))
 if not parsed: raise ValueError("The RDF export contains no placed EventAthlete results")
 return parsed
def meta(key,value):
 c=db()
 with c:c.execute("insert into meta(key,value) values(?,?) on conflict(key) do update set value=excluded.value",(key,value))
 c.close()
def import_file(raw,digest):
 parsed=parse(raw);c=db()
 with c:
  if c.execute("select 1 from imports where fingerprint=?",(digest,)).fetchone(): c.close();return False
  cur=c.execute("insert into imports(fingerprint,imported_at,source_file,event_count,result_count) values(?,?,?,?,?)",(digest,now(),EXPORT_FILENAME,len(parsed),sum(len(r) for *_,r in parsed)));iid=cur.lastrowid
  for eid,name,order,standing in parsed:
   c.execute("insert into events(id,name,sort_order) values(?,?,?) on conflict(id) do update set name=excluded.name,sort_order=excluded.sort_order",(eid,name,order));c.execute("delete from results where event_id=?",(eid,))
   for aid,rider,bib,pos,timing in standing:
    c.execute("insert into athletes(id,name) values(?,?) on conflict(id) do update set name=excluded.name",(aid,rider))
    c.execute("insert into results values(?,?,?,?,?)",(eid,aid,bib,pos,timing));c.execute("insert into result_history values(?,?,?,?,?,?)",(iid,eid,aid,bib,pos,timing))
  c.execute("insert into meta(key,value) values('last_import',?) on conflict(key) do update set value=excluded.value",(now(),));c.execute("insert into meta(key,value) values('last_error','') on conflict(key) do update set value=excluded.value")
  c.execute("insert into meta(key,value) values('status','Live') on conflict(key) do nothing")
 c.close();return True
def fstatus():
 try:
  s=EXPORT_FILE.stat();return {"configured":str(EXPORT_FILE),"exists":True,"bytes":s.st_size,"modified":datetime.fromtimestamp(s.st_mtime,timezone.utc).isoformat()}
 except FileNotFoundError:return {"configured":str(EXPORT_FILE),"exists":False,"bytes":0,"modified":None}
def watch():
 candidate=None
 while True:
  try:
   c=db();paused=c.execute("select value from meta where key='feed_paused'").fetchone();c.close()
   if not paused or paused[0]!="true":
    if not EXPORT_FILE.exists(): candidate=None;meta("last_error","Waiting for "+EXPORT_FILENAME)
    else:
     raw=EXPORT_FILE.read_bytes();digest=hashlib.sha256(raw).hexdigest()
     if candidate==digest:
      if import_file(raw,digest): print("Imported stable RaceTec RDF",flush=True)
      candidate=None;meta("source_state","Imported stable file")
     else: candidate=digest;meta("source_state","File changed; verifying stability")
  except Exception as e:candidate=None;meta("last_error",str(e)[:500]);print("RDF import failed:",e,flush=True)
  time.sleep(POLL_SECONDS)
class App(SimpleHTTPRequestHandler):
 def translate_path(self,path):
  route=urlparse(path).path or "/";route="/index.html" if route=="/" else route;p=(STATIC/route.lstrip("/")).resolve();return str(p if STATIC.resolve() in p.parents or p==STATIC.resolve() else STATIC/"index.html")
 def js(self,b,code=200):
  data=json.dumps(b).encode();self.send_response(code);self.send_header("Content-Type","application/json");self.send_header("Cache-Control","no-store");self.end_headers();self.wfile.write(data)
 def auth(self):return bool(ADMIN_TOKEN) and self.headers.get("Authorization")=="Bearer "+ADMIN_TOKEN
 def do_GET(self):
  path=urlparse(self.path).path
  if path=="/api/public/events":
   c=db();s=c.execute("select value from meta where key='status'").fetchone();e=c.execute("select e.id,e.name,count(r.athlete_id) count from events e left join results r on r.event_id=e.id where e.visible=1 group by e.id having count(r.athlete_id)>0 order by e.sort_order,e.name").fetchall();c.close();return self.js({"status":s[0] if s else "Live","events":[dict(x) for x in e]})
  if path.startswith("/api/public/events/") and path.endswith("/results"):
   c=db();r=c.execute("select r.position,a.name,r.bib,r.time from results r join athletes a on a.id=r.athlete_id where r.event_id=? order by r.position,a.name",(path.split("/")[4],)).fetchall();c.close();return self.js([dict(x) for x in r])
  if path=="/api/admin/status":
   if not self.auth():return self.js({"error":"Unauthorized"},401)
   c=db();m={x[0]:x[1] for x in c.execute("select key,value from meta")};e=[dict(x) for x in c.execute("select e.id,e.name,e.visible,count(r.athlete_id) count from events e left join results r on r.event_id=e.id group by e.id order by e.sort_order,e.name")];c.close();return self.js({"version":VERSION,"pollSeconds":POLL_SECONDS,"file":fstatus(),"sourceConfig":{"hostDirectory":EXPORT_HOST_DIR,"filename":EXPORT_FILENAME},"meta":m,"events":e})
  return super().do_GET()
 def do_POST(self):
  if not self.auth():return self.js({"error":"Unauthorized"},401)
  try:p=json.loads(self.rfile.read(int(self.headers.get("Content-Length","0"))) or "{}")
  except json.JSONDecodeError:return self.js({"error":"Invalid JSON"},400)
  path=urlparse(self.path).path
  if path=="/api/admin/status":meta("status",p.get("status","Live"))
  elif path=="/api/admin/feed":meta("feed_paused","false" if p.get("running") else "true")
  elif path.startswith("/api/admin/events/"):
   c=db()
   with c:c.execute("update events set visible=? where id=?",(1 if p.get("visible") else 0,path.rsplit("/",1)[1]))
   c.close()
  else:return self.js({"error":"Not found"},404)
  return self.js({"ok":True})
if __name__=="__main__":
 DB_FILE.parent.mkdir(parents=True,exist_ok=True);threading.Thread(target=watch,daemon=True).start();ThreadingHTTPServer(("0.0.0.0",int(os.getenv("PORT","6543"))),App).serve_forever()

