"""
generate_ip_trail_map.py
━━━━━━━━━━━━━━━━━━━━━━━━
Generates DAI_AntTrail_IP_Trail.html showing IP address traversal
instead of node/circuit names — the new leadership requirement.

What changed from generate_anttrail_map.py:
  - Input is now the XLSX files instead of the original CSV
  - Each hop in the route now shows the actual IP address
  - Sidebar shows src IP / dest IP per flow
  - Popup shows IP trail with clickable badges per hop
  - Search works on both node name AND IP address

Input files (all three required):
    Complete_Paths_IP_Enriched.xlsx   routes + source/dest IPs
    master_dataframe_long.xlsx        router -> lat/lng coordinates
    master_dataframe_3.xlsx           router -> loopback IP (intermediate hops)

Output:
    DAI_AntTrail_IP_Trail.html        open in Edge, no server needed

Usage:
    python generate_ip_trail_map.py
    python generate_ip_trail_map.py --ip Complete_Paths_IP_Enriched.xlsx
                                    --coords master_dataframe_long.xlsx
                                    --routers master_dataframe_3.xlsx
                                    --out DAI_AntTrail_IP_Trail.html

Requirements:
    pip install pandas openpyxl
"""

import re
import json
import argparse
import sys
from pathlib import Path

try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    print("ERROR: pip install pandas openpyxl")
    sys.exit(1)


# ─────────────────────────────────────────────────────────────────────────────
# STEP 1 — Load coordinates for every router
# Source: master_dataframe_long.xlsx  (Router, Latitude, Longitude)
# ─────────────────────────────────────────────────────────────────────────────

def load_router_coords(xlsx_path: str) -> dict:
    """
    Returns { "UURWMEA110": (39.083, -76.733), ... }
    for every router that has lat/lng data.
    """
    print(f"[1/4] Loading router coordinates from: {xlsx_path}")
    df = pd.read_excel(xlsx_path)
    df = df.drop_duplicates("Router")
    coords = {}
    for _, row in df.iterrows():
        router = str(row["Router"]).strip()
        try:
            coords[router] = (float(row["Latitude"]), float(row["Longitude"]))
        except (ValueError, TypeError):
            pass
    print(f"    -> {len(coords)} routers with coordinates")
    return coords


# ─────────────────────────────────────────────────────────────────────────────
# STEP 2 — Load loopback IPs for intermediate hop routers
# Source: master_dataframe_3.xlsx  (Source_Router, Interface, Destination_IP)
# ─────────────────────────────────────────────────────────────────────────────

def load_router_ips(xlsx_path: str) -> dict:
    """
    Returns { "UJEWMEA040": "33.63.x.x", ... }
    using the loopback (lo0.0) interface IP as the primary IP per router.
    These are the intermediate hop IPs not present in the flow file.
    """
    print(f"[2/4] Loading router IPs from: {xlsx_path}")
    df = pd.read_excel(xlsx_path)

    # Use loopback interface as the canonical IP for each router
    lo = df[df["Interface"].str.contains("lo0", na=False)].copy()
    lo = lo[lo["Destination_IP"] != "RIP-NA"][["Source_Router", "Destination_IP"]]
    lo.columns = ["Router", "IP"]
    lo = lo.drop_duplicates("Router").set_index("Router")["IP"].to_dict()

    print(f"    -> {len(lo)} routers with loopback IPs")
    return lo


# ─────────────────────────────────────────────────────────────────────────────
# STEP 3 — Load flow routes with source/dest IPs
# Source: Complete_Paths_IP_Enriched.xlsx
# ─────────────────────────────────────────────────────────────────────────────

def load_ip_flows(xlsx_path: str) -> tuple:
    """
    Returns:
        flows      list of flow dicts (one per row)
        node_ip    { node_name: ip_address } from flow endpoints
    """
    print(f"[3/4] Loading IP flows from: {xlsx_path}")
    df = pd.read_excel(xlsx_path)

    flows = []
    node_ip = {}

    for _, row in df.iterrows():
        src  = str(row.get("Source", "")).strip()
        dest = str(row.get("Destination", "")).strip()
        src_ip  = str(row.get("Source_IP", "")).strip()
        dest_ip = str(row.get("Dest_IP", "")).strip()

        flows.append({
            "source":      src,
            "destination": dest,
            "source_ip":   src_ip,
            "dest_ip":     dest_ip,
            "flow_path":   str(row.get("Flow_Path", "")).strip(),
            "latency_ms":  float(row.get("Total_Latency_ms", 0) or 0),
        })

        # Build endpoint node -> IP lookup
        if src and src_ip and src not in node_ip:
            node_ip[src] = src_ip
        if dest and dest_ip and dest not in node_ip:
            node_ip[dest] = dest_ip

    print(f"    -> {len(flows)} flow rows | {len(node_ip)} endpoint IPs")
    return flows, node_ip


