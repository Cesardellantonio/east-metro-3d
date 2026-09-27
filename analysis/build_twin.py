"""Assets for the 3D digital twin (analysis/output/twin/).

  buildings.json base64 of the packed extruded buildings: uint32 count, then per building uint16 n, uint16 height_dm, n x (int16 x, int16 z) metres
  ground.png     land, water, parks, roads and flat house footprints
  walk.png       walk-score heat (RGBA overlay)
  twin_data.json listings with analysis fields, schools, SkyTrain, labels, projection

Local metres: x = (lon - LON0) * MX, z = -(lat - LAT0) * MZ.

    python3 analysis/build_twin.py
"""
import base64
import json
import re
import struct
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter

from build_map_layers import BBOX, rdp
from neighbourhood_scores import EXT

HERE = Path(__file__).parent
OUT = HERE / "output" / "twin"
LON0, LAT0 = (BBOX[0] + BBOX[2]) / 2, (BBOX[1] + BBOX[3]) / 2
MX, MZ = 111320 * np.cos(np.radians(LAT0)), 110540
W_M, H_M = (BBOX[2] - BBOX[0]) * MX, (BBOX[3] - BBOX[1]) * MZ
TERR = (-123.40, 49.08, -122.55, 49.50)  # wider than BBOX: North Shore mountains, Burke Mountain, Fraser lowlands
TERR_STEP_M = 90
TEX_W = 6144
TEX_H = int(TEX_W * H_M / W_M)

DEFAULT_H = {"apartments": 12, "residential": 9, "commercial": 8, "retail": 7, "office": 15, "industrial": 9, "warehouse": 9,
             "hotel": 18, "hospital": 18, "school": 9, "university": 12, "college": 12, "civic": 9, "public": 9,
             "church": 12, "parking": 9, "train_station": 10, "stadium": 20}


def xz(lon, lat):
    return (lon - LON0) * MX, -(lat - LAT0) * MZ


def px(lon, lat):
    return (lon - BBOX[0]) / (BBOX[2] - BBOX[0]) * TEX_W, (BBOX[3] - lat) / (BBOX[3] - BBOX[1]) * TEX_H


def parse_height(t):
    h = t.get("height")
    if h:
        m = re.match(r"\s*([\d.]+)\s*(m|ft|')?", h)
        if m:
            v = float(m.group(1))
            return v * 0.3048 if m.group(2) in ("ft", "'") else v
    lv = t.get("building:levels")
    if lv:
        m = re.match(r"\s*([\d.]+)", lv)
        if m:
            return float(m.group(1)) * 3.2 + (1.5 if float(m.group(1)) > 3 else 0)
    return None


def area_m2(pts):
    p = np.array([xz(*q) for q in pts])
    return abs(np.dot(p[:, 0], np.roll(p[:, 1], 1)) - np.dot(p[:, 1], np.roll(p[:, 0], 1))) / 2


