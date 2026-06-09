"""
generate_anttrail_map.py
━━━━━━━━━━━━━━━━━━━━━━━━
Generates DAI_AntTrail_Customer_Routes.html from two input files:

    1. Latency_Comparison.csv          (route + latency data)
    2. DAI_UPDATED_CUS_Network_Map.html (node coordinates)

Usage:
    python generate_anttrail_map.py
    python generate_anttrail_map.py --csv my_latency.csv --map my_network.html --out output.html

Requirements:
    pip install pandas beautifulsoup4
"""

import re
import json
import hashlib
import argparse
import sys
from pathlib import Path

# ── Optional imports (graceful fallback) ─────────────────────────────────────
try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    import csv as _csv
    HAS_PANDAS = False

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False


# ═════════════════════════════════════════════════════════════════════════════
# STEP 1 — Parse node coordinates from the Leaflet HTML map
# ═════════════════════════════════════════════════════════════════════════════

def extract_coords_from_html(html_path: str) -> dict:
    """
    Reads the Folium/Leaflet HTML and extracts every node name + lat/lng.

    The HTML contains patterns like:
        L.marker([39.05, -77.48], {}).bindTooltip(`<div> DK_ASHBURN_VA </div>` ...

    Returns:
        { "DK_ASHBURN_VA": {"lat": 39.05, "lng": -77.48}, ... }
    """
    print(f"[1/4] Reading coordinates from: {html_path}")

    with open(html_path, encoding="utf-8") as f:
        content = f.read()

    # Regex: grab lat,lng from L.marker([...]) then the tooltip text
    pattern = re.compile(
        r'L\.marker\(\s*\[([^\]]+)\].*?bindTooltip\(\s*`<div>\s*(.*?)\s*</div>',
        re.DOTALL
    )
    matches = pattern.findall(content)

    coords = {}
    for lat_lng_str, node_name in matches:
        node_name = node_name.strip()
        parts = [p.strip() for p in lat_lng_str.split(",")]
        try:
            coords[node_name] = {
                "lat": float(parts[0]),
                "lng": float(parts[1])
            }
        except (ValueError, IndexError):
            pass  # skip malformed entries

    print(f"    → Found {len(coords)} nodes with coordinates")
    return coords


# ═════════════════════════════════════════════════════════════════════════════
# STEP 2 — Parse latency routes from the CSV file
# ═════════════════════════════════════════════════════════════════════════════

def load_csv_routes(csv_path: str) -> list:
    """
    Reads Latency_Comparison.csv and returns a list of route dicts.

    Expected CSV columns:
        Source, Destination, Flow_Path,
        Total_Measured_Latency_ms, Total_Geo_Latency_ms,
        Delta_Latency_ms, Occurrence_Count, Multiple_Paths

    Returns:
        [
          {
            "source": "UURWMEA110",
            "destination": "DK_ASHBURN_VA",
            "flow_path": "UURWMEA110 -> UJEWMEA040 -> ...",
            "latency_ms": 1.73,
            "geo_latency_ms": 0.18,
            "delta_ms": 0.0,
            "occurrences": 135,
            "multiple_paths": False
          },
          ...
        ]
    """
    print(f"[2/4] Loading routes from CSV: {csv_path}")

    routes = []

    if HAS_PANDAS:
        df = pd.read_csv(csv_path)
        # Normalise column names (strip whitespace)
        df.columns = [c.strip() for c in df.columns]
        for _, row in df.iterrows():
            routes.append({
                "source":          str(row.get("Source", "")).strip(),
                "destination":     str(row.get("Destination", "")).strip(),
                "flow_path":       str(row.get("Flow_Path", "")).strip(),
                "latency_ms":      float(row.get("Total_Measured_Latency_ms", 0) or 0),
                "geo_latency_ms":  float(row.get("Total_Geo_Latency_ms", 0) or 0),
                "delta_ms":        float(row.get("Delta_Latency_ms", 0) or 0),
                "occurrences":     int(row.get("Occurrence_Count", 1) or 1),
                "multiple_paths":  str(row.get("Multiple_Paths", "False")).strip(),
            })
    else:
        # Fallback: use stdlib csv module
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = _csv.DictReader(f)
            for row in reader:
                routes.append({
                    "source":         row.get("Source", "").strip(),
                    "destination":    row.get("Destination", "").strip(),
                    "flow_path":      row.get("Flow_Path", "").strip(),
                    "latency_ms":     float(row.get("Total_Measured_Latency_ms", 0) or 0),
                    "geo_latency_ms": float(row.get("Total_Geo_Latency_ms", 0) or 0),
                    "delta_ms":       float(row.get("Delta_Latency_ms", 0) or 0),
                    "occurrences":    int(row.get("Occurrence_Count", 1) or 1),
                    "multiple_paths": row.get("Multiple_Paths", "False").strip(),
                })

    print(f"    → Loaded {len(routes)} route rows")
    return routes


