"""Market + mispricing analysis over the Hermes real-estate-radar data.

Source of truth is the canonical Hermes workspace (~/.hermes/projects/real-estate-radar), opened read-only:
  - data/real_estate.sqlite3: 3-bed research runs (listing_snapshots) — the main listing set
  - inventory/exports/current_sale.csv / current_rental.csv: Hermes's all-cities inventory (card-level facts),
    used to add homes the 3-bed research doesn't cover (incl. detached houses) and city rent levels.
GVR HPI benchmarks come from the vre Postgres db when available. Writes analysis/output/analysis.json.

    python3 analysis/market_analysis.py [--hermes ~/.hermes/projects/real-estate-radar] [--out analysis/output/analysis.json]
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

from neighbourhood_scores import school_scores, walk_scores

PSQL = "/opt/homebrew/opt/postgresql@18/bin/psql"
HERMES = Path.home() / ".hermes" / "projects" / "real-estate-radar"
CITIES = {"Burnaby", "Vancouver", "New Westminster", "Port Moody", "Coquitlam", "Port Coquitlam"}
PRICE_CAP = 1_500_000      # Hermes: $1.2M main ceiling, $1.2M-1.5M shown as higher-price comparisons
MAIN_CEILING = 1_200_000
# Hermes v2 flags -> the restriction vocabulary the dashboard understands.
HERMES_FLAGS = {"new_build_resale_unverified": "resale not established", "bedroom_integrity": "third-bedroom integrity conflict",
                "inactive_status": "inactive status"}
LATEST_KIND_ORDER = {"ranking": 0, "watchlist": 1, "candidate": 2}

# SkyTrain / West Coast Express stations near the study area (lat, lon).
STATIONS = {
    "Metrotown": (49.2258, -123.0039), "Patterson": (49.2297, -123.0126), "Royal Oak": (49.2200, -122.9885),
    "Edmonds": (49.2123, -122.9592), "Joyce-Collingwood": (49.2384, -123.0318), "29th Ave": (49.2443, -123.0460),
    "Nanaimo": (49.2483, -123.0559), "Commercial-Broadway": (49.2626, -123.0692), "Renfrew": (49.2590, -123.0450),
    "Rupert": (49.2608, -123.0328), "Gilmore": (49.2649, -123.0137), "Brentwood": (49.2664, -123.0019),
    "Holdom": (49.2647, -122.9822), "Sperling": (49.2593, -122.9640), "Lake City": (49.2546, -122.9391),
    "Production Way": (49.2534, -122.9181), "Lougheed": (49.2485, -122.8970), "Burquitlam": (49.2613, -122.8899),
    "Moody Centre": (49.2779, -122.8459), "Inlet Centre": (49.2772, -122.8279), "Coquitlam Central": (49.2740, -122.8000),
    "Lincoln": (49.2804, -122.7941), "Lafarge Lake": (49.2856, -122.7916), "22nd St": (49.2000, -122.9490),
    "New Westminster": (49.2015, -122.9127), "Columbia": (49.2048, -122.9061), "Sapperton": (49.2248, -122.8894),
    "Braid": (49.2330, -122.8830), "Scott Rd": (49.2044, -122.8741), "Main St": (49.2732, -123.1004),
    "Broadway-City Hall": (49.2627, -123.1149), "King Edward": (49.2491, -123.1155), "Oakridge": (49.2332, -123.1165),
    "Langara": (49.2263, -123.1162), "Marine Dr": (49.2098, -123.1170), "VCC-Clark": (49.2658, -123.0789),
    "Port Coquitlam WCE": (49.2618, -122.7740),
}


def haversine_km(lat, lon, lat2, lon2):
    lat, lon, lat2, lon2 = map(np.radians, (lat, lon, lat2, lon2))
    a = np.sin((lat2 - lat) / 2) ** 2 + np.cos(lat) * np.cos(lat2) * np.sin((lon2 - lon) / 2) ** 2
    return 6371 * 2 * np.arcsin(np.sqrt(a))


def nearest_station(lat, lon):
    names = list(STATIONS)
    coords = np.array([STATIONS[n] for n in names])
    d = haversine_km(lat[:, None], lon[:, None], coords[None, :, 0], coords[None, :, 1])
    i = np.nanargmin(np.where(np.isnan(d), np.inf, d), axis=1)
    return np.array(names)[i], d[np.arange(len(lat)), i]


CITY_SLUGS = ["port-coquitlam", "new-westminster", "north-vancouver", "port-moody", "coquitlam", "burnaby", "vancouver"]


def slug_to_address(slug):
    """'2204-183-keefer-place-vancouver-bc' -> '2204 - 183 Keefer Place' (some Hermes rows carry the URL slug as address)."""
    s = re.sub(r"-bc$", "", slug.strip().lower())
    for c in CITY_SLUGS:
        if s.endswith("-" + c):
            s = s[: -len(c) - 1]
            break
    parts = s.split("-")
    unit = None
    if len(parts) > 2 and re.fullmatch(r"(ph|th|sl)?\d+[a-z]?", parts[0]) and re.fullmatch(r"\d+[a-z]?", parts[1]):
        unit, parts = parts[0].upper(), parts[1:]
    words = [w.upper() if w in ("e", "w", "n", "s", "se", "sw", "ne", "nw") else w.capitalize() for w in parts]
    street = " ".join(words)
    return f"{unit} - {street}" if unit else street


def clean_address(addr):
    """'3-8693 Oolichan Way, Vancouver, BC, V5S 0G7' -> '3 - 8693 Oolichan Way' (legacy style used for matching)."""
    a = re.sub(r",.*$", "", str(addr or "")).strip()
    if a and " " not in a and "-" in a:
        return slug_to_address(a)
    return re.sub(r"^([A-Za-z]*\d+[A-Za-z]?)-(\d+\s)", r"\1 - \2", a)


def type_of(raw_type):
    t = str(raw_type or "")
    if "Condo" in t or "Apartment" in t:
        return "Condo"
    if "Townhouse" in t or "Row" in t:
        return "Townhouse"
    return "House"


def load_snapshots(db: Path) -> pd.DataFrame:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    df = pd.read_sql(
        """select s.*, r.report_date, p.mls, p.zone as pzone, p.neighbourhood as pneigh, p.address, p.latitude, p.longitude,
                  p.is_active, p.first_seen, p.last_seen, p.canonical_url
           from listing_snapshots s join runs r on r.id = s.run_id join properties p on p.id = s.property_id""",
        con,
    )
    raw = df.raw_json.map(json.loads)
    for key in ["tenure", "schools_text", "municipality", "storage", "facts", "description"]:
        df["r_" + key] = raw.map(lambda d, k=key: d.get(k))
    # Hermes v2 rows use different keys than the legacy collector; normalise both.
    df["r_property_type"] = raw.map(lambda d: d.get("property_type") or d.get("type"))
    df["r_tenure"] = [t or ((f or {}).get("Title") if isinstance(f, dict) else None) for t, f in zip(df.r_tenure, df.r_facts)]
    df["r_exclusion_hits"] = raw.map(lambda d: sorted(set((d.get("exclusion_hits") or []) + [HERMES_FLAGS[f] for f in (d.get("flags") or []) if f in HERMES_FLAGS])))
    df["year_built"] = df.year_built.fillna(raw.map(lambda d: d.get("year_built") or d.get("year")).astype(float))
    df["taxes"] = df.taxes.fillna(raw.map(lambda d: d.get("annual_taxes")).astype(float))
    df["hermes_bucket"] = raw.map(lambda d: d.get("bucket"))
    df["hermes_rank"] = raw.map(lambda d: d.get("position") if d.get("bucket") == "shortlist" else None)
    df["fact_level"] = raw.map(lambda d: d.get("fact_level"))
    df["community"] = raw.map(lambda d: (re.search(r"'community': '([^']+)'", str(d.get("listing_claims") or "")) or [None, None])[1])
    df["address"] = df.address.map(clean_address)
    df["source"] = "Hermes 3-bed research"
    return df


def load_inventory(hermes: Path, known_urls: set) -> pd.DataFrame:
    """Homes from Hermes's all-cities inventory that the 3-bed research doesn't already cover."""
    path = hermes / "inventory" / "exports" / "current_sale.csv"
    if not path.exists():
        return pd.DataFrame()
    inv = pd.read_csv(path)
    inv = inv[inv.city.isin(CITIES) & inv.price.le(PRICE_CAP) & inv.latitude.notna() & ~inv.canonical_url.isin(known_urls)].copy()
    inv["type"] = inv.property_type.map(type_of)
    inv = inv[((inv.type != "House") & inv.beds.eq(3)) | ((inv.type == "House") & inv.beds.ge(3))]
    inv = inv[~inv.property_type.isin(["Multifamily", "Fourplex"])]

    def community(raw):
        try:
            ld = json.loads(raw).get("json_ld") or []
            for block in ld:
                for obj in (block if isinstance(block, list) else [block]):
                    loc = (obj.get("address") or {}).get("addressLocality")
                    if loc:
                        return loc
        except Exception:
            return None
        return None
    run_date = str(inv.last_seen_at.max())[:10] if len(inv) else None
    out = pd.DataFrame({
        "property_id": -inv.offer_id, "report_date": run_date, "price": inv.price, "beds": inv.beds, "baths": inv.baths,
        "sqft": inv.sqft, "psf": inv.price / inv.sqft, "strata": inv.strata_fee, "taxes": inv.tax_annual, "year_built": inv.year_built,
        "dom": inv.dom, "parking": inv.parking, "latitude": inv.latitude, "longitude": inv.longitude,
        "address": inv.address.map(clean_address), "pzone": inv.city, "pneigh": inv.current_raw_json.map(community),
        "mls": inv.mls, "listing_url": inv.canonical_url, "canonical_url": inv.canonical_url,
        "r_property_type": inv.property_type, "r_tenure": inv.tenure, "r_schools_text": None, "r_facts": None,
        "r_description": inv.description, "r_exclusion_hits": [[] for _ in range(len(inv))], "list_kind": "inventory",
        "hermes_bucket": None, "hermes_rank": None, "fact_level": "card", "community": None, "source": "Hermes inventory",
    })
    return out[out.sqft.gt(0)]


def inventory_coverage(hermes: Path):
    path = hermes / "inventory" / "exports" / "coverage.json"
    if not path.exists():
        return None
    c = json.loads(path.read_text())
    cities = {}
    for x in c.get("coverage", []):
        if x["offer_kind"] == "sale" and x["locality"] in CITIES:
            cities[x["locality"]] = {"pages": x["pages_succeeded"], "cards": x["cards_accepted"], "complete": bool(x["is_complete"]),
                                     "reason": x["terminal_reason"]}
    return {"finished_at": c.get("finished_at"), "status": c.get("status"), "cities": cities}


RESEARCH_CARRY_DAYS = 7
CARRY_FIELDS = ["year_built", "strata", "taxes", "parking", "dom", "r_tenure", "r_facts", "r_description", "r_exclusion_hits",
                "r_schools_text", "fact_level", "community", "pneigh"]


def carry_research_details(inv: pd.DataFrame, df: pd.DataFrame, hist: pd.DataFrame, latest_date: str) -> pd.DataFrame:
    """Homes still listed in today's inventory keep the verified facts Hermes's research read in the last week.

    Hermes treats dropping out of a research run as unverified (not a sale), and the inventory confirms the listing
    is still up, so its card price is today's price while year, strata, tax, tenure and warnings come from research.
    The research price history is joined too, so a cut between research and today shows up.
    """
    cutoff = (pd.Timestamp(latest_date) - pd.Timedelta(days=RESEARCH_CARRY_DAYS)).strftime("%Y-%m-%d")
    recent = df[df.report_date.ge(cutoff)].sort_values(["report_date", "year_built"], na_position="first")
    by_url = {}
    for _, r in recent.iterrows():
        for u in (r.listing_url, r.canonical_url):
            if isinstance(u, str):
                by_url[u] = r
    inv = inv.copy()
    carried = 0
    for i, row in inv.iterrows():
        r = by_url.get(row.canonical_url)
        if r is None:
            continue
        carried += 1
        for f in CARRY_FIELDS:
            v = r.get(f)
            if f == "r_exclusion_hits" or (v is not None and not (isinstance(v, float) and np.isnan(v))):
                inv.at[i, f] = v if f != "r_exclusion_hits" else list(v or [])
        inv.at[i, "source"] = f"Hermes research ({r.report_date}) + today's inventory"
        if r.property_id in hist.index:
            h = hist.loc[r.property_id]
            series = list(h.history) + ([[row.report_date, float(row.price)]] if h.history and h.history[-1][1] != row.price else [])
            prices = [p for _, p in series]
            inv.at[i, "history"] = series
            inv.at[i, "first_price"] = prices[0]
            inv.at[i, "cuts"] = int(sum(1 for a, b in zip(prices, prices[1:]) if b < a))
            inv.at[i, "cut_pct"] = (prices[-1] / max(prices) - 1) * 100
    print(f"carried research details onto {carried} inventory listings (research within {RESEARCH_CARRY_DAYS} days)")
    return inv


def rent_levels(hermes: Path):
    """Median asking rent for 3-bed homes by city from the Hermes inventory (monthly rents only)."""
    path = hermes / "inventory" / "exports" / "current_rental.csv"
    if not path.exists():
        return {}
    r = pd.read_csv(path)
    r = r[r.city.isin(CITIES) & r.beds.eq(3) & r.price.between(1000, 15000) & r.rent_period.fillna("month").eq("month")]
    if "match_group_id" in r:  # the same rental listed on several sites counts once
        r = pd.concat([r[r.match_group_id.isna()], r[r.match_group_id.notna()].drop_duplicates("match_group_id")])
    return {c: {"median": float(g.price.median()), "n": int(len(g))} for c, g in r.groupby("city") if len(g) >= 5}


def catchment_schools(text):
    """Nearest in-catchment public elementary and secondary school from the listing's schools blurb."""
    if not isinstance(text, str):
        return None, None
    text = re.sub(r"^\s*\d+ Schools are within \d+\s*km", "", text)
    elem = sec = None
    for m in re.finditer(r"\s*(.+?)\s+Public\s+[\w-]+\s*-\s*\d+\s+In Catchment\s+([\d.]+) km", text):
        name = re.sub(r"^.*\d km\s+", "", m.group(1)).strip()
        if "Secondary" in name and sec is None:
            sec = (name, float(m.group(2)))
        elif "Secondary" not in name and elem is None:
            elem = (name, float(m.group(2)))
    return elem, sec


