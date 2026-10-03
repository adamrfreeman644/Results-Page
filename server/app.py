#!/usr/bin/env python3
"""Private-folder RaceTec RDF results service; Python standard library only."""
import csv,difflib,hashlib,io,json,os,sqlite3,threading,time,unicodedata
import re
from datetime import datetime,timezone
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse,unquote,parse_qs
from urllib.request import urlopen
ROOT=Path(__file__).resolve().parents[1]; STATIC=ROOT/"dist"
VERSION=os.getenv("BUILD_VERSION",(ROOT/"BUILD_VERSION").read_text().strip() if (ROOT/"BUILD_VERSION").exists() else (ROOT/"VERSION").read_text().strip()); DB_FILE=Path(os.getenv("DATABASE_FILE","/data/results.sqlite"))
POLL_SECONDS=30; ADMIN_TOKEN=os.getenv("ADMIN_TOKEN","")
EXPORT_DIR=Path(os.getenv("RACE_EXPORT_DIR","/race-export")); EXPORT_FILENAME=os.getenv("RACE_EXPORT_FILENAME","results.rdf"); EXPORT_FILE=EXPORT_DIR/EXPORT_FILENAME
EXPORT_HOST_DIR=os.getenv("RACE_EXPORT_HOST_DIR",str(EXPORT_DIR))
REGISTRATION_CACHE={"stamp":None,"cached_at":0.0,"riders":[]}
REGISTRATION_CACHE_LOCK=threading.Lock()
RDF_BIB_NAME_CACHE={"stamp":None,"names":{}}
def now(): return datetime.now(timezone.utc).isoformat()
def db():
 c=sqlite3.connect(DB_FILE);c.row_factory=sqlite3.Row
 c.executescript("""create table if not exists events(id text primary key,name text not null,visible integer not null default 1,sort_order integer not null default 0);
 create table if not exists athletes(id text primary key,name text not null,category text not null default '');
 create table if not exists results(event_id text,athlete_id text,bib text,position integer,time text,category text not null default '',penalty text not null default '',primary key(event_id,athlete_id));
 create table if not exists imports(id integer primary key,fingerprint text unique,imported_at text,source_file text,event_count integer,result_count integer);
 create table if not exists result_history(import_id integer,event_id text,athlete_id text,bib text,position integer,time text,primary key(import_id,event_id,athlete_id));
 create table if not exists result_laps(event_id text,athlete_id text,lap_number integer,time text,primary key(event_id,athlete_id,lap_number));
 create table if not exists team_laps(event_id text,team text,lap_number integer,rider_bib text,rider_name text,time text,is_start integer not null default 0,primary key(event_id,team,lap_number));
 create table if not exists tournaments(id integer primary key,name text not null unique,highlight_count integer not null default 2);
 create table if not exists levels(id integer primary key,tournament_id integer not null,name text not null,sort_order integer not null default 0,unique(tournament_id,name));
 create table if not exists races(id integer primary key,tournament_id integer not null,level_id integer,name text not null,unique(tournament_id,name));
 create table if not exists event_mappings(event_id text primary key,tournament text,level text,stage text,event_name text,race_id integer);
 create table if not exists meta(key text primary key,value text not null);
 create table if not exists athlete_settings(athlete_id text primary key,registered integer not null default 0,chip_code text not null default ''); create table if not exists historical_results(source text not null,row_number integer not null,season text not null,division text not null,event_name text not null,rider_name text not null,normal_name text not null,position text,points text,athlete_id text,match_score real,primary key(source,row_number)); create table if not exists historical_manual_matches(athlete_id text primary key,normal_name text not null,rider_name text not null,approved_at text not null); create table if not exists historical_match_requests(id integer primary key,athlete_id text not null,normal_name text not null,rider_name text not null,status text not null default 'pending',requested_at text not null);""")
 try:c.execute("alter table events add column tournament text not null default ''")
 except sqlite3.OperationalError:pass
 try:c.execute("alter table events add column stage text not null default ''")
 except sqlite3.OperationalError:pass
 try:c.execute("alter table races add column sort_order integer not null default 0")
 except sqlite3.OperationalError:pass
 try:c.execute("alter table races add column fastest_lap integer not null default 0")
 except sqlite3.OperationalError:pass
 try:c.execute("alter table races add column level_id integer")
 except sqlite3.OperationalError:pass
 try:c.execute("alter table events add column level text not null default ''")
 except sqlite3.OperationalError:pass
 try:c.execute("alter table event_mappings add column level text not null default ''")
 except sqlite3.OperationalError:pass
 try:c.execute("alter table event_mappings add column race_id integer")
 except sqlite3.OperationalError:pass
 try:c.execute("alter table tournaments add column sort_order integer not null default 0")
 except sqlite3.OperationalError:pass
 try:c.execute("alter table tournaments add column highlight_count integer not null default 2")
 except sqlite3.OperationalError:pass
 try:c.execute("alter table events add column publish_mode text not null default 'populated'")
 except sqlite3.OperationalError:pass
 try:c.execute("alter table athlete_settings add column chip_returned integer not null default 0")
 except sqlite3.OperationalError:pass
 try:c.execute("alter table results add column category text not null default ''")
 except sqlite3.OperationalError:pass
 try:c.execute("alter table athletes add column category text not null default ''")
 except sqlite3.OperationalError:pass
 try:c.execute("alter table results add column penalty text not null default ''")
 except sqlite3.OperationalError:pass
 # Early installations used integer athlete IDs. Pasted team records need
 # stable text IDs as well, so widen this key without changing existing rows.
 athlete_id_type=next((str(x[2]).lower() for x in c.execute("pragma table_info(athletes)") if x[1]=="id"),"text")
 if "int" in athlete_id_type:
  c.execute("alter table athletes rename to athletes_legacy")
  c.execute("create table athletes(id text primary key,name text not null,category text not null default '')")
  c.execute("insert into athletes(id,name,category) select cast(id as text),name,coalesce(category,'') from athletes_legacy")
  c.execute("drop table athletes_legacy")
 # Older installations used an INTEGER primary key for events. RaceTec event IDs
 # are compound text values (for example, "14:43"), so migrate without losing
 # the existing published rows before the next import.
 event_id_type=next((str(x[2]).lower() for x in c.execute("pragma table_info(events)") if x[1]=="id"),"text")
 if "int" in event_id_type:
  c.execute("alter table events rename to events_legacy")
  c.execute("create table events(id text primary key,name text not null,visible integer not null default 1,sort_order integer not null default 0,tournament text not null default '',level text not null default '',stage text not null default '')")
  c.execute("insert into events(id,name,visible,sort_order,tournament,level,stage) select cast(id as text),name,coalesce(visible,1),coalesce(sort_order,0),coalesce(tournament,''),coalesce(level,''),coalesce(stage,'') from events_legacy")
  c.execute("drop table events_legacy")
 mapping_id_type=next((str(x[2]).lower() for x in c.execute("pragma table_info(event_mappings)") if x[1]=="event_id"),"text")
 if "int" in mapping_id_type:
  c.execute("alter table event_mappings rename to event_mappings_legacy")
  c.execute("create table event_mappings(event_id text primary key,tournament text,level text,stage text,event_name text,race_id integer)")
  c.execute("insert into event_mappings(event_id,tournament,level,stage,event_name,race_id) select cast(event_id as text),tournament,coalesce(level,''),stage,event_name,race_id from event_mappings_legacy")
  c.execute("drop table event_mappings_legacy")
 for tournament_id, in c.execute("select distinct tournament_id from races where level_id is null"):
  row=c.execute("select id from levels where tournament_id=? and name='General'",(tournament_id,)).fetchone();level_id=row[0] if row else c.execute("insert into levels(tournament_id,name) values(?,?)",(tournament_id,"General")).lastrowid
  c.execute("update races set level_id=? where tournament_id=? and level_id is null",(level_id,tournament_id))
 return c

def ensure_wildcard_brackets():
 c=db();created=0
 with c:
  tournaments=list(c.execute("select id,name from tournaments"))
  for tournament in tournaments:
   # Wild Card - Open, Wild Card - Women and Wild Card - Groms are all
   # 16-rider brackets: quarter-finals, semis, then the finals routes.
   name=clean(tournament["name"])
   if "wild" not in name.casefold() or not re.search(r"open|women|grom",name,re.I):continue
   for level_name,race_names in (
    ("Quarters",("Quarter 1","Quarter 2","Quarter 3","Quarter 4")),
    ("Semi",("Semi 1","Semi 2","3rd's","4th's")),
    ("Finals",("Final","Runner Up's")),
   ):
    level=c.execute("select id from levels where tournament_id=? and lower(name)=lower(?) order by id limit 1",(tournament["id"],level_name)).fetchone()
    level_id=level["id"] if level else c.execute("insert into levels(tournament_id,name) values(?,?)",(tournament["id"],level_name)).lastrowid
    for race_name in race_names:
     exists=c.execute("select 1 from races where tournament_id=? and level_id=? and lower(name)=lower(?)",(tournament["id"],level_id,race_name)).fetchone()
     if not exists:c.execute("insert into races(tournament_id,level_id,name) values(?,?,?)",(tournament["id"],level_id,race_name));created+=1
 c.close();return created

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
 return (str(int(hour))+":" if int(hour) else (minute+":" if int(minute) else ""))+second+"."+decimal
def time_ms(value):
 """Convert RaceTec clock values and displayed durations to milliseconds."""
 m=re.search(r"(?:(\d{1,2}):)?(\d{1,2}):(\d{2})(?:\.(\d+))?",clean(value))
 if not m:return None
 h,minute,second,fraction=m.groups();return (((int(h or 0)*3600)+int(minute)*60+int(second))*1000)+int((fraction or "0")[:3].ljust(3,"0"))
