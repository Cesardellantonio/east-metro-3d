"""Build the 3D-first Two Keys platform (analysis/output/platform/).

Reuses the digital-twin assets (buildings, ground, walk heat, terrain) and adds platform_data.json:
homes in the Two Keys shape plus local x/z, terrain-free ground position and the index of the 3D
building each home sits in (so the page can light up the actual building).

    python3 analysis/build_platform.py   (after build_twin.py and build_home_app.py)
"""
import base64
import re
import json
import shutil
import struct
from pathlib import Path

import numpy as np
import pandas as pd
from matplotlib.path import Path as MPath

from market_analysis import clean_json
from neighbourhood_scores import load_all_schools, school_access

HERE = Path(__file__).parent
OUT = HERE / "output"
PLAT = OUT / "platform"


def decode_buildings():
    raw = base64.b64decode(json.loads((OUT / "twin" / "buildings.json").read_text())["b64"])
    n = struct.unpack_from("<I", raw, 0)[0]
    off, polys, heights = 4, [], []
    for _ in range(n):
        k, h_dm = struct.unpack_from("<HH", raw, off)
        off += 4
        pts = np.frombuffer(raw, dtype="<i2", count=2 * k, offset=off).reshape(k, 2).astype(float)
        off += 4 * k
        polys.append(pts)
        heights.append(h_dm / 10)
    return polys, np.array(heights)


def edge_distance(poly, x, z):
    a, b = poly, np.roll(poly, -1, axis=0)
    ab = b - a
    t = np.clip(((x - a[:, 0]) * ab[:, 0] + (z - a[:, 1]) * ab[:, 1]) / np.maximum((ab ** 2).sum(1), 1e-9), 0, 1)
    q = a + ab * t[:, None]
    return float(np.hypot(q[:, 0] - x, q[:, 1] - z).min())


def main():
    PLAT.mkdir(parents=True, exist_ok=True)
    twin = json.loads((OUT / "twin" / "twin_data.json").read_text())
    tk = (OUT / "two_keys.html").read_text()
    start = tk.index("const DATA = ") + len("const DATA = ")
    end = tk.index(";\nconst DASHBOARD")
    tkdata = json.loads(tk[start:end].replace("<\\/", "</"))
    W = twin["world"]

    polys, heights = decode_buildings()
    boxes = np.array([[p[:, 0].min(), p[:, 1].min(), p[:, 0].max(), p[:, 1].max()] for p in polys])
    hit = 0
    for h in tkdata["homes"]:
        x = (h["lon"] - W["lon0"]) * W["mx"]
        z = -(h["lat"] - W["lat0"]) * W["mz"]
        h["x"], h["z"] = round(x, 1), round(z, 1)
        # The 3D building that contains the listing point, else (condos, townhouses) the closest one by edge distance.
        cand = np.where((boxes[:, 0] - 40 <= x) & (boxes[:, 2] + 40 >= x) & (boxes[:, 1] - 40 <= z) & (boxes[:, 3] + 40 >= z))[0]
        bi = -1
        for i in cand:
            if MPath(polys[i]).contains_point((x, z)):
                bi = int(i)
                break
        limit = {"Condo": 40, "Townhouse": 10}.get(h["t"], 0)  # listing points sit at the street edge of their building
        # A unit number like 2702 means the 27th floor: the building must be tall enough to have it.
        m = re.match(r"^\s*(?:PH|TH|SL)?(\d{3,4})\s*-", h["a"], re.I)
        floor = int(m.group(1)) // 100 if m else 0
        need = max(0, floor * 2.9 - 6)
        if need > 30:  # a high floor: look a little further for its tower
            limit = 70
            cand = np.where((boxes[:, 0] - limit <= x) & (boxes[:, 2] + limit >= x) & (boxes[:, 1] - limit <= z) & (boxes[:, 3] + limit >= z))[0]
        if limit and len(cand):
            near = [(edge_distance(polys[i], x, z), i) for i in cand]
            near = [(d, i) for d, i in near if d <= limit]
            tall = [(d, i) for d, i in near if heights[i] >= need]
            pool = tall or ([] if bi >= 0 else near)
            if pool and (bi < 0 or heights[bi] < need):
                bi = int(min(pool)[1])
        h["bi"] = bi
        # Height implied by the unit's floor, when the map's building is shorter than that (tower missing a height tag).
        h["fh"] = round(floor * 2.9 + 6) if floor and bi >= 0 and heights[bi] < need else 0
        h["floor"] = floor
        hit += bi >= 0
    # Every school a family could use: public and independent, rated on the same FSA scale.
    sch = load_all_schools()
    access = school_access(tkdata["homes"], sch)
    for h, acc in zip(tkdata["homes"], access):
        h["sa"] = acc
    schools = [{"name": r.SCHOOL_NAME, "x": round((r.lon - W["lon0"]) * W["mx"], 1), "z": round(-(r.lat - W["lat0"]) * W["mz"], 1),
                "level": r.level, "public": bool(r.public), "rating": None if pd.isna(r.rating) else float(r.rating), "french": bool(r.french),
                "city": r.PHYSICAL_ADDRESS_CITY} for r in sch.itertuples()]
    data = {**{k: v for k, v in tkdata.items() if k != "map"}, "world": W, "terrain": twin["terrain"], "schools": schools,
            "stations": twin["stations"]}
    (PLAT / "platform_data.json").write_text(json.dumps(clean_json(data), separators=(",", ":"), allow_nan=False))
    for f in ["buildings.json", "ground.png", "walk.png", "terrain.png"]:
        shutil.copy2(OUT / "twin" / f, PLAT / f)
    body = (HERE / "platform_template.html").read_text()
    (PLAT / "index.html").write_text(body)  # fragment: the claude.ai artifact host adds the document shell
    # Full document for running on this Mac (python3 -m http.server); votes save in the browser there.
    (PLAT / "local.html").write_text('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
                                     '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n</head>\n<body>\n'
                                     + body + '\n</body>\n</html>\n')
    print(f"platform: {len(tkdata['homes'])} homes, {hit} matched to a 3D building ->", PLAT)


if __name__ == "__main__":
    main()
