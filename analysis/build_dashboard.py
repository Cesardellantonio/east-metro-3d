"""Embed analysis/output/analysis.json into the dashboard template.

    python3 analysis/build_dashboard.py  ->  analysis/output/east_metro_home_scan.html
"""
from pathlib import Path

HERE = Path(__file__).parent
data = (HERE / "output" / "analysis.json").read_text().replace("</", "<\\/")
layers = (HERE / "output" / "map_layers.json").read_text().replace("</", "<\\/")
page = (HERE / "dashboard_template.html").read_text().replace("/*__DATA__*/null", data).replace("/*__MAP__*/null", layers)
out = HERE / "output" / "east_metro_home_scan.html"
out.write_text(page)
print(out)