def ms_display(total):
 h,total=divmod(total,3600000);minute,total=divmod(total,60000);second,milli=divmod(total,1000)
 return (str(h)+":" if h else (str(minute).zfill(2)+":" if minute else ""))+str(second).zfill(2)+"."+str(milli).zfill(3)
def match_name(value):
 value=re.sub(r"[^a-z0-9]+"," ",clean(value).lower().replace("wild card","wild").replace("wildcard","wild").replace("heats","heat").replace("semifinal","semi").replace("quarterfinal","quarter"))
 return re.sub(r"\b(wild|women|womens|men|mens|grom|groms|open)\b","",value).strip()
def is_wild(value):return bool(re.search(r"\bwild(?:\s*card)?\b",clean(value),re.I))
def match_division(value):
 value=clean(value).lower()
 if "woman" in value:return "women"
 if "grom" in value:return "groms"
 if "men" in value or "open" in value:return "open"
 return ""
def rider_category(values):
 text=" ".join(clean(value).casefold() for value in values)
 if re.search(r"\\bgroms?\\b",text):return "Grom"
 if re.search(r"\\b(female|women|womens)\\b",text):return "Female"
 if re.search(r"\\b(open|men|mens)\\b",text):return "Open"
 return ""
def placeholder_rider_name(name):
 return bool(re.match(r"^(winner|\d+(?:st|nd|rd|th)?|runner\s*up)\b",clean(name),re.I))
def rdf_bib_names():
 # Athlete entries in a later round can be named "Winner…" by RaceTec.
 # The same RDF has the actual people in their earlier race entries.
 if not EXPORT_FILE.exists():return {}
 stat=EXPORT_FILE.stat();stamp=(stat.st_mtime_ns,stat.st_size)
 if RDF_BIB_NAME_CACHE["stamp"]==stamp:return RDF_BIB_NAME_CACHE["names"]
 raw=EXPORT_FILE.read_bytes()
 text=raw.decode("utf-16") if raw.startswith((b"\xff\xfe",b"\xfe\xff")) else raw.decode("utf-8-sig","replace")
 athletes={val(row,0):" ".join(x for x in (val(row,1),val(row,2)) if x) for row in rows(text,"Athlete") if val(row,0)}
 names={}
 for row in rows(text,"EventAthlete"):
  name=clean(athletes.get(val(row,2),""));bib=clean(val(row,18))
  if bib and name and not placeholder_rider_name(name):names.setdefault(bib,name)
 RDF_BIB_NAME_CACHE["stamp"]=stamp;RDF_BIB_NAME_CACHE["names"]=names
 return names
def rider_display_name(c,name,bib):
 # First try the current database, then the authoritative RDF rider list.
 label=clean(name)
 if not placeholder_rider_name(label):return label
 for row in c.execute("select distinct a.name from results r join athletes a on a.id=r.athlete_id where cast(r.bib as text)=? order by a.name",(clean(bib),)):
  candidate=clean(row[0])
  if candidate and not placeholder_rider_name(candidate):return candidate
 # Registration keeps the real person and bib even when RaceTec creates a
 # placeholder athlete for the knockout entry.
 try:
  for rider in registration_riders():
   if clean(rider.get("bib",""))==clean(bib) and clean(rider.get("name","")):return clean(rider["name"])
 except Exception:pass
 return rdf_bib_names().get(clean(bib),label)
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
 athletes={};athlete_categories={};races={};events={};out={};splits={};athlete_splits={};guns={}
 for r in rows(text,"Athlete"):
  if val(r,0):
   athletes[val(r,0)]=" ".join(x for x in (val(r,1),val(r,2)) if x)
   athlete_categories[val(r,0)]=rider_category(r)
 for r in rows(text,"Race"):
  if val(r,0): races[val(r,0)]=val(r,1)
 for r in rows(text,"RaceEvent"):
   if val(r,0) and val(r,1): events[val(r,0)+":"+val(r,1)]=(races.get(val(r,0),"Tournament "+val(r,0)),val(r,2,1,3))
 for r in rows(text,"EventSplit"):
  eid=val(r,0)+":"+val(r,1);lap=val(r,13)
  if eid and val(r,2) and lap.isdigit() and int(lap)>0:splits.setdefault(eid,[]).append((int(lap),val(r,2)))
 for r in rows(text,"EventGun"):
  if val(r,0) and val(r,1):guns[val(r,0)+":"+val(r,1)]=time_ms(val(r,3))
 for r in rows(text,"AthleteSplit"):
  eid,split,aid=val(r,0)+":"+val(r,1),val(r,2),val(r,3);stamp=time_ms(val(r,4))
  if eid and split and aid and stamp is not None:athlete_splits.setdefault((eid,aid),{})[split]=stamp
 out={event_id:[] for event_id in events}
 for r in rows(text,"EventAthlete"):
  eid,aid=val(r,0)+":"+val(r,1),val(r,2)
  # Keep named entrants even before RaceTec publishes a numeric placing. This lets
  # an assigned mass race appear on the public page while results are pending.
  try: pos=int(val(r,24,26)) if val(r,24,26) else None
  except ValueError: pos=None
  if eid and aid and athletes.get(aid):
   status_text=" ".join(clean(item) for item in r)
   dsq=bool(re.search(r"\bdsq\b|disqualif",status_text,re.I))
   dnf=not dsq and bool(re.search(r"\bdnf\b|did\s+not\s+finish|withdrawn",status_text,re.I))
   out.setdefault(eid,[]).append((aid,athletes[aid],val(r,18),None if dsq else pos,"DSQ" if dsq else ("DNF" if dnf else display_time(val(r,21,22,23))),rider_category(r) or athlete_categories.get(aid,"")))
 parsed=[]
 for order,(eid,standing) in enumerate(out.items()):
  tournament,name=events.get(eid,("Tournament", "Event "+eid))
  normal=sorted(standing,key=lambda x:(x[3] is None,x[3] if x[3] is not None else 0,x[1].casefold()))
  lap_splits=sorted(splits.get(eid,[]));fast=[];gun=guns.get(eid);lap_details={}
  if lap_splits and gun is not None:
   for aid,rider,bib,_,_,_ in standing:
    marks=athlete_splits.get((eid,aid),{});previous=gun;laps=[]
    for lap_number,split_id in lap_splits:
     mark=marks.get(split_id)
     if mark is not None and mark>=previous:
      lap=mark-previous
      if lap>0:laps.append((lap_number,lap))
      previous=mark
    if laps:
     lap_details[aid]=[(number,ms_display(lap)) for number,lap in laps]
     fast.append((aid,rider,bib,0,ms_display(min(lap for _,lap in laps)),athlete_categories.get(aid,"")))
  fastest=[(aid,rider,bib,index,timing,category) for index,(aid,rider,bib,_,timing,category) in enumerate(sorted(fast,key=lambda x:(time_ms(x[4]) or 0,x[1].casefold())),1)]
  parsed.append((eid,name,tournament,order,normal,fastest,lap_details))
 if not parsed: raise ValueError("The RDF export contains no RaceEvent definitions")
 return parsed
HISTORICAL_SNAPSHOT=Path(__file__).with_name("historical_snapshot.json")
def history_name(value):
 return re.sub(r"[^a-z0-9]+"," ",unicodedata.normalize("NFKD",clean(value)).encode("ascii","ignore").decode().lower()).strip()
def history_score(left,right):
 a,b=history_name(left),history_name(right)
 if not a or not b:return 0
 return max(difflib.SequenceMatcher(None,a,b).ratio(),difflib.SequenceMatcher(None," ".join(sorted(a.split()))," ".join(sorted(b.split()))).ratio())
def relink_history(c):
 riders=[dict(x) for x in c.execute("select distinct a.id,a.name,e.tournament from athletes a join results r on r.athlete_id=a.id join events e on e.id=r.event_id")]
 for row in list(c.execute("select source,row_number,division,rider_name from historical_results")):
  candidates=[x for x in riders if match_division(x["tournament"])==row[2]]
  best=max(((history_score(row[3],x["name"]),x) for x in candidates),default=(0,None),key=lambda x:x[0])
  c.execute("update historical_results set athlete_id=?,match_score=? where source=? and row_number=?",(best[1]["id"] if best[1] and best[0]>=.999999 else None,best[0],row[0],row[1]))
def import_historical():
 if not HISTORICAL_SNAPSHOT.exists():raise RuntimeError("Bundled historic snapshot is not available")
 try:records=json.loads(HISTORICAL_SNAPSHOT.read_text(encoding="utf-8")).get("records",[])
 except (OSError,json.JSONDecodeError) as e:raise RuntimeError("Bundled historic snapshot could not be read: "+str(e))
 c=db()
 with c:
  c.execute("delete from historical_results")
  for index,row in enumerate(records,1):
   name=clean(row.get("rider_name",""))
   if not name:continue
   source="snapshot:"+str(row.get("season","unknown"))+":"+str(row.get("division","unknown"))
   c.execute("insert into historical_results(source,row_number,season,division,event_name,rider_name,normal_name,position,points) values(?,?,?,?,?,?,?,?,?)",(source,index,str(row.get("season","")),str(row.get("division","")),clean(row.get("event_name","League ranking")),name,history_name(name),clean(str(row.get("position",""))),clean(str(row.get("points","")))))
  relink_history(c)
  c.execute("insert into meta(key,value) values('historical_last_import',?) on conflict(key) do update set value=excluded.value",(now(),))
  c.execute("insert into meta(key,value) values('historical_rows',?) on conflict(key) do update set value=excluded.value",(str(len(records)),))
 c.close();return len(records)
