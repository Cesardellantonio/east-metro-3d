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


# ---------- School access per home ----------
LEVEL = {"Elementary": "elem", "Middle School": "middle", "Secondary": "sec", "Senior Secondary": "sec", "Junior Secondary": "sec",
         "Elementary-Secondary": "k12", "Elementary Jr. Secondary": "k12"}
MIDDLE_DISTRICTS = {"043", "040"}  # SD43 (Coquitlam, Port Moody, Port Coquitlam) and New Westminster run K-5 / 6-8 / 9-12


def load_all_schools(bbox=(-123.32, 49.12, -122.64, 49.38)):
    """Public and independent standard schools around the study area, each rated on the public-school FSA scale."""
    k = pd.read_csv(EXT / "bc_k12_schools.csv", dtype=str, encoding="utf-8-sig")
    k["lat"], k["lon"] = pd.to_numeric(k.LATITUDE, errors="coerce"), pd.to_numeric(k.LONGITUDE, errors="coerce")
    k = k[(k.FACILITY_TYPE == "Standard School") & k.lon.between(bbox[0], bbox[2]) & k.lat.between(bbox[1], bbox[3])].copy()
    k["level"] = k.SCHOOL_EDUCATION_LEVEL.map(LEVEL).fillna("k12")
    k["public"] = k.PUBLIC_OR_INDEPENDENT == "Public School"
    k["french"] = (k.HAS_EARLY_FRENCH_IMMERSION == "YES") | (k.HAS_LATE_FRENCH_IMMERSION == "YES") | (k.HAS_PROG_FRANCOPHONE == "YES")
    f = pd.read_csv(EXT / "fsa_2021_2026.csv", encoding="latin-1", dtype=str)
    f = f[(f.DATA_LEVEL == "School Level") & (f.SUB_POPULATION == "All Students") & f.SCHOOL_YEAR.isin(FSA_YEARS)].copy()
    f["AVG_SCORE"] = pd.to_numeric(f.AVG_SCORE, errors="coerce")
    f["NUMBER_WRITERS"] = pd.to_numeric(f.NUMBER_WRITERS, errors="coerce").fillna(20)
    f = f[f.AVG_SCORE.notna()]
    f["w"] = f.AVG_SCORE * f.NUMBER_WRITERS
    f["meet"] = _num(f.NUMBER_ONTRACK).fillna(0) + _num(f.NUMBER_EXTENDING).fillna(0)
    f["wk"] = _num(f.NUMBER_WRITERS).where(_num(f.NUMBER_ONTRACK).notna())
    g = f.groupby("SCHOOL_NUMBER").agg(ws=("w", "sum"), n=("NUMBER_WRITERS", "sum"), meet=("meet", "sum"), wk=("wk", "sum"))
    k = k.join((g.ws / g.n).rename("fsa"), on="MINCODE").join(g.n.rename("writers"), on="MINCODE")
    k = k.join((g.meet / g.wk * 100).where(g.wk >= 20).round().rename("fsa_meet"), on="MINCODE")
    ref = load_schools()  # public Metro schools: the scale every rating is placed on
    ref = ref[ref.fsa_avg.notna() & (ref.writers >= 20)].fsa_avg.to_numpy()
    k["rating"] = k.fsa.map(lambda v: round(float((ref < v).mean() * 10), 1) if pd.notna(v) else None)
    k = k.reset_index(drop=True)
    if (EXT / "schools").exists():  # profiles: size, class size, graduation, Grade 10/12 assessments
        prof = school_profiles()
        k = k.join(prof, on="MINCODE")
        k["sec_rating"] = k.MINCODE.map(secondary_ratings(prof, k))
        k["district_completion"] = k.DISTRICT_NUMBER.map(district_completion())
    return k


