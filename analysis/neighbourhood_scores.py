"""School and walkability scores for listings.

Inputs (data/external/, open data):
  bc_k12_schools.csv   BC K-12 school locations (BC Data Catalogue, Open Government Licence - BC)
  fsa_2021_2026.csv    Foundation Skills Assessment results by school (same licence)
  osm_amenities.json   Shops, food, parks, services and bus stops (OpenStreetMap, ODbL) via Overpass
"""
from __future__ import annotations

import difflib
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

EXT = Path(__file__).resolve().parent.parent / "data" / "external"
METRO_DISTRICTS = {"036", "037", "038", "039", "040", "041", "043", "044", "045"}
CITY_DISTRICT = {"Burnaby": "041", "New Westminster": "040", "Vancouver": "039", "Port Moody": "043",
                 "Coquitlam": "043", "Port Coquitlam": "043"}
FSA_YEARS = {"2023/2024", "2024/2025", "2025/2026"}


def haversine_km(lat, lon, lat2, lon2):
    lat, lon, lat2, lon2 = map(np.radians, (lat, lon, lat2, lon2))
    a = np.sin((lat2 - lat) / 2) ** 2 + np.cos(lat) * np.cos(lat2) * np.sin((lon2 - lon) / 2) ** 2
    return 6371 * 2 * np.arcsin(np.sqrt(a))


# ---------- Schools ----------

def load_schools():
    k = pd.read_csv(EXT / "bc_k12_schools.csv", dtype=str, encoding="utf-8-sig")
    k = k[(k.DISTRICT_NUMBER.isin(METRO_DISTRICTS)) & (k.PUBLIC_OR_INDEPENDENT == "Public School")
          & (k.FACILITY_TYPE == "Standard School")].copy()
    k["lat"] = pd.to_numeric(k.LATITUDE, errors="coerce")
    k["lon"] = pd.to_numeric(k.LONGITUDE, errors="coerce")
    k = k[k.lat.notna() & k.lon.notna()].copy()
    lvl = k.SCHOOL_EDUCATION_LEVEL
    k["elem"] = lvl.isin(["Elementary", "Elementary Jr. Secondary", "Elementary-Secondary", "Middle School"])
    k["sec"] = lvl.isin(["Secondary", "Senior Secondary", "Junior Secondary", "Elementary-Secondary"])
    k["french"] = (k.HAS_EARLY_FRENCH_IMMERSION == "YES") | (k.HAS_LATE_FRENCH_IMMERSION == "YES")

    f = pd.read_csv(EXT / "fsa_2021_2026.csv", encoding="latin-1", dtype=str)
    f = f[(f.DATA_LEVEL == "School Level") & (f.SUB_POPULATION == "All Students") & f.SCHOOL_YEAR.isin(FSA_YEARS)
          & (f.PUBLIC_OR_INDEPENDENT == "Public School")].copy()
    for c in ["AVG_SCORE", "NUMBER_WRITERS", "NUMBER_EXPECTED_WRITERS", "NUMBER_ONTRACK", "NUMBER_EXTENDING"]:
        f[c] = pd.to_numeric(f[c], errors="coerce")
    # Small cohorts have writer counts masked ("Msk") but still publish an average: give them a nominal weight.
    f["NUMBER_WRITERS"] = f.NUMBER_WRITERS.fillna(20)
    f = f[f.AVG_SCORE.notna()]
    f["w_score"] = f.AVG_SCORE * f.NUMBER_WRITERS
    f["meet"] = f.NUMBER_ONTRACK.fillna(0) + f.NUMBER_EXTENDING.fillna(0)
    g = f.groupby("SCHOOL_NUMBER").agg(ws=("w_score", "sum"), writers=("NUMBER_WRITERS", "sum"),
                                        expected=("NUMBER_EXPECTED_WRITERS", "sum"), meet=("meet", "sum"))
    g["fsa_avg"] = g.ws / g.writers
    g["fsa_meet_pct"] = g.meet / g.writers * 100
    g["participation"] = g.writers / g.expected * 100
    k = k.join(g[["fsa_avg", "fsa_meet_pct", "participation", "writers"]], on="MINCODE")
    ranked = k[k.fsa_avg.notna() & (k.writers >= 20)]
    k["fsa_pctile"] = k.fsa_avg.map(lambda v: float((ranked.fsa_avg < v).mean() * 100) if pd.notna(v) else np.nan)
    return k


