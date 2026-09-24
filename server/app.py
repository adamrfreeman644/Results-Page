#!/usr/bin/env python3
"""Private-folder RaceTec RDF results service; Python standard library only."""
import hashlib,json,os,sqlite3,threading,time
import re
from datetime import datetime,timezone
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
ROOT=Path(__file__).resolve().parents[1]; STATIC=ROOT/"dist"
VERSION=(ROOT/"VERSION").read_text().strip(); DB_FILE=Path(os.getenv("DATABASE_FILE","/data/results.sqlite"))
POLL_SECONDS=30; ADMIN_TOKEN=os.getenv("ADMIN_TOKEN","")
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
 create table if not exists tournaments(id integer primary key,name text not null unique);
 create table if not exists races(id integer primary key,tournament_id integer not null,name text not null,unique(tournament_id,name));
 create table if not exists event_mappings(event_id text primary key,tournament text,stage text,event_name text,race_id integer);
 create table if not exists meta(key text primary key,value text not null);""")
 try:c.execute("alter table events add column tournament text not null default ''")
 except sqlite3.OperationalError:pass
 try:c.execute("alter table events add column stage text not null default ''")
 except sqlite3.OperationalError:pass
 return c
def clean(x):
 x=(x or "").strip();return "" if x.upper() in ("NULL","NONE","-") else x
def val(row,*ix):
 for i in ix:
  if i<len(row) and clean(row[i]): return clean(row[i])
 return ""
def display_time(value):
 value=clean(value)
 match=re.search(r"(\d{1,2}):(\d{2}):(\d{2})(?:\.(\d+))?",value)
 if not match:
  short=re.search(r"(\d{1,2}):(\d{2})(?:\.(\d+))?$",value)
  if not short:return value
  minute,second,decimal=short.groups();decimal=(decimal or "000")[:3].ljust(3,"0")
  return minute.zfill(2)+":"+second+"."+decimal
 hour,minute,second,decimal=match.groups();decimal=(decimal or "000")[:3].ljust(3,"0")
 return (str(int(hour))+":" if int(hour) else "")+minute+":"+second+"."+decimal
def rows(text,table):
 p="[DATA].["+table+"]:"
 for line in text.splitlines():
  if line.startswith(p): yield line[len(p):].split("|")
def parse(raw):
 # RaceTec commonly exports Unicode (UTF-16 LE) RDF; accept both that and UTF-8.
 if raw.startswith(b"\xff\xfe"): text=raw.decode("utf-16")
 elif raw.startswith(b"\xfe\xff"): text=raw.decode("utf-16")
 else: text=raw.decode("utf-8-sig","replace")
 if "[DATA].[EventAthlete]:" not in text: raise ValueError("Not a RaceTec RDF export: EventAthlete data is missing")
 athletes={};races={};events={};out={}
 for r in rows(text,"Athlete"):
  if val(r,0): athletes[val(r,0)]=" ".join(x for x in (val(r,1),val(r,2)) if x)
 for r in rows(text,"Race"):
  if val(r,0): races[val(r,0)]=val(r,1)
 for r in rows(text,"RaceEvent"):
  if val(r,0) and val(r,1): events[val(r,0)+":"+val(r,1)]=(races.get(val(r,0),"Tournament "+val(r,0)),val(r,2,1,3))
 out={event_id:[] for event_id in events}
 for r in rows(text,"EventAthlete"):
  eid,aid=val(r,0)+":"+val(r,1),val(r,2)
  try: pos=int(val(r,24,26))
  except ValueError: continue
  if eid and aid: out.setdefault(eid,[]).append((aid,athletes.get(aid,"Rider "+aid),val(r,18),pos,display_time(val(r,21,22,23))))
 parsed=[]
 for order,(eid,standing) in enumerate(out.items()):
  tournament,name=events.get(eid,("Tournament", "Event "+eid))
  parsed.append((eid,name,tournament,order,sorted(standing,key=lambda x:(x[3],x[1].casefold()))))
 if not parsed: raise ValueError("The RDF export contains no RaceEvent definitions")
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
  mappings={r[0]:r for r in c.execute("select event_id,tournament,stage,event_name from event_mappings")}
  c.execute("delete from results");c.execute("delete from events")
  for eid,name,tournament,order,standing in parsed:
   mapping=mappings.get(eid)
   if mapping: tournament=mapping[1] or tournament;stage=mapping[2] or "";name=mapping[3] or name
   else: stage=""
   c.execute("insert into events(id,name,tournament,stage,sort_order) values(?,?,?,?,?)",(eid,name,tournament,stage,order))
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
      if import_file(raw,digest+"-tournaments-v2"): print("Imported stable RaceTec RDF",flush=True)
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
   c=db();s=c.execute("select value from meta where key='status'").fetchone();show=c.execute("select value from meta where key='force_show_all'").fetchone();e=c.execute("select e.id,e.name,e.tournament,e.stage,count(r.athlete_id) count from events e left join results r on r.event_id=e.id where e.visible=1 group by e.id "+("" if show and show[0]=="true" else "having count(r.athlete_id)>0")+" order by e.tournament,e.sort_order,e.name").fetchall();c.close();return self.js({"status":s[0] if s else "Live","events":[dict(x) for x in e]})
  if path.startswith("/api/public/events/") and path.endswith("/results"):
   c=db();r=[dict(x) for x in c.execute("select r.position,a.name,r.bib,r.time from results r join athletes a on a.id=r.athlete_id where r.event_id=? order by r.position,a.name",(path.split("/")[4],))];c.close()
   for item in r:item["time"]=display_time(item["time"])
   return self.js(r)
  if path=="/api/admin/status":
   if not self.auth():return self.js({"error":"Unauthorized"},401)
   c=db();m={x[0]:x[1] for x in c.execute("select key,value from meta")};e=[dict(x) for x in c.execute("select e.id,e.name,e.tournament,e.stage,e.visible,count(r.athlete_id) count from events e left join results r on r.event_id=e.id group by e.id order by e.sort_order,e.name")];ts=[dict(x) for x in c.execute("select * from tournaments order by name")];rs=[dict(x) for x in c.execute("select * from races order by name")];c.close();return self.js({"version":VERSION,"pollSeconds":POLL_SECONDS,"file":fstatus(),"sourceConfig":{"hostDirectory":EXPORT_HOST_DIR,"filename":EXPORT_FILENAME},"meta":m,"events":e,"tournaments":ts,"races":rs})
  return super().do_GET()
 def do_POST(self):
  if not self.auth():return self.js({"error":"Unauthorized"},401)
  try:p=json.loads(self.rfile.read(int(self.headers.get("Content-Length","0"))) or "{}")
  except json.JSONDecodeError:return self.js({"error":"Invalid JSON"},400)
  path=urlparse(self.path).path
  if path=="/api/admin/status":meta("status",p.get("status","Live"))
  elif path=="/api/admin/tournaments":
   c=db()
   with c:c.execute("insert into tournaments(name) values(?)",(p.get("name","").strip(),))
   c.close()
  elif path=="/api/admin/standard-structure":
   names=[*(f"Heat {n}" for n in range(1,9)),*(f"Quarter {n}" for n in range(1,5)),*(f"Semi {n}" for n in range(1,3)),"4th's","3rd's","Runner Up's","Final"];c=db()
   with c:
    for tournament in ("Women","Open","Groms"):
     row=c.execute("select id from tournaments where name=?",(tournament,)).fetchone()
     tournament_id=row[0] if row else c.execute("insert into tournaments(name) values(?)",(tournament,)).lastrowid
     for name in names:c.execute("insert or ignore into races(tournament_id,name) values(?,?)",(tournament_id,name))
   c.close()
  elif path=="/api/admin/races":
   c=db()
   with c:c.execute("insert into races(tournament_id,name) values(?,?)",(p.get("tournamentId"),p.get("name","").strip()))
   c.close()
  elif path.startswith("/api/admin/tournaments/") and path.endswith("/duplicate"):
   source_id=int(path.split("/")[4]);name=p.get("name","").strip();c=db()
   with c:
    new_id=c.execute("insert into tournaments(name) values(?)",(name,)).lastrowid
    for row in c.execute("select name from races where tournament_id=?",(source_id,)):c.execute("insert into races(tournament_id,name) values(?,?)",(new_id,row[0]))
   c.close()
  elif path=="/api/admin/feed":meta("feed_paused","false" if p.get("running") else "true")
  elif path=="/api/admin/show-empty":meta("force_show_all","true" if p.get("enabled") else "false")
  elif path=="/api/admin/save":meta("setup_saved",now())
  elif path.startswith("/api/admin/events/"):
   c=db()
   event_id=path.rsplit("/",1)[1]
   with c:
    if p.get("mapping"):
     c.execute("insert into event_mappings(event_id,tournament,stage,event_name) values(?,?,?,?) on conflict(event_id) do update set tournament=excluded.tournament,stage=excluded.stage,event_name=excluded.event_name",(event_id,p.get("tournament",""),p.get("stage",""),p.get("name","")))
     c.execute("update events set tournament=?,stage=?,name=? where id=?",(p.get("tournament",""),p.get("stage",""),p.get("name",""),event_id))
    else:c.execute("update events set visible=? where id=?",(1 if p.get("visible") else 0,event_id))
   c.close()
  else:return self.js({"error":"Not found"},404)
  return self.js({"ok":True})
if __name__=="__main__":
 DB_FILE.parent.mkdir(parents=True,exist_ok=True);threading.Thread(target=watch,daemon=True).start();ThreadingHTTPServer(("0.0.0.0",int(os.getenv("PORT","6543"))),App).serve_forever()

