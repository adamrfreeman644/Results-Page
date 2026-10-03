#!/usr/bin/env python3
"""Self-contained OWAR live-results server.  Uses only Python's standard library."""
import csv, hashlib, json, os, re, sqlite3, subprocess, threading, time
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from html.parser import HTMLParser

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "dist"
APP_VERSION = (ROOT / "VERSION").read_text().strip()
DB_FILE = Path(os.getenv("DATABASE_FILE", "/data/results.sqlite"))
POLL_SECONDS = max(1, int(os.getenv("POLL_SECONDS", "5")))
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")
RACETEC_URLS = [x.strip() for x in os.getenv("RACETEC_URLS", "").split("|") if x.strip()]
RDF_FILE = Path(os.getenv("RDF_FILE", "")) if os.getenv("RDF_FILE") else None
TIMING_FILE = Path(os.getenv("TIMING_FILE", "")) if os.getenv("TIMING_FILE") else None
UPDATER_URL = os.getenv("SHARED_UPDATER_URL", "http://host.docker.internal:8093/apps/results-page").rstrip("/")
TEAM_RACE_START = os.getenv("TEAM_RACE_START", "18:18:30.0")
TEAM_ROSTERS = {
    "The Floating Jabronis": ("129", "136", "159", "174", "169", "172"),
    "Boeufoeuf": ("132", "160", "139", "140", "147", "179"),
    "Pigeon and Tonic": ("133", "134", "121", "173", "153", "163"),
    "Flying OWAT's": ("146", "137", "156", "168", "157"),
    "Flyboi": ("143", "161", "184", "193"),
    "Keeping up with the Jones's": ("148", "149", "150", "151", "250", "127", "138"),
    "Stoke Seekers": ("152", "164", "167", "170", "188"),
    "Double Slow Sevens": ("171", "177", "178", "192"),
    "Poland": ("154", "155", "182", "181"),
}

def db():
    con = sqlite3.connect(DB_FILE)
    con.row_factory = sqlite3.Row
    con.executescript("""
      create table if not exists events (id integer primary key, name text not null, visible integer not null default 1, sort_order integer not null default 0);
      create table if not exists athletes (id integer primary key, name text not null);
      create table if not exists results (event_id integer, athlete_id integer, bib text, position integer, time text, primary key(event_id,athlete_id));
      create table if not exists meta (key text primary key, value text not null);
      create table if not exists sources (url text primary key, active integer not null default 1, last_success text, last_error text);
      create table if not exists tournament_stages (event_id integer primary key, label text not null, phase text not null default 'Race', display_order integer not null default 0);
      create table if not exists advancement_rules (source_event_id integer not null, position integer not null, target_event_id integer not null, target_slot integer not null, primary key(source_event_id, position));
      create table if not exists progression_entries (target_event_id integer not null, target_slot integer not null, athlete_id integer not null, source_event_id integer not null, source_position integer not null, updated_at text not null, primary key(target_event_id, target_slot));
      create table if not exists team_laps (timing_id integer primary key, bib text not null, chip text, rider_name text not null, finish_time text not null, elapsed_seconds real not null, lap_number integer not null, lap_seconds real);
    """)
    # Existing installations predate source import diagnostics.  SQLite's
    # CREATE TABLE IF NOT EXISTS does not add later columns, so migrate safely.
    for column in ("last_success", "last_error"):
        try: con.execute(f"alter table sources add column {column} text")
        except sqlite3.OperationalError: pass
    try: con.execute("alter table events add column sort_order integer not null default 0")
    except sqlite3.OperationalError: pass
    return con

def parse_clock(value):
    """Return seconds after midnight for RaceTec clock values."""
    value = value.strip()
    for pattern in ("%H:%M:%S.%f", "%H:%M:%S"):
        try:
            parsed = datetime.strptime(value, pattern)
            return parsed.hour * 3600 + parsed.minute * 60 + parsed.second + parsed.microsecond / 1_000_000
        except ValueError: pass
    raise ValueError(f"Invalid clock time: {value}")

