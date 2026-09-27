# East Metro 3D

**Live site:** https://cesardellantonio.github.io/east-metro-3d/

A 3D model of Vancouver, Burnaby, New Westminster, Port Moody, Coquitlam and Port Coquitlam built from open data:
real terrain, about 15,000 buildings, SkyTrain, every public school coloured by its test-score rating, and a
walkability glow. It runs in the browser with three.js. Nothing to install.

This repository also holds the code behind a private home-search tool built on the same engine (market analysis,
fair-value model, school and walkability scoring, and a shared two-person voting app). That tool runs on listing
data that isn't redistributable, so **no listing data, prices or analysis outputs are published here**.

## What's in the public site

| Layer | Source | Licence |
| --- | --- | --- |
| Buildings, roads, parks, shops and services | OpenStreetMap | © OpenStreetMap contributors, ODbL |
| Land and shoreline | ParcelMap BC | Open Government Licence – British Columbia |
| School locations, FSA results | BC Data Catalogue | Open Government Licence – British Columbia |
| Terrain | Terrain Tiles on AWS (Canadian Digital Elevation Model, SRTM) | see the Terrain Tiles attribution |

School ratings rank each school's 2023–2026 Grade 4 and 7 Foundation Skills Assessment results among Metro Vancouver
public schools (10 = top 10%). The walk score follows the Walk Score method, rebuilt from OpenStreetMap amenities.
Both are our own calculations. Building heights are estimates where OpenStreetMap has none.

## Code

| File | What it does |
| --- | --- |
| `analysis/market_analysis.py` | Market analysis and fair-value model over a listing database |
| `analysis/neighbourhood_scores.py` | School ratings (FSA) and walkability scores |
| `analysis/build_map_layers.py`, `build_twin.py` | Map layers, 3D buildings, terrain and walk heat |
| `analysis/platform_template.html` | The 3D app (public mode when the data has no homes) |
| `analysis/build_public_site.py` | Builds `docs/`, the public open-data site |
| `analysis/*_browser_test.mjs` | Puppeteer tests for the 3D pages |

Build the public site (after the map layers and twin assets exist): `python3 analysis/build_public_site.py`.