# ---------- Luxury ----------
# (pattern, points, label). Building amenities come from the listing's facts; finishes from its description.
LUX_AMENITIES = [
    (r"Concierge", 2.0, "Concierge"), (r"Exercise Centre", 0.75, "Gym"), (r"Pool", 1.5, "Pool"),
    (r"Sauna|Steam Room", 0.75, "Sauna / steam"), (r"Clubhouse", 0.5, "Clubhouse"), (r"Recreation Facilities", 0.25, "Rec room"),
    (r"Guest Suite", 0.5, "Guest suite"), (r"Security System", 0.5, "Security system"), (r"Geothermal", 1.0, "Geothermal"),
]
LUX_TEXT = [
    (r"\b(miele|sub-?zero|wolf|gaggenau|thermador|fisher\s*&?\s*paykel|jenn-?air|bosch|liebherr)\b", 1.5, "Premium appliances"),
    (r"\b(quartz|marble|porcelain slab|granite)\b", 0.75, "Stone counters"),
    (r"\b(hardwood|engineered wood|wide[- ]plank)\b", 0.5, "Hardwood floors"),
    (r"heated floor|radiant (floor|heat)", 1.0, "Heated floors"),
    (r"\b(sub-?)?penthouse\b", 2.0, "Penthouse"),
    (r"floor[- ]to[- ]ceiling|panoramic|sweeping view", 1.0, "Floor-to-ceiling / panoramic"),
    (r"\b(water|ocean|inlet|river|mountain|city|harbour|harbor)\s+views?\b", 1.0, "Views"),
    (r"air[- ]condition|\ba/c\b|heat pump|central air", 1.5, "Air conditioning"),
    (r"roof ?top", 0.5, "Rooftop deck"),
    (r"\b(designer|luxury|luxurious|high[- ]end|bespoke|custom millwork|custom[- ]built)\b", 1.0, "Designer finishes"),
    (r"\bev charg|electric vehicle", 0.5, "EV charging"),
    (r"smart home|nest thermostat|keyless", 0.5, "Smart home"),
    (r"(fully |completely |totally )?(renovated|remodel+ed)", 0.75, "Renovated"),
    (r"spa[- ](like|inspired)|soaker tub|rain shower", 0.5, "Spa bathroom"),
    (r"\bdouble garage|side[- ]by[- ]side garage|2[- ]car garage", 0.75, "Double garage"),
]
LUX_TIERS = [(6.5, "Luxury"), (4.0, "Premium"), (2.0, "Upgraded"), (-1, "Standard")]