def meta(key,value):
 c=db()
 with c:c.execute("insert into meta(key,value) values(?,?) on conflict(key) do update set value=excluded.value",(key,value))
 c.close()
def seed_withdrawals():
 c=db();row=c.execute("select value from meta where key='seed_withdrawals'").fetchone();c.close()
 if not row or not row[0]:return []
 try:
  data=json.loads(row[0]);return data if isinstance(data,list) else []
 except (TypeError,json.JSONDecodeError):return []
def save_seed_withdrawals(items):
 meta("seed_withdrawals",json.dumps(items,ensure_ascii=False))
def seed_fills():
 c=db();row=c.execute("select value from meta where key='seed_fills'").fetchone();c.close()
 if not row or not row[0]:return {}
 try:
  data=json.loads(row[0]);return data if isinstance(data,dict) else {}
 except (TypeError,json.JSONDecodeError):return {}
def save_seed_fills(data):
 meta("seed_fills",json.dumps(data if isinstance(data,dict) else {},ensure_ascii=False))
def normalize_seed_fills(payload,category=None):
 """Accept {Category:[...]} or a list for one category; keep ordered name/bib rows (including extras)."""
 current=seed_fills()
 if category is not None:
  rows=payload if isinstance(payload,list) else []
  cleaned=[]
  for row in rows:
   if not isinstance(row,dict):continue
   name=clean(str(row.get("name","")));bib=clean(str(row.get("bib","")))
   if not name and not bib:continue
   cleaned.append({"id":clean(str(row.get("id",""))) or hashlib.sha1((category+"|"+name+"|"+bib+"|"+str(len(cleaned))).encode()).hexdigest()[:10],"name":name,"bib":bib,"note":clean(str(row.get("note","")))})
  current[category]=cleaned;return current
 if isinstance(payload,dict):
  out={}
  for cat,rows in payload.items():
   cat=clean(str(cat)) or "Open"
   cleaned=[]
   for row in rows if isinstance(rows,list) else []:
    if not isinstance(row,dict):continue
    name=clean(str(row.get("name","")));bib=clean(str(row.get("bib","")))
    if not name and not bib:continue
    cleaned.append({"id":clean(str(row.get("id",""))) or hashlib.sha1((cat+"|"+name+"|"+bib+"|"+str(len(cleaned))).encode()).hexdigest()[:10],"name":name,"bib":bib,"note":clean(str(row.get("note","")))})
   out[cat]=cleaned
  return out
 return current
def import_file(raw,digest):
 parsed=parse(raw);c=db()
 if c.execute("select 1 from imports where fingerprint=?",(digest,)).fetchone():
  c.close();return False
 with c:
  cur=c.execute("insert into imports(fingerprint,imported_at,source_file,event_count,result_count) values(?,?,?,?,?)",(digest,now(),EXPORT_FILENAME,len(parsed),sum(len(r[4]) for r in parsed)));iid=cur.lastrowid
  mappings={r[0]:r for r in c.execute("select event_id,tournament,level,stage,event_name,race_id from event_mappings")}
  prepared=[dict(r) for r in c.execute("select r.id,t.name tournament,coalesce(l.name,'General') level,r.name race,r.fastest_lap from races r join tournaments t on t.id=r.tournament_id left join levels l on l.id=r.level_id")]
  prepared_by_id={x["id"]:x for x in prepared};prepared_by_name={(x["tournament"],x["race"]):x for x in prepared}
  c.execute("delete from results");c.execute("delete from result_laps");c.execute("delete from events")
  for eid,name,tournament,order,standing,fastest,lap_details in parsed:
   mapping=mappings.get(eid)
   race_config=None;level=""
   if mapping:
    tournament=mapping[1] or tournament;level=mapping[2] or "";stage=mapping[3] or "";name=mapping[4] or name
    race_config=prepared_by_id.get(mapping[5]) or prepared_by_name.get((tournament,stage));level=level or (race_config["level"] if race_config else "")
   else:
    division=match_division(name);candidates=[x for x in prepared if match_name(x["race"])==match_name(name) and is_wild(x["tournament"])==is_wild(name) and (not division or match_division(x["tournament"])==division)]
    if len(candidates)==1:
     tournament,level,stage=candidates[0]["tournament"],candidates[0]["level"],candidates[0]["race"]
     race_config=candidates[0];c.execute("insert into event_mappings(event_id,tournament,level,stage,event_name,race_id) values(?,?,?,?,?,?)",(eid,tournament,level,stage,name,race_config["id"]))
    else: stage=""
   multi_lap=(race_config and race_config["fastest_lap"]) or clean(tournament).casefold()=="multi lap"
   if multi_lap and fastest: standing=fastest
   c.execute("insert into events(id,name,tournament,level,stage,sort_order) values(?,?,?,?,?,?)",(eid,name,tournament,level,stage,order))
   for aid,rider,bib,pos,timing,category in standing:
    c.execute("insert into athletes(id,name,category) values(?,?,?) on conflict(id) do update set name=excluded.name,category=case when excluded.category<>'' then excluded.category else athletes.category end",(aid,rider,category))
    c.execute("insert into results(event_id,athlete_id,bib,position,time,category) values(?,?,?,?,?,?)",(eid,aid,bib,pos,timing,category));c.execute("insert into result_history values(?,?,?,?,?,?)",(iid,eid,aid,bib,pos,timing))
    for lap_number,lap_time in lap_details.get(aid,[]):c.execute("insert into result_laps values(?,?,?,?)",(eid,aid,lap_number,lap_time))
  c.execute("insert into meta(key,value) values('last_import',?) on conflict(key) do update set value=excluded.value",(now(),));c.execute("insert into meta(key,value) values('last_error','') on conflict(key) do update set value=excluded.value")
  c.execute("insert into meta(key,value) values('status','Live') on conflict(key) do nothing")
 relink_history(c)
 c.close();return True

def pasted_key(value):
 return re.sub(r"[^a-z0-9]+","",clean(value).lower())
def team_paste_headers(text):
 first=text.lstrip("\ufeff").splitlines()[0] if text.lstrip("\ufeff").splitlines() else ""
 headers={pasted_key(value) for value in next(csv.reader([first],dialect=csv.excel_tab if "\t" in first else csv.excel),[])}
 return {"team","officiallaps","elapsed"}.issubset(headers)
def team_elapsed(value):
 value=clean(value);match=re.fullmatch(r"(\d{1,2}):(\d{2}):(\d{2})(?:\.(\d+))?",value)
 if not match:return display_time(value)
 hour,minute,second,decimal=match.groups();return str(int(hour))+":"+minute+":"+second+("."+decimal[:3].ljust(3,"0") if decimal else "")
def parse_team_paste(text):
 sample=text.lstrip("\ufeff").strip()
 if not sample:raise ValueError("Paste a Team Race results table first")
 reader=csv.DictReader(io.StringIO(sample),dialect=csv.excel_tab if "\t" in sample.splitlines()[0] else csv.excel)
 headers={pasted_key(name):name for name in (reader.fieldnames or []) if name}
 def field(name):return headers.get(pasted_key(name))
 team_col,lap_col,elapsed_col=field("Team"),field("OfficialLaps"),field("Elapsed")
 if not all((team_col,lap_col,elapsed_col)):raise ValueError("The Team Race table needs Team, OfficialLaps, and Elapsed columns")
 pairs=[]
 for key,rider_col in headers.items():
  match=re.fullmatch(r"lap(\d+)rider",key)
  if match and (time_col:=headers.get("lap"+match.group(1)+"time")):pairs.append((int(match.group(1)),rider_col,time_col))
 teams=[]
 for row in reader:
  team=clean(row.get(team_col,""))
  if not team:continue
  laps=int(re.search(r"\d+",clean(row.get(lap_col,"")) or "0").group()) if re.search(r"\d+",clean(row.get(lap_col,"")) or "") else 0
  circuits=[]
  for number,rider_col,time_col in pairs:
   rider,time_value=clean(row.get(rider_col,"")),display_time(clean(row.get(time_col,"")))
   if not rider or not time_value:continue
   match=re.match(r"\s*(\d+)\s*-\s*(.+)",rider);bib,name=(match.group(1),clean(match.group(2))) if match else ("",rider)
   circuits.append({"number":number,"bib":bib,"name":name,"time":time_value,"start":number==1})
  teams.append({"team":team,"official_laps":laps,"elapsed":team_elapsed(row.get(elapsed_col,"")),"penalty":clean(row.get(field("Penalty"),"")) if field("Penalty") else "","laps":circuits})
 if not teams:raise ValueError("The Team Race table has no team rows")
 teams.sort(key=lambda item:(-item["official_laps"],time_ms(item["elapsed"]) if time_ms(item["elapsed"]) is not None else 10**15,item["team"].casefold()))
 for position,team in enumerate(teams,1):team["position"]=position
 return teams