def import_timing_export(path):
    """Import a tab-separated reader export. Base owns a completed lap; Base-3 only supplies a split."""
    with path.open(encoding="utf-8-sig", errors="replace", newline="") as source:
        readings = list(csv.DictReader(source, delimiter="\t"))
    normalized=[]
    for row in readings:
        if row.get("Timing Point") not in ("Base", "Base-3") or row.get("Can Use", "").upper() != "TRUE": continue
        try:
            normalized.append({"id":int(row["Id"]), "point":row["Timing Point"], "chip":row.get("Chip Code", ""), "bib":row.get("Race No", ""), "name":row.get("Name", "").strip(), "clock":parse_clock(row["Chip Time"]), "time":row["Chip Time"]})
        except (KeyError, ValueError): continue
    normalized.sort(key=lambda read: (read["chip"], read["clock"], read["id"]))
    starts, laps, lap_numbers = {}, [], {}
    for read in normalized:
        if read["point"] == "Base-3":
            starts[read["chip"]] = read
        elif read["point"] == "Base":
            lap_numbers[read["bib"]] = lap_numbers.get(read["bib"], 0) + 1
            start = starts.pop(read["chip"], None)
            lap_seconds = read["clock"] - start["clock"] if start else None
            # An unplugged Base-3 produces no split, but the Base finish remains valid.
            if lap_seconds is not None and not 60 <= lap_seconds <= 900: lap_seconds = None
            laps.append((read["id"], read["bib"], read["chip"], read["name"], read["time"], read["clock"] - parse_clock(TEAM_RACE_START), lap_numbers[read["bib"]], lap_seconds))
    con=db()
    with con:
        con.execute("delete from team_laps")
        con.executemany("insert into team_laps(timing_id,bib,chip,rider_name,finish_time,elapsed_seconds,lap_number,lap_seconds) values(?,?,?,?,?,?,?,?)", laps)
        con.execute("insert into meta(key,value) values('team_laps_imported',?) on conflict(key) do update set value=excluded.value", (datetime.now(timezone.utc).isoformat(),))
    con.close()

def team_race_payload():
    con=db(); rows=[dict(row) for row in con.execute("select * from team_laps order by elapsed_seconds,timing_id")]; con.close()
    riders={}
    for row in rows:
        rider=riders.setdefault(row['bib'], {"bib":row['bib'], "name":row['rider_name'], "laps":[]})
        rider['laps'].append({"number":row['lap_number'], "finish":row['finish_time'], "elapsed":row['elapsed_seconds'], "time":row['lap_seconds']})
    for rider in riders.values():
        rider['lap_count']=len(rider['laps']); rider['fastest']=min((lap['time'] for lap in rider['laps'] if lap['time'] is not None), default=None)
    teams=[]
    for name, bibs in TEAM_ROSTERS.items():
        members=[riders.get(bib, {"bib":bib,"name":"No recorded finish","laps":[],"lap_count":0,"fastest":None}) for bib in bibs]
        laps=[lap for member in members for lap in member['laps']]
        teams.append({"name":name,"laps":len(laps),"completion":max((lap['elapsed'] for lap in laps), default=None),"fastest":min((lap['time'] for lap in laps if lap['time'] is not None), default=None),"members":members})
    teams.sort(key=lambda team: (-team['laps'], team['completion'] if team['completion'] is not None else float('inf')))
    return {"start":TEAM_RACE_START,"teams":teams,"riders":sorted(riders.values(), key=lambda rider:(-rider['lap_count'], rider['name']))}

def reconcile_progression(con):
    """Refresh provisional next-race entries from authoritative imported results."""
    rules = con.execute("select source_event_id,position,target_event_id,target_slot from advancement_rules").fetchall()
    for rule in rules:
        rider = con.execute("select athlete_id from results where event_id=? and position=?", (rule['source_event_id'], rule['position'])).fetchone()
        if rider:
            con.execute("insert into progression_entries(target_event_id,target_slot,athlete_id,source_event_id,source_position,updated_at) values(?,?,?,?,?,?) on conflict(target_event_id,target_slot) do update set athlete_id=excluded.athlete_id,source_event_id=excluded.source_event_id,source_position=excluded.source_position,updated_at=excluded.updated_at", (rule['target_event_id'], rule['target_slot'], rider['athlete_id'], rule['source_event_id'], rule['position'], datetime.now(timezone.utc).isoformat()))
        else:
            con.execute("delete from progression_entries where target_event_id=? and target_slot=? and source_event_id=? and source_position=?", (rule['target_event_id'], rule['target_slot'], rule['source_event_id'], rule['position']))

