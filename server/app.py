#!/usr/bin/env python3
"""Self-contained OWAR live-results server.  Uses only Python's standard library."""
import json, os, sqlite3, threading, time
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "dist"
DATA_FILE = Path(os.getenv("RDF_FILE", "/data/race.rdf"))
DB_FILE = Path(os.getenv("DATABASE_FILE", "/data/results.sqlite"))
POLL_SECONDS = max(1, int(os.getenv("POLL_SECONDS", "5")))
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")

def db():
    con = sqlite3.connect(DB_FILE)
    con.row_factory = sqlite3.Row
    con.executescript("""
      create table if not exists events (id integer primary key, name text not null, visible integer not null default 1);
      create table if not exists athletes (id integer primary key, name text not null);
      create table if not exists results (event_id integer, athlete_id integer, bib text, position integer, time text, primary key(event_id,athlete_id));
      create table if not exists meta (key text primary key, value text not null);
    """)
    return con

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
                headers[table] = {n:i for i,n in enumerate(fields(raw))}
            elif raw.startswith("[DATA].[RaceEvent]:"):
                row, h = fields(raw), headers.get("RaceEvent", {})
                event_id, name = value(row,h,"EventId"), value(row,h,"WebName","EventDescr")
                if event_id and name and name != "NULL": events[int(event_id)] = name
            elif raw.startswith("[DATA].[Athlete]:"):
                row, h = fields(raw), headers.get("Athlete", {})
                athlete_id = value(row,h,"AthleteId")
                if athlete_id: athletes[int(athlete_id)] = " ".join(filter(None,[value(row,h,"FirstName"),value(row,h,"LastName")])).replace("NULL","").strip()
            elif raw.startswith("[DATA].[EventAthlete]:"):
                row, h = fields(raw), headers.get("EventAthlete", {})
                position = value(row,h,"OverallFinishPosition","AdjOverallPos")
                event_id, athlete_id = value(row,h,"EventId"), value(row,h,"AthleteId")
                if event_id and athlete_id and position and position.isdigit():
                    stamp = value(row,h,"FinishTime","NetTime") or ""
                    results.append((int(event_id),int(athlete_id),value(row,h,"RaceNo") or "",int(position),stamp.replace("1900/01/01 ","")))
    con = db()
    with con:
        for event_id, name in events.items(): con.execute("insert into events(id,name) values(?,?) on conflict(id) do update set name=excluded.name",(event_id,name))
        for athlete_id, name in athletes.items(): con.execute("insert into athletes(id,name) values(?,?) on conflict(id) do update set name=excluded.name",(athlete_id,name or f"Rider {athlete_id}"))
        con.execute("delete from results")
        con.executemany("insert into results(event_id,athlete_id,bib,position,time) values(?,?,?,?,?)",results)
        con.execute("insert into meta(key,value) values('last_import',?) on conflict(key) do update set value=excluded.value",(datetime.now(timezone.utc).isoformat(),))
        con.execute("insert into meta(key,value) values('status','Live') on conflict(key) do nothing")
    con.close()

def watch():
    previous = None
    while True:
        try:
            current = DATA_FILE.stat().st_mtime_ns
            if current != previous:
                import_rdf(DATA_FILE); previous = current
                print(f"Imported {DATA_FILE}", flush=True)
        except FileNotFoundError: pass
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
            con=db(); meta={r[0]:r[1] for r in con.execute("select key,value from meta")}; events=[dict(r) for r in con.execute("select e.id,e.name,e.visible,count(r.athlete_id) count from events e left join results r on r.event_id=e.id group by e.id order by e.id")]; con.close(); return self.json({"file":str(DATA_FILE),"pollSeconds":POLL_SECONDS,"meta":meta,"events":events})
        return super().do_GET()
    def do_POST(self):
        path=urlparse(self.path).path
        if not self.authorized(): return self.json({"error":"Unauthorized"},401)
        try: payload=json.loads(self.rfile.read(int(self.headers.get("Content-Length","0"))) or "{}")
        except json.JSONDecodeError: return self.json({"error":"Invalid JSON"},400)
        con=db()
        with con:
            if path == "/api/admin/status": con.execute("insert into meta(key,value) values('status',?) on conflict(key) do update set value=excluded.value",(payload.get("status","Live"),))
            elif path.startswith("/api/admin/events/"):
                con.execute("update events set visible=? where id=?",(1 if payload.get("visible") else 0,path.rsplit("/",1)[1]))
            else: con.close(); return self.json({"error":"Not found"},404)
        con.close(); return self.json({"ok":True})

if __name__ == "__main__":
    DB_FILE.parent.mkdir(parents=True,exist_ok=True); db().close()
    threading.Thread(target=watch,daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0",int(os.getenv("PORT","8080"))),App).serve_forever()