def _norm(name: str) -> str:
    name = name.lower().replace("’", "'")
    name = re.sub(r"\b(elementary|elem|school|community|secondary|annex|public|jr|sr|middle|the)\b", " ", name)
    return re.sub(r"[^a-z0-9 ]", "", re.sub(r"\s+", " ", name)).strip()


def _match(name, district, pool):
    if not name:
        return None
    cands = pool[pool.DISTRICT_NUMBER == district] if district else pool
    keys = {_norm(n): i for i, n in zip(cands.index, cands.SCHOOL_NAME)}
    hit = difflib.get_close_matches(_norm(name), list(keys), n=1, cutoff=0.8)
    return keys[hit[0]] if hit else None


def school_scores(cur: pd.DataFrame) -> pd.DataFrame:
    k = load_schools()
    elem_pool, sec_pool = k[k.elem], k[k.sec]
    rows = {}
    for idx, r in cur.iterrows():
        lat, lon = r.latitude, r.longitude
        district = CITY_DISTRICT.get(r.area)
        out = {}
        for kind, pool, named, named_km in (("elem", elem_pool, r.elem, r.elem_km), ("sec", sec_pool, r.sec, r.sec_km)):
            i = _match(named, district, pool)
            src = "catchment"
            if i is None and pd.notna(lat):
                near = pool[pool.SCHOOL_EDUCATION_LEVEL != "Middle School"] if kind == "elem" else pool
                d = haversine_km(lat, lon, near.lat.to_numpy(), near.lon.to_numpy())
                i = near.index[int(np.argmin(d))]
                src = "nearest"
            if i is None:
                continue
            s = pool.loc[i]
            rated = s
            if kind == "elem" and pd.isna(s.fsa_pctile):
                # Annexes (K-3) have no Grade 4 results: rate them by the main school their students move up to.
                base = pool[(pool.DISTRICT_NUMBER == s.DISTRICT_NUMBER) & pool.fsa_pctile.notna()]
                keys = {_norm(n): j for j, n in zip(base.index, base.SCHOOL_NAME)}
                hit = difflib.get_close_matches(_norm(s.SCHOOL_NAME), list(keys), n=1, cutoff=0.8)
                if hit:
                    rated = base.loc[keys[hit[0]]]
            dist = named_km if src == "catchment" and pd.notna(named_km) else (
                float(haversine_km(lat, lon, s.lat, s.lon)) if pd.notna(lat) else None)
            out[kind] = {"name": s.SCHOOL_NAME, "src": src, "km": round(float(dist), 2) if dist is not None else None,
                         "fsa": None if pd.isna(rated.fsa_avg) else round(float(rated.fsa_avg), 1),
                         "pctile": None if pd.isna(rated.fsa_pctile) else round(float(rated.fsa_pctile)),
                         "rating": None if pd.isna(rated.fsa_pctile) else round(float(rated.fsa_pctile) / 10, 1),
                         "meet": None if pd.isna(rated.fsa_meet_pct) else round(float(rated.fsa_meet_pct)),
                         "rated_as": rated.SCHOOL_NAME if rated is not s else None,
                         "french": bool(s.french)}
        e, s2 = out.get("elem"), out.get("sec")

        def prox(km, full, zero):
            return 0.0 if km is None else float(np.clip(1 - (km - full) / (zero - full), 0, 1)) * 100
        quality = e["pctile"] if e and e["pctile"] is not None else 50.0
        score = 0.6 * quality + 0.25 * prox(e and e["km"], 0.8, 3.0) + 0.15 * prox(s2 and s2["km"], 1.2, 5.0)
        rows[idx] = {"school_elem": e, "school_sec": s2, "school_score": round(score, 1)}
    return pd.DataFrame.from_dict(rows, orient="index")


