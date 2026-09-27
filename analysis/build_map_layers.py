"""Map layers for the dashboard: land, roads, parks, schools and a walk-score grid.

Inputs in data/external/: land.geojson (parcel union from the vre db), osm_basemap.json (roads/parks),
osm_amenities.json, bc_k12_schools.csv, fsa_2021_2026.csv.

    python3 analysis/build_map_layers.py  ->  analysis/output/map_layers.json
"""
import json
from pathlib import Path

import numpy as np
from matplotlib.path import Path as MPath

from market_analysis import STATIONS, haversine_km
from neighbourhood_scores import EXT, WALK_CATS, decay, load_osm, load_schools

BBOX = (-123.27, 49.17, -122.69, 49.34)
OUT = Path(__file__).parent / "output" / "map_layers.json"


def rdp(pts, eps):
    """Douglas-Peucker line simplification on [[lon, lat], ...]."""
    if len(pts) < 3:
        return pts
    a, b = np.array(pts[0]), np.array(pts[-1])
    arr = np.array(pts[1:-1])
    ab = b - a
    n = np.hypot(*ab)
    rel = arr - a
    d = np.abs(ab[0] * rel[:, 1] - ab[1] * rel[:, 0]) / n if n else np.hypot(*rel.T)
    i = int(np.argmax(d))
    if d[i] > eps:
        return rdp(pts[: i + 2], eps)[:-1] + rdp(pts[i + 1:], eps)
    return [pts[0], pts[-1]]


def rnd(pts):
    return [[round(x, 4), round(y, 4)] for x, y in pts]


def poly_area_m2(pts):
    p = np.array(pts)
    x = (p[:, 0] - p[0, 0]) * 111320 * np.cos(np.radians(49.25))
    y = (p[:, 1] - p[0, 1]) * 110540
    return abs(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1))) / 2


def main():
    land = json.loads((EXT / "land.geojson").read_text())
    land_polys = [[rnd(rdp(ring, 0.00012)) for ring in poly] for poly in land["coordinates"]]
    land_polys = [p for p in land_polys if len(p[0]) >= 4 and poly_area_m2(p[0]) > 20000]

    roads = {"motorway": [], "trunk": [], "primary": [], "secondary": []}
    parks = []
    for e in json.loads((EXT / "osm_basemap.json").read_text())["elements"]:
        t, g = e.get("tags", {}), e.get("geometry")
        if not g:
            continue
        pts = [[p["lon"], p["lat"]] for p in g]
        hw = t.get("highway")
        if hw in roads:
            roads[hw].append(rnd(rdp(pts, 0.0001)))
        elif (t.get("leisure") == "park" or t.get("natural") == "wood") and pts[0] == pts[-1] and poly_area_m2(pts) > 15000:
            parks.append(rnd(rdp(pts, 0.00012)))

    k = load_schools()
    k = k[(k.lon.between(BBOX[0], BBOX[2])) & (k.lat.between(BBOX[1], BBOX[3]))]
    schools = [{"name": r.SCHOOL_NAME, "lat": round(r.lat, 5), "lon": round(r.lon, 5),
                "level": "sec" if r.sec and not r.elem else "elem",
                "rating": None if np.isnan(r.fsa_pctile) else round(r.fsa_pctile / 10, 1),
                "french": bool(r.french)} for r in k.itertuples()]

    # Walk-score grid (~250 m cells) over land, same method as the per-listing score.
    lat_step, lon_step = 0.00225, 0.0034
    lats = np.arange(BBOX[1] + lat_step / 2, BBOX[3], lat_step)
    lons = np.arange(BBOX[0] + lon_step / 2, BBOX[2], lon_step)
    LA, LO = np.meshgrid(lats, lons, indexing="ij")
    pts = np.column_stack([LO.ravel(), LA.ravel()])
    on_land = np.zeros(len(pts), bool)
    for poly in land_polys:
        inside = MPath(poly[0]).contains_points(pts)
        for hole in poly[1:]:
            inside &= ~MPath(hole).contains_points(pts)
        on_land |= inside
    cells = pts[on_land]
    osm = load_osm()
    total_w = sum(w for _, w, _, _ in WALK_CATS) + 2
    score = np.zeros(len(cells))
    for _, w, kinds, ranks in WALK_CATS:
        sub = osm[osm.kind.isin(kinds)]
        d = haversine_km(cells[:, 1:2], cells[:, 0:1], sub.lat.to_numpy()[None, :], sub.lon.to_numpy()[None, :])
        kk = min(len(ranks), d.shape[1])
        near = np.sort(np.partition(d, kk - 1, axis=1)[:, :kk], axis=1)
        score += w * (decay(near) * np.array(ranks[:kk])).sum(axis=1) / np.sum(ranks)
    st = np.array(list(STATIONS.values()))
    sd = haversine_km(cells[:, 1:2], cells[:, 0:1], st[None, :, 0], st[None, :, 1]).min(axis=1)
    score += 2 * decay(sd)
    walk = np.round(score / total_w * 100).astype(int)
    grid = {"lat_step": lat_step, "lon_step": lon_step,
            "cells": [[round(float(x), 4), round(float(y), 4), int(v)] for (x, y), v in zip(cells, walk)]}

    OUT.write_text(json.dumps({"bbox": BBOX, "land": land_polys, "roads": roads, "parks": parks,
                               "schools": schools, "walk_grid": grid, "stations": STATIONS}, separators=(",", ":")))
    print(OUT, f"{OUT.stat().st_size / 1e6:.2f} MB", len(land_polys), "land polys", sum(map(len, roads.values())), "roads",
          len(parks), "parks", len(schools), "schools", len(cells), "walk cells")


if __name__ == "__main__":
    main()