# ═════════════════════════════════════════════════════════════════════════════
# STEP 3 — Merge: augment missing coordinates using city/state fallback
# ═════════════════════════════════════════════════════════════════════════════

# Approximate state centres (fallback when a node has no coords in the HTML)
STATE_CENTRES = {
    "AL": (32.8, -86.8),  "AZ": (34.0, -111.0), "AR": (35.0, -92.4),
    "CA": (37.0, -120.0), "CO": (39.0, -105.5),  "CT": (41.6, -72.7),
    "DE": (39.0, -75.5),  "FL": (28.0, -81.5),   "GA": (32.7, -83.5),
    "IA": (42.0, -93.5),  "IL": (40.0, -89.0),   "IN": (40.3, -86.1),
    "KS": (38.5, -98.4),  "KY": (37.5, -85.3),   "LA": (31.2, -91.8),
    "MA": (42.4, -71.4),  "MD": (39.0, -76.8),   "ME": (45.5, -69.2),
    "MI": (44.3, -84.5),  "MN": (46.4, -93.1),   "MO": (38.6, -92.6),
    "NC": (35.6, -79.8),  "NE": (41.5, -99.9),   "NH": (43.7, -71.6),
    "NJ": (40.1, -74.4),  "NM": (34.5, -106.0),  "NV": (39.5, -116.4),
    "NY": (42.9, -75.5),  "OH": (40.4, -82.8),   "OK": (35.5, -97.5),
    "OR": (44.1, -120.5), "PA": (40.6, -77.2),   "RI": (41.6, -71.5),
    "SC": (33.8, -81.2),  "TN": (35.9, -86.7),   "TX": (31.5, -99.3),
    "UT": (39.8, -111.1), "VA": (37.8, -78.2),   "VT": (44.0, -72.7),
    "WA": (47.4, -120.4), "WI": (44.3, -89.8),   "WV": (38.6, -80.5),
    "XX": (39.0, -77.0),
    # International
    "GB": (51.5, -0.12),  "CY": (35.1, 33.4),
}

# Well-known DISA site overrides: CITY_STATE_KEY -> (lat, lng)
KNOWN_SITES = {
    "ASHBURN_VA":   (39.05, -77.48), "PENTAGON_DC":  (38.87, -77.06),
    "FTBELVOR_VA":  (38.68, -77.13), "CRYSTLCY_VA":  (38.88, -77.12),
    "ANNAPOLS_MD":  (38.98, -76.50), "STERLING_VA":  (39.00, -77.43),
    "MCLEAN_VA":    (38.93, -77.18), "CHANTLLY_VA":  (38.88, -77.44),
    "FLSCHRCH_VA":  (38.85, -77.17), "MANASSAS_VA":  (38.75, -77.48),
    "LEXINGTN_KY":  (38.04, -84.50), "RICHMOND_VA":  (37.54, -77.43),
    "ARLNGTNG_VA":  (38.88, -77.10), "ARLINGTN_VA":  (38.88, -77.10),
    "FTKNOX_KY":    (37.89, -85.96), "EGLINAFB_FL":  (30.48, -86.53),
    "HILLAFB_UT":   (41.12, -111.97),"TINRAFB_OK":   (35.42, -97.39),
    "SANJOSE_CA":   (37.33, -121.88),"SAN_JOSE_CA":  (37.33, -121.88),
    "QUANTICO_VA":  (38.50, -77.30), "SANANTON_TX":  (29.42, -98.49),
    "DALLAS_TX":    (32.78, -96.80), "HOUSTON_TX":   (29.76, -95.37),
    "AUSTIN_TX":    (30.27, -97.74), "COLUMBUS_OH":  (39.96, -82.99),
    "INDINPLS_IN":  (39.77, -86.16), "PHILDLPH_PA":  (39.95, -75.16),
    "ATLANTA_GA":   (33.75, -84.39), "ORLANDO_FL":   (28.54, -81.38),
    "FTHUACHC_AZ":  (31.55, -110.35),"LUKEAFB_AZ":   (33.54, -112.36),
    "FTBLISS_TX":   (31.81, -106.42),"SCHQMIAM_FL":  (25.69, -80.43),
    "PTRSNSFB_CO":  (38.73, -104.75),"RAFAKRTR_CY":  (34.58, 32.99),
    "BEASMLSB_GB":  (51.50, -0.60),  "WYTON_GB":     (52.36, -0.10),
    "OTTAWA_CA":    (45.42, -75.70), "KTLNDAFB_NM":  (34.96, -106.61),
}