def luxury(facts, description):
    facts = facts if isinstance(facts, dict) else None
    description = description if isinstance(description, str) and description.strip() else None
    if not facts and not description:
        return None, None, []
    facts = facts or {}
    fact_text = " ".join(str(facts.get(k, "")) for k in ("Amenities", "Features", "Pool", "Cooling", "Heating Type", "Appliances"))
    text = (description or "").lower()
    score, hits = 0.0, []
    for pat, pts, label in LUX_AMENITIES:
        if re.search(pat, fact_text):
            score += pts; hits.append(label)
    if facts.get("Cooling") and "Air conditioning" not in hits:
        score += 1.5; hits.append("Air conditioning")
    for pat, pts, label in LUX_TEXT:
        if label not in hits and re.search(pat, text):
            score += pts; hits.append(label)
    score = min(score, 10.0)
    tier = next(t for cut, t in LUX_TIERS if score >= cut)
    return round(score, 2), tier, hits


# ---------- Build year ----------
AGE_BANDS = [(0, 1980, "Before 1980"), (1980, 1990, "1980s"), (1990, 2000, "1990s"), (2000, 2010, "2000s"),
             (2010, 2020, "2010s"), (2020, 2100, "2020 or newer")]


def age_band(year):
    if year is None or pd.isna(year):
        return "Unknown"
    return next(label for lo, hi, label in AGE_BANDS if lo <= year < hi)