def parse_pasted_results(text):
 sample=text.lstrip("\ufeff").strip()
 if not sample:raise ValueError("Paste a RaceTec results table first")
 dialect=csv.excel_tab if "\t" in sample.splitlines()[0] else csv.excel
 reader=csv.DictReader(io.StringIO(sample),dialect=dialect)
 headers={pasted_key(name):name for name in (reader.fieldnames or []) if name}
 def field(*names):
  for name in names:
   if pasted_key(name) in headers:return headers[pasted_key(name)]
  return None
 event_col=field("EventDescr","Event description","Event")
 first_col=field("First name","First")
 last_col=field("Last name","Last")
 bib_col=field("Race number","Bib","Race no")
 category_col=field("Gender","Category")
 status_col=field("Finish status","Status")
 finish_col=field("Finish time","Finish")
 net_col=field("Net time","Net")
 leg_col=field("Finish Leg Time","Leg time")
 race_total_col=field("Finish Race Time","Lap 2 Race Time")
 penalty_col=field("Penalty","Time penalty","Penalty time","Penalty seconds")
 position_col=field("Overall position","Position","Overall")
 lap_cols=[]
 for key,header in headers.items():
  match=re.fullmatch(r"lap(\d+)legtime",key,re.I)
  if match:lap_cols.append((int(match.group(1)),header));continue
  # RaceTec's Chair Race export calls lap one simply "Lap Split Time".
  if key=="lapsplittime":lap_cols.append((1,header));continue
  match=re.fullmatch(r"lap(\d+)splittime",key,re.I)
  if match:lap_cols.append((int(match.group(1)),header))
 lap_cols.sort(key=lambda item:item[0])
 if not all((event_col,first_col,last_col,bib_col)):raise ValueError("The pasted table needs EventDescr, First name, Last name, and Race number columns")
 grouped={}
 for row in reader:
  event,name=clean(row.get(event_col,""))," ".join(x for x in (clean(row.get(first_col,"")),clean(row.get(last_col,""))) if x)
  if not event or not name:continue
  status=clean(row.get(status_col,"")) if status_col else ""
  laps=[(number,clean(row.get(header,""))) for number,header in lap_cols if clean(row.get(header,""))]
  timing=clean(row.get(finish_col,"")) if finish_col else ""
  timing=timing or (clean(row.get(race_total_col,"")) if race_total_col else "")
  timing=timing or (clean(row.get(net_col,"")) if net_col else "") or (clean(row.get(leg_col,"")) if leg_col else "")
  # A completed Chair Race is two laps. RaceTec may leave its total blank,
  # while providing both individual split times, so use their sum as the
  # ranking time. Do not rank a rider from a partial lap.
  if not timing and re.search(r"\bchair\s+race\b",event,re.I) and len(laps)>=2:
   values=[time_ms(value) for _,value in laps[:2]]
   if all(value is not None for value in values):timing=ms_display(sum(values))
  # Other multi-lap RaceTec exports often leave Finish time blank. Their race
  # result is the fastest recorded lap in those columns.
  if not timing and laps:
   timing=min((value for _,value in laps if time_ms(value) is not None),key=time_ms,default="")
  dsq=bool(re.search(r"\bdsq\b|disqualif",status,re.I))
  if dsq:timing="DSQ"
  elif re.search(r"\bdnf\b|did\s+not\s+finish|withdrawn",status,re.I):timing="DNF"
  position_text=clean(row.get(position_col,"")) if position_col else ""
  match=re.search(r"\d+",position_text)
  position=None if dsq else (int(match.group()) if match else None)
  grouped.setdefault(event,[]).append({"name":name,"bib":clean(row.get(bib_col,"")),"category":rider_category([row.get(category_col,"")]) if category_col else "","time":display_time(timing),"position":position,"penalty":clean(row.get(penalty_col,"")) if penalty_col else "","laps":[(number,display_time(value)) for number,value in laps]})
 # RaceTec may serialise equal finish times as consecutive positions.
 # Normalise those records so a genuine tie displays the shared placing.
 for event_rows in grouped.values():
  tied={}
  for row in event_rows:
   if row["position"] is not None and time_ms(row["time"]) is not None:
    tied.setdefault(time_ms(row["time"]),[]).append(row)
  for rows_at_time in tied.values():
   if len(rows_at_time)>1:
    place=min(row["position"] for row in rows_at_time)
    for row in rows_at_time:
     row["position"]=place
 if not grouped:raise ValueError("The pasted table has no usable rider rows")
 return grouped
def import_pasted_results(text,replace=False):
 parsed=parse_pasted_results(text);c=db();fingerprint=hashlib.sha256(text.encode("utf-8")).hexdigest()+"-pasted-"+str(time.time_ns())
 with c:
  iid=c.execute("insert into imports(fingerprint,imported_at,source_file,event_count,result_count) values(?,?,?,?,?)",(fingerprint,now(),"Pasted RaceTec table",len(parsed),sum(len(rows) for rows in parsed.values()))).lastrowid
  mappings={clean(row["event_name"]).casefold():row for row in c.execute("select event_name,tournament,level,stage,race_id from event_mappings") if clean(row["event_name"])}
  prepared=[dict(row) for row in c.execute("select r.id,t.name tournament,coalesce(l.name,'General') level,r.name race from races r join tournaments t on t.id=r.tournament_id left join levels l on l.id=r.level_id")]
  for order,(event_name,rows_for_event) in enumerate(parsed.items()):
   event_id="paste:"+hashlib.sha1(event_name.casefold().encode()).hexdigest()[:16]
   # Normal pastes merge into this event. Explicit replacement is available
   # when an operator deliberately wants to remove riders absent from the paste.
   if replace:
    c.execute("delete from result_laps where event_id=?",(event_id,));c.execute("delete from results where event_id=?",(event_id,));c.execute("delete from events where id=?",(event_id,))
   mapping=mappings.get(event_name.casefold())
   if not mapping:
    division=match_division(event_name)
    candidates=[item for item in prepared if match_name(item["race"])==match_name(event_name) and is_wild(item["tournament"])==is_wild(event_name) and (not division or match_division(item["tournament"])==division)]
    if len(candidates)==1:
     item=candidates[0];mapping={"tournament":item["tournament"],"level":item["level"],"stage":item["race"],"race_id":item["id"]}
   tournament,level,stage=(mapping["tournament"],mapping["level"],mapping["stage"]) if mapping else ("","","")
   c.execute("insert into events(id,name,tournament,level,stage,sort_order) values(?,?,?,?,?,?) on conflict(id) do update set name=excluded.name,tournament=excluded.tournament,level=excluded.level,stage=excluded.stage,sort_order=excluded.sort_order",(event_id,event_name,tournament,level,stage,order))
   if mapping:c.execute("insert into event_mappings(event_id,tournament,level,stage,event_name,race_id) values(?,?,?,?,?,?) on conflict(event_id) do update set tournament=excluded.tournament,level=excluded.level,stage=excluded.stage,event_name=excluded.event_name,race_id=excluded.race_id",(event_id,tournament,level,stage,event_name,mapping["race_id"]))
   timed=sorted((row for row in rows_for_event if time_ms(row["time"]) is not None),key=lambda row:(time_ms(row["time"]),row["name"].casefold()))
   if not any(row["position"] is not None for row in rows_for_event):
    for position,row in enumerate(timed,1):row["position"]=position
   for row in rows_for_event:
    athlete=c.execute("select id from athletes where lower(name)=lower(?) order by id limit 1",(row["name"],)).fetchone()
    athlete_id=athlete[0] if athlete else "paste:"+hashlib.sha1(row["name"].casefold().encode()).hexdigest()[:16]
    c.execute("insert into athletes(id,name,category) values(?,?,?) on conflict(id) do update set name=excluded.name,category=case when excluded.category<>'' then excluded.category else athletes.category end",(athlete_id,row["name"],row["category"]))
    # Preserve omitted riders, but replace only a stale RaceTec feeder placeholder
    # when the same event + bib is now pasted with that person's real name.
    if row["bib"] and not placeholder_rider_name(row["name"]):
     stale=[item[0] for item in c.execute("select r.athlete_id from results r join athletes a on a.id=r.athlete_id where r.event_id=? and r.bib=? and r.athlete_id<>? and (lower(a.name) like 'winner%' or lower(a.name) like '2nd%' or lower(a.name) like 'runner up%')",(event_id,row["bib"],athlete_id))]
     for stale_id in stale:
      c.execute("delete from result_laps where event_id=? and athlete_id=?",(event_id,stale_id))
      c.execute("delete from results where event_id=? and athlete_id=?",(event_id,stale_id))
    c.execute("insert into results(event_id,athlete_id,bib,position,time,category,penalty) values(?,?,?,?,?,?,?) on conflict(event_id,athlete_id) do update set bib=excluded.bib,position=case when excluded.time='DSQ' then null when excluded.position is not null then excluded.position else results.position end,time=case when excluded.time<>'' then excluded.time else results.time end,category=case when excluded.category<>'' then excluded.category else results.category end,penalty=case when excluded.penalty<>'' then excluded.penalty else results.penalty end",(event_id,athlete_id,row["bib"],row["position"],row["time"],row["category"],row.get("penalty","")))
    if row.get("laps"):
     c.execute("delete from result_laps where event_id=? and athlete_id=?",(event_id,athlete_id))
     for lap_number,lap_time in row["laps"]:
      c.execute("insert into result_laps values(?,?,?,?)",(event_id,athlete_id,lap_number,lap_time))
    c.execute("insert into result_history values(?,?,?,?,?,?)",(iid,event_id,athlete_id,row["bib"],row["position"],row["time"]))
  c.execute("insert into meta(key,value) values('source_mode','paste') on conflict(key) do update set value=excluded.value")
  c.execute("insert into meta(key,value) values('last_import',?) on conflict(key) do update set value=excluded.value",(now(),))
  c.execute("insert into meta(key,value) values('last_error','') on conflict(key) do update set value=excluded.value")
 c.close();return {"events":len(parsed),"riders":sum(len(rows) for rows in parsed.values())}