def fields(line): return line.split(':', 1)[1].rstrip('\n').split('|')
def value(row, names, *candidates):
    for name in candidates:
        i = names.get(name)
        if i is not None and i < len(row): return row[i]
    return None

def import_rdf(path):
    headers, events, athletes, results = {}, {}, {}, []
    with path.open(encoding="utf-8-sig", errors="replace") as source:
        for raw in source:
            if raw.startswith("[TABLE].["):
                table = raw.split("].[", 1)[1].split("]:", 1)[0]
                headers[table] = {name:index for index,name in enumerate(fields(raw))}
            elif raw.startswith("[DATA].[RaceEvent]:"):
                row, header = fields(raw), headers.get("RaceEvent", {})
                event_id, name = value(row,header,"EventId"), value(row,header,"WebName","EventDescr")
                if event_id and name and name != "NULL": events[int(event_id)] = name
            elif raw.startswith("[DATA].[Athlete]:"):
                row, header = fields(raw), headers.get("Athlete", {})
                athlete_id = value(row,header,"AthleteId")
                if athlete_id:
                    athletes[int(athlete_id)] = " ".join(filter(None,[value(row,header,"FirstName"),value(row,header,"LastName")])).replace("NULL","").strip()
            elif raw.startswith("[DATA].[EventAthlete]:"):
                row, header = fields(raw), headers.get("EventAthlete", {})
                position = value(row,header,"OverallFinishPosition","AdjOverallPos")
                event_id, athlete_id = value(row,header,"EventId"), value(row,header,"AthleteId")
                if event_id and athlete_id and position and position.isdigit():
                    result_time = value(row,header,"FinishTime","NetTime") or ""
                    results.append((int(event_id),int(athlete_id),value(row,header,"RaceNo") or "",int(position),result_time.replace("1900/01/01 ","")))
    con=db()
    with con:
        for event_id, name in events.items(): con.execute("insert into events(id,name,sort_order) values(?,?,?) on conflict(id) do update set name=excluded.name,sort_order=excluded.sort_order",(event_id,name,event_id))
        for athlete_id, name in athletes.items(): con.execute("insert into athletes(id,name) values(?,?) on conflict(id) do update set name=excluded.name",(athlete_id,name or f"Rider {athlete_id}"))
        con.execute("delete from results")
        con.executemany("insert into results(event_id,athlete_id,bib,position,time) values(?,?,?,?,?)",results)
        reconcile_progression(con)
        con.execute("insert into meta(key,value) values('last_import',?) on conflict(key) do update set value=excluded.value",(datetime.now(timezone.utc).isoformat(),))
        con.execute("insert into meta(key,value) values('status','Live') on conflict(key) do nothing")
    con.close()

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

def fetch_racetec_page(url):
    headers={'User-Agent':'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131 Safari/537.36','Accept':'text/html,application/xhtml+xml','Accept-Language':'en-GB,en;q=0.9'}
    try:
        return urlopen(Request(url,headers=headers),timeout=30).read().decode('utf-8','replace')
    except HTTPError as exc:
        if exc.code != 403: raise
    # RaceTec accepts normal browsers but sometimes blocks plain server HTTP
    # clients. Chromium renders the public page, without credentials or AI.
    browser=os.getenv('CHROMIUM_BIN','chromium-browser')
    result=subprocess.run([browser,'--headless','--no-sandbox','--disable-gpu','--disable-dev-shm-usage','--dump-dom',url],capture_output=True,text=True,timeout=45)
    if result.returncode:
        # Chromium emits harmless DBus warnings first in Docker, so report the
        # final lines where the actual failure is normally written.
        detail=' '.join((result.stderr or result.stdout or 'Chromium exited without a response').strip().splitlines()[-4:])
        raise ValueError(f'RaceTec browser request failed: {detail[:300]}')
    if 'ctl00_Content_Main_tblResults' not in result.stdout:
        title=re.search(r'<title[^>]*>(.*?)</title>',result.stdout,re.I|re.S)
        text=re.sub(r'<[^>]+>',' ',result.stdout)
        detail=re.sub(r'\s+',' ',text).strip()
        heading=re.sub(r'\s+',' ',title.group(1)).strip() if title else 'no page title'
        raise ValueError(f'RaceTec returned no result table ({heading}): {detail[:220]}')
    return result.stdout