def build_heightmap():
    """Resample the terrarium tiles (z12) onto a lon/lat grid over TERR; returns grid (rows north->south) in metres."""
    import math
    z = 12
    tiles = {}
    for f in (EXT / "terrain").glob("12_*.png"):
        _, x, y = f.stem.split("_")
        a = np.asarray(Image.open(f).convert("RGB"), dtype=np.float64)
        tiles[(int(x), int(y))] = a[:, :, 0] * 256 + a[:, :, 1] + a[:, :, 2] / 256 - 32768
    xs, ys = [k[0] for k in tiles], [k[1] for k in tiles]
    X0, Y0 = min(xs), min(ys)
    mosaic = np.zeros(((max(ys) - Y0 + 1) * 256, (max(xs) - X0 + 1) * 256))
    for (x, y), a in tiles.items():
        mosaic[(y - Y0) * 256:(y - Y0 + 1) * 256, (x - X0) * 256:(x - X0 + 1) * 256] = a
    w_m = (TERR[2] - TERR[0]) * MX
    h_m = (TERR[3] - TERR[1]) * MZ
    nx, nz = int(w_m / TERR_STEP_M) + 1, int(h_m / TERR_STEP_M) + 1
    lons = np.linspace(TERR[0], TERR[2], nx)
    lats = np.linspace(TERR[3], TERR[1], nz)
    n = 2 ** z
    px_ = ((lons + 180) / 360 * n - X0) * 256
    py_ = ((1 - np.arcsinh(np.tan(np.radians(lats))) / math.pi) / 2 * n - Y0) * 256
    PX, PY = np.meshgrid(px_, py_)
    x0, y0 = np.floor(PX).astype(int), np.floor(PY).astype(int)
    fx, fy = PX - x0, PY - y0
    m = mosaic
    grid = (m[y0, x0] * (1 - fx) * (1 - fy) + m[y0, x0 + 1] * fx * (1 - fy) + m[y0 + 1, x0] * (1 - fx) * fy + m[y0 + 1, x0 + 1] * fx * fy)
    return grid, nx, nz


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    tall, flat = [], []
    seen = set()
    for f in sorted((EXT / "buildings").glob("tile*.json")):
        for e in json.loads(f.read_text())["elements"]:
            if e["id"] in seen or not e.get("geometry"):
                continue
            seen.add(e["id"])
            t = e.get("tags", {})
            if t.get("building") in ("roof", "no", "construction") or t.get("location") == "underground":
                continue
            pts = [[p["lon"], p["lat"]] for p in e["geometry"]]
            if len(pts) < 4:
                continue
            a = area_m2(pts)
            if a < 25:
                continue
            h = parse_height(t)
            kind = t.get("building", "yes")
            is_3d = (h is not None and h >= 9) or (kind in DEFAULT_H and kind not in ("residential",) and a >= 500) or a >= 1500
            if is_3d:
                h = h or DEFAULT_H.get(kind, 8)
                ring = rdp(pts[:-1] + [pts[0]], 0.000012)[:-1]  # ~1 m tolerance
                if len(ring) >= 3:
                    tall.append((ring, min(h, 330)))
            else:
                flat.append(pts)
    buf = bytearray(struct.pack("<I", len(tall)))
    for ring, h in tall:
        buf += struct.pack("<HH", len(ring), int(round(h * 10)))
        for lon, lat in ring:
            x, z = xz(lon, lat)
            buf += struct.pack("<hh", int(round(x)), int(round(z)))
    # Artifacts serve JSON but not raw binary, so the packed buffer ships base64-encoded.
    (OUT / "buildings.json").write_text(json.dumps({"format": "uint32 n; n x (uint16 k, uint16 h_dm, k x (int16 x, int16 z))",
                                                     "b64": base64.b64encode(bytes(buf)).decode()}))

    # Terrain: 16-bit height in R (high byte) and G (low byte), decimetres + 500 dm offset so the sea floor clamps at -50 m.
    grid, nx, nz = build_heightmap()
    v = np.clip(np.round((grid + 50) * 10), 0, 65535).astype(np.uint32)
    rgb = np.zeros((nz, nx, 3), np.uint8)
    rgb[:, :, 0], rgb[:, :, 1] = v >> 8, v & 255
    Image.fromarray(rgb).save(OUT / "terrain.png", optimize=True)
    terr_x0, terr_z0 = xz(TERR[0], TERR[3])
    terr_x1, terr_z1 = xz(TERR[2], TERR[1])
    terrain_meta = {"nx": nx, "nz": nz, "x0": terr_x0, "z0": terr_z0, "x1": terr_x1, "z1": terr_z1, "max": float(grid.max()),
                    "bbox_x0": xz(BBOX[0], BBOX[3])[0], "bbox_z0": xz(BBOX[0], BBOX[3])[1], "bbox_x1": xz(BBOX[2], BBOX[1])[0], "bbox_z1": xz(BBOX[2], BBOX[1])[1]}
    print("terrain", nx, "x", nz, "max", round(float(grid.max())), "m")

    # Ground texture
    layers = json.loads((HERE / "output" / "map_layers.json").read_text())
    img = Image.new("RGB", (TEX_W, TEX_H), (178, 205, 214))  # water
    d = ImageDraw.Draw(img)
    for poly in layers["land"]:
        d.polygon([px(*p) for p in poly[0]], fill=(238, 236, 229))
        for hole in poly[1:]:
            d.polygon([px(*p) for p in hole], fill=(178, 205, 214))
    for ring in layers["parks"]:
        d.polygon([px(*p) for p in ring], fill=(205, 226, 196))
    for pts in flat:
        d.polygon([px(*p) for p in pts], fill=(214, 209, 199))
    for cls, col, w in (("secondary", (255, 255, 255), 3), ("primary", (255, 255, 255), 4), ("trunk", (246, 221, 170), 5), ("motorway", (240, 200, 130), 6)):
        for line in layers["roads"][cls]:
            d.line([px(*p) for p in line], fill=col, width=w, joint="curve")
    img.save(OUT / "ground.png", optimize=True)

    # Walk-score heat overlay (RGBA): teal, stronger where walkable.
    heat = Image.new("RGBA", (TEX_W, TEX_H), (0, 0, 0, 0))
    hd = ImageDraw.Draw(heat)
    g = layers["walk_grid"]
    for lon, lat, v in g["cells"]:
        if v < 15:
            continue
        x0, y0 = px(lon - g["lon_step"] / 2, lat + g["lat_step"] / 2)
        x1, y1 = px(lon + g["lon_step"] / 2, lat - g["lat_step"] / 2)
        a = int(210 * (v / 100) ** 1.5)
        hd.rectangle([x0, y0, x1, y1], fill=(14, 124, 112, a))
    heat = heat.filter(ImageFilter.GaussianBlur(14))
    land_mask = Image.new("L", (TEX_W, TEX_H), 0)
    md = ImageDraw.Draw(land_mask)
    for poly in layers["land"]:
        md.polygon([px(*p) for p in poly[0]], fill=255)
        for hole in poly[1:]:
            md.polygon([px(*p) for p in hole], fill=0)
    r, g2, b, a = heat.split()
    heat = Image.merge("RGBA", (r, g2, b, ImageChops.multiply(a, land_mask)))
    heat.resize((TEX_W // 4, TEX_H // 4), Image.LANCZOS).save(OUT / "walk.png", optimize=True)

    # Listings + analysis, schools, transit, labels
    data = json.loads((HERE / "output" / "analysis.json").read_text())
    keep = ["mls", "address", "area", "neighbourhood", "type", "price", "fair", "gap", "sqft", "beds", "baths", "year", "leaky_era",
            "lux", "lux_tier", "loc_prem", "loc_tier", "walk", "school_score", "station", "station_km", "url", "flags", "tenure",
            "strata", "taxes", "psf", "cuts", "cut_pct", "verified", "source", "band", "hermes_rank", "fact_level", "rent_city"]
    listings = []
    for l in data["listings"]:
        if l.get("lat") is None or l.get("lon") is None:
            continue
        x, z = xz(l["lon"], l["lat"])
        row = {k: l.get(k) for k in keep}
        row.update(x=round(x, 1), z=round(z, 1), school=(l.get("school_elem") or {}).get("name"),
                   school_rating=(l.get("school_elem") or {}).get("rating"), school_src=(l.get("school_elem") or {}).get("src"))
        listings.append(row)
    schools = []
    for s in layers["schools"]:
        x, z = xz(s["lon"], s["lat"])
        schools.append({"name": s["name"], "x": round(x, 1), "z": round(z, 1), "level": s["level"], "rating": s["rating"], "french": s["french"]})
    stations = {k: [round(v, 1) for v in xz(lon, lat)] for k, (lat, lon) in layers["stations"].items()}
    out = {"latest_scan": data["latest_scan"], "world": {"w": W_M, "h": H_M, "lon0": LON0, "lat0": LAT0, "mx": MX, "mz": MZ},
           "terrain": terrain_meta, "listings": listings, "schools": schools, "stations": stations, "model": data["model"]}
    (OUT / "twin_data.json").write_text(json.dumps(out, separators=(",", ":"), allow_nan=False))
    (OUT / "index.html").write_text((HERE / "twin_template.html").read_text())
    for p in sorted(OUT.iterdir()):
        print(p.name, f"{p.stat().st_size / 1e6:.2f} MB")
    print(len(tall), "3D buildings,", len(flat), "flat footprints,", TEX_W, "x", TEX_H, "texture")


if __name__ == "__main__":
    main()