# ---------- Walkability ----------
# (category, weight, tags, per-rank weights) — in the spirit of Walk Score's gravity method, rebuilt from OSM.
WALK_CATS = [
    ("Groceries", 3, {"supermarket", "greengrocer", "butcher", "bakery", "marketplace"}, [1, .5]),
    ("Restaurants & cafes", 3, {"restaurant", "cafe", "fast_food", "pub"}, [.75, .45, .25, .25, .225, .225, .225, .225, .2, .2]),
    ("Shops", 2, {"convenience", "variety_store", "mall", "department_store", "hardware", "books", "clothes", "chemist"}, [.5, .45, .4, .35, .3]),
    ("Errands & health", 2, {"pharmacy", "bank", "post_office", "clinic", "doctors", "dentist"}, [.5, .45, .4, .35, .3]),
    ("Parks & playgrounds", 2, {"park", "playground"}, [1, .6, .4]),
    ("Rec & library", 1, {"library", "community_centre", "sports_centre", "fitness_centre", "swimming_pool"}, [1, .5, .3]),
    ("Childcare", 1, {"kindergarten", "childcare"}, [1, .5]),
    ("Bus stops", 2, {"bus_stop"}, [1, .6, .4, .3]),
]


def decay(d):
    # Full credit within a 4-minute walk (300 m), 12% at 15 minutes (1.2 km), nothing past 2 km.
    return np.where(d <= 0.3, 1.0, np.where(d <= 1.2, 1 - 0.88 * (d - 0.3) / 0.9,
                                            np.where(d <= 2.0, 0.12 * (1 - (d - 1.2) / 0.8), 0.0)))


def load_osm():
    els = json.loads((EXT / "osm_amenities.json").read_text())["elements"]
    rows = []
    for e in els:
        t = e.get("tags", {})
        kind = t.get("amenity") or t.get("shop") or t.get("leisure") or t.get("highway")
        if kind == "swimming_pool" and not (t.get("name") and t.get("access", "public") in ("public", "yes")):
            continue  # skip private backyard pools
        lat = e.get("lat", e.get("center", {}).get("lat"))
        lon = e.get("lon", e.get("center", {}).get("lon"))
        if lat is not None:
            rows.append((kind, lat, lon))
    return pd.DataFrame(rows, columns=["kind", "lat", "lon"])


def walk_scores(cur: pd.DataFrame) -> pd.DataFrame:
    osm = load_osm()
    total_w = sum(w for _, w, _, _ in WALK_CATS) + 2  # +2 for SkyTrain
    rows = {}
    for idx, r in cur.iterrows():
        if pd.isna(r.latitude):
            continue
        detail, score = {}, 0.0
        for name, w, kinds, ranks in WALK_CATS:
            sub = osm[osm.kind.isin(kinds)]
            d = np.sort(haversine_km(r.latitude, r.longitude, sub.lat.to_numpy(), sub.lon.to_numpy()))[:len(ranks)]
            cat = float(np.sum(np.array(ranks[:len(d)]) * decay(d)) / np.sum(ranks))
            score += w * cat
            detail[name] = {"score": round(cat * 100), "n800": int((haversine_km(r.latitude, r.longitude, sub.lat.to_numpy(), sub.lon.to_numpy()) <= 0.8).sum())}
        sky = float(decay(np.array([r.station_km]))[0])
        score += 2 * sky
        detail["SkyTrain"] = {"score": round(sky * 100), "n800": int(r.station_km <= 0.8)}
        rows[idx] = {"walk": round(score / total_w * 100, 1), "walk_detail": detail}
    return pd.DataFrame.from_dict(rows, orient="index")