def building_key(address):
    return re.sub(r"^\s*[\w]+\s*-\s*", "", str(address)).strip().lower() if " - " in str(address) else str(address).strip().lower()


SUFFIX = {"street": "ST", "avenue": "AVE", "drive": "DR", "place": "PL", "road": "RD", "crescent": "CRES", "court": "CRT",
          "way": "WAY", "highway": "HWY", "crossing": "CROSSING", "close": "CLOSE", "mews": "MEWS", "lane": "LANE",
          "square": "SQ", "circle": "CIR", "walk": "WALK", "boulevard": "BLVD"}


def city_assessments(cur):
    """Match City of Vancouver listings to the 2026 tax report: build year, assessed value, tax."""
    van = cur[cur.area == "Vancouver"]
    parsed = {}
    for idx, addr in van.address.items():
        m = re.match(r"^\s*(?:(\w+)\s*-\s*)?(\d+)\s+(.+)$", str(addr))
        if m:
            parsed[idx] = (m.group(1), m.group(2), m.group(3).split())
    nums = sorted({v[1] for v in parsed.values()})
    if not nums:
        return pd.DataFrame()
    q = ("select coalesce(json_agg(row_to_json(t)), '[]') from (select unit, street_number, street_name, year_built, "
         "land_value, improvement_value, tax_levy from core.assessment where report_year = 2026 and street_number in ("
         + ",".join("'%s'" % n for n in nums) + ")) t")
    try:
        rows = json.loads(subprocess.run([PSQL, "-d", "vre", "-Atc", q], capture_output=True, text=True, timeout=30, check=True).stdout)
    except Exception as exc:
        print("assessments unavailable:", exc)
        return pd.DataFrame()
    out = {}
    for idx, (unit, num, words) in parsed.items():
        core = [w.upper() for w in words if w.upper() not in {"E", "W", "N", "S", "SE", "SW", "NE", "NW", "NORTH", "SOUTH", "EAST", "WEST"}]
        suffix = SUFFIX.get(words[-1].lower())
        for r in rows:
            name = (r["street_name"] or "").split()
            if r["street_number"] != num or (r["unit"] or None) != unit or not core or core[0] not in name:
                continue
            if suffix and suffix not in name and len(core) > 1:
                continue
            out[idx] = {"cov_year": r["year_built"], "assessed": (r["land_value"] or 0) + (r["improvement_value"] or 0) or None,
                        "cov_tax": float(r["tax_levy"]) if r["tax_levy"] else None}
            break
    return pd.DataFrame.from_dict(out, orient="index")