def racetec_event_name(page, fallback):
    selected=re.search(r'<option[^>]*selected[^>]*>\s*(.*?)\s*</option>',page,re.I|re.S)
    if selected: return re.sub('<.*?>','',selected.group(1)).strip()
    view=re.search(r'id=["\']ctl00_Content_Main_divV3ViewBar["\'][^>]*>\s*(?:<[^>]+>)*\s*(.*?)\s*<',page,re.I|re.S)
    return re.sub('<.*?>','',view.group(1)).strip() if view else fallback

def racetec_event_urls(page, source_url):
    # A meeting URL exposes all of its stages in one select menu.  Preserve the
    # supplied CId/RId and visit every distinct EId automatically.
    ids=re.findall(r'<option[^>]*value=["\']?(\d+)',page,re.I)
    parsed=urlparse(source_url); query=dict(parse_qsl(parsed.query,keep_blank_values=True))
    urls=[]
    for event_id in ids:
        query['EId']=event_id
        urls.append(urlunparse(parsed._replace(query=urlencode(query))))
    return list(dict.fromkeys(urls)) or [source_url]

def import_racetec(url, page=None):
    page=page or fetch_racetec_page(url)
    parser=RaceTecTable(); parser.feed(page)
    if len(parser.rows)<2: raise ValueError('RaceTec returned no published result table')
    header=[x.upper() for x in parser.rows[0]]; pos=header.index('POS') if 'POS' in header else 1; name=header.index('NAME') if 'NAME' in header else 2; timing=header.index('TIME') if 'TIME' in header else 3
    event_id=stable_id(url); event_order=int(dict(parse_qsl(urlparse(url).query)).get('EId','0')); title=re.search(r'<title>\s*(.*?)\s*</title>',page,re.I|re.S); event_name=racetec_event_name(page,re.sub('<.*?>','',title.group(1)).strip() if title else 'RaceTec results')
    rows=[]
    for row in parser.rows[1:]:
        if len(row)<=max(pos,name,timing) or not row[pos].isdigit(): continue
        label=row[name]; bib=re.search(r'#(\S+)',label); rider=re.sub(r'\s*#\S+','',label).strip(); rows.append((event_id,stable_id(url+'|'+rider),bib.group(1) if bib else '',int(row[pos]),row[timing],rider))
    con = db()
    with con:
        con.execute("insert into events(id,name,sort_order) values(?,?,?) on conflict(id) do update set name=excluded.name,sort_order=excluded.sort_order",(event_id,event_name,event_order))
        con.execute("delete from results where event_id=?",(event_id,))
        for eid,aid,bib,position,timing,rider in rows:
            con.execute("insert into athletes(id,name) values(?,?) on conflict(id) do update set name=excluded.name",(aid,rider))
            con.execute("insert into results(event_id,athlete_id,bib,position,time) values(?,?,?,?,?)",(eid,aid,bib,position,timing))
        reconcile_progression(con)
        con.execute("insert into meta(key,value) values('last_import',?) on conflict(key) do update set value=excluded.value",(datetime.now(timezone.utc).isoformat(),))
        con.execute("update sources set last_success=?,last_error=null where url=?",(datetime.now(timezone.utc).isoformat(),url))
        con.execute("insert into meta(key,value) values('status','Live') on conflict(key) do nothing")
    con.close()

