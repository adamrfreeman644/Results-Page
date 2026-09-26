"""Provisional Google Sheet progression and RDF-authoritative resolution."""
import csv, io, os
from urllib.parse import quote
from urllib.request import urlopen

SHEET_ID = os.getenv("PREDICTION_SHEET_ID", "1v7CB9cEK0itJEwwVCthFKcPy2asdJnsxyRi_LGBOeB8")
SHEETS = {
    "open": "Male Heats - Top 32",
    "women": "Female Heats - Top 16",
    "groms": "GROMS Heats - Top 16",
}
TOP32_HEATS = ((1,16,17,25),(2,15,18,26),(3,14,19,27),(4,13,20,28),(5,12,21,29),(6,11,22,30),(7,10,23,31),(8,9,24,32))
TOP16_HEATS = ((1,8,9,16),(2,7,10,15),(3,6,11,14),(4,5,12,13))
TOP32_QF = ((("Heat 1",1),("Heat 8",1),("Heat 4",2),("Heat 5",2)),(("Heat 2",1),("Heat 7",1),("Heat 3",2),("Heat 6",2)),(("Heat 3",1),("Heat 6",1),("Heat 2",2),("Heat 7",2)),(("Heat 4",1),("Heat 5",1),("Heat 1",2),("Heat 8",2)))

def sheet_csv(division):
    tab = SHEETS[division]
    url = "https://docs.google.com/spreadsheets/d/%s/gviz/tq?tqx=out:csv&sheet=%s" % (SHEET_ID, quote(tab))
    with urlopen(url, timeout=12) as response:
        return list(csv.reader(io.TextIOWrapper(response, encoding="utf-8-sig")))

def heat_assignments(seeded_bibs, division):
    pattern = TOP32_HEATS if division == "open" else TOP16_HEATS
    return {"Heat %d" % (index + 1): [seeded_bibs[position - 1] for position in positions if position <= len(seeded_bibs)] for index, positions in enumerate(pattern)}

def predicted_quarters(heat_standings):
    return {"Quarter-final %d" % (index + 1): [heat_standings.get(heat, [])[position - 1] for heat, position in recipe if len(heat_standings.get(heat, [])) >= position] for index, recipe in enumerate(TOP32_QF)}

def resolved_race(official_bibs, predicted_bibs):
    """Official RDF list always wins; otherwise present the provisional Sheet list."""
    if official_bibs:
        return {"source": "rdf", "bibs": official_bibs, "confirmed": True}
    return {"source": "sheet", "bibs": predicted_bibs, "confirmed": False}