# ---------- Location ----------
LOC_TIERS = [(10, "Prime"), (3, "Above average"), (-3, "Average"), (-10, "Below average"), (-1e9, "Value location")]


def location_premium(cur, k=3.0):
    """Premium each city/neighbourhood commands over homes of the same size, type, age, tenure and finish.

    A structural model (no location terms) is fitted first; its residuals are then averaged per city and per
    neighbourhood, shrunk toward the parent (k pseudo-listings) so thin neighbourhoods don't swing wildly.
    """
    X = pd.DataFrame(index=cur.index)
    X["const"] = 1.0
    X["log_sqft"] = np.log(cur.sqft) - np.log(cur.sqft).mean()
    X["baths"] = cur.baths.fillna(cur.baths.median()) - 2
    X["townhouse"] = (cur.type == "Townhouse").astype(float)
    X["house"] = (cur.type == "House").astype(float)
    age = 2026 - cur.year
    X["age_known"] = age.notna().astype(float)
    X["age10"] = (age.fillna(0).clip(0, 60) / 10) * X.age_known - 2 * X.age_known
    tenure = cur.r_tenure.fillna("")
    X["leasehold"] = tenure.str.contains("Leasehold").astype(float)
    X["undivided"] = tenure.str.contains("Undivided").astype(float)
    X["lux_known"] = cur.lux.notna().astype(float)
    X["lux"] = cur.lux.fillna(0) - 3 * X.lux_known
    y = np.log(cur.price.to_numpy(dtype=float))
    A = X.to_numpy()
    with np.errstate(all="ignore"):
        beta = np.linalg.solve(A.T @ A + np.diag([0] + [0.05] * (A.shape[1] - 1)), A.T @ y)
        r = pd.Series(y - A @ beta, index=cur.index)
    area_eff = r.groupby(cur.area).agg(lambda v: v.sum() / (len(v) + k))
    dev = r - cur.area.map(area_eff)
    nb_eff = dev.groupby([cur.area, cur.neighbourhood]).agg(lambda v: v.sum() / (len(v) + k))
    nb_key = list(zip(cur.area, cur.neighbourhood))
    total = cur.area.map(area_eff) + pd.Series([nb_eff.get(key, 0.0) for key in nb_key], index=cur.index)
    prem = (np.exp(total) - 1) * 100
    nb = (pd.DataFrame({"area": cur.area, "neighbourhood": cur.neighbourhood, "prem": prem, "psf": cur.psf, "price": cur.price})
          .groupby(["area", "neighbourhood"]).agg(n=("prem", "size"), premium=("prem", "first"), med_psf=("psf", "median"),
                                                  med_price=("price", "median")).reset_index())
    areas = pd.DataFrame({"area": area_eff.index, "premium": (np.exp(area_eff.to_numpy()) - 1) * 100})
    coefs = {c: float(b) for c, b in zip(X.columns, beta)}
    return prem, nb, areas, coefs