def school_access(homes, schools):
    """For each home: likely catchment elementary / middle / secondary, nearest French immersion, and other options.

    Catchment comes from the listing when it names the school, otherwise the nearest public school of that level
    (flagged 'likely'; districts draw catchments by boundary, not distance). Distances are straight-line.
    """
    lat, lon = schools.lat.to_numpy(), schools.lon.to_numpy()
    pub, lvl, fr = schools.public.to_numpy(), schools.level.to_numpy(), schools.french.to_numpy()
    dist_code = schools.DISTRICT_NUMBER.to_numpy()
    names = {n.lower(): i for i, n in enumerate(schools.SCHOOL_NAME)}
    out = []
    for h in homes:
        d = haversine_km(h["lat"], h["lon"], lat, lon)
        district = CITY_DISTRICT.get(h["c"])

        def nearest(mask):
            idx = np.where(mask)[0]
            return int(idx[np.argmin(d[idx])]) if len(idx) else None
        same = dist_code == district if district else np.ones(len(d), bool)
        elem_i, elem_src = None, "likely"
        if h.get("es") and h.get("ec"):
            elem_i = names.get(h["es"].lower())
            elem_src = "listing" if elem_i is not None else "likely"
        if elem_i is None:
            elem_i = nearest(pub & (lvl == "elem") & same) if same.any() else nearest(pub & (lvl == "elem"))
        middle_i = nearest(pub & (lvl == "middle") & same) if district in MIDDLE_DISTRICTS else None
        sec_i = nearest(pub & np.isin(lvl, ["sec", "k12"]) & same) if same.any() else nearest(pub & (lvl == "sec"))
        fi_i = nearest(pub & fr & np.isin(lvl, ["elem", "k12", "middle"]) & (d <= 6))
        taken = {elem_i, middle_i, sec_i, fi_i}
        others = [int(i) for i in np.argsort(d) if int(i) not in taken and (
            (pub[i] and lvl[i] == "elem" and d[i] <= 2.0) or (not pub[i] and d[i] <= 3.0))][:8]
        entry = lambda i, role, src="": None if i is None else [int(i), round(float(d[i]), 2), role, src]
        out.append({"likely": [e for e in (entry(elem_i, "elem", elem_src), entry(middle_i, "middle", "likely"), entry(sec_i, "sec", "likely")) if e],
                    "fi": entry(fi_i, "fi"), "other": [entry(i, "other") for i in others],
                    "walk15": int(((d <= 1.2) & pub & (lvl == "elem")).sum()),
                    "indep3": int(((d <= 3.0) & ~pub).sum())})
    return out


# ---------- School profiles: size, class size, graduation, Grade 10/12 assessments ----------
SCH = EXT / "schools"
RECENT = ["2022/2023", "2023/2024", "2024/2025"]
METRO_ALL = METRO_DISTRICTS


def _num(s):
    return pd.to_numeric(s, errors="coerce")