# ─────────────────────────────────────────────────────────────────────────────
# STEP 4 — Merge everything into map data
# ─────────────────────────────────────────────────────────────────────────────

HUBS = ["UURWMEA110", "UURWMEA120"]


def parse_hops(path_str: str) -> list:
    """
    Splits a flow path string into a clean list of hop node names.

    Input:  'UURWMEA110 -> UJEWMEA040 --2.5-> UJPWMEC010 -> DK_DALLAS_TX'
    Output: ['UURWMEA110', 'UJEWMEA040', 'UJPWMEC010', 'DK_DALLAS_TX']

    Strips latency annotations (--0.5->), mesh tags ([UJP_MESH]), etc.
    """
    hops = re.split(r"\s*-+[\d.]*->\s*|\s*->\s*", path_str)
    return [
        h.strip()
        for h in hops
        if h.strip() and "UJP_MESH" not in h and "[" not in h
    ]


def build_ip_path_str(hops: list, node_ip: dict, router_ips: dict) -> str:
    """
    Builds a human-readable IP trail string for a route.

    Example output:
        214.36.22.104(UURWMEA110) -> 33.63.x.x(UJEWMEA040) -> 131.67.x.x(DK_DALLAS_TX)
    """
    parts = []
    for h in hops:
        ip = node_ip.get(h) or router_ips.get(h)
        if ip:
            parts.append(f"{ip}({h})")
        else:
            parts.append(h)
    return " -> ".join(parts)


def build_map_data(flows: list, node_ip: dict,
                   router_ips: dict, router_coords: dict) -> dict:
    """
    Merges flows, IPs, and coordinates into the map data structure.
    Only includes hub -> customer routes (from UURWMEA110 / UURWMEA120).
    Keeps one route per src->dest pair (lowest latency wins).
    """
    # Filter to hub->customer routes only, deduplicate by lowest latency
    from_hub = [f for f in flows if f["source"] in HUBS]
    from_hub.sort(key=lambda x: x["latency_ms"])
    seen_pairs = {}
    for f in from_hub:
        key = (f["source"], f["destination"])
        if key not in seen_pairs:
            seen_pairs[key] = f

    map_routes = []
    nodes = {}
    node_routes = {}
    skipped = 0

    for f in seen_pairs.values():
        src  = f["source"]
        dest = f["destination"]

        src_c  = router_coords.get(src)
        dest_c = router_coords.get(dest)
        if not src_c or not dest_c:
            skipped += 1
            continue

        hops        = parse_hops(f["flow_path"])
        ip_path_str = build_ip_path_str(hops, node_ip, router_ips)

        # Structured hop list for the detail panel
        hop_list = []
        for h in hops:
            ip = node_ip.get(h) or router_ips.get(h) or "N/A"
            hop_list.append({"node": h, "ip": ip})

        route = {
            "from":     src,
            "to":       dest,
            "src_ip":   f["source_ip"],
            "dest_ip":  f["dest_ip"],
            "lat1":     src_c[0],
            "lng1":     src_c[1],
            "lat2":     dest_c[0],
            "lng2":     dest_c[1],
            "ms":       round(f["latency_ms"], 3),
            "path":     f["flow_path"],
            "ip_path":  ip_path_str,
            "hops":     hop_list,
            "multi":    "False",
        }
        map_routes.append(route)

        # Build node summary
        for name, coords in [(src, src_c), (dest, dest_c)]:
            if name not in nodes:
                nodes[name] = {
                    "lat": coords[0],
                    "lng": coords[1],
                    "ip":  node_ip.get(name) or router_ips.get(name) or "N/A",
                    "out": 0,
                    "in":  0,
                }
        nodes[src]["out"]  += 1
        nodes[dest]["in"]  += 1

        # Node routes for detail panel
        if dest not in node_routes:
            node_routes[dest] = []
        node_routes[dest].append({
            "from":     src,
            "ms":       round(f["latency_ms"], 3),
            "path":     f["flow_path"],
            "ip_path":  ip_path_str,
            "src_ip":   f["source_ip"],
            "dest_ip":  f["dest_ip"],
            "occ":      1,
        })

    print(f"    -> {len(map_routes)} unique routes | "
          f"{len(nodes)} nodes | {skipped} skipped (no coords)")

    return {
        "routes":      map_routes,
        "nodes":       nodes,
        "node_routes": node_routes,
        "hubs":        HUBS,
    }