def import_team_pasted_results(text):
 teams=parse_team_paste(text);event_id="paste:team-race";c=db();fingerprint=hashlib.sha256(text.encode("utf-8")).hexdigest()+"-team-"+str(time.time_ns())
 with c:
  iid=c.execute("insert into imports(fingerprint,imported_at,source_file,event_count,result_count) values(?,?,?,?,?)",(fingerprint,now(),"Pasted Team Race table",1,len(teams))).lastrowid
  c.execute("delete from team_laps where event_id=?",(event_id,));c.execute("delete from result_laps where event_id=?",(event_id,));c.execute("delete from results where event_id=?",(event_id,));c.execute("delete from events where id=?",(event_id,));c.execute("delete from event_mappings where event_id=?",(event_id,))
  c.execute("insert into events(id,name,tournament,level,stage,sort_order) values(?,?,?,?,?,?)",(event_id,"Team Race","Team Race","Results","Team Race",0))
  c.execute("insert into event_mappings(event_id,tournament,level,stage,event_name) values(?,?,?,?,?)",(event_id,"Team Race","Results","Team Race","Team Race"))
  rider_rows={}
  for team in teams:
   # Legacy live databases may still require numeric athlete IDs. Negative IDs
   # are reserved for internal team records and cannot collide with RaceTec.
   team_id=str(-700000000-team["position"])
   c.execute("insert into athletes(id,name) values(?,?) on conflict(id) do update set name=excluded.name",(team_id,team["team"]))
   c.execute("insert into results(event_id,athlete_id,bib,position,time,penalty) values(?,?,?,?,?,?)",(event_id,team_id,str(team["official_laps"]),team["position"],team["elapsed"],team["penalty"]))
   c.execute("insert into result_history values(?,?,?,?,?,?)",(iid,event_id,team_id,str(team["official_laps"]),team["position"],team["elapsed"]))
   for lap in team["laps"]:
    c.execute("insert into team_laps values(?,?,?,?,?,?,?)",(event_id,team["team"],lap["number"],lap["bib"],lap["name"],lap["time"],1 if lap["start"] else 0))
    if lap["start"]:continue
    key=(lap["bib"],lap["name"]);rider_rows.setdefault(key,[]).append(lap)
  for (bib,name),laps in rider_rows.items():
   athlete=c.execute("select id from athletes where lower(name)=lower(?) limit 1",(name,)).fetchone() or c.execute("select athlete_id from results where cast(bib as text)=? limit 1",(bib,)).fetchone();athlete_id=athlete[0] if athlete else str(-800000000-(int(hashlib.sha1((bib+name).casefold().encode()).hexdigest()[:8],16)%100000000))
   c.execute("insert into athletes(id,name) values(?,?) on conflict(id) do update set name=excluded.name",(athlete_id,name))
   fastest=min((lap["time"] for lap in laps),key=lambda value:time_ms(value) or 10**15)
   c.execute("insert into results(event_id,athlete_id,bib,position,time) values(?,?,?,?,?)",(event_id,athlete_id,bib,None,fastest))
   for lap in laps:c.execute("insert into result_laps values(?,?,?,?)",(event_id,athlete_id,lap["number"]-1,lap["time"]))
  c.execute("insert into meta(key,value) values('source_mode','paste') on conflict(key) do update set value=excluded.value")
  c.execute("insert into meta(key,value) values('last_import',?) on conflict(key) do update set value=excluded.value",(now(),))
  c.execute("insert into meta(key,value) values('last_error','') on conflict(key) do update set value=excluded.value")
 c.close();return {"events":1,"teams":len(teams),"riders":len(rider_rows)}
def registration_riders():
 # The export stays authoritative, while parsing is reused until the file changes
 # (or for at most one reader interval).
 if not EXPORT_FILE.exists():raise FileNotFoundError("Results RDF file not found")
 stat=EXPORT_FILE.stat();stamp=(stat.st_mtime_ns,stat.st_size)
 with REGISTRATION_CACHE_LOCK:
  if REGISTRATION_CACHE["stamp"]==stamp and time.monotonic()-REGISTRATION_CACHE["cached_at"]<POLL_SECONDS:return REGISTRATION_CACHE["riders"]
 raw=EXPORT_FILE.read_bytes()
 if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):text=raw.decode("utf-16")
 else:text=raw.decode("utf-8-sig","replace")
 athletes={}
 for row in rows(text,"Athlete"):
  athlete_id=val(row,0);name=" ".join(x for x in (val(row,1),val(row,2)) if x)
  if athlete_id and name:athletes[athlete_id]={"id":athlete_id,"name":name,"bib":""}
 races={};event_names={}
 for row in rows(text,"Race"):
  if val(row,0):races[val(row,0)]=val(row,1)
 for row in rows(text,"RaceEvent"):
  race_id,event_id=val(row,0),val(row,1)
  if race_id and event_id:event_names[race_id+":"+event_id]=" ".join(x for x in (races.get(race_id,""),val(row,1),val(row,2),val(row,3)) if x)
 registered={}
 for row in rows(text,"EventAthlete"):
  event_id,athlete_id=val(row,0)+":"+val(row,1),val(row,2)
  if athlete_id in athletes and "regist" in event_names.get(event_id,"").casefold():
   item={**athletes[athlete_id],"bib":val(row,18),"_rdf_fields":row}
   if athlete_id not in registered or (item["bib"] and not registered[athlete_id]["bib"]):registered[athlete_id]=item
 # The registration race is the source of truth: every listed rider has a
 # chip. Return state is only true when the export explicitly records it.
 for athlete_id,item in registered.items():
  item["chipAssigned"]=True
  item["chipReturned"]=any(re.fullmatch(r"(?:chip[ _-]*)?returned",clean(value),re.I) for value in item.pop("_rdf_fields",[]))
 result=sorted(registered.values(),key=lambda item:(item["name"].casefold(),item["id"]))
 with REGISTRATION_CACHE_LOCK:
  REGISTRATION_CACHE.update({"stamp":stamp,"cached_at":time.monotonic(),"riders":result})
 return result

def fstatus():
 try:
  s=EXPORT_FILE.stat();return {"configured":str(EXPORT_FILE),"exists":True,"bytes":s.st_size,"modified":datetime.fromtimestamp(s.st_mtime,timezone.utc).isoformat()}
 except FileNotFoundError:return {"configured":str(EXPORT_FILE),"exists":False,"bytes":0,"modified":None}
def watch():
 candidate=None
 while True:
  try:
   c=db();paused=c.execute("select value from meta where key='feed_paused'").fetchone()
   with c:
    c.execute("insert into meta(key,value) values('last_feed_check',?) on conflict(key) do update set value=excluded.value",(now(),))
   source_mode=(c.execute("select value from meta where key='source_mode'").fetchone() or ["rdf"])[0]
   c.close()
   if source_mode=="paste":
    candidate=None;meta("source_state","Using pasted RaceTec table")
   elif not paused or paused[0]!="true":
    if not EXPORT_FILE.exists(): candidate=None;meta("last_error","Waiting for "+EXPORT_FILENAME)
    else:
     raw=EXPORT_FILE.read_bytes();digest=hashlib.sha256(raw).hexdigest()
     if candidate==digest:
      c=db();revision=(c.execute("select value from meta where key='config_revision'").fetchone() or ["0"])[0];c.close()
      if import_file(raw,digest+"-tournaments-v3-"+revision): print("Imported stable RaceTec RDF",flush=True)
      candidate=None;meta("source_state","Imported stable file")
     else: candidate=digest;meta("source_state","File changed; verifying stability")
  except Exception as e:candidate=None;meta("last_error",str(e)[:500]);print("RDF import failed:",e,flush=True)
  time.sleep(POLL_SECONDS)