# Hard-coded hub positions (these are routers, not physical sites in HTML)
HUB_COORDS = {
    "UURWMEA110": {"lat": 39.10, "lng": -76.77},
    "UURWMEA120": {"lat": 39.08, "lng": -76.74},
}


def _jitter(name: str, base_lat: float, base_lng: float) -> tuple:
    """Add deterministic tiny offset so nodes in the same state don't stack."""
    h = int(hashlib.md5(name.encode()).hexdigest()[:4], 16)
    return (
        base_lat + (h % 100 - 50) * 0.02,
        base_lng + (h % 97  - 48) * 0.03,
    )


def augment_coords(node_name: str, known_coords: dict) -> dict | None:
    """
    Returns {"lat": ..., "lng": ...} for a node, using:
      1. HTML-extracted coords  (most accurate)
      2. KNOWN_SITES lookup     (hand-coded DISA sites)
      3. STATE_CENTRES fallback (approximate)
    Returns None if no match found.
    """
    if node_name in known_coords:
        return known_coords[node_name]

    # Parse PREFIX_CITY_STATE  e.g. DK_ASHBURN_VA  →  city_key = ASHBURN_VA
    parts = node_name.split("_")
    if len(parts) >= 3:
        city_key = "_".join(parts[1:])   # e.g. ASHBURN_VA
        state    = parts[-1]             # e.g. VA

        if city_key in KNOWN_SITES:
            lat, lng = KNOWN_SITES[city_key]
            return {"lat": lat, "lng": lng}

        if state in STATE_CENTRES:
            base_lat, base_lng = STATE_CENTRES[state]
            lat, lng = _jitter(node_name, base_lat, base_lng)
            return {"lat": lat, "lng": lng}

    return None


def build_map_data(routes: list, html_coords: dict) -> dict:
    """
    Merges CSV routes with coordinates.
    Returns the data structure the HTML map needs.
    """
    print("[3/4] Merging CSV routes with node coordinates…")

    # Resolve all node coords (HTML coords + fallback)
    all_nodes = {}

    # Start with HTML coords
    for name, coord in html_coords.items():
        all_nodes[name] = coord

    # Add hubs
    all_nodes.update(HUB_COORDS)

    # Build route list and fill in any missing coords
    map_routes = []
    node_routes = {}   # dest_name → [route, ...]
    skipped = 0

    # We only draw routes FROM the main hubs
    hubs = list(HUB_COORDS.keys())

    for r in routes:
        src  = r["source"]
        dest = r["destination"]

        if src not in hubs:
            continue   # only visualise hub → customer routes

        # Resolve destination coords
        if dest not in all_nodes:
            coord = augment_coords(dest, html_coords)
            if coord is None:
                skipped += 1
                continue
            all_nodes[dest] = coord

        src_c  = all_nodes[src]
        dest_c = all_nodes[dest]

        map_routes.append({
            "from":    src,
            "to":      dest,
            "lat1":    src_c["lat"],
            "lng1":    src_c["lng"],
            "lat2":    dest_c["lat"],
            "lng2":    dest_c["lng"],
            "ms":      round(r["latency_ms"], 3),
            "geo_ms":  round(r["geo_latency_ms"], 3),
            "delta":   round(r["delta_ms"], 3),
            "occ":     r["occurrences"],
            "path":    r["flow_path"],
            "multi":   r["multiple_paths"],
        })

        if dest not in node_routes:
            node_routes[dest] = []
        node_routes[dest].append({
            "from": src,
            "ms":   round(r["latency_ms"], 3),
            "path": r["flow_path"],
            "occ":  r["occurrences"],
        })

    # Build slim node summary
    slim_nodes = {}
    for name, coord in all_nodes.items():
        out_count = sum(1 for r in routes if r["source"] == name)
        in_count  = sum(1 for r in routes if r["destination"] == name)
        slim_nodes[name] = {
            "lat": coord["lat"],
            "lng": coord["lng"],
            "out": out_count,
            "in":  in_count,
        }

    # Deduplicate routes (keep one per destination — lowest latency)
    seen = {}
    for r in sorted(map_routes, key=lambda x: x["ms"]):
        key = (r["from"], r["to"])
        if key not in seen:
            seen[key] = r
    deduped_routes = list(seen.values())

    print(f"    → {len(deduped_routes)} unique routes | "
          f"{len(slim_nodes)} nodes | {skipped} skipped (no coords)")

    return {
        "routes":      deduped_routes,
        "nodes":       slim_nodes,
        "node_routes": node_routes,
        "hubs":        hubs,
    }