def school_profiles():
    """Per-school facts from BC open data (Open Government Licence – BC), keyed by MINCODE."""
    out = pd.DataFrame()
    # Enrolment (latest year) and English-language learners.
    h = pd.read_csv(SCH / "student_headcount_by_grade_2017_18_to_2025_26.csv", encoding="latin-1", dtype=str, low_memory=False)
    h = h[(h.DATA_LEVEL == "School Level") & (h.GRADE == "All Grades")]
    latest = h.SCHOOL_YEAR.max()
    h = h[h.SCHOOL_YEAR == latest].assign(total=lambda d: _num(d.TOTAL_STUDENTS), ell=lambda d: _num(d.ELL_STUDENTS))
    h = h.sort_values("total").drop_duplicates("SCHOOL_NUMBER", keep="last").set_index("SCHOOL_NUMBER")
    out["enrol"] = h.total
    out["ell_pct"] = (h.ell / h.total * 100).round()
    out["enrol_year"] = latest
    # Average class size (public schools report it).
    c = pd.read_csv(SCH / "class_size_2006-07_to_2025-26.csv", encoding="latin-1", dtype=str)
    c = c[(c.DATA_LEVEL == "School Level") & (c.SCHOOL_YEAR == c.SCHOOL_YEAR.max())].set_index("SCHOOL_NUMBER")
    out = out.join(_num(c.AVG_CLASS_SIZE_ALL_GRADES).round(1).rename("class_size"), how="outer")
    # First-time Grade 12 graduation and honours, pooled over the last three years.
    g = pd.read_csv(SCH / "first_time_g12_graduation_rate_1996-97_to_2024-25_residents_only.csv", encoding="latin-1", dtype=str)
    g = g[(g.DATA_LEVEL == "School Level") & (g.SUB_POPULATION == "All Students") & g.SCHOOL_YEAR.isin(RECENT)].copy()
    for col in ["FIRST_TIME_GRADE_12_COUNT", "FIRST_TIME_GRADUATION_COUNT", "HONOURS_COUNT"]:
        g[col] = _num(g[col])
    gg = g.groupby("SCHOOL_NUMBER")[["FIRST_TIME_GRADE_12_COUNT", "FIRST_TIME_GRADUATION_COUNT", "HONOURS_COUNT"]].sum(min_count=1)
    gg = gg[gg.FIRST_TIME_GRADE_12_COUNT >= 20]
    out = out.join(pd.DataFrame({"grad_rate": (gg.FIRST_TIME_GRADUATION_COUNT / gg.FIRST_TIME_GRADE_12_COUNT * 100).round(),
                                 "honours_rate": (gg.HONOURS_COUNT / gg.FIRST_TIME_GRADE_12_COUNT * 100).round(),
                                 "grade12": gg.FIRST_TIME_GRADE_12_COUNT}), how="outer")
    # Grade 10/12 graduation assessments: mean score per writer, and share proficient or extending.
    a = pd.read_csv(SCH / "graduation_assessment_2017-18_to_2024-25_proficiency_result.csv", encoding="latin-1", dtype=str)
    a = a[(a.DATA_LEVEL == "School Level") & (a.SUB_POPULATION == "All Students") & a.SCHOOL_YEAR.isin(RECENT) & (a.ASSESSMENT_LANGUAGE == "English")].copy()
    a["w"], a["score"] = _num(a.NUMBER_WRITERS), _num(a.SCORE)
    a["prof"] = _num(a.NUMBER_PROFICIENT) + _num(a.NUMBER_EXTENDING)  # NaN when either is masked (<10)
    rows = {}
    for (school, test), d in a.groupby(["SCHOOL_NUMBER", "GRADUATION_ASSESSMENT"]):
        w = d.w.sum()
        if w >= 20:
            known = d[d.prof.notna()]
            rows.setdefault(school, {})[test] = (d.score.sum() / w, (known.prof.sum() / known.w.sum() * 100) if known.w.sum() >= 20 else None)
    ga = pd.DataFrame({s: {"ga_num": v.get("Numeracy Assessment 10", (None, None))[0], "ga_lit10": v.get("Literacy Assessment 10", (None, None))[0],
                           "ga_lit12": v.get("Literacy Assessment 12", (None, None))[0],
                           "num_prof": v.get("Numeracy Assessment 10", (None, None))[1], "lit_prof": v.get("Literacy Assessment 10", (None, None))[1]}
                       for s, v in rows.items()}).T
    out = out.join(ga, how="outer")
    return out


def district_completion():
    c = pd.read_csv(SCH / "completion_rate_residents_only_1999-2000_to_2024-2025.csv", encoding="latin-1", dtype=str)
    c = c[(c.SUB_POPULATION == "All Students") & (c.COMPLETION_RATE_MODEL == "6 Year Completion") & (c.PUBLIC_OR_INDEPENDENT == "Public School")
          & (c.DATA_LEVEL == "District Level")]
    c = c[c.YEAR_6_OF_COHORT == c.YEAR_6_OF_COHORT.max()].drop_duplicates("DISTRICT_NUMBER")
    return {r.DISTRICT_NUMBER: round(float(r.ESTIMATED_COMPLETION_RATE)) for r in c.itertuples() if pd.notna(_num(r.ESTIMATED_COMPLETION_RATE))}


def secondary_ratings(prof: pd.DataFrame, schools: pd.DataFrame):
    """0–10 rating for secondaries from Grade 10/12 assessments, ranked among Metro public secondaries."""
    cols = ["ga_num", "ga_lit10", "ga_lit12"]
    ref = prof.loc[prof.index.isin(schools[schools.public & schools.level.isin(["sec", "k12"])].MINCODE), cols].astype(float)
    pct = pd.DataFrame(index=prof.index)
    for c in cols:
        r = ref[c].dropna().to_numpy()
        pct[c] = prof[c].astype(float).map(lambda v: (r < v).mean() if pd.notna(v) and len(r) else np.nan)
    return (pct.mean(axis=1, skipna=True) * 10).round(1)


# ---------- The price of a top school ----------
TOP = 9.0  # rating 9+ = top 10% of Metro Vancouver public schools
# Tuition is not published as open data: these are editable planning estimates by school type (CAD per child per year).
TUITION = {"catholic": 7000, "faith": 14000, "independent": 22000, "prep": 36000}
PREP = ["st. george's", "york house", "crofton house", "west point grey academy", "collingwood school", "mulgrave",
        "st. john's school", "stratford hall", "southridge", "meadowridge", "vancouver college", "little flower academy",
        "fraser academy", "st. patrick's regional", "bodwell", "pacific academy"]