class App(SimpleHTTPRequestHandler):
 def translate_path(self,path):
  route=urlparse(path).path or "/";route="/index.html" if route=="/" else route;p=(STATIC/route.lstrip("/")).resolve();return str(p if STATIC.resolve() in p.parents or p==STATIC.resolve() else STATIC/"index.html")
 def js(self,b,code=200):
  data=json.dumps(b).encode();self.send_response(code);self.send_header("Content-Type","application/json");self.end_headers();self.wfile.write(data)
 def end_headers(self):
  route=urlparse(self.path).path
  if route.startswith("/api/") or route.endswith(".html") or route=="/":self.send_header("Cache-Control","no-store, max-age=0")
  # Results scripts change during live race control. Do not let a browser
  # retain an older bracket renderer after an operator deploys a correction.
  elif re.search(r"\.(?:css|js)$",route,re.I):self.send_header("Cache-Control","no-store, max-age=0")
  elif re.search(r"\.(?:png|svg|jpg|jpeg|webp|ico)$",route,re.I):self.send_header("Cache-Control","public, max-age=604800, immutable")
  else:self.send_header("Cache-Control","no-store, max-age=0")
  super().end_headers()
 def auth(self):return bool(ADMIN_TOKEN) and self.headers.get("Authorization")=="Bearer "+ADMIN_TOKEN
 def do_GET(self):
  path=urlparse(self.path).path
  if path=="/api/public/events":
   c=db();s=c.execute("select value from meta where key='status'").fetchone();updated=c.execute("select value from meta where key='last_import'").fetchone();show=c.execute("select value from meta where key='force_show_all'").fetchone();source_mode=(c.execute("select value from meta where key='source_mode'").fetchone() or ["rdf"])[0];source_filter=" and e.id like 'paste:%'" if source_mode=="paste" else " and e.id not like 'paste:%'";e=[dict(x) for x in c.execute("select e.id,e.name,e.tournament,e.level,e.stage,coalesce(t.highlight_count,2) highlight_count,case when lower(e.tournament)='multi lap' or exists(select 1 from event_mappings m join races mr on mr.id=m.race_id where m.event_id=e.id and mr.fastest_lap=1) then 1 else 0 end multi_lap,count(r.athlete_id) count from events e left join tournaments t on t.name=e.tournament left join results r on r.event_id=e.id where exists(select 1 from event_mappings m where m.event_id=e.id)"+source_filter+("" if show and show[0]=="true" else " and e.publish_mode!='hide'")+" group by e.id "+("" if show and show[0]=="true" else "having e.publish_mode='always' or count(r.athlete_id)>0")+" order by e.tournament,e.level,e.sort_order,e.name")]
   manual_filter="" if show and show[0]=="true" else " and lower(t.name) in ('open','open men','women','groms','grom')"
   manual_source_filter=" and e.id like 'paste:%'" if source_mode=="paste" else " and e.id not like 'paste:%'"
   for row in c.execute("select r.id,r.name,t.name tournament,coalesce(l.name,'General') level from races r join tournaments t on t.id=r.tournament_id left join levels l on l.id=r.level_id where not exists(select 1 from events e where e.tournament=t.name and e.stage=r.name"+manual_source_filter+")"+manual_filter+" order by t.name,l.sort_order,r.sort_order,r.id"):e.append({"id":"manual:"+str(row[0]),"name":row[1],"tournament":row[2],"level":row[3],"stage":row[1],"count":0})
   c.close();return self.js({"status":s[0] if s else "Live","updatedAt":updated[0] if updated else None,"events":e})
  if path=="/api/public/results":
   ids=[item for item in parse_qs(urlparse(self.path).query).get("ids",[""])[0].split(",") if item and not item.startswith("manual:")]
   if len(ids)>120:return self.js({"error":"Too many races requested"},400)
   c=db();out={item:[] for item in ids}
   if ids:
    marks=",".join("?" for _ in ids)
    for item in c.execute("select r.event_id,r.athlete_id,r.position,a.name,a.name display_name,r.bib,r.time,r.penalty,coalesce(nullif(a.category,''),r.category) category from results r join athletes a on a.id=r.athlete_id where r.event_id in ("+marks+") order by r.event_id,r.position is null,r.position,a.name",ids):
     row=dict(item);row["name"]=rider_display_name(c,row["name"],row["bib"]);row["laps"]=[dict(x) for x in c.execute("select lap_number,time from result_laps where event_id=? and athlete_id=? order by lap_number",(row["event_id"],row["athlete_id"]))];row["time"]=display_time(row["time"]);out.setdefault(row.pop("event_id"),[]).append(row)
   c.close();return self.js(out)
  if path=="/api/public/team-race":
   event_id=clean(parse_qs(urlparse(self.path).query).get("event",["paste:team-race"])[0]);c=db()
   teams=[]
   for row in c.execute("select r.position,a.name team,r.bib official_laps,r.time elapsed,r.penalty from results r join athletes a on a.id=r.athlete_id where r.event_id=? and exists(select 1 from team_laps tl where tl.event_id=r.event_id and tl.team=a.name) order by r.position,a.name",(event_id,)):
    team=dict(row);team["laps"]=[]
    for lap in c.execute("select lap_number,rider_bib,rider_name,time,is_start from team_laps where event_id=? and team=? order by lap_number",(event_id,team["team"])):team["laps"].append(dict(lap))
    completed=[lap["time"] for lap in team["laps"] if not lap["is_start"] and time_ms(lap["time"]) is not None]
    team["fastest_lap"]=min(completed,key=time_ms) if completed else ""
    teams.append(team)
   c.close();return self.js({"teams":teams})
  if path.startswith("/api/public/events/") and path.endswith("/results"):
   event_id=unquote(path.split("/")[4]);c=db();r=[] if event_id.startswith("manual:") else [dict(x) for x in c.execute("select r.athlete_id,r.position,a.name,a.name display_name,r.bib,r.time,r.penalty,coalesce(nullif(a.category,''),r.category) category from results r join athletes a on a.id=r.athlete_id where r.event_id=? order by r.position is null,r.position,a.name",(event_id,))]
   for item in r:
    item["name"]=rider_display_name(c,item["name"],item["bib"])
    item["laps"]=[dict(x) for x in c.execute("select lap_number,time from result_laps where event_id=? and athlete_id=? order by lap_number",(event_id,item["athlete_id"]))]
   c.close()
   for item in r:item["time"]=display_time(item["time"])
   return self.js(r)
  if path=="/api/public/seed-withdrawals":
   return self.js({"withdrawals":seed_withdrawals(),"fills":seed_fills()})
  if path=="/api/admin/seed-fills":
   if not self.auth():return self.js({"error":"Unauthorized"},401)
   return self.js({"fills":seed_fills()})
  if path=="/api/public/registrations":
   try:
    c=db();return_mode=(c.execute("select value from meta where key='chip_return_mode'").fetchone() or ["false"])[0]=="true";c.close()
    return self.js({"riders":registration_riders(),"chipReturnMode":return_mode})
   except Exception as e:return self.js({"error":"Registration list unavailable: "+str(e)[:200]},503)
  if path=="/api/public/riders/search":
   query=parse_qs(urlparse(self.path).query).get("name",[""])[0].strip();c=db();rows=[]
   source_mode=(c.execute("select value from meta where key='source_mode'").fetchone() or ["rdf"])[0]
   source_like="paste:%" if source_mode=="paste" else "paste:%"
   source_op="like" if source_mode=="paste" else "not like"
   if query.isdigit():
    rows=[dict(x) for x in c.execute("select distinct a.id,a.name from athletes a join results r on r.athlete_id=a.id join events e on e.id=r.event_id where e.id "+source_op+" ? and cast(r.bib as text) like ? order by case when cast(r.bib as text)=? then 0 else 1 end,a.name limit 10",(source_like,query+"%",query))]
   elif len(query)>=2:
    key=match_name(query);ranked=[]
    for athlete in c.execute("select id,name from athletes a where exists(select 1 from results r join events e on e.id=r.event_id where r.athlete_id=a.id and e.id "+source_op+" ?)",(source_like,)):
     name_key=match_name(athlete["name"])
     token_score=max([difflib.SequenceMatcher(None,key,part).ratio() for part in name_key.split()] or [0])
     score=max(difflib.SequenceMatcher(None,key,name_key).ratio(),token_score)
     if key in name_key:score+=1
     if score>=.58:ranked.append((score,dict(athlete)))
    rows=[item[1] for item in sorted(ranked,key=lambda item:(-item[0],item[1]["name"]))[:10]]
   c.close();return self.js(rows)
  if path=="/api/public/historical/search":
   query=parse_qs(urlparse(self.path).query).get("name",[""])[0].strip();c=db()
   rows=[] if len(query)<2 else [dict(x) for x in c.execute("select normal_name,min(rider_name) rider_name,count(*) records from historical_results where lower(rider_name) like ? group by normal_name order by records desc,rider_name limit 12",("%"+query.lower()+"%",))]
   c.close();return self.js(rows)
  if path=="/api/public/historical/by-name":
   key=parse_qs(urlparse(self.path).query).get("key",[""])[0];c=db();rows=[dict(x) for x in c.execute("select season,division,event_name,rider_name,position,points from historical_results where normal_name=? order by season desc,event_name",(key,))];c.close();return self.js(rows)
  if path.startswith("/api/public/riders/"):
   athlete_id=unquote(path.rsplit("/",1)[1]);c=db()
   source_mode=(c.execute("select value from meta where key='source_mode'").fetchone() or ["rdf"])[0]
   source_like="paste:%" if source_mode=="paste" else "paste:%"
   source_op="like" if source_mode=="paste" else "not like"
   rider=c.execute("select a.id,a.name,a.category,coalesce(s.registered,0) registered,coalesce(s.chip_code,'') chip_code,coalesce(s.chip_returned,0) chip_returned from athletes a left join athlete_settings s on s.athlete_id=a.id where a.id=?",(athlete_id,)).fetchone()
   if not rider:
    c.close()
    try:rider=next((item for item in registration_riders() if item["id"]==athlete_id),None)
    except Exception:rider=None
    if not rider:return self.js({"error":"Rider not found"},404)
    return self.js({"id":rider["id"],"name":rider["name"],"bib":rider.get("bib",""),"records":[],"historical":[],"registered":True,"chipReturned":bool(rider.get("chipReturned")),"chipCode":"","chipReturnInfo":""})
   records=[dict(x) for x in c.execute("select e.id event_id,e.tournament,e.level,e.stage,e.name race,r.bib,r.position,r.time,r.penalty from results r join events e on e.id=r.event_id where r.athlete_id=? and e.id "+source_op+" ? order by e.tournament,e.level,e.sort_order,r.position",(athlete_id,source_like))]
   for record in records:
    record["time"]=display_time(record["time"])
    record["laps"]=[dict(x) for x in c.execute("select lap_number,time from result_laps where event_id=? and athlete_id=? order by lap_number",(record["event_id"],athlete_id))]
    for lap in record["laps"]:lap["time"]=display_time(lap["time"])
   historical=[dict(x) for x in c.execute("select season,division,event_name,rider_name,position,points,match_score from historical_results where athlete_id=? and match_score>=0.999999 order by season desc,event_name",(athlete_id,))]
   notice=(c.execute("select value from meta where key='chip_return_info'").fetchone() or [""])[0];c.close();return self.js({"id":rider["id"],"name":rider["name"],"category":rider["category"],"records":records,"historical":historical,"registered":True,"chipReturned":bool(rider["chip_returned"]),"chipCode":rider["chip_code"],"chipReturnInfo":notice})
  if path=="/api/admin/riders":
   if not self.auth():return self.js({"error":"Unauthorized"},401)
   c=db();riders=[dict(x) for x in c.execute("select a.id,a.name,coalesce(s.registered,0) registered,coalesce(s.chip_code,'') chip_code,coalesce(s.chip_returned,0) chip_returned from athletes a left join athlete_settings s on s.athlete_id=a.id where exists(select 1 from results r where r.athlete_id=a.id) order by a.name")];notice=(c.execute("select value from meta where key='chip_return_info'").fetchone() or [""])[0];c.close();return self.js({"returnInfo":notice,"riders":riders})
  if path=="/api/admin/seed-withdrawals":
   if not self.auth():return self.js({"error":"Unauthorized"},401)
   return self.js({"withdrawals":seed_withdrawals()})
  if path=="/api/admin/status":
   if not self.auth():return self.js({"error":"Unauthorized"},401)
   c=db();m={x[0]:x[1] for x in c.execute("select key,value from meta")};e=[dict(x) for x in c.execute("select e.id,e.name,e.tournament,e.level,e.stage,e.visible,e.publish_mode,count(r.athlete_id) count from events e left join results r on r.event_id=e.id group by e.id order by e.sort_order,e.name")];ts=[dict(x) for x in c.execute("select * from tournaments order by sort_order,id")];ls=[dict(x) for x in c.execute("select * from levels order by sort_order,id")];rs=[dict(x) for x in c.execute("select * from races order by sort_order,id")];requests=[dict(x) for x in c.execute("select q.id,q.athlete_id,a.name athlete_name,q.rider_name,q.status,q.requested_at from historical_match_requests q left join athletes a on a.id=q.athlete_id where q.status='pending' order by q.id")];bibs={};
   for row in c.execute("select r.event_id,r.bib from results r where nullif(trim(r.bib),'') is not null order by r.event_id,r.position is null,r.position,r.bib"):
    bibs.setdefault(row[0],[]).append(str(row[1]))
   results_by_event={};
   for row in c.execute("select event_id,bib,position,time from results where nullif(trim(bib),'') is not null order by event_id,position is null,position,bib"):
    results_by_event.setdefault(row[0],[]).append({"bib":str(row[1]),"position":row[2],"time":display_time(row[3])})
   c.close();return self.js({"version":VERSION,"pollSeconds":POLL_SECONDS,"file":fstatus(),"sourceConfig":{"hostDirectory":EXPORT_HOST_DIR,"filename":EXPORT_FILENAME},"meta":m,"events":e,"eventBibs":bibs,"eventResults":results_by_event,"tournaments":ts,"levels":ls,"races":rs,"matchRequests":requests,"seedWithdrawals":seed_withdrawals(),"seedFills":seed_fills()})
  return super().do_GET()
 def do_POST(self):
  path=urlparse(self.path).path
  try:p=json.loads(self.rfile.read(int(self.headers.get("Content-Length","0"))) or "{}")
  except json.JSONDecodeError:return self.js({"error":"Invalid JSON"},400)
  if path=="/api/public/historical/match-request":
   athlete_id=str(p.get("athleteId",""));key=str(p.get("key",""));c=db();athlete=c.execute("select name from athletes where id=?",(athlete_id,)).fetchone();candidate=c.execute("select min(rider_name) from historical_results where normal_name=?",(key,)).fetchone()
   if not athlete or not candidate or not key:c.close();return self.js({"error":"Invalid historic match request"},400)
   existing=c.execute("select id from historical_match_requests where athlete_id=? and normal_name=? and status='pending'",(athlete_id,key)).fetchone()
   with c:
    if not existing:c.execute("insert into historical_match_requests(athlete_id,normal_name,rider_name,status,requested_at) values(?,?,?,'pending',?)",(athlete_id,key,candidate[0],datetime.now().isoformat(timespec="seconds")))
   c.close();return self.js({"ok":True,"pending":True})
  if not self.auth():return self.js({"error":"Unauthorized"},401)
  if path=="/api/admin/seed-setup":
   c=db();created=0
   with c:
    for tournament in c.execute("select id,name from tournaments"):
     if not re.search(r"open|women|grom",tournament["name"],re.I):continue
     level=c.execute("select id from levels where tournament_id=? and lower(name) in ('qualifiers','qualifier') order by id limit 1",(tournament["id"],)).fetchone()
     if not level:level_id=c.execute("insert into levels(tournament_id,name) values(?,?)",(tournament["id"],"Qualifiers")).lastrowid
     else:level_id=level["id"]
     race_names=("Seeding Q1","Seeding Q2") if re.search(r"open",tournament["name"],re.I) else ("Seeding",)
     for race_name in race_names:
      exists=c.execute("select 1 from races where tournament_id=? and level_id=? and lower(name)=lower(?)",(tournament["id"],level_id,race_name)).fetchone()
      if not exists:c.execute("insert into races(tournament_id,level_id,name) values(?,?,?)",(tournament["id"],level_id,race_name));created+=1
   c.close();return self.js({"ok":True,"created":created})
  elif path=="/api/admin/seed-withdrawals":
   items=seed_withdrawals();action=clean(str(p.get("action","add"))).lower()
   if action=="clear":
    save_seed_withdrawals([]);return self.js({"ok":True,"withdrawals":[],"fills":seed_fills()})
   if action=="remove":
    wid=clean(str(p.get("id","")));bib=clean(str(p.get("bib","")));cat=clean(str(p.get("category","")))
    kept=[x for x in items if not ((wid and x.get("id")==wid) or (bib and cat and str(x.get("bib",""))==bib and str(x.get("category",""))==cat))]
    save_seed_withdrawals(kept);return self.js({"ok":True,"withdrawals":kept,"fills":seed_fills()})
   # add / upsert by category+bib or category+athlete
   category=clean(str(p.get("category","Open"))) or "Open"
   bib=clean(str(p.get("bib","")));athlete_id=clean(str(p.get("athleteId",p.get("athlete_id",""))));name=clean(str(p.get("name","")));note=clean(str(p.get("note","")))
   seed_val=p.get("seed",None)
   try:seed_num=int(seed_val) if seed_val not in (None,"") else None
   except (TypeError,ValueError):seed_num=None
   if not bib and not athlete_id and not name and seed_num is None:return self.js({"error":"Provide a chip/bib, rider name, athlete id, or seed number"},400)
   # Resolve identity from current results when possible (include DNF / untimed rows).
   if (bib or name) and not athlete_id:
    c=db()
    if bib:
     hit=c.execute("select a.id,a.name,r.bib from results r join athletes a on a.id=r.athlete_id where trim(cast(r.bib as text))=? order by case when upper(coalesce(r.time,''))='DNF' then 1 else 0 end,r.position is null,r.position limit 1",(bib,)).fetchone()
     if hit:athlete_id,name,bib=str(hit[0]),hit[1] or name,str(hit[2] or bib)
    if not athlete_id and name:
     hit=c.execute("select a.id,a.name,r.bib from results r join athletes a on a.id=r.athlete_id where lower(a.name)=lower(?) or lower(a.name) like ? order by r.position is null,r.position limit 1",(name,"%"+name.lower()+"%")).fetchone()
     if hit:athlete_id,name,bib=str(hit[0]),hit[1] or name,str(hit[2] or bib or "")
    c.close()
   if not athlete_id and not name and bib:
    return self.js({"error":"No rider found for chip/bib "+bib+" — check the number or pick them from the seed list"},404)
   entry={"id":hashlib.sha1((category+"|"+athlete_id+"|"+bib+"|"+name+"|"+str(seed_num or "")).encode()).hexdigest()[:12],"category":category,"athlete_id":athlete_id,"bib":bib,"name":name,"seed":seed_num,"note":note,"at":now()}
   items=[x for x in items if not (str(x.get("category",""))==category and ((athlete_id and str(x.get("athlete_id",""))==str(athlete_id)) or (bib and str(x.get("bib",""))==bib) or (name and history_name(x.get("name",""))==history_name(name))))]
   items.append(entry);save_seed_withdrawals(items);return self.js({"ok":True,"withdrawal":entry,"withdrawals":items,"fills":seed_fills()})
  elif path=="/api/admin/seed-fills":
   category=clean(str(p.get("category","")))
   if category:data=normalize_seed_fills(p.get("fills",[]),category)
   else:data=normalize_seed_fills(p.get("fills",{}))
   save_seed_fills(data);return self.js({"ok":True,"fills":data,"withdrawals":seed_withdrawals()})
  elif path=="/api/admin/chip-return-info":meta("chip_return_info",str(p.get("returnInfo","")).strip())
  elif path=="/api/admin/chip-return-mode":meta("chip_return_mode","true" if p.get("enabled") else "false")
  elif re.fullmatch(r"/api/admin/riders/[^/]+",path):
   athlete_id=unquote(path.rsplit("/",1)[1]);c=db()
   with c:c.execute("insert into athlete_settings(athlete_id,registered,chip_code,chip_returned) values(?,?,?,?) on conflict(athlete_id) do update set registered=excluded.registered,chip_code=excluded.chip_code,chip_returned=excluded.chip_returned",(athlete_id,1 if p.get("registered") else 0,clean(str(p.get("chipCode",""))),1 if p.get("chipReturned") else 0))
   c.close()
  elif path=="/api/admin/status":meta("status",p.get("status","Live"))
  elif re.fullmatch(r"/api/admin/historical-match-requests/\d+",path):
   request_id=int(path.rsplit("/",1)[1]);c=db();row=c.execute("select athlete_id,normal_name,rider_name from historical_match_requests where id=? and status='pending'",(request_id,)).fetchone()
   if row:
    with c:
     if p.get("approve"):c.execute("insert into historical_manual_matches(athlete_id,normal_name,rider_name,approved_at) values(?,?,?,?) on conflict(athlete_id) do update set normal_name=excluded.normal_name,rider_name=excluded.rider_name,approved_at=excluded.approved_at",(row[0],row[1],row[2],datetime.now().isoformat(timespec="seconds")));c.execute("update historical_match_requests set status='approved' where id=?",(request_id,))
     else:c.execute("update historical_match_requests set status='declined' where id=?",(request_id,))
   c.close()
  elif path=="/api/admin/tournaments":
   c=db()
   with c:c.execute("insert into tournaments(name) values(?)",(p.get("name","").strip(),))
   c.close()
  elif path=="/api/admin/levels":
   c=db()
   with c:c.execute("insert into levels(tournament_id,name) values(?,?)",(p.get("tournamentId"),p.get("name","").strip()))
   c.close()
  elif path=="/api/admin/races":
   c=db()
   with c:c.execute("insert into races(tournament_id,level_id,name) values(?,?,?)",(p.get("tournamentId"),p.get("levelId"),p.get("name","").strip()))
   c.close()
  elif path.startswith("/api/admin/races/") and path.endswith("/move"):
   race_id=int(path.split("/")[4]);c=db();row=c.execute("select tournament_id from races where id=?",(race_id,)).fetchone()
   if row:
    ordered=[x[0] for x in c.execute("select id from races where tournament_id=? order by sort_order,id",(row[0],))];index=ordered.index(race_id);other=index+(-1 if p.get("direction")=="up" else 1)
    if 0<=other<len(ordered):ordered[index],ordered[other]=ordered[other],ordered[index]
    with c:
     for position,item in enumerate(ordered):c.execute("update races set sort_order=? where id=?",(position,item))
   c.close()
  elif path.startswith("/api/admin/races/") and path.endswith("/fastest-lap"):
   race_id=int(path.split("/")[4]);c=db()
   with c:c.execute("update races set fastest_lap=? where id=?",(1 if p.get("enabled") else 0,race_id))
   c.close();meta("config_revision",str(time.time_ns()))
  elif re.fullmatch(r"/api/admin/tournaments/\d+/highlight-count",path):
   item_id=int(path.split("/")[4]);count=max(0,min(10,int(p.get("count",2))));c=db()
   with c:c.execute("update tournaments set highlight_count=? where id=?",(count,item_id))
   c.close()
  elif re.fullmatch(r"/api/admin/tournaments/\d+",path):
   item_id=int(path.rsplit("/",1)[1]);c=db()
   with c:
    row=c.execute("select name from tournaments where id=?",(item_id,)).fetchone()
    if row and p.get("delete"):
     old=row[0];c.execute("delete from event_mappings where tournament=?",(old,));c.execute("update events set tournament='',level='',stage='' where tournament=?",(old,));c.execute("delete from races where tournament_id=?",(item_id,));c.execute("delete from levels where tournament_id=?",(item_id,));c.execute("delete from tournaments where id=?",(item_id,))
    elif row and p.get("name","").strip():
     new=p["name"].strip();old=row[0];c.execute("update tournaments set name=? where id=?",(new,item_id));c.execute("update event_mappings set tournament=? where tournament=?",(new,old));c.execute("update events set tournament=? where tournament=?",(new,old))
   c.close()
  elif re.fullmatch(r"/api/admin/tournaments/\d+/move",path):
   item_id=int(path.split("/")[4]);c=db();ordered=[x[0] for x in c.execute("select id from tournaments order by sort_order,id")];i=ordered.index(item_id) if item_id in ordered else -1;j=i+(-1 if p.get("direction")=="up" else 1)
   if 0<=i<len(ordered) and 0<=j<len(ordered): ordered[i],ordered[j]=ordered[j],ordered[i]
   with c:
    for n,item in enumerate(ordered):c.execute("update tournaments set sort_order=? where id=?",(n,item))
   c.close()
  elif re.fullmatch(r"/api/admin/levels/\d+",path):
   item_id=int(path.rsplit("/",1)[1]);c=db()
   with c:
    row=c.execute("select l.name,t.name tournament from levels l join tournaments t on t.id=l.tournament_id where l.id=?",(item_id,)).fetchone()
    if row and p.get("delete"):
     old,tournament=row[0],row[1];c.execute("delete from event_mappings where tournament=? and level=?",(tournament,old));c.execute("update events set level='',stage='' where tournament=? and level=?",(tournament,old));c.execute("delete from races where level_id=?",(item_id,));c.execute("delete from levels where id=?",(item_id,))
    elif row and p.get("name","").strip():
     new=p["name"].strip();old,tournament=row[0],row[1];c.execute("update levels set name=? where id=?",(new,item_id));c.execute("update event_mappings set level=? where tournament=? and level=?",(new,tournament,old));c.execute("update events set level=? where tournament=? and level=?",(new,tournament,old))
   c.close()
  elif re.fullmatch(r"/api/admin/levels/\d+/move",path):
   item_id=int(path.split("/")[4]);c=db();row=c.execute("select tournament_id from levels where id=?",(item_id,)).fetchone();ordered=[] if not row else [x[0] for x in c.execute("select id from levels where tournament_id=? order by sort_order,id",(row[0],))];i=ordered.index(item_id) if item_id in ordered else -1;j=i+(-1 if p.get("direction")=="up" else 1)
   if 0<=i<len(ordered) and 0<=j<len(ordered): ordered[i],ordered[j]=ordered[j],ordered[i]
   with c:
    for n,item in enumerate(ordered):c.execute("update levels set sort_order=? where id=?",(n,item))
   c.close()
  elif re.fullmatch(r"/api/admin/races/\d+",path):
   item_id=int(path.rsplit("/",1)[1]);c=db()
   with c:
    row=c.execute("select r.name,t.name tournament,coalesce(l.name,'') level from races r join tournaments t on t.id=r.tournament_id left join levels l on l.id=r.level_id where r.id=?",(item_id,)).fetchone()
    if row and p.get("delete"):
     old,tournament,level=row[0],row[1],row[2];c.execute("delete from event_mappings where race_id=?",(item_id,));c.execute("update events set stage='' where tournament=? and level=? and stage=?",(tournament,level,old));c.execute("delete from races where id=?",(item_id,))
    elif row and p.get("name","").strip():
     new=p["name"].strip();old,tournament,level=row[0],row[1],row[2];c.execute("update races set name=? where id=?",(new,item_id));c.execute("update event_mappings set stage=? where race_id=?",(new,item_id));c.execute("update events set stage=? where tournament=? and level=? and stage=?",(new,tournament,level,old))
   c.close()
  elif path=="/api/admin/historical-import":
   try:return self.js({"ok":True,"rows":import_historical()})
   except Exception as e:return self.js({"error":"Historic import failed: "+str(e)[:300]},502)
  elif path=="/api/admin/import-now":
   if not EXPORT_FILE.exists():return self.js({"error":"RDF file not found"},404)
   raw=EXPORT_FILE.read_bytes();import_file(raw,hashlib.sha256(raw).hexdigest()+"-manual-"+str(time.time_ns()));meta("source_mode","rdf");meta("source_state","Manually imported current RDF file")
  elif path=="/api/admin/import-text":
   text=str(p.get("text",""));result=import_team_pasted_results(text) if team_paste_headers(text) else import_pasted_results(text,bool(p.get("replace")));meta("source_state","Imported Team Race table" if team_paste_headers(text) else "Imported pasted RaceTec table");return self.js({"ok":True,**result})
  elif path=="/api/admin/source-mode":
   mode="paste" if p.get("mode")=="paste" else "rdf";meta("source_mode",mode);meta("source_state","Using pasted RaceTec table" if mode=="paste" else "Waiting for RDF import")
  elif path=="/api/admin/feed":meta("feed_paused","false" if p.get("running") else "true")
  elif path=="/api/admin/show-empty":meta("force_show_all","true" if p.get("enabled") else "false")
  elif path=="/api/admin/save":meta("setup_saved",now())
  elif path=="/api/admin/assignments":
   c=db()
   with c:
    for item in p.get("assignments",[]):
     c.execute("insert into event_mappings(event_id,tournament,level,stage,event_name,race_id) values(?,?,?,?,?,?) on conflict(event_id) do update set tournament=excluded.tournament,level=excluded.level,stage=excluded.stage,event_name=excluded.event_name,race_id=excluded.race_id",(item["eventId"],item["tournament"],item.get("level",""),item["race"],item["name"],item.get("raceId")))
     c.execute("update events set tournament=?,level=?,stage=?,name=? where id=?",(item["tournament"],item.get("level",""),item["race"],item["name"],item["eventId"]))
   c.close()
  elif path.startswith("/api/admin/events/"):
   c=db()
   event_id=unquote(path.rsplit("/",1)[1])
   with c:
    if p.get("mapping"):
     c.execute("insert into event_mappings(event_id,tournament,stage,event_name) values(?,?,?,?) on conflict(event_id) do update set tournament=excluded.tournament,stage=excluded.stage,event_name=excluded.event_name",(event_id,p.get("tournament",""),p.get("stage",""),p.get("name","")))
     c.execute("update events set tournament=?,stage=?,name=? where id=?",(p.get("tournament",""),p.get("stage",""),p.get("name",""),event_id))
    elif p.get("mode") in ("populated","always","hide"):
     mode=p["mode"];c.execute("update events set publish_mode=?,visible=? where id=?",(mode,0 if mode=="hide" else 1,event_id))
    else:c.execute("update events set visible=? where id=?",(1 if p.get("visible") else 0,event_id))
   c.close()
  else:return self.js({"error":"Not found"},404)
  return self.js({"ok":True})
if __name__=="__main__":
 DB_FILE.parent.mkdir(parents=True,exist_ok=True)
 try:import_historical()
 except Exception as e:print("Bundled historic history import failed:",e,flush=True)
 threading.Thread(target=watch,daemon=True).start();ThreadingHTTPServer(("0.0.0.0",int(os.getenv("PORT","6543"))),App).serve_forever()