# ═════════════════════════════════════════════════════════════════════════════
# STEP 4 — Generate the HTML file
# ═════════════════════════════════════════════════════════════════════════════

def generate_html(map_data: dict, output_path: str) -> None:
    """
    Injects map_data as JSON into the self-contained HTML template
    and writes the output file.
    """
    print(f"[4/4] Generating HTML → {output_path}")

    map_data_json = json.dumps(map_data, separators=(",", ":"))

    html = """<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>DAI Application Routes – All Customers</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/leaflet@1.9.3/dist/leaflet.css"/>
<link rel="stylesheet"
      href="https://cdnjs.cloudflare.com/ajax/libs/Leaflet.awesome-markers/2.0.2/leaflet.awesome-markers.css"/>
<link rel="stylesheet"
      href="https://cdn.jsdelivr.net/npm/@fortawesome/fontawesome-free@6.2.0/css/all.min.css"/>
<script src="https://cdn.jsdelivr.net/npm/leaflet@1.9.3/dist/leaflet.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Leaflet.awesome-markers/2.0.2/leaflet.awesome-markers.js">
</script>
<script src="https://cdn.jsdelivr.net/npm/leaflet-ant-path@1.1.2/dist/leaflet-ant-path.min.js"></script>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Courier New',monospace;background:#0a0e1a;color:#c8d8ff;overflow:hidden}
#header{position:fixed;top:0;left:0;right:0;z-index:2000;
  background:linear-gradient(90deg,#0d1b3e,#091225 60%,#0a0e1a);
  border-bottom:1px solid #1e3a6e;padding:8px 16px;
  display:flex;align-items:center;gap:16px}
#header h1{font-size:14px;font-weight:700;color:#4fc3f7;
  letter-spacing:2px;text-transform:uppercase}
.subtitle{font-size:11px;color:#5a7fa0;letter-spacing:1px}
.badge{background:#1a3a5c;border:1px solid #2a5a8c;border-radius:3px;
  padding:2px 8px;font-size:10px;color:#7ec8f8}
#map{position:fixed;top:42px;left:0;right:340px;bottom:0}
#sidebar{position:fixed;top:42px;right:0;width:340px;bottom:0;
  background:#0a0e1a;border-left:1px solid #1e3a6e;
  display:flex;flex-direction:column;overflow:hidden}
#sidebar-header{padding:12px 14px 8px;border-bottom:1px solid #1a2e4a;flex-shrink:0}
#sidebar-title{font-size:12px;color:#7ec8f8;letter-spacing:1.5px;
  text-transform:uppercase;margin-bottom:6px}
#search-box{width:100%;background:#0d1b2e;border:1px solid #1e3a6e;
  color:#c8d8ff;padding:5px 8px;font-size:11px;border-radius:3px;font-family:inherit}
#search-box:focus{outline:none;border-color:#4fc3f7}
#filter-bar{display:flex;gap:6px;margin-top:6px;flex-wrap:wrap}
.filter-btn{font-size:10px;padding:2px 8px;border-radius:2px;
  border:1px solid #1e3a6e;background:#0d1b2e;color:#7ec8f8;
  cursor:pointer;letter-spacing:1px}
.filter-btn.active{background:#1a3a5c;border-color:#4fc3f7;color:#4fc3f7}
#node-list{flex:1;overflow-y:auto}
#node-list::-webkit-scrollbar{width:4px}
#node-list::-webkit-scrollbar-track{background:#0a0e1a}
#node-list::-webkit-scrollbar-thumb{background:#1e3a6e;border-radius:2px}
.node-item{padding:8px 14px;border-bottom:1px solid #0f1e30;
  cursor:pointer;transition:background .15s;display:flex;align-items:center;gap:8px}
.node-item:hover{background:#0d1b2e}
.node-item.selected{background:#0f2040;border-left:2px solid #4fc3f7}
.node-dot{width:8px;height:8px;border-radius:50%;flex-shrink:0}
.node-dot.hub{background:#ff6b35}
.node-dot.customer{background:#4fc3f7}
.node-dot.intl{background:#ab47bc}
.node-name{font-size:11px;color:#c8d8ff;flex:1}
.node-latency{font-size:10px;color:#5a9a6a;text-align:right;white-space:nowrap}
.node-latency.high{color:#e57373}
.node-latency.med{color:#ffb74d}
#detail-panel{border-top:1px solid #1a2e4a;flex-shrink:0;
  max-height:260px;overflow-y:auto;display:none}
#detail-panel::-webkit-scrollbar{width:4px}
#detail-panel::-webkit-scrollbar-thumb{background:#1e3a6e}
#detail-panel.visible{display:block}
.detail-header{background:#0d1b2e;padding:8px 14px;
  border-bottom:1px solid #1a2e4a;display:flex;
  align-items:center;justify-content:space-between}
.detail-node-name{font-size:12px;color:#4fc3f7;font-weight:700}
.detail-close{cursor:pointer;color:#5a7fa0;font-size:14px}
.detail-close:hover{color:#c8d8ff}
.detail-section{padding:8px 14px}
.detail-label{font-size:9px;color:#5a7fa0;letter-spacing:1.5px;
  text-transform:uppercase;margin-bottom:4px;margin-top:6px}
.detail-value{font-size:11px;color:#c8d8ff}
.detail-path{font-size:9px;color:#7ec8f8;word-break:break-all;line-height:1.5}
.detail-stat-row{display:flex;gap:16px;margin-bottom:4px}
.detail-stat{text-align:center}
.detail-stat .val{font-size:16px;color:#4fc3f7;font-weight:700}
.detail-stat .lbl{font-size:9px;color:#5a7fa0;letter-spacing:1px}
.route-item{padding:4px 0;border-bottom:1px solid #0f1e30}
.route-item:last-child{border-bottom:none}
#stats-bar{display:flex;gap:20px;align-items:center}
.stat-item .val{font-size:13px;color:#4fc3f7;font-weight:700}
.stat-item .lbl{font-size:9px;color:#5a7fa0}
#legend{position:fixed;bottom:16px;left:16px;z-index:1500;
  background:rgba(10,14,26,.9);border:1px solid #1e3a6e;
  border-radius:4px;padding:10px 14px;font-size:10px}
.legend-row{display:flex;align-items:center;gap:8px;margin-bottom:4px}
.legend-line{width:24px;height:2px}
.legend-dot-sm{width:8px;height:8px;border-radius:50%}
</style>
</head>
<body>

<div id="header">
  <h1>DAI Application Routes</h1>
  <span class="subtitle">All Customers · Hop-by-Hop Latency</span>
  <div id="stats-bar" style="margin-left:auto">
    <div class="stat-item">
      <div class="val" id="stat-routes">-</div><div class="lbl">ROUTES</div>
    </div>
    <div class="stat-item">
      <div class="val" id="stat-nodes">-</div><div class="lbl">NODES</div>
    </div>
    <div class="stat-item">
      <div class="val" id="stat-avg">-</div><div class="lbl">AVG MS</div>
    </div>
    <div class="badge">UURWMEA HUB</div>
  </div>
</div>

<div id="map"></div>

<div id="sidebar">
  <div id="sidebar-header">
    <div id="sidebar-title">Customer Nodes</div>
    <input id="search-box" type="text"
           placeholder="Search node name…" oninput="filterNodes()"/>
    <div id="filter-bar">
      <button class="filter-btn active" onclick="setFilter('all',this)">ALL</button>
      <button class="filter-btn" onclick="setFilter('low',this)">LOW (&lt;3ms)</button>
      <button class="filter-btn" onclick="setFilter('med',this)">MED (3-7ms)</button>
      <button class="filter-btn" onclick="setFilter('high',this)">HIGH (&gt;7ms)</button>
      <button class="filter-btn" onclick="setFilter('multi',this)">MULTI-PATH</button>
    </div>
  </div>
  <div id="node-list"></div>
  <div id="detail-panel">
    <div class="detail-header">
      <span class="detail-node-name" id="detail-name">-</span>
      <span class="detail-close" onclick="closeDetail()">&#x2715;</span>
    </div>
    <div class="detail-section" id="detail-content"></div>
  </div>
</div>

<div id="legend">
  <div style="font-size:9px;color:#5a7fa0;letter-spacing:1.5px;margin-bottom:6px">
    LEGEND
  </div>
  <div class="legend-row">
    <div class="legend-dot-sm" style="background:#ff6b35"></div>
    <span>Hub (UURWMEA)</span>
  </div>
  <div class="legend-row">
    <div class="legend-dot-sm" style="background:#4fc3f7"></div>
    <span>Customer Node</span>
  </div>
  <div class="legend-row">
    <div class="legend-dot-sm" style="background:#ab47bc"></div>
    <span>International</span>
  </div>
  <div class="legend-row">
    <div class="legend-line"
         style="background:linear-gradient(90deg,#4fc3f7,transparent)"></div>
    <span>Ant Trail Route</span>
  </div>
  <div class="legend-row">
    <div class="legend-line" style="background:#e57373"></div>
    <span>High Latency (&gt;7ms)</span>
  </div>
</div>

<script>
// ── Embedded data (injected by Python) ──────────────────────────────────────
const MAP_DATA = """ + map_data_json + """;

// ── Map init ─────────────────────────────────────────────────────────────────
const map = L.map('map', { center: [38.5, -95.0], zoom: 4, preferCanvas: true });
L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
  attribution: '&copy; OpenStreetMap contributors &copy; CARTO',
  subdomains: 'abcd', maxZoom: 20,
}).addTo(map);

// ── State ─────────────────────────────────────────────────────────────────────
let antPaths    = [];
let markers     = {};
let selectedNode = null;
let currentFilter = 'all';
let currentSearch = '';

// ── Helpers ───────────────────────────────────────────────────────────────────
function latencyColor(ms) {
  return ms < 3 ? '#4fc3f7' : ms < 7 ? '#ffb74d' : '#e57373';
}
function latencyClass(ms) {
  return ms < 3 ? '' : ms < 7 ? 'med' : 'high';
}
function isInternational(name) {
  return /_(GB|CY|DE|CA)$/.test(name);
}

// Build per-destination summary once
const routeSummary = {};
MAP_DATA.routes.forEach(r => {
  if (!routeSummary[r.to]) {
    routeSummary[r.to] = { minMs: Infinity, maxMs: 0, count: 0, paths: [] };
  }
  const s = routeSummary[r.to];
  s.minMs = Math.min(s.minMs, r.ms);
  s.maxMs = Math.max(s.maxMs, r.ms);
  s.count++;
  s.paths.push(r);
});

// ── Draw ant-path lines ───────────────────────────────────────────────────────
function drawRoutes(filter) {
  antPaths.forEach(p => map.removeLayer(p));
  antPaths = [];
  const drawn = new Set();

  MAP_DATA.routes.forEach(r => {
    if (drawn.has(r.to)) return;
    if (filter === 'low'   && r.ms >= 3)              return;
    if (filter === 'med'   && (r.ms < 3 || r.ms >= 7)) return;
    if (filter === 'high'  && r.ms < 7)               return;
    if (filter === 'multi' && r.multi !== 'True')      return;
    drawn.add(r.to);

    const color  = latencyColor(r.ms);
    const weight = r.ms > 10 ? 2.5 : r.ms > 5 ? 1.8 : 1.2;
    try {
      const path = L.polyline.antPath(
        [[r.lat1, r.lng1], [r.lat2, r.lng2]],
        { color, weight, opacity: 0.7,
          delay: 1200 + Math.random() * 800,
          dashArray: [10, 20], pulseColor: '#ffffff' }
      );
      path.addTo(map);
      antPaths.push(path);
    } catch(e) { /* skip bad coords */ }
  });
}

// ── Place markers ─────────────────────────────────────────────────────────────
function drawMarkers() {
  // Hubs
  MAP_DATA.hubs.forEach(hubName => {
    const n = MAP_DATA.nodes[hubName];
    if (!n) return;
    const m = L.circleMarker([n.lat, n.lng], {
      radius: 12, color: '#ff6b35', fillColor: '#ff6b35',
      fillOpacity: 0.9, weight: 2,
    }).addTo(map);
    m.bindPopup(
      '<div style="font-family:monospace;font-size:12px">' +
      '<b style="color:#e65100">' + hubName + '</b><br/>' +
      '<span style="color:#555">Hub Node</span><br/><br/>' +
      'Lat: ' + n.lat.toFixed(4) + '  Lng: ' + n.lng.toFixed(4) + '<br/>' +
      'Outbound: ' + (n.out||0) + ' routes</div>',
      { maxWidth: 280 }
    );
    m.on('click', () => selectNode(hubName));
    markers[hubName] = m;
  });

  // Customer nodes
  const drawn = new Set(MAP_DATA.hubs);
  MAP_DATA.routes.forEach(r => {
    const name = r.to;
    if (drawn.has(name)) return;
    drawn.add(name);
    const n = MAP_DATA.nodes[name];
    if (!n) return;

    const intl  = isInternational(name);
    const color = intl ? '#ab47bc' : latencyColor(r.ms);
    const s     = routeSummary[name] || {};

    const m = L.circleMarker([n.lat, n.lng], {
      radius: 5, color, fillColor: color, fillOpacity: 0.85, weight: 1.5,
    }).addTo(map);

    m.bindPopup(
      '<div style="font-family:monospace;font-size:11px;min-width:220px">' +
      '<b style="font-size:13px;color:#1565c0">' + name + '</b><br/>' +
      (intl ? '&#127757; International' : '&#127482;&#127480; US DoD Site') +
      '<hr style="margin:4px 0"/>' +
      '<b>Min Latency:</b> <span style="color:' + latencyColor(s.minMs||0) + '">' +
        (s.minMs||0).toFixed(3) + ' ms</span><br/>' +
      '<b>Max Latency:</b> <span style="color:' + latencyColor(s.maxMs||0) + '">' +
        (s.maxMs||0).toFixed(3) + ' ms</span><br/>' +
      '<b>Route Count:</b> ' + (s.count||1) + '<br/>' +
      '<b>Coords:</b> ' + n.lat.toFixed(3) + ', ' + n.lng.toFixed(3) +
      (s.paths&&s.paths.length ?
        '<hr style="margin:4px 0"/><b>Best Path:</b><br/>' +
        '<div style="font-size:9px;color:#444;word-break:break-all;max-height:60px;overflow:hidden">' +
        s.paths[0].path + '</div>' : '') +
      '</div>',
      { maxWidth: 320 }
    );
    m.on('click', () => selectNode(name));
    markers[name] = m;
  });
}

// ── Sidebar list ──────────────────────────────────────────────────────────────
function buildNodeList() {
  const search = currentSearch.toLowerCase();
  const filter = currentFilter;

  let items = MAP_DATA.routes.filter(r => {
    if (!r.to.toLowerCase().includes(search)) return false;
    if (filter === 'low'   && r.ms >= 3)              return false;
    if (filter === 'med'   && (r.ms < 3||r.ms >= 7)) return false;
    if (filter === 'high'  && r.ms < 7)               return false;
    if (filter === 'multi' && r.multi !== 'True')      return false;
    return true;
  });

  // Dedupe by dest, keep lowest latency
  const seen = {};
  items.forEach(r => {
    if (!seen[r.to] || r.ms < seen[r.to].ms) seen[r.to] = r;
  });
  items = Object.values(seen).sort((a, b) => a.ms - b.ms);

  const list = document.getElementById('node-list');
  list.innerHTML = items.map(r => {
    const intl = isInternational(r.to);
    const sel  = selectedNode === r.to ? ' selected' : '';
    return '<div class="node-item' + sel + '" onclick="selectNode(\\''+r.to+'\\')">'+
      '<div class="node-dot '+(intl?'intl':'customer')+'"></div>'+
      '<span class="node-name">'+r.to+'</span>'+
      '<span class="node-latency '+latencyClass(r.ms)+'">'+r.ms.toFixed(2)+'ms</span>'+
      '</div>';
  }).join('');

  const avg = items.length
    ? (items.reduce((s,r)=>s+r.ms,0)/items.length).toFixed(1)+'ms'
    : '-';
  document.getElementById('stat-routes').textContent = items.length;
  document.getElementById('stat-nodes').textContent  =
    Object.keys(MAP_DATA.nodes).length;
  document.getElementById('stat-avg').textContent    = avg;
}

function filterNodes() {
  currentSearch = document.getElementById('search-box').value;
  buildNodeList();
}

function setFilter(f, btn) {
  currentFilter = f;
  document.querySelectorAll('.filter-btn').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  buildNodeList();
  drawRoutes(f);
}

// ── Node selection ────────────────────────────────────────────────────────────
function selectNode(name) {
  selectedNode = name;
  buildNodeList();
  const n = MAP_DATA.nodes[name];
  if (n) {
    map.flyTo([n.lat, n.lng], 7, { duration: 1.2 });
    if (markers[name]) markers[name].openPopup();
  }
  showDetail(name);
}

function showDetail(name) {
  const panel   = document.getElementById('detail-panel');
  const content = document.getElementById('detail-content');
  document.getElementById('detail-name').textContent = name;

  const node       = MAP_DATA.nodes[name] || {};
  const nodeRoutes = MAP_DATA.node_routes[name] || [];
  const minMs = nodeRoutes.length ? Math.min(...nodeRoutes.map(r=>r.ms)) : 0;
  const maxMs = nodeRoutes.length ? Math.max(...nodeRoutes.map(r=>r.ms)) : 0;

  content.innerHTML =
    '<div class="detail-stat-row">' +
      '<div class="detail-stat"><div class="val">'+minMs.toFixed(2)+'</div>'+
        '<div class="lbl">MIN MS</div></div>' +
      '<div class="detail-stat"><div class="val">'+maxMs.toFixed(2)+'</div>'+
        '<div class="lbl">MAX MS</div></div>' +
      '<div class="detail-stat"><div class="val">'+nodeRoutes.length+'</div>'+
        '<div class="lbl">PATHS</div></div>' +
      '<div class="detail-stat"><div class="val">'+(node.out||0)+'</div>'+
        '<div class="lbl">OUTBOUND</div></div>' +
    '</div>' +
    '<div class="detail-label">Coordinates</div>' +
    '<div class="detail-value">'+(node.lat||0).toFixed(4)+
      ', '+(node.lng||0).toFixed(4)+'</div>' +
    (nodeRoutes.length ?
      '<div class="detail-label">Route Paths</div>' +
      nodeRoutes.slice(0,5).map(r=>
        '<div class="route-item">' +
          '<div style="display:flex;justify-content:space-between;margin-bottom:2px">' +
            '<span style="font-size:10px;color:#7ec8f8">&larr; '+r.from+'</span>' +
            '<span style="font-size:10px;color:'+latencyColor(r.ms)+
              ';font-weight:bold">'+r.ms.toFixed(3)+' ms</span>' +
            '<span style="font-size:9px;color:#5a7fa0">&times;'+r.occ+'</span>' +
          '</div>' +
          '<div class="detail-path">'+r.path+'</div>' +
        '</div>'
      ).join('')
    : '<div class="detail-value" style="color:#5a7fa0">No route data</div>');

  panel.classList.add('visible');
}

function closeDetail() {
  document.getElementById('detail-panel').classList.remove('visible');
  selectedNode = null;
  buildNodeList();
}

// ── Reset control ─────────────────────────────────────────────────────────────
const resetBtn = L.control({ position: 'topright' });
resetBtn.onAdd = () => {
  const d = L.DomUtil.create('div');
  d.innerHTML =
    '<button onclick="map.setView([38.5,-95.0],4)" ' +
    'style="background:#0d1b2e;color:#4fc3f7;border:1px solid #1e3a6e;' +
    'padding:6px 10px;cursor:pointer;font-family:monospace;' +
    'font-size:11px;border-radius:3px">&#8962; RESET</button>';
  return d;
};
resetBtn.addTo(map);

// ── Boot ──────────────────────────────────────────────────────────────────────
drawRoutes('all');
drawMarkers();
buildNodeList();
</script>
</body>
</html>"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    size_kb = Path(output_path).stat().st_size / 1024
    print(f"\n✅  Done!  Output: {output_path}  ({size_kb:.0f} KB)")
    print("    Open the file in any browser — no server needed.")


# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Generate DAI Ant-Trail Customer Routes HTML from CSV + Leaflet map"
    )
    parser.add_argument(
        "--csv",
        default="Latency_Comparison.csv",
        help="Path to the latency CSV file (default: Latency_Comparison.csv)"
    )
    parser.add_argument(
        "--map",
        default="DAI_UPDATED_CUS_Network_Map.html",
        help="Path to the existing Leaflet HTML with node coordinates"
    )
    parser.add_argument(
        "--out",
        default="DAI_AntTrail_Customer_Routes.html",
        help="Output HTML file name (default: DAI_AntTrail_Customer_Routes.html)"
    )
    args = parser.parse_args()

    # Validate inputs
    if not Path(args.csv).exists():
        print(f"❌  CSV not found: {args.csv}")
        sys.exit(1)
    if not Path(args.map).exists():
        print(f"❌  HTML map not found: {args.map}")
        sys.exit(1)

    print("=" * 60)
    print("  DAI Ant-Trail Map Generator")
    print("=" * 60)

    # Run the four steps
    html_coords = extract_coords_from_html(args.map)   # Step 1
    routes      = load_csv_routes(args.csv)            # Step 2
    map_data    = build_map_data(routes, html_coords)  # Step 3
    generate_html(map_data, args.out)                  # Step 4


if __name__ == "__main__":
    main()