# ─────────────────────────────────────────────────────────────────────────────
# STEP 5 — Write HTML
# ─────────────────────────────────────────────────────────────────────────────

def write_html(map_data: dict, output_path: str) -> None:
    print(f"[5/5] Writing HTML -> {output_path}")

    data_json = json.dumps(map_data, separators=(",", ":"))

    # HTML uses CDN src= links (same as co-worker's Folium file)
    # This is what makes it work in Edge on file:// without a server
    html = (
        '<!DOCTYPE html>\n'
        '<html>\n'
        '<head>\n'
        '<meta charset="UTF-8"/>\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1.0"/>\n'
        '<title>DAI Application Routes - IP Trail</title>\n'
        '<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/leaflet@1.9.3/dist/leaflet.css"/>\n'
        '<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/Leaflet.awesome-markers/2.0.2/leaflet.awesome-markers.css"/>\n'
        '<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@fortawesome/fontawesome-free@6.2.0/css/all.min.css"/>\n'
        '<script src="https://cdn.jsdelivr.net/npm/leaflet@1.9.3/dist/leaflet.js"></script>\n'
        '<script src="https://cdnjs.cloudflare.com/ajax/libs/Leaflet.awesome-markers/2.0.2/leaflet.awesome-markers.js"></script>\n'
        '<script src="https://cdn.jsdelivr.net/npm/leaflet-ant-path@1.1.2/dist/leaflet-ant-path.min.js"></script>\n'
        '<style>\n'
        '*{box-sizing:border-box;margin:0;padding:0}\n'
        'body{font-family:\'Courier New\',monospace;background:#0a0e1a;color:#c8d8ff;overflow:hidden}\n'
        '#header{position:fixed;top:0;left:0;right:0;z-index:2000;\n'
        '  background:linear-gradient(90deg,#0d1b3e,#091225 60%,#0a0e1a);\n'
        '  border-bottom:1px solid #1e3a6e;padding:8px 16px;\n'
        '  display:flex;align-items:center;gap:16px}\n'
        '#header h1{font-size:14px;font-weight:700;color:#4fc3f7;letter-spacing:2px;text-transform:uppercase}\n'
        '.subtitle{font-size:11px;color:#5a7fa0;letter-spacing:1px}\n'
        '.badge{background:#1a3a5c;border:1px solid #2a5a8c;border-radius:3px;padding:2px 8px;font-size:10px;color:#7ec8f8}\n'
        '#stats-bar{display:flex;gap:20px;align-items:center}\n'
        '.stat-item .val{font-size:13px;color:#4fc3f7;font-weight:700}\n'
        '.stat-item .lbl{font-size:9px;color:#5a7fa0}\n'
        '#map{position:fixed;top:42px;left:0;right:360px;bottom:0}\n'
        '#sidebar{position:fixed;top:42px;right:0;width:360px;bottom:0;\n'
        '  background:#0a0e1a;border-left:1px solid #1e3a6e;\n'
        '  display:flex;flex-direction:column;overflow:hidden}\n'
        '#sidebar-header{padding:12px 14px 8px;border-bottom:1px solid #1a2e4a;flex-shrink:0}\n'
        '#sidebar-title{font-size:12px;color:#7ec8f8;letter-spacing:1.5px;text-transform:uppercase;margin-bottom:6px}\n'
        '#search-box{width:100%;background:#0d1b2e;border:1px solid #1e3a6e;\n'
        '  color:#c8d8ff;padding:5px 8px;font-size:11px;border-radius:3px;font-family:inherit}\n'
        '#search-box:focus{outline:none;border-color:#4fc3f7}\n'
        '#filter-bar{display:flex;gap:6px;margin-top:6px;flex-wrap:wrap}\n'
        '.filter-btn{font-size:10px;padding:2px 8px;border-radius:2px;border:1px solid #1e3a6e;\n'
        '  background:#0d1b2e;color:#7ec8f8;cursor:pointer;letter-spacing:1px}\n'
        '.filter-btn.active{background:#1a3a5c;border-color:#4fc3f7;color:#4fc3f7}\n'
        '#node-list{flex:1;overflow-y:auto}\n'
        '#node-list::-webkit-scrollbar{width:4px}\n'
        '#node-list::-webkit-scrollbar-thumb{background:#1e3a6e;border-radius:2px}\n'
        '.node-item{padding:8px 14px;border-bottom:1px solid #0f1e30;cursor:pointer;\n'
        '  transition:background .15s;display:flex;align-items:center;gap:8px}\n'
        '.node-item:hover{background:#0d1b2e}\n'
        '.node-item.selected{background:#0f2040;border-left:2px solid #4fc3f7}\n'
        '.node-dot{width:8px;height:8px;border-radius:50%;flex-shrink:0}\n'
        '.node-dot.hub{background:#ff6b35}.node-dot.customer{background:#4fc3f7}.node-dot.intl{background:#ab47bc}\n'
        '.node-name{font-size:10px;color:#c8d8ff;flex:1}\n'
        '.node-ip{font-size:9px;color:#4fc3f7;white-space:nowrap}\n'
        '.node-latency{font-size:10px;color:#5a9a6a;text-align:right;white-space:nowrap;min-width:44px}\n'
        '.node-latency.high{color:#e57373}.node-latency.med{color:#ffb74d}\n'
        '#detail-panel{border-top:1px solid #1a2e4a;flex-shrink:0;max-height:300px;overflow-y:auto;display:none}\n'
        '#detail-panel::-webkit-scrollbar{width:4px}\n'
        '#detail-panel::-webkit-scrollbar-thumb{background:#1e3a6e}\n'
        '#detail-panel.visible{display:block}\n'
        '.detail-header{background:#0d1b2e;padding:8px 14px;border-bottom:1px solid #1a2e4a;\n'
        '  display:flex;align-items:center;justify-content:space-between}\n'
        '.detail-node-name{font-size:12px;color:#4fc3f7;font-weight:700}\n'
        '.detail-close{cursor:pointer;color:#5a7fa0;font-size:14px}\n'
        '.detail-close:hover{color:#c8d8ff}\n'
        '.detail-section{padding:8px 14px}\n'
        '.detail-label{font-size:9px;color:#5a7fa0;letter-spacing:1.5px;text-transform:uppercase;margin:6px 0 3px}\n'
        '.detail-value{font-size:11px;color:#c8d8ff}\n'
        '.detail-ip{font-size:13px;color:#4fc3f7;font-weight:700}\n'
        '.detail-stat-row{display:flex;gap:16px;margin-bottom:4px}\n'
        '.detail-stat{text-align:center}\n'
        '.detail-stat .val{font-size:16px;color:#4fc3f7;font-weight:700}\n'
        '.detail-stat .lbl{font-size:9px;color:#5a7fa0;letter-spacing:1px}\n'
        '.route-item{padding:5px 0;border-bottom:1px solid #0f1e30}\n'
        '.route-item:last-child{border-bottom:none}\n'
        '.ip-badge{display:inline-block;background:#0d2a3e;border:1px solid #1e5a6e;\n'
        '  border-radius:3px;padding:1px 6px;font-size:9px;color:#4fc3f7;white-space:nowrap;\n'
        '  margin:1px 0}\n'
        '.hop-arrow{color:#5a7fa0;font-size:10px;margin:0 2px}\n'
        '#legend{position:fixed;bottom:16px;left:16px;z-index:1500;\n'
        '  background:rgba(10,14,26,.92);border:1px solid #1e3a6e;\n'
        '  border-radius:4px;padding:10px 14px;font-size:10px}\n'
        '.legend-row{display:flex;align-items:center;gap:8px;margin-bottom:4px}\n'
        '.legend-line{width:24px;height:2px}\n'
        '.legend-dot-sm{width:8px;height:8px;border-radius:50%}\n'
        '</style>\n'
        '</head>\n'
        '<body>\n'
        '<div id="header">\n'
        '  <h1>DAI Application Routes</h1>\n'
        '  <span class="subtitle">IP Trail &middot; All Customers &middot; Hop-by-Hop Latency</span>\n'
        '  <div id="stats-bar" style="margin-left:auto">\n'
        '    <div class="stat-item"><div class="val" id="stat-routes">-</div><div class="lbl">ROUTES</div></div>\n'
        '    <div class="stat-item"><div class="val" id="stat-nodes">-</div><div class="lbl">NODES</div></div>\n'
        '    <div class="stat-item"><div class="val" id="stat-avg">-</div><div class="lbl">AVG MS</div></div>\n'
        '    <div class="badge">UURWMEA HUB</div>\n'
        '  </div>\n'
        '</div>\n'
        '<div id="map"></div>\n'
        '<div id="sidebar">\n'
        '  <div id="sidebar-header">\n'
        '    <div id="sidebar-title">Customer Nodes &amp; IPs</div>\n'
        '    <input id="search-box" type="text" placeholder="Search node name or IP address..." oninput="filterNodes()"/>\n'
        '    <div id="filter-bar">\n'
        '      <button class="filter-btn active" onclick="setFilter(\'all\',this)">ALL</button>\n'
        '      <button class="filter-btn" onclick="setFilter(\'low\',this)">LOW (&lt;3ms)</button>\n'
        '      <button class="filter-btn" onclick="setFilter(\'med\',this)">MED (3-7ms)</button>\n'
        '      <button class="filter-btn" onclick="setFilter(\'high\',this)">HIGH (&gt;7ms)</button>\n'
        '    </div>\n'
        '  </div>\n'
        '  <div id="node-list"></div>\n'
        '  <div id="detail-panel">\n'
        '    <div class="detail-header">\n'
        '      <span class="detail-node-name" id="detail-name">-</span>\n'
        '      <span class="detail-close" onclick="closeDetail()">&#x2715;</span>\n'
        '    </div>\n'
        '    <div class="detail-section" id="detail-content"></div>\n'
        '  </div>\n'
        '</div>\n'
        '<div id="legend">\n'
        '  <div style="font-size:9px;color:#5a7fa0;letter-spacing:1.5px;margin-bottom:6px">LEGEND</div>\n'
        '  <div class="legend-row"><div class="legend-dot-sm" style="background:#ff6b35"></div><span>Hub (UURWMEA)</span></div>\n'
        '  <div class="legend-row"><div class="legend-dot-sm" style="background:#4fc3f7"></div><span>Customer Node</span></div>\n'
        '  <div class="legend-row"><div class="legend-dot-sm" style="background:#ab47bc"></div><span>International</span></div>\n'
        '  <div class="legend-row"><div class="legend-line" style="background:linear-gradient(90deg,#4fc3f7,transparent)"></div><span>Ant Trail Route</span></div>\n'
        '  <div class="legend-row"><div class="legend-line" style="background:#e57373"></div><span>High Latency (&gt;7ms)</span></div>\n'
        '</div>\n'
        '<script>\n'
        'const MAP_DATA = ' + data_json + ';\n\n'
        'const map = L.map(\'map\',{center:[38.5,-95.0],zoom:4,preferCanvas:true});\n'
        'L.tileLayer(\'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png\',{\n'
        '  attribution:\'&copy; OpenStreetMap contributors &copy; CARTO\',\n'
        '  subdomains:\'abcd\',maxZoom:20\n'
        '}).addTo(map);\n\n'
        'var antPaths=[],markers={},selectedNode=null,currentFilter=\'all\',currentSearch=\'\';\n\n'
        'function latencyColor(ms){return ms<3?\'#4fc3f7\':ms<7?\'#ffb74d\':\'#e57373\';}\n'
        'function latencyClass(ms){return ms<3?\'\':ms<7?\'med\':\'high\';}\n'
        'function isIntl(n){return /_(GB|CY|DE|CA|RO)$/.test(n);}\n\n'
        'var routeSummary={};\n'
        'MAP_DATA.routes.forEach(function(r){\n'
        '  if(!routeSummary[r.to]) routeSummary[r.to]={minMs:Infinity,maxMs:0,count:0,paths:[]};\n'
        '  var s=routeSummary[r.to];\n'
        '  s.minMs=Math.min(s.minMs,r.ms); s.maxMs=Math.max(s.maxMs,r.ms);\n'
        '  s.count++; s.paths.push(r);\n'
        '});\n\n'
        'function drawRoutes(filter){\n'
        '  antPaths.forEach(function(p){map.removeLayer(p);}); antPaths=[];\n'
        '  var drawn={};\n'
        '  MAP_DATA.routes.forEach(function(r){\n'
        '    if(drawn[r.to]) return;\n'
        '    if(filter===\'low\'&&r.ms>=3) return;\n'
        '    if(filter===\'med\'&&(r.ms<3||r.ms>=7)) return;\n'
        '    if(filter===\'high\'&&r.ms<7) return;\n'
        '    drawn[r.to]=1;\n'
        '    try{\n'
        '      var p=L.polyline.antPath([[r.lat1,r.lng1],[r.lat2,r.lng2]],{\n'
        '        color:latencyColor(r.ms),\n'
        '        weight:r.ms>10?2.5:r.ms>5?1.8:1.2,\n'
        '        opacity:0.7,\n'
        '        delay:1200+Math.floor(Math.random()*800),\n'
        '        dashArray:[10,20],\n'
        '        pulseColor:\'#ffffff\'\n'
        '      });\n'
        '      p.addTo(map); antPaths.push(p);\n'
        '    }catch(e){}\n'
        '  });\n'
        '}\n\n'
        'function drawMarkers(){\n'
        '  MAP_DATA.hubs.forEach(function(h){\n'
        '    var n=MAP_DATA.nodes[h]; if(!n) return;\n'
        '    var m=L.circleMarker([n.lat,n.lng],{radius:12,color:\'#ff6b35\',fillColor:\'#ff6b35\',fillOpacity:.9,weight:2}).addTo(map);\n'
        '    m.bindPopup(\'<div style="font-family:monospace;font-size:12px;min-width:220px">\'\n'
        '      +\'<b style="color:#e65100">\'+h+\'</b><br/>Hub Node &mdash; Fort Meade MD\'\n'
        '      +\'<hr style="margin:4px 0"/>IP: <b style="color:#4fc3f7">\'+(n.ip||\'N/A\')+\'</b><br/>\'\n'
        '      +\'Lat: \'+n.lat.toFixed(4)+\' Lng: \'+n.lng.toFixed(4)+\'<br/>Outbound: \'+(n.out||0)+\'</div>\',{maxWidth:280});\n'
        '    m.on(\'click\',function(){selectNode(h);}); markers[h]=m;\n'
        '  });\n'
        '  var drawn={}; MAP_DATA.hubs.forEach(function(h){drawn[h]=1;});\n'
        '  MAP_DATA.routes.forEach(function(r){\n'
        '    var nm=r.to; if(drawn[nm]) return; drawn[nm]=1;\n'
        '    var n=MAP_DATA.nodes[nm]; if(!n) return;\n'
        '    var intl=isIntl(nm), c=intl?\'#ab47bc\':latencyColor(r.ms), s=routeSummary[nm]||{};\n'
        '    var m=L.circleMarker([n.lat,n.lng],{radius:5,color:c,fillColor:c,fillOpacity:.85,weight:1.5}).addTo(map);\n'
        '    m.bindPopup(\'<div style="font-family:monospace;font-size:11px;min-width:240px">\'\n'
        '      +\'<b style="font-size:13px;color:#1565c0">\'+nm+\'</b><br/>\'\n'
        '      +(intl?\'&#127757; International\':\'&#127482;&#127480; US DoD Site\')\n'
        '      +\'<hr style="margin:4px 0"/>IP: <b style="color:#4fc3f7">\'+(n.ip||\'N/A\')+\'</b><br/>\'\n'
        '      +\'<b>Min:</b> <span style="color:\'+latencyColor(s.minMs||0)+\'">\'+( s.minMs||0).toFixed(3)+\' ms</span><br/>\'\n'
        '      +\'<b>Max:</b> <span style="color:\'+latencyColor(s.maxMs||0)+\'">\'+( s.maxMs||0).toFixed(3)+\' ms</span><br/>\'\n'
        '      +\'<b>Routes:</b> \'+(s.count||1)+\'<br/>\'\n'
        '      +\'<b>Coords:</b> \'+n.lat.toFixed(3)+\', \'+n.lng.toFixed(3)\n'
        '      +(s.paths&&s.paths.length?\'<hr style="margin:4px 0"/>IP Trail:<br/>\'\n'
        '        +\'<div style="font-size:9px;color:#4fc3f7;word-break:break-all;max-height:60px;overflow:hidden">\'\n'
        '        +s.paths[0].ip_path+\'</div>\':\'\'),{maxWidth:340});\n'
        '    m.on(\'click\',function(){selectNode(nm);}); markers[nm]=m;\n'
        '  });\n'
        '}\n\n'
        'var selectedNode=null;\n'
        'function buildNodeList(){\n'
        '  var q=currentSearch.toLowerCase(), f=currentFilter;\n'
        '  var items=MAP_DATA.routes.filter(function(r){\n'
        '    var n=MAP_DATA.nodes[r.to]||{};\n'
        '    var ip=(n.ip||\'\').toLowerCase();\n'
        '    if(r.to.toLowerCase().indexOf(q)<0 && ip.indexOf(q)<0) return false;\n'
        '    if(f===\'low\'&&r.ms>=3) return false;\n'
        '    if(f===\'med\'&&(r.ms<3||r.ms>=7)) return false;\n'
        '    if(f===\'high\'&&r.ms<7) return false;\n'
        '    return true;\n'
        '  });\n'
        '  var best={};\n'
        '  items.forEach(function(r){if(!best[r.to]||r.ms<best[r.to].ms) best[r.to]=r;});\n'
        '  items=Object.values(best).sort(function(a,b){return a.ms-b.ms;});\n'
        '  document.getElementById(\'node-list\').innerHTML=items.map(function(r){\n'
        '    var n=MAP_DATA.nodes[r.to]||{};\n'
        '    var intl=isIntl(r.to);\n'
        '    return \'<div class="node-item\'+(selectedNode===r.to?\' selected\':\'\')+\'" onclick="selectNode(\\\'\'+ r.to +\'\\\')">\'\n'
        '      +\'<div class="node-dot \'+(intl?\'intl\':\'customer\')+\'"></div>\'\n'
        '      +\'<div style="flex:1;min-width:0"><div class="node-name">\'+r.to+\'</div>\'\n'
        '      +\'<div class="node-ip">\'+(n.ip||\'\')+\'</div></div>\'\n'
        '      +\'<span class="node-latency \'+(r.ms<3?\'\':r.ms<7?\'med\':\'high\')+\'">\'+r.ms.toFixed(2)+\'ms</span></div>\';\n'
        '  }).join(\'\');\n'
        '  var tot=items.reduce(function(s,r){return s+r.ms;},0);\n'
        '  document.getElementById(\'stat-routes\').textContent=items.length;\n'
        '  document.getElementById(\'stat-nodes\').textContent=Object.keys(MAP_DATA.nodes).length;\n'
        '  document.getElementById(\'stat-avg\').textContent=items.length?(tot/items.length).toFixed(1)+\'ms\':\'-\';\n'
        '}\n\n'
        'function filterNodes(){currentSearch=document.getElementById(\'search-box\').value;buildNodeList();}\n'
        'function setFilter(f,btn){\n'
        '  currentFilter=f;\n'
        '  document.querySelectorAll(\'.filter-btn\').forEach(function(b){b.classList.remove(\'active\');});\n'
        '  btn.classList.add(\'active\'); buildNodeList(); drawRoutes(f);\n'
        '}\n\n'
        'function selectNode(name){\n'
        '  selectedNode=name; buildNodeList();\n'
        '  var n=MAP_DATA.nodes[name];\n'
        '  if(n) map.flyTo([n.lat,n.lng],7,{duration:1.2});\n'
        '  if(markers[name]) markers[name].openPopup();\n'
        '  showDetail(name);\n'
        '  document.querySelectorAll(\'.node-item\').forEach(function(el){\n'
        '    if(el.querySelector(\'.node-name\').textContent===name)\n'
        '      el.scrollIntoView({behavior:\'smooth\',block:\'nearest\'});\n'
        '  });\n'
        '}\n\n'
        'function showDetail(name){\n'
        '  document.getElementById(\'detail-name\').textContent=name;\n'
        '  var node=MAP_DATA.nodes[name]||{}, nr=MAP_DATA.node_routes[name]||[];\n'
        '  var minMs=nr.length?Math.min.apply(null,nr.map(function(r){return r.ms;})):0;\n'
        '  var maxMs=nr.length?Math.max.apply(null,nr.map(function(r){return r.ms;})):0;\n'
        '  document.getElementById(\'detail-content\').innerHTML=\n'
        '    \'<div class="detail-stat-row">\'\n'
        '    +\'<div class="detail-stat"><div class="val">\'+minMs.toFixed(2)+\'</div><div class="lbl">MIN MS</div></div>\'\n'
        '    +\'<div class="detail-stat"><div class="val">\'+maxMs.toFixed(2)+\'</div><div class="lbl">MAX MS</div></div>\'\n'
        '    +\'<div class="detail-stat"><div class="val">\'+nr.length+\'</div><div class="lbl">PATHS</div></div>\'\n'
        '    +\'<div class="detail-stat"><div class="val">\'+(node.out||0)+\'</div><div class="lbl">OUTBOUND</div></div>\'\n'
        '    +\'</div>\'\n'
        '    +\'<div class="detail-label">IP Address</div>\'\n'
        '    +\'<div class="detail-ip">\'+(node.ip||\'N/A\')+\'</div>\'\n'
        '    +\'<div class="detail-label">Coordinates</div>\'\n'
        '    +\'<div class="detail-value">\'+(node.lat||0).toFixed(4)+\', \'+(node.lng||0).toFixed(4)+\'</div>\'\n'
        '    +(nr.length\n'
        '      ?\'<div class="detail-label">IP Trail Paths</div>\'+nr.slice(0,5).map(function(r){\n'
        '          var hops=r.ip_path?r.ip_path.split(\' -> \'):[];\n'
        '          var badges=hops.map(function(h,i){\n'
        '            var ip=h.split(\'(\')[0];\n'
        '            var nd=h.match(/\\(([^)]+)\\)/);\n'
        '            return (i>0?\'<span class="hop-arrow">&#8594;</span>\':\'\')\n'
        '              +\'<span class="ip-badge" title="\'+(nd?nd[1]:ip)+\'">\'+ip+\'</span>\';\n'
        '          }).join(\'\');\n'
        '          return \'<div class="route-item">\'\n'
        '            +\'<div style="display:flex;justify-content:space-between;margin-bottom:3px">\'\n'
        '            +\'<span style="font-size:10px;color:#7ec8f8">&larr; \'+r.from+\'</span>\'\n'
        '            +\'<span style="font-size:10px;color:\'+latencyColor(r.ms)+\';font-weight:bold">\'+r.ms.toFixed(3)+\' ms</span>\'\n'
        '            +\'</div>\'\n'
        '            +\'<div style="font-size:9px;color:#5a7fa0;margin-bottom:4px">\'\n'
        '            +\'Src: <span style="color:#4fc3f7">\'+r.src_ip+\'</span>\'\n'
        '            +\' &nbsp; Dst: <span style="color:#4fc3f7">\'+r.dest_ip+\'</span></div>\'\n'
        '            +\'<div style="display:flex;flex-wrap:wrap;gap:2px;align-items:center">\'+badges+\'</div></div>\';\n'
        '        }).join(\'\')\n'
        '      :\'<div class="detail-value" style="color:#5a7fa0">No route data</div>\');\n'
        '  document.getElementById(\'detail-panel\').classList.add(\'visible\');\n'
        '}\n\n'
        'function closeDetail(){\n'
        '  document.getElementById(\'detail-panel\').classList.remove(\'visible\');\n'
        '  selectedNode=null; buildNodeList();\n'
        '}\n\n'
        'var rc=L.control({position:\'topright\'});\n'
        'rc.onAdd=function(){\n'
        '  var d=L.DomUtil.create(\'div\');\n'
        '  d.innerHTML=\'<button onclick="map.setView([38.5,-95.0],4)" \'\n'
        '    +\'style="background:#0d1b2e;color:#4fc3f7;border:1px solid #1e3a6e;\'\n'
        '    +\'padding:6px 10px;cursor:pointer;font-family:monospace;font-size:11px;border-radius:3px">\'\n'
        '    +\'&#8962; RESET</button>\';\n'
        '  return d;\n'
        '};\n'
        'rc.addTo(map);\n\n'
        'drawRoutes(\'all\'); drawMarkers(); buildNodeList();\n'
        '</script>\n'
        '</body>\n'
        '</html>\n'
    )

    html = html.replace("MAP_DATA_PLACEHOLDER", data_json)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    size_kb = Path(output_path).stat().st_size / 1024
    print(f"\n  Done!  ->  {output_path}  ({size_kb:.0f} KB)")
    print("    Open in Edge — no server needed.")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Generate IP Trail ant-trail map from XLSX flow data"
    )
    parser.add_argument("--ip",      default="Complete_Paths_IP_Enriched.xlsx",
                        help="IP-enriched flow paths (default: Complete_Paths_IP_Enriched.xlsx)")
    parser.add_argument("--coords",  default="master_dataframe_long.xlsx",
                        help="Router coordinates (default: master_dataframe_long.xlsx)")
    parser.add_argument("--routers", default="master_dataframe_3.xlsx",
                        help="Router loopback IPs (default: master_dataframe_3.xlsx)")
    parser.add_argument("--out",     default="DAI_AntTrail_IP_Trail.html",
                        help="Output HTML (default: DAI_AntTrail_IP_Trail.html)")
    args = parser.parse_args()

    for f in [args.ip, args.coords, args.routers]:
        if not Path(f).exists():
            print(f"ERROR: File not found: {f}")
            sys.exit(1)

    print("=" * 60)
    print("  DAI IP Trail Map Generator")
    print("=" * 60)

    router_coords = load_router_coords(args.coords)          # Step 1
    router_ips    = load_router_ips(args.routers)            # Step 2
    flows, node_ip = load_ip_flows(args.ip)                  # Step 3
    map_data      = build_map_data(flows, node_ip,           # Step 4
                                   router_ips, router_coords)
    write_html(map_data, args.out)                           # Step 5


if __name__ == "__main__":
    main()