def import_racetec_meeting(source_url):
    first_page=fetch_racetec_page(source_url)
    imported=0
    errors=[]
    for event_url in racetec_event_urls(first_page,source_url):
        try:
            import_racetec(event_url, first_page if event_url == source_url else None)
            imported += 1
        except ValueError as exc:
            # Unpublished heats are normal while a tournament is in progress.
            errors.append(str(exc))
    if not imported:
        raise ValueError(errors[0] if errors else 'RaceTec has no published events yet')
    con=db()
    with con: con.execute("update sources set last_success=?,last_error=null where url=?",(datetime.now(timezone.utc).isoformat(),source_url))
    con.close()
    return imported

def updater_request(path='', method='GET'):
    try:
        request=Request(UPDATER_URL+path, method=method, headers={'Accept':'application/json'})
        with urlopen(request,timeout=12) as response:
            return json.loads(response.read().decode('utf-8'))
    except Exception as exc:
        return {'available':False,'error':str(exc)}

def watch():
    rdf_pending = None
    rdf_imported = None
    timing_pending = None
    timing_imported = None
    while True:
        try:
            con=db(); paused=con.execute("select value from meta where key='feed_paused'").fetchone(); con.close()
            if not paused or paused[0] != 'true':
                if RDF_FILE and RDF_FILE.exists():
                    stamp = (RDF_FILE.stat().st_mtime_ns, RDF_FILE.stat().st_size)
                    # Import only when the same replacement file is seen twice.
                    if stamp != rdf_imported and stamp == rdf_pending:
                        import_rdf(RDF_FILE)
                        rdf_imported = stamp
                        print(f"Imported RDF results from {RDF_FILE}", flush=True)
                    else:
                        rdf_pending = stamp
                if TIMING_FILE and TIMING_FILE.exists():
                    stamp = (TIMING_FILE.stat().st_mtime_ns, TIMING_FILE.stat().st_size)
                    if stamp != timing_imported and stamp == timing_pending:
                        import_timing_export(TIMING_FILE)
                        timing_imported = stamp
                        print(f"Imported team timing from {TIMING_FILE}", flush=True)
                    else:
                        timing_pending = stamp
                con=db(); urls=[r[0] for r in con.execute("select url from sources where active=1")]; con.close()
                successful=0
                for url in urls:
                    try:
                        import_racetec_meeting(url)
                        successful += 1
                    except Exception as exc:
                        con=db(); con.execute("update sources set last_error=? where url=?",(str(exc)[:300],url)); con.commit(); con.close(); print(f"Import failed for {url}: {exc}",flush=True)
                if successful: print(f"Imported RaceTec results from {successful} source(s)", flush=True)
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
        if path == "/api/public/team-race":
            return self.json(team_race_payload())
        if path == "/api/public/events":
            con=db(); status=con.execute("select value from meta where key='status'").fetchone(); rows=con.execute("select e.id,e.name,count(r.athlete_id) count from events e left join results r on r.event_id=e.id where e.visible=1 group by e.id having count(r.athlete_id)>0 order by e.sort_order,e.name").fetchall(); con.close(); return self.json({"status":status[0] if status else "Live","events":[dict(x) for x in rows]})
        if path.startswith("/api/public/events/") and path.endswith("/results"):
            event_id=path.split("/")[4]; con=db(); rows=con.execute("select r.position,a.name,r.bib,r.time from results r join athletes a on a.id=r.athlete_id where r.event_id=? order by r.position",(event_id,)).fetchall(); con.close(); return self.json([dict(x) for x in rows])
        if path == "/api/admin/status":
            if not self.authorized(): return self.json({"error":"Unauthorized"},401)
            con=db(); meta={r[0]:r[1] for r in con.execute("select key,value from meta")}; events=[dict(r) for r in con.execute("select e.id,e.name,e.visible,count(r.athlete_id) count from events e left join results r on r.event_id=e.id group by e.id order by e.sort_order,e.name")]; sources=[dict(r) for r in con.execute("select rowid,url,active,last_success,last_error from sources order by rowid")]; con.close(); return self.json({"version":APP_VERSION,"sources":sources,"pollSeconds":POLL_SECONDS,"meta":meta,"events":events})
        if path == "/api/admin/tournament":
            if not self.authorized(): return self.json({"error":"Unauthorized"},401)
            con=db(); events=[dict(r) for r in con.execute("select id,name from events order by sort_order,name")]; stages=[dict(r) for r in con.execute("select * from tournament_stages order by display_order,event_id")]; rules=[dict(r) for r in con.execute("select * from advancement_rules order by source_event_id,position")]; con.close(); return self.json({"events":events,"stages":stages,"rules":rules})
        if path == "/api/admin/tree":
            if not self.authorized(): return self.json({"error":"Unauthorized"},401)
            con=db(); stages=[dict(r) for r in con.execute("select s.*,e.name event_name from tournament_stages s left join events e on e.id=s.event_id order by s.display_order,s.event_id")]; rules=[dict(r) for r in con.execute("select * from advancement_rules order by source_event_id,position")]; entries=[dict(r) for r in con.execute("select p.*,a.name,a.id athlete_id from progression_entries p join athletes a on a.id=p.athlete_id order by p.target_event_id,p.target_slot")]; con.close(); return self.json({"stages":stages,"rules":rules,"entries":entries})
        if path == "/api/admin/update":
            if not self.authorized(): return self.json({"error":"Unauthorized"},401)
            return self.json(updater_request())
        return super().do_GET()
    def do_POST(self):
        path=urlparse(self.path).path
        if not self.authorized(): return self.json({"error":"Unauthorized"},401)
        try: payload=json.loads(self.rfile.read(int(self.headers.get("Content-Length","0"))) or "{}")
        except json.JSONDecodeError: return self.json({"error":"Invalid JSON"},400)
        if path == "/api/admin/update": return self.json(updater_request('/install','POST'))
        if path == "/api/admin/sources" and not payload.get('url','').strip().startswith('https://www.racetecresults.com/results.aspx?'):
            return self.json({"error":"Enter a RaceTec results URL"},400)
        con=db()
        with con:
            if path == "/api/admin/status": con.execute("insert into meta(key,value) values('status',?) on conflict(key) do update set value=excluded.value",(payload.get("status","Live"),))
            elif path == "/api/admin/feed": con.execute("insert into meta(key,value) values('feed_paused',?) on conflict(key) do update set value=excluded.value",('false' if payload.get('running') else 'true',))
            elif path == "/api/admin/sources":
                url=payload.get('url','').strip()
                con.execute("insert into sources(url,active) values(?,1) on conflict(url) do update set active=1",(url,))
            elif path.startswith("/api/admin/sources/"):
                con.execute("update sources set active=? where rowid=?",(1 if payload.get('active') else 0,path.rsplit("/",1)[1]))
            elif path.startswith("/api/admin/events/"):
                con.execute("update events set visible=? where id=?",(1 if payload.get("visible") else 0,path.rsplit("/",1)[1]))
            elif path == "/api/admin/tournament":
                stages=payload.get('stages',[]); rules=payload.get('rules',[])
                con.execute("delete from tournament_stages"); con.execute("delete from advancement_rules"); con.execute("delete from progression_entries")
                for index, stage in enumerate(stages):
                    event_id=int(stage['event_id']); con.execute("insert into tournament_stages(event_id,label,phase,display_order) values(?,?,?,?)", (event_id,stage.get('label') or f"Event {event_id}",stage.get('phase') or 'Race',index))
                for rule in rules:
                    con.execute("insert into advancement_rules(source_event_id,position,target_event_id,target_slot) values(?,?,?,?)", (int(rule['source_event_id']),int(rule['position']),int(rule['target_event_id']),int(rule['target_slot'])))
                reconcile_progression(con)
            else: con.close(); return self.json({"error":"Not found"},404)
        con.close(); return self.json({"ok":True})

if __name__ == "__main__":
    DB_FILE.parent.mkdir(parents=True,exist_ok=True)
    con=db()
    for url in RACETEC_URLS: con.execute("insert into sources(url,active) values(?,1) on conflict(url) do nothing",(url,))
    con.commit(); con.close()
    threading.Thread(target=watch,daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0",int(os.getenv("PORT","8080"))),App).serve_forever()
