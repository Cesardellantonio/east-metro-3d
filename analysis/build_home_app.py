"""Build the shared "Two Keys" house-hunting site from analysis/output/analysis.json.

    python3 analysis/build_home_app.py  ->  analysis/output/two_keys.html
"""
import json
import re
from pathlib import Path

from build_map_layers import rdp

HERE = Path(__file__).parent
OUT = HERE / "output"
HARD = {"leasehold", "resale not established", "third-bedroom integrity conflict", "adult oriented"}
SKY_LINES = [
    ["Waterfront", "Main St", "Commercial-Broadway", "Nanaimo", "29th Ave", "Joyce-Collingwood", "Patterson", "Metrotown", "Royal Oak", "Edmonds", "22nd St", "New Westminster", "Columbia", "Scott Rd"],
    ["Columbia", "Sapperton", "Braid", "Lougheed", "Production Way"],
    ["VCC-Clark", "Commercial-Broadway", "Renfrew", "Rupert", "Gilmore", "Brentwood", "Holdom", "Sperling", "Lake City", "Production Way", "Lougheed", "Burquitlam", "Moody Centre", "Inlet Centre", "Coquitlam Central", "Lincoln", "Lafarge Lake"],
    ["Waterfront", "Broadway-City Hall", "King Edward", "Oakridge", "Langara", "Marine Dr"],
]
EXTRA_ST = {"Waterfront": (49.2856, -123.1115)}


def key_of(url, address):
    m = re.search(r"/properties/([^/?#]+)", url or "")
    slug = m.group(1) if m else re.sub(r"[^a-z0-9]+", "-", (address or "").lower()).strip("-")
    return re.sub(r"[^A-Za-z0-9_\-.~:@+]", "-", slug)[:180]


def pct_rank(vals):
    s = sorted(vals)
    return lambda v: (sum(1 for x in s if x < v) / (len(s) - 1)) if len(s) > 1 else .5


def main():
    d = json.loads((OUT / "analysis.json").read_text())
    layers = json.loads((OUT / "map_layers.json").read_text())
    L = [l for l in d["listings"] if l.get("lat") is not None and l.get("lon") is not None]
    sq_rank = pct_rank([l["sqft"] for l in L])
    loc_rank = pct_rank([l["loc_prem"] for l in L])
    clamp = lambda v: round(max(35, min(99, v)))
    homes = []
    for l in L:
        age = 2026 - l["year"] if l.get("year") else None
        bld = max(0, min(100, 100 - age * 1.6)) if age is not None else 50
        if l.get("lux") is not None:
            bld = .6 * bld + .4 * l["lux"] * 10
        if l.get("leaky_era"):
            bld -= 12
        e, s = l.get("school_elem") or {}, l.get("school_sec") or {}
        wd = l.get("walk_detail") or {}
        homes.append({
            "k": key_of(l["url"], l["address"]), "a": l["address"], "n": l["neighbourhood"], "c": l["area"], "t": l["type"],
            "pt": l.get("property_type"), "p": l["price"], "f": l["fair"], "g": l["gap"], "sq": l["sqft"], "b": l["beds"], "ba": l.get("baths"),
            "y": l.get("year"), "st": l.get("strata"), "tx": l.get("taxes"), "ten": l.get("tenure"), "w": l.get("walk"),
            "wg": (wd.get("Groceries") or {}).get("n800"), "wp": (wd.get("Parks & playgrounds") or {}).get("n800"),
            "wc": (wd.get("Childcare") or {}).get("n800"),
            "es": e.get("name"), "er": e.get("rating"), "ek": e.get("km"), "ec": e.get("src") == "catchment", "fr": bool(e.get("french")),
            "ss": s.get("name"), "sk": s.get("km"), "stn": l.get("station"), "skm": l.get("station_km"),
            "lp": l.get("loc_prem"), "lt": l.get("loc_tier"), "lx": l.get("lux_tier"), "lh": (l.get("lux_hits") or [])[:5],
            "lk": bool(l.get("leaky_era")), "rs": bool(set(l.get("flags") or []) & HARD) or bool(re.search("Leasehold|Undivided", l.get("tenure") or "")),
            "fl": [f for f in (l.get("flags") or []) if f in HARD], "cu": l.get("cuts") or 0, "cp": l.get("cut_pct") or 0,
            "src": l.get("source"), "card": l.get("fact_level") == "card", "band": l.get("band"), "hr": l.get("hermes_rank"),
            "rent": l.get("rent_city"), "u": l["url"], "lat": round(l["lat"], 5), "lon": round(l["lon"], 5),
            "at": {"VAL": clamp(70 - l["gap"] * 1.5), "SPC": clamp(45 + 54 * sq_rank(l["sqft"])), "SCH": clamp(40 + .59 * (l.get("school_score") or 50)),
                   "WLK": clamp(40 + .59 * (l.get("walk") or 50)), "LOC": clamp(45 + 54 * loc_rank(l["loc_prem"])), "BLD": clamp(40 + .59 * bld)},
        })
    # A light locator map: coarse shoreline + SkyTrain.
    land = []
    for poly in layers["land"]:
        ring = rdp(poly[0], 0.0015)
        if len(ring) >= 4:
            land.append([[round(x, 3), round(y, 3)] for x, y in ring])
    st = {k: [round(lat, 4), round(lon, 4)] for k, (lat, lon) in {**layers["stations"], **EXTRA_ST}.items()}
    lines = [[st[s] for s in line if s in st] for line in SKY_LINES]
    gv = [g for g in d["gvr"] if g["area"] in ("Greater Vancouver", "Burnaby South", "Burnaby North", "Vancouver East", "Port Moody", "Coquitlam", "New Westminster", "Port Coquitlam")]
    nb = sorted([n for n in d["location"]["neighbourhoods"] if n["n"] >= 3 and n["neighbourhood"] != n["area"]], key=lambda n: -n["premium"])
    data = {"scan": d["latest_scan"], "sources": d["sources"], "model": {"r2": d["model"]["r2"], "sigma": d["model"]["sigma_pct"], "age10": d["model"]["coefs"].get("age10"), "lux": d["model"]["coefs"].get("lux")},
            "gvr": gv, "summary": d["summary"], "rents": d["rents"], "areas": d["location"]["areas"], "nb_top": nb[:5], "nb_bottom": nb[-5:],
            "homes": homes, "map": {"land": land, "lines": lines, "bbox": layers["bbox"]}}
    page = (HERE / "two_keys_template.html").read_text().replace("/*__DATA__*/null", json.dumps(data, separators=(",", ":"), allow_nan=False).replace("</", "<\\/"))
    (OUT / "two_keys.html").write_text(page)
    print(OUT / "two_keys.html", f"{len(page) / 1e3:.0f} KB", len(homes), "homes")


if __name__ == "__main__":
    main()