CATHOLIC = re.compile(r"\b(st\.?|saint|our lady|holy|sacred|immaculate|blessed|corpus christi|queen of|notre dame|catholic|assumption|guardian angels|star of the sea|good shepherd|st)\b", re.I)
FAITH = re.compile(r"christian|baptist|adventist|sda\b|lutheran|jewish|islamic|mennonite|khalsa|hebrew|torah|talmud|faith|covenant|cedar|john knox", re.I)


def tuition_tier(name):
    n = name.lower()
    if any(p in n for p in PREP):
        return "prep"
    if FAITH.search(name):
        return "faith"
    if CATHOLIC.search(name):
        return "catholic"
    return "independent"


def top_school_routes(homes, schools, top=TOP):
    """Per home: is the likely catchment top-rated, and the nearest top public / top independent options."""
    lat, lon = schools.lat.to_numpy(), schools.lon.to_numpy()
    pub, lvl = schools.public.to_numpy(), schools.level.to_numpy()
    er = schools.rating.astype(float).to_numpy()
    sr = schools.sec_rating.astype(float).to_numpy() if "sec_rating" in schools else np.full(len(schools), np.nan)
    elem_r = np.where(np.isnan(er), sr, er)
    sec_r = np.where(np.isnan(sr), er, sr)
    elem_ok = np.isin(lvl, ["elem", "k12", "middle"])
    sec_ok = np.isin(lvl, ["sec", "k12"])
    out = []
    for h in homes:
        d = haversine_km(h["lat"], h["lon"], lat, lon)

        def nearest(mask, maxkm):
            idx = np.where(mask & (d <= maxkm))[0]
            if not len(idx):
                return None
            i = int(idx[np.argmin(d[idx])])
            return [i, round(float(d[i]), 2)]
        likely = {e[2]: e for e in h["sa"]["likely"]}
        e_i = likely.get("elem", [None])[0]
        s_i = likely.get("sec", [None])[0]
        out.append({
            "e_top": bool(e_i is not None and elem_r[e_i] >= top),
            "s_top": bool(s_i is not None and sec_r[s_i] >= top),
            "pub_e": nearest(pub & elem_ok & (elem_r >= top), 15),
            "pub_s": nearest(pub & sec_ok & (sec_r >= top), 20),
            "prv_e": nearest(~pub & elem_ok & (elem_r >= top), 10),
            "prv_s": nearest(~pub & sec_ok & (sec_r >= top), 15),
        })
    return out


def catchment_premium(homes, schools):
    """How much more homes ask per point of catchment-school rating, holding size, type, age, city and transit equal."""
    rows = []
    for h in homes:
        likely = {e[2]: e for e in h["sa"]["likely"]}
        if "elem" not in likely:
            continue
        r = schools.rating.iloc[likely["elem"][0]]
        if pd.isna(r):
            continue
        rows.append({"lp": np.log(h["p"]), "ls": np.log(h["sq"]), "t": h["t"], "c": h["c"], "b": h.get("ba") or 2,
                     "age": (2026 - h["y"]) if h.get("y") else np.nan, "stn": np.log(max(h.get("skm") or 1, .1)), "r": float(r),
                     "top": float(r >= TOP)})
    d = pd.DataFrame(rows)
    d["age_k"] = d.age.notna().astype(float)
    d["age"] = d.age.fillna(0) / 10
    X = pd.get_dummies(d[["ls", "b", "age", "age_k", "stn", "t", "c"]], columns=["t", "c"], drop_first=True, dtype=float)
    X.insert(0, "const", 1.0)
    res = {}
    for name, col in (("per_point", "r"), ("top10", "top")):
        A = np.column_stack([X.to_numpy(), d[col].to_numpy()])
        y = d.lp.to_numpy()
        beta, *_ = np.linalg.lstsq(A, y, rcond=None)
        resid = y - A @ beta
        sigma2 = resid @ resid / (len(y) - A.shape[1])
        se = np.sqrt(np.diag(sigma2 * np.linalg.pinv(A.T @ A)))[-1]
        res[name] = {"pct": round(float((np.exp(beta[-1]) - 1) * 100), 2), "lo": round(float((np.exp(beta[-1] - 1.96 * se) - 1) * 100), 2),
                     "hi": round(float((np.exp(beta[-1] + 1.96 * se) - 1) * 100), 2)}
    res["n"] = int(len(d))
    res["n_top"] = int(d.top.sum())
    return res