def price_history(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values(["property_id", "report_date"])
    rows = []
    for pid, g in df.groupby("property_id"):
        prices = g.drop_duplicates("report_date").price.to_numpy(dtype=float)
        cuts = int(np.sum(np.diff(prices) < 0))
        rows.append({
            "property_id": pid,
            "first_price": prices[0],
            "cuts": cuts,
            "cut_pct": (prices[-1] / prices.max() - 1) * 100 if prices.max() else 0.0,
            "tracked_days": (pd.Timestamp(g.report_date.iloc[-1]) - pd.Timestamp(g.report_date.iloc[0])).days,
            "history": [[d, float(p)] for d, p in g.drop_duplicates("report_date")[["report_date", "price"]].to_numpy()],
        })
    return pd.DataFrame(rows).set_index("property_id")


def latest_per_property(df: pd.DataFrame, report_date: str) -> pd.DataFrame:
    cur = df[df.report_date == report_date].copy()
    cur["kind_order"] = cur.list_kind.map(LATEST_KIND_ORDER)
    # One row per property; prefer the row that carries verified detail facts.
    cur["has_detail"] = cur.year_built.notna()
    cur = cur.sort_values(["property_id", "has_detail", "kind_order"], ascending=[True, False, True])
    return cur.drop_duplicates("property_id").copy()


def fit_fair_value(cur: pd.DataFrame, ridge: float = 1.0):
    """Hedonic log-price model; returns leave-one-out predicted price (no listing prices itself)."""
    X = pd.DataFrame(index=cur.index)
    X["const"] = 1.0
    X["log_sqft"] = np.log(cur.sqft) - np.log(cur.sqft).mean()
    X["baths"] = cur.baths.fillna(cur.baths.median()) - 2
    X["townhouse"] = (cur.type == "Townhouse").astype(float)
    X["house"] = (cur.type == "House").astype(float)
    X["log_station_km"] = np.log(cur.station_km.clip(0.1, 10)) - np.log(0.8)
    age = 2026 - cur.year
    X["age_known"] = age.notna().astype(float)
    X["age10"] = (age.fillna(0).clip(0, 60) / 10) * X.age_known - 2 * X.age_known
    tenure = cur.r_tenure.fillna("")
    hits = cur.r_exclusion_hits.map(lambda h: h or [])
    X["leasehold"] = tenure.str.contains("Leasehold").astype(float)
    X["undivided"] = tenure.str.contains("Undivided").astype(float)
    X["not_resale"] = hits.map(lambda h: "resale not established" in h).astype(float)
    X["parking_known"] = cur.parking.notna().astype(float)
    X["parking"] = cur.parking.fillna(0).clip(0, 3)
    X["lux_known"] = cur.lux.notna().astype(float)
    X["lux"] = cur.lux.fillna(0) - 3 * X.lux_known
    # Area effects: municipality + neighbourhood (neighbourhood shrunk harder via ridge penalty).
    for z in sorted(cur.area.unique())[1:]:
        X["area_" + z] = (cur.area == z).astype(float)
    counts = cur.neighbourhood.value_counts()
    for n in counts[counts >= 4].index:
        X["nb_" + n] = (cur.neighbourhood == n).astype(float)
    y = np.log(cur.price.to_numpy(dtype=float))
    A = X.to_numpy()
    pen = np.array([0 if c == "const" else (3 * ridge if c.startswith("nb_") else ridge * 0.05) for c in X.columns])
    with np.errstate(all="ignore"):
        M = A.T @ A + np.diag(pen)
    with np.errstate(all="ignore"):  # numpy 2.0 + Accelerate emits spurious matmul warnings
        beta = np.linalg.solve(M, A.T @ y)
        H = np.einsum("ij,jk,ik->i", A, np.linalg.inv(M), A)
        resid = y - A @ beta
    assert np.all(np.isfinite(resid))
    loo = resid / (1 - H)
    fair = np.exp(y - loo)
    r2 = 1 - np.sum(resid**2) / np.sum((y - y.mean()) ** 2)
    sigma = float(np.std(loo))
    coefs = {c: float(b) for c, b in zip(X.columns, beta) if not c.startswith("nb_")}
    return fair, loo, r2, sigma, coefs


def gvr_index():
    q = ("select coalesce(json_agg(row_to_json(t)), '[]') from (select area, property_type, month::text, benchmark_price, "
         "pct_1m, pct_1y, pct_3y, pct_5y, pct_10y from core.market_index order by property_type, area) t")
    try:
        out = subprocess.run([PSQL, "-d", "vre", "-Atc", q], capture_output=True, text=True, timeout=20, check=True)
        return json.loads(out.stdout)
    except Exception as exc:  # Postgres offline: dashboard falls back to listing data only
        print("GVR index unavailable:", exc)
        return []


GVR_AREA = {"Burnaby": None, "Metrotown": "Burnaby South", "New Westminster": "New Westminster",
            "Port Moody": "Port Moody", "Vancouver": "Vancouver East", "Coquitlam": "Coquitlam",
            "Port Coquitlam": "Port Coquitlam"}


def clean_json(o):
    """Replace NaN/inf (from missing inventory fields) with null so the output is strict JSON."""
    if isinstance(o, float) and not np.isfinite(o):
        return None
    if isinstance(o, dict):
        return {k: clean_json(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean_json(v) for v in o]
    if isinstance(o, np.floating):
        return clean_json(float(o))
    if isinstance(o, np.integer):
        return int(o)
    return o


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hermes", default=str(HERMES))
    ap.add_argument("--out", default="analysis/output/analysis.json")
    args = ap.parse_args()

    hermes = Path(args.hermes).expanduser()
    df = load_snapshots(hermes / "data" / "real_estate.sqlite3")
    latest_date = df.report_date.max()
    hist = price_history(df)
    cur = latest_per_property(df, latest_date)
    cur = cur.join(hist, on="property_id")
    cur = cur[cur.price.le(PRICE_CAP)]
    inv = load_inventory(hermes, set(cur.listing_url.dropna()) | set(cur.canonical_url.dropna()))
    if len(inv):
        inv = inv.assign(first_price=inv.price, cuts=0, cut_pct=0.0, tracked_days=0,
                         history=[[[d, float(p)]] for d, p in zip(inv.report_date, inv.price)])
        inv = carry_research_details(inv, df, hist, latest_date)
        cur = pd.concat([cur, inv], ignore_index=True)
    cur = cur.reset_index(drop=True)
    rents = rent_levels(hermes)
    cur["pneigh"] = cur.pneigh.fillna(cur.community)
    cur["area"] = cur.pzone.replace({"Metrotown": "Burnaby"})
    cur["neighbourhood"] = cur.pneigh.fillna(cur.area)
    lat = cur.latitude.to_numpy(dtype=float)
    lon = cur.longitude.to_numpy(dtype=float)
    cur["station"], cur["station_km"] = nearest_station(lat, lon)
    cur["station_km"] = cur.station_km.astype(float).fillna(2.0)
    cur["type"] = cur.r_property_type.map(type_of)
    cur["verified"] = cur.year_built.notna()

    # Build year: the listing itself, else the City of Vancouver tax report, else another unit in the same building.
    cur["year"] = cur.year_built
    cur["year_source"] = np.where(cur.year.notna(), "listing", None)
    cov = city_assessments(cur)
    cur = cur.join(cov) if len(cov) else cur.assign(cov_year=np.nan, assessed=np.nan, cov_tax=np.nan)
    fill = cur.year.isna() & cur.cov_year.notna()
    cur.loc[fill, "year"] = cur.loc[fill, "cov_year"]
    cur.loc[fill, "year_source"] = "city tax report"
    cur["taxes_src"] = np.where(cur.taxes.notna(), "listing", np.where(cur.cov_tax.notna(), "city tax report", None))
    cur["taxes"] = cur.taxes.fillna(cur.cov_tax)
    df["bkey"] = df.address.map(building_key)
    bldg_year = df[df.year_built.notna()].groupby("bkey").year_built.median()
    cur["bkey"] = cur.address.map(building_key)
    fill = cur.year.isna() & cur.bkey.isin(bldg_year.index)
    cur.loc[fill, "year"] = cur.loc[fill, "bkey"].map(bldg_year)
    cur.loc[fill, "year_source"] = "same building"
    cur["age_band"] = cur.year.map(age_band)

    lux = [luxury(f, d) for f, d in zip(cur.r_facts, cur.r_description)]
    cur["lux"] = [x[0] for x in lux]
    cur["lux_tier"] = [x[1] for x in lux]
    cur["lux_hits"] = [x[2] for x in lux]

    fair, loo, r2, sigma, coefs = fit_fair_value(cur)
    cur["fair_value"] = fair
    cur["gap_pct"] = (np.exp(loo) - 1) * 100  # negative = asking below model value
    cur["z"] = loo / sigma

    schools = cur.r_schools_text.map(catchment_schools)
    cur["elem"] = schools.map(lambda s: s[0][0] if s[0] else None)
    cur["elem_km"] = schools.map(lambda s: s[0][1] if s[0] else None)
    cur["sec"] = schools.map(lambda s: s[1][0] if s[1] else None)
    cur["sec_km"] = schools.map(lambda s: s[1][1] if s[1] else None)

    cur = cur.join(school_scores(cur)).join(walk_scores(cur))
    cur["loc_prem"], nb_prem, area_prem, loc_coefs = location_premium(cur)
    cur["loc_tier"] = cur.loc_prem.map(lambda v: next(t for cut, t in LOC_TIERS if v >= cut))

    # Build-age bands (years from listing, city tax report or sibling units).
    known = cur[cur.year.notna()].copy()
    known["strata_psf"] = known.strata / known.sqft
    bands = (known.groupby(["age_band", "type"])
             .agg(n=("price", "size"), med_psf=("psf", "median"), med_price=("price", "median"),
                  med_strata_psf=("strata_psf", "median"), med_gap=("gap_pct", "median"), med_lux=("lux", "median"))
             .reset_index())
    lux_tiers = (cur[cur.lux.notna()].groupby(["lux_tier", "type"])
                 .agg(n=("price", "size"), med_psf=("psf", "median"), med_price=("price", "median"), med_gap=("gap_pct", "median"))
                 .reset_index())

    listings = []
    for _, r in cur.iterrows():
        listings.append({
            "mls": r.mls if isinstance(r.mls, str) else None, "address": r.address, "area": r.area, "neighbourhood": r.neighbourhood,
            "type": r.type, "property_type": r.r_property_type if isinstance(r.r_property_type, str) else None,
            "price": float(r.price), "fair": round(float(r.fair_value), -3), "gap": round(float(r.gap_pct), 1),
            "z": round(float(r.z), 2), "beds": int(r.beds), "baths": float(r.baths) if pd.notna(r.baths) else None,
            "sqft": int(r.sqft), "psf": round(float(r.price / r.sqft)),
            "year": int(r.year) if pd.notna(r.year) else None, "year_source": r.year_source,
            "age_band": r.age_band, "leaky_era": bool(pd.notna(r.year) and 1983 <= r.year <= 1999),
            "lux": r.lux if pd.notna(r.lux) else None, "lux_tier": r.lux_tier, "lux_hits": r.lux_hits,
            "loc_prem": round(float(r.loc_prem), 1), "loc_tier": r.loc_tier,
            "assessed": float(r.assessed) if pd.notna(r.assessed) else None, "taxes_src": r.taxes_src,
            "school_elem": r.school_elem if isinstance(r.school_elem, dict) else None,
            "school_sec": r.school_sec if isinstance(r.school_sec, dict) else None,
            "school_score": float(r.school_score) if pd.notna(r.school_score) else None,
            "walk": float(r.walk) if pd.notna(r.walk) else None,
            "walk_detail": r.walk_detail if isinstance(r.walk_detail, dict) else None,
            "strata": float(r.strata) if pd.notna(r.strata) else None,
            "taxes": float(r.taxes) if pd.notna(r.taxes) else None,
            "parking": float(r.parking) if pd.notna(r.parking) else None,
            "dom": int(r.dom) if pd.notna(r.dom) else None,
            "cuts": int(r.cuts), "cut_pct": round(float(r.cut_pct), 1), "first_price": float(r.first_price),
            "station": r.station, "station_km": round(float(r.station_km), 2),
            "elem": r.elem, "elem_km": r.elem_km, "sec": r.sec, "sec_km": r.sec_km,
            "tenure": r.r_tenure, "flags": list(r.r_exclusion_hits or []), "verified": bool(r.verified),
            "lat": r.latitude, "lon": r.longitude,
            "url": r.listing_url or r.canonical_url, "history": r.history,
            "source": r.source, "fact_level": r.fact_level if isinstance(r.fact_level, str) else None,
            "band": "main" if r.price <= MAIN_CEILING else "comparison",
            "hermes_rank": int(r.hermes_rank) if pd.notna(r.hermes_rank) else None,
            "rent_city": rents.get(r.area, {}).get("median"),
        })

    # Market trends from the daily scans (asking side), by area × type × beds.
    df["area"] = df.pzone.replace({"Metrotown": "Burnaby"})
    df["type"] = np.where(df.r_property_type == "Townhouse", "Townhouse", "Condo")
    daily = df.drop_duplicates(["report_date", "property_id"])
    trend = (daily.groupby(["report_date", "area", "type", "beds"])
             .agg(n=("price", "size"), med_price=("price", "median"), med_psf=("psf", "median"))
             .reset_index())
    trend = trend[trend.n >= 5]

    # Exits: listings last seen before the latest scan (sold, expired or withdrawn — unknown which).
    last_seen = daily.groupby("property_id").report_date.max()
    first_seen = daily.groupby("property_id").report_date.min()
    tracked = daily.drop_duplicates("property_id", keep="last").set_index("property_id")
    exits = tracked.loc[last_seen[last_seen < latest_date].index]
    # Only the 2-bed condo scans (Jul 10 – Aug 8) ran daily; use them for time-on-market of exits.
    two = daily[(daily.beds == 2) & (daily.report_date <= "2026-08-08")]
    two_last = two.groupby("property_id").report_date.max()
    two_first = two.groupby("property_id").report_date.min()
    two_exit = two_last[two_last < "2026-08-08"].index
    exit_days = (pd.to_datetime(two_last[two_exit]) - pd.to_datetime(two_first[two_exit])).dt.days
    two_meta = two.drop_duplicates("property_id", keep="last").set_index("property_id")
    two_cut = two.sort_values("report_date").groupby("property_id").price.agg(lambda p: bool((p.diff() < 0).any()))
    exit_stats = {
        "window": "2026-07-10 to 2026-08-08, 2-bed condos, Metrotown + Port Moody",
        "tracked": int(two.property_id.nunique()),
        "exited": int(len(two_exit)),
        "median_days_seen": float(exit_days.median()) if len(exit_days) else None,
        "share_cut_all": round(float(two_cut.mean() * 100), 1),
        "by_area": json.loads(two_meta.assign(exited=two_meta.index.isin(two_exit)).groupby("area")
                              .agg(tracked=("price", "size"), exited=("exited", "sum"), med_price=("price", "median"),
                                   med_psf=("psf", "median")).reset_index().to_json(orient="records")),
    }

    summary = (cur.groupby(["area", "type"])
               .agg(n=("price", "size"), med_price=("price", "median"), med_psf=("psf", "median"),
                    med_sqft=("sqft", "median"), med_strata=("strata", "median"),
                    share_cut=("cuts", lambda s: float((s > 0).mean() * 100)))
               .reset_index())

    out = {
        "latest_scan": latest_date,
        "scan_count": int(df.report_date.nunique()),
        "first_scan": df.report_date.min(),
        "properties_tracked": int(df.property_id.nunique()),
        "active": len(cur),
        "exits": int(len(exits)),
        "exit_stats": exit_stats,
        "model": {"r2": round(float(r2), 3), "sigma_pct": round(float(np.exp(sigma) - 1) * 100, 1),
                  "n": len(cur), "coefs": coefs},
        "summary": json.loads(summary.to_json(orient="records")),
        "rents": rents,
        "sources": {"hermes_db": str(hermes / "data" / "real_estate.sqlite3"), "research_date": latest_date,
                    "research_listings": int((cur.source == "Hermes 3-bed research").sum()),
                    "inventory_listings": int((cur.source == "Hermes inventory").sum()),
                    "inventory_coverage": inventory_coverage(hermes)},
        "location": {"neighbourhoods": json.loads(nb_prem.to_json(orient="records")),
                     "areas": json.loads(area_prem.to_json(orient="records")), "coefs": loc_coefs},
        "age_bands": json.loads(bands.to_json(orient="records")),
        "lux_tiers": json.loads(lux_tiers.to_json(orient="records")),
        "coverage": {"year": int(cur.year.notna().sum()), "year_by_source": cur.year_source.value_counts().to_dict(),
                     "lux": int(cur.lux.notna().sum()), "assessed": int(cur.assessed.notna().sum())},
        "trend": json.loads(trend.to_json(orient="records")),
        "gvr": gvr_index(),
        "listings": listings,
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(clean_json(out), default=lambda o: None if pd.isna(o) else str(o), allow_nan=False))
    print(f"scan {latest_date}: {len(cur)} active, R²={r2:.2f}, typical error ±{(np.exp(sigma)-1)*100:.0f}%")
    top = cur.sort_values("gap_pct").head(15)
    print(top[["mls", "address", "area", "r_property_type", "price", "fair_value", "gap_pct", "sqft", "year_built", "cuts", "station_km"]].to_string())


if __name__ == "__main__":
    main()
