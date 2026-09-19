"""
map.py -- Plot every mapped public toilet on an interactive folium map.

Reads coordinates straight out of toilets.db and writes toilets_map.html with
two switchable layers and a filter panel:

  * Clustered markers -- zoom in and the clusters break apart into individual
    facilities, each with a popup listing its amenities.
  * Heatmap -- density at a glance, which is the more useful view when the
    whole continent is on screen.
  * Filter panel -- search by name/town, pick a state, and tick any
    combination of amenities (AND-ed together). Runs entirely in the browser.

Two ways to narrow what gets plotted:

  * In the browser, with the panel, after the fact.
  * At build time, with --where, using any SQL condition over the toilets
    table. Handy for turning a query from queries.sql into a map.

Usage
-----
    python map.py                       # all facilities
    python map.py --sample 5000         # a random subset, for a smaller file
    python map.py --state QLD           # one jurisdiction
    python map.py --where "Shower=1 AND PaymentRequired=0 AND OpeningHours LIKE '%24 hours%'"
    python map.py --out my_map.html
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sqlite3
import sys

import folium
from folium.plugins import FastMarkerCluster, HeatMap

DEFAULT_DB = "toilets.db"
DEFAULT_OUT = "toilets_map.html"

# Centre of the continent-ish, with a zoom that frames the mainland.
AUSTRALIA_CENTRE = (-25.6, 134.35)
AUSTRALIA_ZOOM = 4

# Custom cursors. The default arrow disappears against the pale basemap, so the
# map uses a black dot instead, switching to a ring over anything clickable.
# Both are inline SVGs (hotspot at the centre) with a thin white halo so they
# stay visible over dark markers too. '#' must be written as %23 in a data URI.
_DOT_CURSOR = (
    "url(\"data:image/svg+xml;utf8,"
    "<svg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 16 16'>"
    "<circle cx='8' cy='8' r='5' fill='%23111' stroke='%23fff' stroke-width='1.5'/>"
    "</svg>\") 8 8, auto"
)
_RING_CURSOR = (
    "url(\"data:image/svg+xml;utf8,"
    "<svg xmlns='http://www.w3.org/2000/svg' width='22' height='22' viewBox='0 0 22 22'>"
    "<circle cx='11' cy='11' r='7.5' fill='none' stroke='%23fff' stroke-width='4'/>"
    "<circle cx='11' cy='11' r='7.5' fill='none' stroke='%23111' stroke-width='2'/>"
    "<circle cx='11' cy='11' r='1.5' fill='%23111'/>"
    "</svg>\") 11 11, pointer"
)
# Marker categories. The three hues are the first three categorical slots of a
# palette validated for colour-vision-deficiency separation across all pairs,
# so any two pins are distinguishable regardless of the reader's vision.
PIN_COLOURS = {
    "acc": "#1baf7a",   # wheelchair accessible
    "std": "#2a78d6",   # not flagged accessible
    "pay": "#eb6834",   # payment required
}

# ---------------------------------------------------------------------------
# Basemap
#
# OpenStreetMap's raster tiles are a general-purpose reference map: every
# footpath, railway and POI icon, in saturated colour, and place names that
# only appear at high zoom. That is the wrong tool for "where am I, roughly,
# and where is the nearest toilet". A raster can't be restyled, so the map
# uses OpenFreeMap's *vector* tiles (free, no API key) rendered by MapLibre,
# starting from their Positron style and recolouring it in the browser to a
# Google-Maps-like palette: three soft colours with two shades each, roads in
# exactly two greys, railways hidden, footpaths and buildings only at street
# zoom, and dark place labels with a white halo from the country level down.
#
# If WebGL is unavailable or the style can't be fetched, the page falls back
# to Esri's Light Gray raster so there is always a map under the pins.
# ---------------------------------------------------------------------------
BASEMAP = {
    "land":        "#f7f7f4",   # base land
    "land_2":      "#efefeb",   # built-up areas
    "building":    "#e8e8e3",
    "green":       "#d5ead0",   # parks
    "green_2":     "#c9e3c4",   # woodland
    "water":       "#b3d3ee",
    "water_text":  "#4a7ab3",
    "road":        "#e4e4e1",   # minor roads and paths
    "road_2":      "#d0d0cc",   # major roads and motorways
    "boundary":    "#cfcfcb",
    "place_text":  "#2b2b2b",   # cities, towns, villages
    "area_text":   "#4d4d4d",   # suburbs and localities
    "street_text": "#737373",
    "halo":        "#ffffff",
}

# Per-layer overrides applied to the Positron style, keyed by its layer ids.
BASEMAP_PAINT = {
    "background":              {"background-color": BASEMAP["land"]},
    "landuse_residential":     {"fill-color": BASEMAP["land_2"]},
    "park":                    {"fill-color": BASEMAP["green"]},
    "landcover_wood":          {"fill-color": BASEMAP["green_2"]},
    "water":                   {"fill-color": BASEMAP["water"]},
    "waterway":                {"line-color": BASEMAP["water"]},
    "building":                {"fill-color": BASEMAP["building"], "fill-outline-color": BASEMAP["building"]},
    "aeroway-area":            {"fill-color": BASEMAP["land_2"]},
    "aeroway-runway":          {"line-color": BASEMAP["road"]},
    "aeroway-taxiway":         {"line-color": BASEMAP["road"]},
    "highway_path":            {"line-color": BASEMAP["road"]},
    "highway_minor":           {"line-color": BASEMAP["road"]},
    "highway_major_casing":    {"line-color": BASEMAP["road_2"]},
    "highway_major_inner":     {"line-color": BASEMAP["road_2"]},
    "highway_major_subtle":    {"line-color": BASEMAP["road_2"]},
    "highway_motorway_casing": {"line-color": BASEMAP["road_2"]},
    "highway_motorway_inner":  {"line-color": BASEMAP["road_2"]},
    "highway_motorway_subtle": {"line-color": BASEMAP["road_2"]},
    "highway_motorway_bridge_casing": {"line-color": BASEMAP["road_2"]},
    "highway_motorway_bridge_inner":  {"line-color": BASEMAP["road_2"]},
    "tunnel_motorway_casing":  {"line-color": BASEMAP["road_2"]},
    "tunnel_motorway_inner":   {"line-color": BASEMAP["road_2"]},
    "boundary_2":              {"line-color": BASEMAP["boundary"]},
    "boundary_3":              {"line-color": BASEMAP["boundary"]},
    "boundary_disputed":       {"line-color": BASEMAP["boundary"]},
    "water_name_point_label":  {"text-color": BASEMAP["water_text"], "text-halo-color": BASEMAP["halo"]},
    "water_name_line_label":   {"text-color": BASEMAP["water_text"], "text-halo-color": BASEMAP["halo"]},
    "waterway_line_label":     {"text-color": BASEMAP["water_text"], "text-halo-color": BASEMAP["halo"]},
    "highway-name-path":       {"text-color": BASEMAP["street_text"], "text-halo-color": BASEMAP["halo"]},
    "highway-name-minor":      {"text-color": BASEMAP["street_text"], "text-halo-color": BASEMAP["halo"], "text-halo-width": 1.5},
    "highway-name-major":      {"text-color": BASEMAP["street_text"], "text-halo-color": BASEMAP["halo"], "text-halo-width": 1.5},
    "airport":                 {"text-color": BASEMAP["area_text"], "text-halo-color": BASEMAP["halo"]},
    "label_other":             {"text-color": BASEMAP["area_text"], "text-halo-color": BASEMAP["halo"], "text-halo-width": 1.5},
    "label_village":           {"text-color": BASEMAP["place_text"], "text-halo-color": BASEMAP["halo"], "text-halo-width": 2},
    "label_town":              {"text-color": BASEMAP["place_text"], "text-halo-color": BASEMAP["halo"], "text-halo-width": 2},
    "label_city":              {"text-color": BASEMAP["place_text"], "text-halo-color": BASEMAP["halo"], "text-halo-width": 2},
    "label_city_capital":      {"text-color": BASEMAP["place_text"], "text-halo-color": BASEMAP["halo"], "text-halo-width": 2},
    "label_state":             {"text-color": BASEMAP["area_text"], "text-halo-color": BASEMAP["halo"], "text-halo-width": 2},
    "label_country_1":         {"text-color": BASEMAP["place_text"]},
    "label_country_2":         {"text-color": BASEMAP["place_text"]},
    "label_country_3":         {"text-color": BASEMAP["place_text"]},
}
BASEMAP_LAYOUT = {
    # Nothing below 12px on screen; Positron's suburb labels sit under that.
    "label_other":   {"text-size": 12},
    "label_village": {"text-size": 12.5},
}
BASEMAP_MINZOOM = {"highway_path": 15, "building": 15}   # street-level detail only
BASEMAP_HIDE = ["railway", "railway_dashline", "railway_service", "railway_service_dashline",
                "railway_transit", "railway_transit_dashline"]

BASEMAP_STYLE_URL = "https://tiles.openfreemap.org/styles/positron"
BASEMAP_ATTRIBUTION = (
    '&copy; <a href="https://openfreemap.org">OpenFreeMap</a> '
    '&copy; <a href="https://www.openmaptiles.org/">OpenMapTiles</a> '
    'Data &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
)
FALLBACK_TILES = "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_{layer}/MapServer/tile/{{z}}/{{y}}/{{x}}"
FALLBACK_ATTRIBUTION = "Tiles &copy; Esri &mdash; Esri, DeLorme, NAVTEQ"


def basemap_layer() -> str:
    """The scripts that put the vector basemap under the Leaflet map."""
    return f"""
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/maplibre-gl@4.7.1/dist/maplibre-gl.css">
<script defer src="https://cdn.jsdelivr.net/npm/maplibre-gl@4.7.1/dist/maplibre-gl.js"></script>
<script defer src="https://cdn.jsdelivr.net/npm/@maplibre/maplibre-gl-leaflet@0.0.22/leaflet-maplibre-gl.js"></script>
<script>
(function () {{
  var STYLE_URL = {json.dumps(BASEMAP_STYLE_URL)};
  var PAINT = {json.dumps(BASEMAP_PAINT)};
  var LAYOUT = {json.dumps(BASEMAP_LAYOUT)};
  var MINZOOM = {json.dumps(BASEMAP_MINZOOM)};
  var HIDE = {json.dumps(BASEMAP_HIDE)};
  var ATTRIBUTION = {json.dumps(BASEMAP_ATTRIBUTION)};
  var FALLBACK = {json.dumps(FALLBACK_TILES)};
  var FALLBACK_ATTRIBUTION = {json.dumps(FALLBACK_ATTRIBUTION)};

  function findMap() {{
    for (var k in window) {{
      if (k.indexOf('map_') === 0 && window[k] instanceof L.Map) return window[k];
    }}
    return null;
  }}

  function restyle(style) {{
    style.layers.forEach(function (layer) {{
      var id = layer.id;
      if (PAINT[id])  {{ layer.paint  = Object.assign({{}}, layer.paint  || {{}}, PAINT[id]); }}
      if (LAYOUT[id]) {{ layer.layout = Object.assign({{}}, layer.layout || {{}}, LAYOUT[id]); }}
      if (MINZOOM[id] !== undefined) {{ layer.minzoom = MINZOOM[id]; }}
      if (HIDE.indexOf(id) !== -1) {{
        layer.layout = Object.assign({{}}, layer.layout || {{}}, {{visibility: 'none'}});
      }}
    }});
    return style;
  }}

  function fallback(map, why) {{
    if (why) console.warn('Vector basemap unavailable (' + why + '); using raster fallback.');
    var opts = {{maxNativeZoom: 16, maxZoom: 19, attribution: FALLBACK_ATTRIBUTION}};
    L.tileLayer(FALLBACK.replace('{{layer}}', 'Base'), opts).addTo(map);
    L.tileLayer(FALLBACK.replace('{{layer}}', 'Reference'), opts).addTo(map);
  }}

  window.addEventListener('DOMContentLoaded', function () {{
    var map = findMap();
    if (!map) return;
    if (!window.maplibregl || !L.maplibreGL) return fallback(map, 'MapLibre did not load');

    fetch(STYLE_URL).then(function (r) {{
      if (!r.ok) throw new Error('style HTTP ' + r.status);
      return r.json();
    }}).then(function (style) {{
      var layer = L.maplibreGL({{style: restyle(style), attribution: ATTRIBUTION}});
      layer.addTo(map);
      // MapLibre throws asynchronously if WebGL is missing; catch that too.
      var gl = layer.getMaplibreMap && layer.getMaplibreMap();
      if (gl) gl.once('error', function (e) {{
        if (e && e.error && /webgl/i.test(String(e.error.message))) {{
          map.removeLayer(layer); fallback(map, e.error.message);
        }}
      }});
    }}).catch(function (err) {{ fallback(map, err.message); }});
  }});
}})();
</script>
"""


# Visual design of the map, applied through one stylesheet in the page head.
#
# The organising idea is *hierarchy*: the data has to sit visually above the
# basemap. The basemap above is soft and low-contrast on purpose; the pins get
# a dark outline plus a white halo so they read on any ground, and clusters
# are dark and sized by count. One sans-serif family throughout, no text below
# 12px, secondary text demoted with grey and letter-spaced caps.
THEME_CSS = f"""
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Source+Sans+3:ital,wght@0,400;0,600;0,700;1,400&display=swap">
<style>
  :root {{
    --font: "Source Sans 3", "Segoe UI", system-ui, -apple-system, sans-serif;
    --ink: #141414; --ink-2: #4a4a4a; --ink-3: #767676;
    --line: #e6e6e6; --surface: rgba(255,255,255,.97);
    --pin-acc: {PIN_COLOURS["acc"]}; --pin-std: {PIN_COLOURS["std"]}; --pin-pay: {PIN_COLOURS["pay"]};
  }}
  body {{ font-family: var(--font); }}

  /* Pins. Dark outline + white halo = visible on light, dark, green or blue ground. */
  .tf-pin {{
    width: 14px; height: 14px; border-radius: 50%;
    background: var(--c); border: 2px solid #fff;
    box-shadow: 0 0 0 1.5px rgba(0,0,0,.6), 0 2px 5px rgba(0,0,0,.35);
    transition: transform .12s ease;
  }}
  .tf-pin--acc {{ --c: var(--pin-acc); }}
  .tf-pin--std {{ --c: var(--pin-std); }}
  .tf-pin--pay {{ --c: var(--pin-pay); }}
  .tf-pin:hover {{ transform: scale(1.3); z-index: 1000 !important; }}

  /* Clusters: dark, bold, bigger for more. Reads as a different kind of thing
     from a pin, and the count is legible at a glance. */
  .tf-cluster {{ background: none; border: none; }}
  .tf-cluster > * {{
    width: 100%; height: 100%; border-radius: 50%; display: flex;
    align-items: center; justify-content: center;
    background: #141414; color: #fff; border: 2.5px solid #fff;
    box-shadow: 0 0 0 1.5px rgba(0,0,0,.35), 0 3px 8px rgba(0,0,0,.3);
    font: 700 13px/1 var(--font); font-variant-numeric: tabular-nums;
    transition: transform .12s ease;
  }}
  .tf-cluster--m > * {{ font-size: 14px; }}
  .tf-cluster--l > * {{ font-size: 15px; }}
  .tf-cluster:hover > * {{ transform: scale(1.08); }}

  /* Popup: name promoted, place and type demoted, one clear hours line. */
  .leaflet-popup-content-wrapper {{
    border-radius: 12px; box-shadow: 0 6px 24px rgba(0,0,0,.18); padding: 0;
  }}
  .leaflet-popup-content {{ margin: 14px 16px 14px; font: 14px/1.4 var(--font); color: var(--ink); min-width: 230px; }}
  .leaflet-popup-tip {{ box-shadow: none; }}
  .leaflet-popup-close-button {{ font-size: 20px; padding: 6px 8px 0 0; color: var(--ink-3) !important; }}
  .tf-pop-kicker {{ font-size: 12px; letter-spacing: .07em; text-transform: uppercase; color: var(--ink-3); font-weight: 600; }}
  .tf-pop-name {{ font-size: 17px; font-weight: 700; line-height: 1.2; margin: 2px 0 1px; display: flex; align-items: center; gap: 8px; }}
  .tf-pop-name i {{ flex: none; width: 10px; height: 10px; border-radius: 50%; background: var(--c); box-shadow: 0 0 0 1.5px rgba(0,0,0,.5); }}
  .tf-pop-where {{ color: var(--ink-2); }}
  .tf-pop-hours {{
    display: inline-block; margin: 8px 0 2px; padding: 3px 9px; border-radius: 999px;
    font-size: 12.5px; font-weight: 600; background: #f0f0f0; color: var(--ink-2);
  }}
  .tf-pop-hours--24 {{ background: #141414; color: #fff; }}
  .tf-pop-amen {{
    list-style: none; margin: 8px 0 0; padding: 8px 0 0; border-top: 1px solid var(--line);
    display: grid; grid-template-columns: 1fr 1fr; gap: 3px 12px; font-size: 13px; color: var(--ink-2);
  }}
  .tf-pop-amen li::before {{ content: "\\2713\\00a0"; color: var(--ink); font-weight: 700; }}
  .tf-pop-none {{ margin-top: 8px; padding-top: 8px; border-top: 1px solid var(--line); color: var(--ink-3); font-size: 13px; }}
  .tf-pop-link {{ display: inline-block; margin-top: 10px; font-size: 13px; font-weight: 600; color: var(--ink); text-decoration: none; border-bottom: 1.5px solid #bbb; }}
  .tf-pop-link:hover {{ border-color: var(--ink); }}

  /* Tooltip: dark label, no box outline, promoted so it wins over basemap text. */
  .leaflet-tooltip {{
    background: #141414; color: #fff; border: 0; border-radius: 6px;
    font: 600 12.5px/1.3 var(--font); padding: 4px 8px;
    box-shadow: 0 2px 8px rgba(0,0,0,.3);
  }}
  .leaflet-tooltip-top::before {{ border-top-color: #141414; }}
  .leaflet-tooltip-bottom::before {{ border-bottom-color: #141414; }}
  .leaflet-tooltip-left::before {{ border-left-color: #141414; }}
  .leaflet-tooltip-right::before {{ border-right-color: #141414; }}

  /* Controls restyled to match the panel. */
  .leaflet-control-layers, .leaflet-bar {{
    border: 0 !important; border-radius: 10px !important;
    box-shadow: 0 4px 16px rgba(0,0,0,.14) !important; background: var(--surface);
  }}
  .leaflet-control-layers-expanded {{ padding: 10px 14px 10px 12px; font: 13px/1.6 var(--font); color: var(--ink); }}
  .leaflet-control-layers-separator {{ border-color: var(--line); margin: 6px -14px 6px -12px; }}
  .leaflet-control-layers label {{ display: flex; align-items: center; gap: 6px; }}
  .leaflet-control-layers input {{ accent-color: #141414; margin: 0; }}
  .leaflet-bar a {{ border-bottom-color: var(--line) !important; color: var(--ink) !important; font-weight: 600; }}
  .leaflet-bar a:first-child {{ border-radius: 10px 10px 0 0 !important; }}
  .leaflet-bar a:last-child {{ border-radius: 0 0 10px 10px !important; }}
  .leaflet-control-scale-line {{ font: 11.5px/1.3 var(--font); color: var(--ink-2); border-color: #777; background: rgba(255,255,255,.75); }}
  .leaflet-control-attribution {{ font: 12px/1.4 var(--font); color: var(--ink-2); background: rgba(255,255,255,.85); padding: 2px 6px; }}
  .leaflet-control-attribution a {{ color: var(--ink); }}

  /* Cursors: a black dot on the map (the arrow vanished against the pale
     tiles) and a ring over anything clickable. Inline SVGs, hotspot centred. */
  html, body,
  .leaflet-container,
  .leaflet-container .leaflet-grab,
  .leaflet-dragging .leaflet-grab,
  .leaflet-dragging .leaflet-marker-draggable {{
    cursor: {_DOT_CURSOR} !important;
  }}
  .leaflet-container .leaflet-interactive,
  .leaflet-container .leaflet-marker-icon,
  .leaflet-container a,
  .leaflet-container button,
  .leaflet-container .leaflet-control-layers label,
  .leaflet-container .leaflet-popup-close-button {{
    cursor: {_RING_CURSOR} !important;
  }}
</style>
"""

# (column, popup label). The order also fixes the bit positions in the
# amenity bitmask handed to the browser, so don't reorder it casually.
AMENITIES = [
    ("Accessible", "Wheelchair accessible"),
    ("Ambulant", "Ambulant"),
    ("BabyChange", "Baby change"),
    ("ChangingPlaces", "Changing Places"),
    ("Shower", "Shower"),
    ("DrinkingWater", "Drinking water"),
    ("Parking", "Parking"),
    ("ParkingAccessible", "Accessible parking"),
    ("DumpPoint", "RV dump point"),
    ("SharpsDisposal", "Sharps disposal"),
    ("SanitaryDisposal", "Sanitary disposal"),
    ("KeyRequired", "Key required"),
    ("PaymentRequired", "Payment required"),
]


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=DEFAULT_DB, help="SQLite database (default: toilets.db)")
    parser.add_argument("--out", default=DEFAULT_OUT, help="output HTML file (default: toilets_map.html)")
    parser.add_argument("--state", help="restrict to one state, e.g. QLD")
    parser.add_argument("--where", metavar="SQL",
                        help="only plot rows matching this SQL condition, "
                             "e.g. \"Shower=1 AND OpeningHours LIKE '%%24 hours%%'\"")
    parser.add_argument("--sample", type=int, help="plot a random sample of N facilities")
    parser.add_argument("--no-markers", action="store_true", help="heatmap only")
    parser.add_argument("--no-heatmap", action="store_true", help="markers only")
    return parser.parse_args(argv)


def fetch_rows(db_path: str, state: str | None, where: str | None,
               sample: int | None) -> list[sqlite3.Row]:
    columns = ["Name", "Town", "State", "FacilityType", "OpeningHours",
               "Latitude", "Longitude", "URL"] + [c for c, _ in AMENITIES]

    sql = f"""
        SELECT {', '.join(columns)}
        FROM toilets
        WHERE Latitude IS NOT NULL
          AND Longitude IS NOT NULL
          AND Latitude BETWEEN -90 AND 90
          AND Longitude BETWEEN -180 AND 180
    """
    params: list[object] = []
    if state:
        sql += " AND UPPER(State) = UPPER(?)"
        params.append(state)
    if where:
        # A raw SQL fragment from the command line, against a read-only local
        # database the user already owns -- the same trust level as queries.sql.
        sql += f" AND ({where})"
    if sample:
        sql += " ORDER BY RANDOM() LIMIT ?"
        params.append(sample)

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(sql, params).fetchall()


def pack_row(row: sqlite3.Row) -> list:
    """
    Flatten one facility into the compact array the browser expects:

        [lat, lon, name, town, state, facility_type, hours, facility_id, bits]

    Pre-rendering 25,000 popups in Python produced a 48 MB HTML file. Shipping
    the fields instead and building each popup on demand in JavaScript gets the
    same map in a fraction of the size, so the bitmask below carries the
    amenity flags, one bit per entry in AMENITIES.
    """
    bits = 0
    for position, (column, _label) in enumerate(AMENITIES):
        if row[column] == 1:
            bits |= 1 << position

    facility_url = row["URL"] or ""
    facility_id = facility_url.rsplit("/", 1)[-1] if facility_url else ""

    return [
        round(row["Latitude"], 5),
        round(row["Longitude"], 5),
        row["Name"] or "",
        row["Town"] or "",
        row["State"] or "",
        row["FacilityType"] or "",
        row["OpeningHours"] or "",
        facility_id,
        bits,
    ]


def marker_callback() -> str:
    """The JavaScript that turns one packed row into a Leaflet marker."""
    labels = json.dumps([label for _column, label in AMENITIES])
    payment_bit = next(i for i, (c, _) in enumerate(AMENITIES) if c == "PaymentRequired")
    accessible_bit = next(i for i, (c, _) in enumerate(AMENITIES) if c == "Accessible")

    return f"""(function () {{
    var LABELS = {labels};
    var PAYMENT_BIT = {payment_bit}, ACCESSIBLE_BIT = {accessible_bit};
    // Every marker is registered here with its row so the filter panel can
    // re-evaluate the whole set without a round trip to Python.
    window.__toilets = [];

    function esc(text) {{
        return String(text).replace(/[&<>"']/g, function (c) {{
            return {{'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}}[c];
        }});
    }}

    function category(bits) {{
        return (bits & (1 << PAYMENT_BIT)) ? 'pay'
             : (bits & (1 << ACCESSIBLE_BIT)) ? 'acc' : 'std';
    }}

    // Icons are shared per category: Leaflet only needs one L.DivIcon instance
    // for every pin of the same kind.
    var ICONS = {{}};
    ['acc', 'std', 'pay'].forEach(function (cat) {{
        ICONS[cat] = L.divIcon({{
            className: 'tf-pin tf-pin--' + cat,
            iconSize: [18, 18], iconAnchor: [9, 9], popupAnchor: [0, -12], tooltipAnchor: [0, -10]
        }});
    }});

    function popup(row) {{
        var bits = row[8], cat = category(bits);
        var list = [];
        for (var i = 0; i < LABELS.length; i++) {{
            if (bits & (1 << i)) {{ list.push('<li>' + esc(LABELS[i]) + '</li>'); }}
        }}
        var amenities = list.length
            ? '<ul class="tf-pop-amen">' + list.join('') + '</ul>'
            : '<div class="tf-pop-none">No amenities flagged</div>';
        var kicker = [row[5], row[4]].filter(Boolean).map(esc).join(' \\u00b7 ');
        var is24 = /24 hours/i.test(row[6]);
        var hours = row[6]
            ? '<div class="tf-pop-hours' + (is24 ? ' tf-pop-hours--24' : '') + '">' + esc(row[6]) + '</div>'
            : '';
        var link = row[7]
            ? '<a class="tf-pop-link" target="_blank" rel="noopener" href="https://toiletmap.gov.au/facility/'
              + encodeURIComponent(row[7]) + '">View on toiletmap.gov.au \\u2197</a>'
            : '';
        return '<div class="tf-pop">'
             + '<div class="tf-pop-kicker">' + kicker + '</div>'
             + '<div class="tf-pop-name"><i class="tf-pin--' + cat + '"></i>' + esc(row[2] || 'Unnamed facility') + '</div>'
             + (row[3] ? '<div class="tf-pop-where">' + esc(row[3]) + '</div>' : '')
             + hours + amenities + link + '</div>';
    }}

    return function (row) {{
        var marker = L.marker(new L.LatLng(row[0], row[1]), {{
            icon: ICONS[category(row[8])], riseOnHover: true, keyboard: false
        }});
        marker.bindTooltip(esc(row[2] || 'Public toilet'), {{direction: 'top'}});
        marker.bindPopup(popup(row), {{maxWidth: 340}});
        window.__toilets.push([marker, row]);
        return marker;
    }};
}})()"""


# Cluster bubbles: dark, bold, three sizes by count. Counts of a thousand or
# more are abbreviated so the bubble never has to grow to fit its label.
CLUSTER_ICON_JS = """function (cluster) {
    var n = cluster.getChildCount();
    var size = n < 100 ? 'S' : n < 1000 ? 'M' : 'L';
    var px = {S: 34, M: 42, L: 52}[size];
    var label = n >= 10000 ? Math.round(n / 1000) + 'k'
              : n >= 1000  ? (n / 1000).toFixed(1).replace(/\\.0$/, '') + 'k'
              : String(n);
    return L.divIcon({
        html: '<div>' + label + '</div>',
        className: 'tf-cluster tf-cluster--' + size.toLowerCase(),
        iconSize: L.point(px, px)
    });
}"""


# Filters offered in the browser panel. "bit" = amenity flag must be set,
# "notbit" = must be clear, "hours" = OpeningHours mentions 24 hours.
# Combined with AND, which is the combination toiletmap.gov.au doesn't offer.
FILTERS = [
    ("Wheelchair accessible", "bit",    "Accessible"),
    ("Ambulant",              "bit",    "Ambulant"),
    ("Accessible parking",    "bit",    "ParkingAccessible"),
    ("Changing Places",       "bit",    "ChangingPlaces"),
    ("Baby change",           "bit",    "BabyChange"),
    ("Shower",                "bit",    "Shower"),
    ("Drinking water",        "bit",    "DrinkingWater"),
    ("Parking",               "bit",    "Parking"),
    ("RV dump point",         "bit",    "DumpPoint"),
    ("Open 24 hours",         "hours",  None),
    ("Free (no payment)",     "notbit", "PaymentRequired"),
    ("No key required",       "notbit", "KeyRequired"),
]


def filter_panel(rows: list[sqlite3.Row], cluster_name: str | None,
                 heat_name: str | None, where: str | None) -> str:
    """
    The floating filter card plus the script that drives it.

    Filtering happens entirely in the browser: every marker was registered in
    window.__toilets by the cluster callback, so a change to any control just
    re-evaluates the packed rows, swaps the cluster's layer set and hands the
    heatmap a new point list. No server, no reload.
    """
    bit_index = {column: i for i, (column, _label) in enumerate(AMENITIES)}
    filters_json = json.dumps([
        {"id": f"f{i}", "label": label, "kind": kind,
         "mask": (1 << bit_index[column]) if column else 0}
        for i, (label, kind, column) in enumerate(FILTERS)
    ])
    states_json = json.dumps(sorted({r["State"] for r in rows if r["State"]}))
    prefilter = (
        f"<div class='tf-note'>Pre-filtered by <code>{html.escape(where)}</code></div>"
        if where else ""
    )

    return f"""
<style>
  /* The card sizes itself to its widest row (width: max-content), so every
     label sits on one line and there is nothing to scroll. Elements that would
     otherwise stretch to their single-line length (the note, the inputs) are
     told to follow the card's width instead of dictating it. */
  #tf-panel {{
    position: fixed; top: 12px; left: 12px; z-index: 9999;
    width: max-content; max-width: calc(100vw - 24px);
    background: rgba(255,255,255,.96); backdrop-filter: blur(8px);
    border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,.14);
    font: 13.5px/1.45 var(--font); color: var(--ink);
    overflow: hidden;
  }}
  #tf-head {{
    display: flex; align-items: center; justify-content: space-between; gap: 16px;
    padding: 11px 14px; border-bottom: 1px solid #ececec; user-select: none;
  }}
  #tf-head b {{ font-size: 14px; font-weight: 600; }}
  #tf-count {{ font-size: 12px; color: #666; margin-top: 1px; }}
  #tf-toggle {{
    border: 0; background: #111; color: #fff; font: inherit; font-size: 15px;
    width: 34px; height: 30px; border-radius: 8px; line-height: 1; flex: none;
  }}
  #tf-toggle:hover {{ background: #333; }}
  #tf-body {{ padding: 12px 14px 10px; max-height: calc(100vh - 96px); overflow: hidden auto; }}
  /* Minimised: a small square holding only the toggle, so the map is clear. */
  #tf-panel.min {{ width: 46px; height: 46px; }}
  #tf-panel.min #tf-head {{ padding: 0; border: 0; height: 100%; justify-content: center; }}
  #tf-panel.min #tf-title, #tf-panel.min #tf-body {{ display: none; }}
  #tf-panel.min #tf-toggle {{ width: 34px; height: 34px; }}
  .tf-grid {{ display: grid; grid-template-columns: max-content max-content; gap: 5px 18px; margin: 4px 0 10px; }}
  .tf-note,
  #tf-body input[type=text],
  #tf-body select {{ width: 0; min-width: 100%; }}
  #tf-body input[type=text], #tf-body select {{
    box-sizing: border-box; padding: 7px 10px; margin-bottom: 8px;
    border: 1px solid #ddd; border-radius: 8px; font: inherit; background: #fff;
  }}
  #tf-body input[type=text]:focus, #tf-body select:focus {{
    outline: none; border-color: #111;
  }}
  .tf-grid label {{ display: flex; align-items: center; gap: 6px; white-space: nowrap; }}
  .tf-grid input {{ accent-color: #111; margin: 0; }}
  .tf-row {{ display: flex; align-items: center; justify-content: space-between; }}
  #tf-reset {{
    border: 1px solid #ddd; background: #fff; font: inherit; font-size: 12px;
    padding: 4px 10px; border-radius: 999px; color: #333;
  }}
  #tf-reset:hover {{ border-color: #111; color: #111; }}
  .tf-section {{
    font-size: 12px; font-weight: 600; letter-spacing: .07em; text-transform: uppercase;
    color: var(--ink-3); margin: 2px 0 6px;
  }}
  .tf-legend {{ margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--line); color: var(--ink-2); font-size: 13px; }}
  .tf-legend > div {{ display: flex; align-items: center; gap: 9px; margin: 3px 0; }}
  .tf-legend .tf-pin {{ flex: none; width: 10px; height: 10px; border-width: 1.5px; box-shadow: 0 0 0 1.2px rgba(0,0,0,.6); margin: 0 2px; }}
  .tf-legend .tf-cluster {{ flex: none; width: 18px; height: 18px; }}
  .tf-legend .tf-cluster > * {{ font-size: 9px; border-width: 1.5px; }}
  .tf-note {{ font-size: 12.5px; color: var(--ink-2); margin-bottom: 8px; }}
  .tf-note code {{ background: #f3f3f3; padding: 1px 5px; border-radius: 4px; font-size: 12px; }}
  /* Native cursors inside the panel: the dot is for the map, not for forms. */
  #tf-panel input[type=text], #tf-panel select {{ cursor: text !important; }}
  #tf-panel select {{ cursor: pointer !important; }}
  #tf-panel label, #tf-panel button, #tf-panel input[type=checkbox] {{ cursor: pointer !important; }}
</style>

<div id="tf-panel">
  <div id="tf-head">
    <div id="tf-title"><b>Public toilets</b><div id="tf-count">&nbsp;</div></div>
    <button id="tf-toggle" type="button" title="Minimise filters">&#10231;</button>
  </div>
  <div id="tf-body">
    {prefilter}
    <input id="tf-q" type="text" placeholder="Search name or town" autocomplete="off">
    <select id="tf-state"><option value="">All states</option></select>
    <div class="tf-section">Amenities</div>
    <div class="tf-grid" id="tf-flags"></div>
    <div class="tf-row">
      <span style="color:var(--ink-3);font-size:12px">Filters combine with AND</span>
      <button id="tf-reset" type="button">Reset</button>
    </div>
    <div class="tf-legend">
      <div class="tf-section">Legend</div>
      <div><span class="tf-pin tf-pin--acc"></span>Wheelchair accessible</div>
      <div><span class="tf-pin tf-pin--std"></span>Not flagged accessible</div>
      <div><span class="tf-pin tf-pin--pay"></span>Payment required</div>
      <div><span class="tf-cluster"><span>12</span></span>Facilities grouped &mdash; zoom in or click</div>
    </div>
  </div>
</div>

<script>
(function () {{
  var FILTERS = {filters_json};
  var STATES = {states_json};
  var CLUSTER = {json.dumps(cluster_name)};
  var HEAT = {json.dumps(heat_name)};

  function ready(fn) {{
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', fn);
    else fn();
  }}

  ready(function () {{
    var panel = document.getElementById('tf-panel');
    var all = window.__toilets || [];          // [[marker, row], ...]
    var cluster = CLUSTER ? window[CLUSTER] : null;
    var heat = HEAT ? window[HEAT] : null;
    var total = all.length;

    var q = document.getElementById('tf-q');
    var stateSel = document.getElementById('tf-state');
    var flags = document.getElementById('tf-flags');
    var count = document.getElementById('tf-count');

    STATES.forEach(function (s) {{
      var o = document.createElement('option'); o.value = s; o.textContent = s; stateSel.appendChild(o);
    }});
    FILTERS.forEach(function (f) {{
      var l = document.createElement('label');
      var c = document.createElement('input'); c.type = 'checkbox'; c.id = f.id;
      l.appendChild(c); l.appendChild(document.createTextNode(f.label));
      flags.appendChild(l);
    }});

    if (!total) {{                                // heatmap-only build: nothing to filter
      document.getElementById('tf-body').querySelectorAll('input, select, .tf-row').forEach(function (el) {{ el.style.display = 'none'; }});
      flags.style.display = 'none';
      count.textContent = 'Heatmap only -- rerun map.py without --no-markers to filter';
      return;
    }}

    function fmt(n) {{ return n.toLocaleString(); }}

    function matches(row, needle, state, active) {{
      if (state && row[4] !== state) return false;
      if (needle && (row[2] + ' ' + row[3]).toLowerCase().indexOf(needle) === -1) return false;
      var bits = row[8];
      for (var i = 0; i < active.length; i++) {{
        var f = active[i];
        if (f.kind === 'bit'    && !(bits & f.mask)) return false;
        if (f.kind === 'notbit' &&  (bits & f.mask)) return false;
        if (f.kind === 'hours'  && !/24 hours/i.test(row[6])) return false;
      }}
      return true;
    }}

    function apply() {{
      var needle = q.value.trim().toLowerCase();
      var state = stateSel.value;
      var active = FILTERS.filter(function (f) {{ return document.getElementById(f.id).checked; }});
      var markers = [], points = [];
      for (var i = 0; i < all.length; i++) {{
        var row = all[i][1];
        if (matches(row, needle, state, active)) {{
          markers.push(all[i][0]);
          points.push([row[0], row[1]]);
        }}
      }}
      if (cluster) {{ cluster.clearLayers(); cluster.addLayers(markers); }}
      if (heat && heat.setLatLngs) {{ heat.setLatLngs(points); }}
      count.textContent = markers.length === total
        ? fmt(total) + ' facilities'
        : 'Showing ' + fmt(markers.length) + ' of ' + fmt(total);
    }}

    var timer = null;
    q.addEventListener('input', function () {{ clearTimeout(timer); timer = setTimeout(apply, 180); }});
    stateSel.addEventListener('change', apply);
    flags.addEventListener('change', apply);
    document.getElementById('tf-reset').addEventListener('click', function () {{
      q.value = ''; stateSel.value = '';
      FILTERS.forEach(function (f) {{ document.getElementById(f.id).checked = false; }});
      apply();
    }});
    // Full panel by default; minimised is a small square with just the toggle.
    // The choice is remembered per browser. Phones start minimised.
    var MIN_KEY = 'toiletmap.panel.min';
    var toggle = document.getElementById('tf-toggle');
    function setMin(on, remember) {{
      panel.classList.toggle('min', on);
      toggle.title = on ? 'Show filters' : 'Minimise filters';
      if (remember) {{
        try {{ localStorage.setItem(MIN_KEY, on ? '1' : '0'); }} catch (e) {{ /* private mode etc. */ }}
      }}
    }}
    toggle.addEventListener('click', function () {{
      setMin(!panel.classList.contains('min'), true);
    }});
    var startMin = window.innerWidth < 560;
    try {{
      var saved = localStorage.getItem(MIN_KEY);
      if (saved !== null) startMin = saved === '1';
    }} catch (e) {{}}
    setMin(startMin, false);

    count.textContent = fmt(total) + ' facilities';
  }});
}})();
</script>
"""


def build_map(rows: list[sqlite3.Row], args: argparse.Namespace) -> folium.Map:
    if args.state and rows:
        centre = (
            sum(r["Latitude"] for r in rows) / len(rows),
            sum(r["Longitude"] for r in rows) / len(rows),
        )
        zoom = 6
    else:
        centre, zoom = AUSTRALIA_CENTRE, AUSTRALIA_ZOOM

    # No raster tiles: the basemap is a vector style added by BASEMAP_JS once
    # the page loads (see basemap_layer). Zoom buttons go top-right so the
    # filter panel can have the top-left corner.
    # maxZoom has to be on the map itself: markercluster needs it and, with no
    # tile layer, nothing else supplies it. (folium's max_zoom only reaches a
    # tile layer, hence the camelCase Leaflet option.)
    fmap = folium.Map(location=centre, zoom_start=zoom, tiles=None, maxZoom=19,
                      control_scale=True, zoom_control="topright")
    fmap.get_root().header.add_child(folium.Element(THEME_CSS))
    fmap.get_root().html.add_child(folium.Element(basemap_layer()))

    heat_name = cluster_name = None

    if not args.no_heatmap:
        heat_layer = folium.FeatureGroup(name="Heatmap", show=args.no_markers)
        heat = HeatMap(
            [(r["Latitude"], r["Longitude"]) for r in rows],
            radius=9, blur=13, min_opacity=0.3,
            # One hue, light to dark: density is a magnitude, not a category.
            gradient={0.15: "#cde2fb", 0.4: "#86b6ef", 0.65: "#3987e5", 0.85: "#1c5cab", 1.0: "#0d366b"},
        )
        heat.add_to(heat_layer)
        heat_layer.add_to(fmap)
        heat_name = heat.get_name()

    if not args.no_markers:
        cluster = FastMarkerCluster(
            data=[pack_row(row) for row in rows],
            callback=marker_callback(),
            icon_create_function=CLUSTER_ICON_JS,
            name="Facilities",
            disableClusteringAtZoom=14,
            chunkedLoading=True,
            maxClusterRadius=70,
            # The hover hull, restyled to match the moving-coverage layer but
            # in the palette blue so the one you're pointing at stands out.
            polygonOptions={"className": "tf-hull", "color": PIN_COLOURS["std"],
                            "weight": 1.5, "opacity": 0.8, "fillOpacity": 0.1},
        )
        cluster.add_to(fmap)
        cluster_name = cluster.get_name()

    control = folium.LayerControl(collapsed=False)
    control.add_to(fmap)
    fmap.get_root().html.add_child(
        folium.Element(filter_panel(rows, cluster_name, heat_name, args.where))
    )
    return fmap


def main(argv: list[str]) -> int:
    args = parse_args(argv)

    if args.no_markers and args.no_heatmap:
        print("Nothing to draw: --no-markers and --no-heatmap cancel each other out.")
        return 1
    if not os.path.exists(args.db):
        print(f"Database {args.db!r} not found. Run: python load_data.py")
        return 1

    try:
        rows = fetch_rows(args.db, args.state, args.where, args.sample)
    except sqlite3.Error as exc:
        print(f"Could not run the --where condition: {exc}")
        print("Column names are as printed by load_data.py, e.g. Shower=1 AND PaymentRequired=0")
        return 1
    if not rows:
        print("No facilities with usable coordinates matched.")
        return 1

    scope = f" in {args.state.upper()}" if args.state else ""
    scope += f" where {args.where}" if args.where else ""
    print(f"Plotting {len(rows):,} facilities{scope}")
    fmap = build_map(rows, args)
    fmap.save(args.out)
    size_mb = os.path.getsize(args.out) / 1e6
    print(f"Wrote {args.out} ({size_mb:.1f} MB) -- open it in a browser.")
    if size_mb > 25:
        print("Tip: --sample 5000 produces a much lighter file if the page feels sluggish.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
