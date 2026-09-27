"""Build the public open-data website (docs/, served by GitHub Pages).

Same 3D engine as the private Two Keys platform, in public mode: terrain, buildings, SkyTrain,
schools and walkability only. No listings, prices, votes or analysis leave this machine.

    python3 analysis/build_public_site.py   (after build_twin.py)
"""
import json
import shutil
from pathlib import Path

HERE = Path(__file__).parent
TWIN = HERE / "output" / "twin"
DOCS = HERE.parent / "docs"
TITLE = "East Metro 3D"


def main():
    DOCS.mkdir(exist_ok=True)
    twin = json.loads((TWIN / "twin_data.json").read_text())
    data = {"public": True, "homes": [], "scan": twin["latest_scan"], "world": twin["world"], "terrain": twin["terrain"],
            "schools": twin["schools"], "stations": twin["stations"],
            # Unused in public mode; present so shared code paths find the keys.
            "gvr": [], "summary": [], "rents": {}, "areas": [], "nb_top": [], "nb_bottom": [], "model": {"sigma": 0},
            "sources": {"research_listings": 0, "inventory_listings": 0}}
    (DOCS / "platform_data.json").write_text(json.dumps(data, separators=(",", ":"), allow_nan=False))
    for f in ["buildings.json", "ground.png", "walk.png", "terrain.png"]:
        shutil.copy2(TWIN / f, DOCS / f)
    body = (HERE / "platform_template.html").read_text()
    body = body.replace("<title>Two Keys</title>", f"<title>{TITLE}</title>").replace("<h1>Two Keys</h1>", f"<h1>{TITLE}</h1>")
    body = body.replace('<b style="font: 800 22px var(--display)">Two Keys</b>', f'<b style="font: 800 22px var(--display)">{TITLE}</b>')
    page = ("<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1, viewport-fit=cover\">\n"
            f"<meta name=\"description\" content=\"Vancouver, Burnaby, New Westminster and the Tri-Cities in 3D from open data: terrain, buildings, SkyTrain, school ratings and walkability.\">\n"
            "</head>\n<body>\n" + body + "\n</body>\n</html>\n")
    (DOCS / "index.html").write_text(page)
    (DOCS / ".nojekyll").write_text("")
    # Guard: nothing listing-derived may end up in the public folder.
    for f in DOCS.iterdir():
        if f.suffix in (".json", ".html"):
            t = f.read_text()
            assert "rew.ca/properties" not in t and '"homes":[{' not in t, f"listing data leaked into {f.name}"
    print("public site ->", DOCS, sorted(p.name for p in DOCS.iterdir()))


if __name__ == "__main__":
    main()
