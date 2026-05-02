#!/usr/bin/env python3
"""
fetch_live_data.py
Fetches BOM state warning XML feeds and state incident feeds, writes live_data.json
Run by GitHub Actions every 15 minutes — no CORS restrictions server-side.

Incident feeds currently active:
  QLD — publiccontent-gis-psba-qld-gov-au.s3.amazonaws.com  (JSON)
  SA  — data.eso.sa.gov.au/prod/cfs/criimson              (JSON)
"""

import requests
import xml.etree.ElementTree as ET
import json
import re
from datetime import datetime, timezone

# BOM state XML warning summary feeds
# Each feed lists only currently active warnings for that state
BOM_WARNING_FEEDS = {
    "NSW": "http://www.bom.gov.au/fwo/IDZ00054.warnings_nsw.xml",
    "VIC": "http://www.bom.gov.au/fwo/IDZ00059.warnings_vic.xml",
    "QLD": "http://www.bom.gov.au/fwo/IDZ00056.warnings_qld.xml",
    "SA":  "http://www.bom.gov.au/fwo/IDZ00057.warnings_sa.xml",
    "WA":  "http://www.bom.gov.au/fwo/IDZ00060.warnings_wa.xml",
    "TAS": "http://www.bom.gov.au/fwo/IDZ00058.warnings_tas.xml",
    "NT":  "http://www.bom.gov.au/fwo/IDZ00055.warnings_nt.xml",
    "ACT": "http://www.bom.gov.au/fwo/IDZ00054.warnings_nsw.xml",  # ACT included in NSW feed
}

# NSW RFS CAP-AU alert zones feed — polygon warning areas for active incidents
NSW_ALERT_ZONES_URL = "https://www.rfs.nsw.gov.au/feeds/IncidentAlerts.xml"

# Titles to exclude — routine summaries, not emergency warnings
EXCLUDE_TITLES = [
    "marine wind warning summary",  # daily routine marine summaries
    "wind warning summary",
    "coastal wind warning summary",
]

# Warning type classification based on title keywords
def classify_warning(title):
    t = title.lower()
    if "cyclone" in t or "tropical" in t:
        return "cyclone"
    if "fire weather" in t or "fire danger" in t:
        return "fire_weather"
    if "heatwave" in t or "heat wave" in t or "extreme heat" in t:
        return "heatwave"
    if "severe thunderstorm" in t or "thunderstorm" in t:
        return "thunderstorm"
    if "severe weather" in t:
        return "severe_weather"
    if "flood" in t:
        return "flood"
    if "wind" in t or "gale" in t or "marine" in t:
        return "wind"
    return "other"

def is_excluded(title):
    t = title.lower()
    return any(ex in t for ex in EXCLUDE_TITLES)

def extract_pid_from_link(text):
    """Extract product ID from BOM warning link URL or text.
    Handles both:
      http://www.bom.gov.au/products/IDN21000.shtml  -> IDN21000
      http://www.bom.gov.au/qld/warnings/flood/diamantina-river.shtml/IDQ20865 -> IDQ20865
      https://www.bom.gov.au/warning/flood-warning/IDQ20865 -> IDQ20865
    """
    # Match product ID pattern anywhere in the text (path segment, bare, or with extension)
    m = re.search(r'(ID[A-Z]\d{5,6})(?:\.shtml|\.txt|/|$|\b)', text)
    if m:
        return m.group(1)
    return ""

def parse_bom_xml(xml_text, state):
    """Parse BOM warnings XML feed — RSS 2.0 format with <item> entries."""
    warnings = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        print(f"  XML parse error for {state}: {e}")
        return warnings

    # Handle RSS 2.0 namespace variations
    ns = {"atom": "http://www.w3.org/2005/Atom"}

    # Find all <item> elements (RSS) or <entry> elements (Atom)
    items = root.findall(".//item") or root.findall(".//entry")

    for item in items:
        # Extract fields — try both direct tag and with namespace
        def get(tag):
            el = item.find(tag)
            return el.text.strip() if el is not None and el.text else ""

        title = get("title")
        link  = get("link") or get("guid")
        desc  = get("description") or get("summary")
        pub   = get("pubDate") or get("updated") or get("published")

        if not title:
            continue

        # Skip routine summaries (marine wind summaries etc)
        if is_excluded(title):
            continue

        # Extract product ID from link URL or text
        pid = extract_pid_from_link(link + " " + desc)
        if not pid:
            # Try title text as fallback
            m = re.search(r'\b(ID[A-Z]\d{5,6})\b', title)
            pid = m.group(1) if m else ""

        # Skip non-warning items (e.g. "No warnings current" placeholder entries)
        if re.search(r'no\s+\w+\s+warning|no warnings', title.lower()):
            continue
        if title.lower().strip() in ("warnings", "weather warnings", ""):
            continue

        warn_type = classify_warning(title)

        # Determine actual state from product ID prefix (ACT warnings come via NSW feed)
        actual_state = state
        if pid:
            prefix_map = {
                "IDN": "NSW", "IDV": "VIC", "IDQ": "QLD",
                "IDS": "SA",  "IDW": "WA",  "IDT": "TAS",
                "IDD": "NT"
            }
            prefix = pid[:3]
            if prefix in prefix_map:
                actual_state = prefix_map[prefix]
            # ACT RFS products start with IDN but state is ACT
            if "capital territory" in title.lower() or "act" in title.lower():
                actual_state = "ACT"

        warnings.append({
            "pid":     pid,
            "title":   title,
            "type":    warn_type,
            "state":   actual_state,
            "link":    link,
            "desc":    desc[:500] if desc else "",
            "issued":  pub,
        })

    return warnings

def fetch_state(state, url):
    """Fetch and parse one state's warning feed."""
    try:
        r = requests.get(url, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (compatible; WeatherMap/1.0)"
        })
        r.raise_for_status()
        warnings = parse_bom_xml(r.text, state)
        print(f"  {state}: {len(warnings)} active warnings")
        return warnings, None
    except requests.RequestException as e:
        print(f"  {state}: fetch failed — {e}")
        return [], str(e)

# ── INCIDENT FEEDS ──────────────────────────────────────────────────────────

INCIDENT_FEEDS = {
    "nsw": {
        "url": "https://www.rfs.nsw.gov.au/feeds/majorIncidents.json",
        "label": "NSW RFS",
        "source_url": "https://www.rfs.nsw.gov.au/fire-information/fires-near-me",
        "agency": "RFS",
    },
    "vic": {
        "url": "https://data.emergency.vic.gov.au/Show?pageId=getIncidentJSON",
        "label": "VIC EMV",
        "source_url": "https://www.emergency.vic.gov.au/respond/",
        "agency": "EMV",
    },
    "qld": {
        "url": "https://publiccontent-gis-psba-qld-gov-au.s3.amazonaws.com/content/Feeds/BushfireCurrentIncidents/bushfireAlert.json",
        "label": "QLD QFD",
        "source_url": "https://www.fire.qld.gov.au/Current-Incidents",
        "agency": "QFD",
    },
    "sa": {
        "url": "https://data.eso.sa.gov.au/prod/cfs/criimson/cfs_current_incidents.json",
        "label": "SA CFS",
        "source_url": "https://www.cfs.sa.gov.au/home/warnings-and-incidents/",
        "agency": "CFS",
    },
}

# Incident types to exclude — planned/prescribed burns are not active emergencies
EXCLUDE_INC_TYPES = [
    "planned burn", "hazard reduction", "prescribed burn", "burn off",
    "controlled burn", "fuel reduction burn", "back burn", "hazard reduction burn",
]

# Statuses that mean the incident is resolved — exclude these
EXCLUDE_INC_STATUSES = [
    "under control", "patrol", "monitored", "no longer exists", "safe",
]


def normalise_alert_level(raw):
    """Normalise varied alert level strings to standard AU levels."""
    r = (raw or "").lower()
    if "emergency" in r:
        return "Emergency Warning"
    if "watch" in r:
        return "Watch and Act"
    if "advice" in r or r == "1":
        return "Advice"
    if "information" in r:
        return "Information"  # QFD monitoring notices — filtered out downstream
    if raw:
        return raw.strip()
    return ""


def parse_compact_timestamp(raw):
    """Parse YYYYMMDDHHmmss compact UTC timestamp → ISO 8601 string."""
    s = str(raw or "").strip()
    if re.match(r'^\d{14}$', s):
        return (f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
                f"T{s[8:10]}:{s[10:12]}:{s[12:14]}Z")
    return s  # Return as-is if not compact format


def is_excluded_incident(inc_type, inc_status):
    """Return True if an incident should be excluded (burn, resolved, etc)."""
    t = (inc_type or "").lower()
    s = (inc_status or "").lower()
    if any(x in t for x in EXCLUDE_INC_TYPES):
        return True
    if any(s == x or x in s for x in EXCLUDE_INC_STATUSES):
        return True
    return False


def parse_nsw_incidents(data):
    """
    Parse NSW RFS majorIncidents.json (GeoJSON FeatureCollection).
    Each feature has geometry (Point or Polygon) and properties including:
      title, category (alert level), status, type, size, updated, guid
    Excludes: Not Applicable alert level, planned burns, resolved statuses.
    """
    incidents = []
    features = data.get("features", []) if isinstance(data, dict) else []

    for f in features:
        p = f.get("properties", {})
        geom = f.get("geometry") or {}

        # Centroid: use Point geometry if available, else first coord of polygon
        lat, lng = 0, 0
        if geom.get("type") == "Point":
            coords = geom.get("coordinates", [])
            if len(coords) >= 2:
                lng, lat = float(coords[0]), float(coords[1])
        elif geom.get("type") in ("Polygon", "MultiPolygon"):
            # Use first coordinate of outer ring as centroid approximation
            try:
                ring = geom["coordinates"][0]
                if geom["type"] == "MultiPolygon":
                    ring = geom["coordinates"][0][0]
                lngs = [c[0] for c in ring]
                lats = [c[1] for c in ring]
                lng = sum(lngs) / len(lngs)
                lat = sum(lats) / len(lats)
            except (IndexError, TypeError, ZeroDivisionError):
                pass

        if not lat or not lng:
            continue

        # NSW uses "category" for alert level
        alert_level = normalise_alert_level(p.get("category") or "")

        # Exclude Not Applicable — non-actionable monitoring entries
        if (alert_level or "").lower() in ("not applicable", ""):
            continue

        inc_type   = p.get("type") or ""
        inc_status = p.get("status") or ""

        if is_excluded_incident(inc_type, inc_status):
            continue

        # Extract polygons for site-risk intersection (store as [lng,lat] pairs)
        polys = []
        if geom.get("type") == "Polygon":
            polys = [geom["coordinates"][0]]  # outer ring only
        elif geom.get("type") == "MultiPolygon":
            polys = [ring[0] for ring in geom["coordinates"]]

        incidents.append({
            "title":      p.get("title") or "NSW Incident",
            "alertLevel": alert_level,
            "status":     inc_status,
            "type":       inc_type,
            "size":       str(p.get("size") or ""),
            "agency":     p.get("respondsTo") or "RFS",
            "updated":    p.get("updated") or "",
            "description": p.get("location") or "",
            "lat":        lat,
            "lng":        lng,
            "polys":      polys,   # [lng,lat] coordinate rings for polygon rendering
            "state":      "NSW",
            "sourceUrl":  INCIDENT_FEEDS["nsw"]["source_url"],
        })

    return incidents


def parse_vic_incidents(data):
    """
    Parse VIC EMV incident JSON.
    Feed structure: {"results": [...]} — flat list of incident objects.
    Key fields: name, incidentLocation, category2 (alert level), category1 (type),
                incidentStatus, incidentType, incidentSize, agency, lastUpdated,
                latitude, longitude.
    Excludes: Safe status, Small size.
    """
    incidents = []
    records = data.get("results", []) if isinstance(data, dict) else []

    EXCLUDE_VIC_SIZES = ["small"]

    for r in records:
        lat = float(r.get("latitude") or 0)
        lng = float(r.get("longitude") or 0)
        if not lat or not lng:
            continue

        inc_status = r.get("incidentStatus") or ""
        inc_type   = r.get("incidentType") or r.get("category1") or ""
        inc_size   = (r.get("incidentSize") or "").lower()

        if is_excluded_incident(inc_type, inc_status):
            continue

        # VIC: exclude small incidents — not operationally significant
        if inc_size in EXCLUDE_VIC_SIZES:
            continue

        # VIC uses category2 for alert level, category1 for type
        alert_level = normalise_alert_level(r.get("category2") or r.get("category1") or "")

        incidents.append({
            "title":      r.get("name") or r.get("incidentLocation") or "VIC Incident",
            "alertLevel": alert_level,
            "status":     inc_status,
            "type":       inc_type,
            "size":       r.get("incidentSize") or "",
            "agency":     r.get("agency") or "EMV",
            "updated":    r.get("lastUpdated") or "",
            "description": r.get("incidentLocation") or "",
            "lat":        lat,
            "lng":        lng,
            "polys":      [],   # VIC feed is point-only, no polygon data
            "state":      "VIC",
            "sourceUrl":  INCIDENT_FEEDS["vic"]["source_url"],
        })

    return incidents


def parse_nsw_alert_zones(xml_text):
    """
    Parse NSW RFS IncidentAlerts.xml — CAP-AU format.
    Returns list of warning zone objects with polygon coordinates.
    Blank file = no active warning zones (expected and normal).

    Each zone object:
      title, areaName, alertLevel, updated,
      coords: [[lng,lat], ...] — closed polygon ring, [lng,lat] order for Leaflet
    """
    zones = []
    if not xml_text or not xml_text.strip():
        return zones  # Blank file — no active warning zones

    try:
        root = ET.fromstring(xml_text.strip())
    except ET.ParseError as e:
        print(f"  NSW alert zones: XML parse error — {e}")
        return zones

    CAP_NS = "urn:oasis:names:tc:emergency:cap:1.2"

    def get_text(el, tag, ns=None):
        found = el.findall(f".//{{{ns}}}{tag}") if ns else el.findall(f".//{tag}")
        return found[0].text.strip() if found and found[0].text else ""

    # Handle both Atom feed format (<feed><entry>) and direct CAP (<alert>)
    entries = root.findall(".//entry") or root.findall(".//alert")

    for entry in entries:
        title   = get_text(entry, "title")
        updated = get_text(entry, "updated") or get_text(entry, "sent", CAP_NS)

        # Alert level from title text
        tl = title.lower()
        alert_level = ("Emergency Warning" if "emergency warning" in tl else
                       "Watch and Act"      if "watch and act"      in tl else
                       "Advice"             if "advice"             in tl else "")

        # Find all polygon elements — try namespaced then bare
        poly_els = (entry.findall(f".//{{{CAP_NS}}}polygon") or
                    entry.findall(".//cap:polygon") or
                    entry.findall(".//polygon"))

        # Area description
        area_els = (entry.findall(f".//{{{CAP_NS}}}areaDesc") or
                    entry.findall(".//cap:areaDesc") or
                    entry.findall(".//areaDesc"))
        area_name = area_els[0].text.strip() if area_els and area_els[0].text else ""

        for poly_el in poly_els:
            raw = (poly_el.text or "").strip()
            if not raw:
                continue

            # CAP polygon format: "lat,lng lat,lng lat,lng ..."
            # Convert to [lng,lat] pairs for Leaflet consistency
            coords = []
            for pair in raw.split():
                parts = pair.split(",")
                if len(parts) < 2:
                    continue
                try:
                    lat, lng = float(parts[0]), float(parts[1])
                    coords.append([lng, lat])
                except ValueError:
                    continue

            if len(coords) < 3:
                continue

            # Close the ring if not already closed
            if coords[0] != coords[-1]:
                coords.append(coords[0])

            zones.append({
                "title":      title,
                "areaName":   area_name,
                "alertLevel": alert_level,
                "updated":    updated,
                "coords":     coords,  # [lng,lat] pairs, closed ring
            })

    print(f"  NSW alert zones: {len(zones)} polygon zone(s)")
    return zones


def fetch_nsw_alert_zones():
    """Fetch and parse NSW RFS IncidentAlerts.xml. Returns (list, error_or_None)."""
    try:
        r = requests.get(NSW_ALERT_ZONES_URL, timeout=20, headers={
            "User-Agent": "Mozilla/5.0 (compatible; WeatherMap/1.0)",
        })
        r.raise_for_status()
        zones = parse_nsw_alert_zones(r.text)
        return zones, None
    except requests.RequestException as e:
        print(f"  NSW alert zones: fetch failed — {e}")
        return [], str(e)


def parse_qld_incidents(data):
    """
    Parse QLD QFD bushfire GeoJSON feed into normalised incident list.

    Confirmed schema from Action log (format=geojson, fields in properties):
      OBJECTID, UniqueID, WarningTitle, WarningLevel, CallToAction,
      WarningText, Header, Impacts, LeaveSafely, FurtherInformation,
      WarningLevelSort, ShouldDo, WarningArea, Latitude, Longitude

    Note: This feed contains QFD *warnings* (not raw incidents) — each record
    is an active community-level warning, equivalent to NSW/VIC alert levels.
    WarningLevel maps to alert level. WarningTitle is the incident/warning name.
    Planned burns appear as "Advice - AVOID SMOKE (HAZARD REDUCTION BURN)" in WarningTitle.
    """
    incidents = []

    # Determine record list
    use_geojson_wrapper = False
    if isinstance(data, dict) and data.get("type") == "FeatureCollection":
        records = data.get("features", [])
        use_geojson_wrapper = True
    elif isinstance(data, dict) and "features" in data:
        records = data["features"]
        use_geojson_wrapper = True
    elif isinstance(data, dict) and "Incidents" in data:
        records = data["Incidents"]
        use_geojson_wrapper = False
    elif isinstance(data, list):
        records = data
        use_geojson_wrapper = False
    else:
        print(f"  QLD: unexpected structure — keys: {list(data.keys()) if isinstance(data, dict) else type(data)}")
        return incidents

    print(f"  QLD: {len(records)} raw records (format={'geojson' if use_geojson_wrapper else 'flat'})")

    for rec in records:
        if use_geojson_wrapper:
            p = rec.get("properties", {}) if isinstance(rec, dict) else {}
            geom = rec.get("geometry") or {}
            coords = geom.get("coordinates", [])
            if geom.get("type") == "Point" and len(coords) >= 2:
                lng, lat = float(coords[0]), float(coords[1])
            else:
                lat = float(p.get("Latitude") or p.get("latitude") or 0)
                lng = float(p.get("Longitude") or p.get("longitude") or 0)
        else:
            p = rec if isinstance(rec, dict) else {}
            lat = float(p.get("Latitude") or p.get("latitude") or 0)
            lng = float(p.get("Longitude") or p.get("longitude") or 0)

        if not lat or not lng:
            continue

        # WarningTitle contains both the alert level prefix and description
        # e.g. "Emergency Warning - Bushfire at Somewhere"
        # e.g. "Advice - AVOID SMOKE (HAZARD REDUCTION BURN) - Blackwater..."
        raw_title = (p.get("WarningTitle") or p.get("Header") or p.get("IncidentName") or "")

        # Derive type from title — Hazard Reduction Burns are in the title text
        title_lower = raw_title.lower()
        inc_type = ""
        if "hazard reduction" in title_lower or "prescribed burn" in title_lower or "avoid smoke" in title_lower:
            inc_type = "hazard reduction burn"
        elif "bushfire" in title_lower or "fire" in title_lower:
            inc_type = "Bush Fire"
        elif "flood" in title_lower:
            inc_type = "Flood"

        inc_status = ""  # QLD warnings feed doesn't have a separate status field

        # Alert level from WarningLevel field
        # Confirmed values from QFD: "Emergency Warning", "Watch and Act", "Advice", "Information"
        alert_level = normalise_alert_level(
            p.get("WarningLevel") or p.get("AlertLevel") or ""
        )

        # Filter out burns before we do anything else
        if is_excluded_incident(inc_type, inc_status):
            continue

        # Skip "Information" level — QFD monitoring notices, no community action required
        # (equivalent to NSW "Not Applicable")
        if alert_level == "Information":
            continue

        # Strip the alert level prefix from the title if present
        # "Emergency Warning - Bushfire at X" → "Bushfire at X"
        clean_title = raw_title
        for prefix in ["Emergency Warning - ", "Watch and Act - ", "Advice - "]:
            if clean_title.startswith(prefix):
                clean_title = clean_title[len(prefix):]
                break

        updated = parse_compact_timestamp(
            p.get("UpdateDateTime") or p.get("LastUpdate") or ""
        )

        incidents.append({
            "title":       clean_title or raw_title or "QLD Incident",
            "alertLevel":  alert_level,
            "status":      inc_status,
            "type":        inc_type,
            "size":        str(p.get("AreaBurnt") or ""),
            "agency":      "QFD",
            "updated":     updated,
            "description": (p.get("WarningArea") or p.get("AreaDescription") or ""),
            "lat":         lat,
            "lng":         lng,
            "state":       "QLD",
            "sourceUrl":   INCIDENT_FEEDS["qld"]["source_url"],
        })

    return incidents


def parse_sa_incidents(data):
    """
    Parse SA CFS criimson JSON into normalised incident list.
    Feed structure: {"incidents": {"features": [...]}} — GeoJSON FeatureCollection.
    Each feature: {"geometry": {"coordinates": [lng, lat]}, "properties": {...}}
    """
    incidents = []

    # Unwrap nested structure — SA feed wraps FeatureCollection under "incidents" key
    if isinstance(data, dict) and "incidents" in data:
        feature_coll = data["incidents"]
    else:
        feature_coll = data

    if isinstance(feature_coll, dict):
        records = feature_coll.get("features", [])
    elif isinstance(feature_coll, list):
        records = feature_coll
    else:
        print("  SA: unexpected JSON structure")
        return incidents

    for rec in records:
        p = rec.get("properties", {}) if isinstance(rec, dict) else rec
        geom = rec.get("geometry") or {} if isinstance(rec, dict) else {}
        coords = geom.get("coordinates", [])

        if geom.get("type") == "Point" and len(coords) >= 2:
            lng, lat = float(coords[0]), float(coords[1])
        else:
            lat = float(p.get("lat") or p.get("latitude") or p.get("Latitude") or 0)
            lng = float(p.get("lng") or p.get("longitude") or p.get("Longitude") or 0)

        if not lat or not lng:
            continue

        inc_type = (p.get("type") or p.get("Type") or p.get("incident_type") or "")
        inc_status = (p.get("status") or p.get("Status") or "")

        if is_excluded_incident(inc_type, inc_status):
            continue

        alert_level = normalise_alert_level(
            p.get("warning_level") or p.get("alertLevel") or p.get("alert_level") or ""
        )

        # SA timestamps are typically ISO 8601 already
        updated = (p.get("updated") or p.get("last_updated") or
                   p.get("update_time") or p.get("Updated") or "")

        size_ha = p.get("size") or p.get("area") or p.get("area_ha") or ""
        if size_ha:
            size_ha = f"{size_ha} ha"

        incidents.append({
            "title":      p.get("name") or p.get("title") or p.get("incident_name") or "SA Incident",
            "alertLevel": alert_level,
            "status":     inc_status,
            "type":       inc_type,
            "size":       str(size_ha),
            "agency":     "CFS",
            "updated":    str(updated),
            "description": p.get("location") or p.get("description") or p.get("area") or "",
            "lat":        lat,
            "lng":        lng,
            "state":      "SA",
            "sourceUrl":  INCIDENT_FEEDS["sa"]["source_url"],
        })

    return incidents


def fetch_incidents(key, feed_cfg):
    """Fetch and parse one state's incident feed. Returns (list, error_or_None)."""
    url = feed_cfg["url"]
    try:
        r = requests.get(url, timeout=20, headers={
            "User-Agent": "Mozilla/5.0 (compatible; WeatherMap/1.0)",
            "Accept": "application/json",
        })
        r.raise_for_status()

        text = r.text.strip().lstrip('\ufeff')  # Strip BOM if present

        # Unwrap allorigins-style envelope (shouldn't appear server-side, but guard anyway)
        try:
            obj = json.loads(text)
            if isinstance(obj, dict) and "contents" in obj:
                obj = json.loads(obj["contents"])
        except json.JSONDecodeError as e:
            print(f"  {key.upper()}: JSON parse error — {e}")
            return [], str(e)

        if key == "nsw":
            incidents = parse_nsw_incidents(obj)
        elif key == "vic":
            incidents = parse_vic_incidents(obj)
        elif key == "qld":
            incidents = parse_qld_incidents(obj)
            # Debug: log first record's field names and values to verify schema
            try:
                raw_records = (obj.get("features") or obj.get("Incidents") or
                               (obj if isinstance(obj, list) else []))
                if raw_records:
                    first = raw_records[0]
                    if "properties" in first:
                        first = first["properties"]
                    print(f"  QLD schema keys: {list(first.keys())[:15]}")
                    print(f"  QLD sample — title:{first.get('WarningTitle')} "
                          f"level:{first.get('WarningLevel')} "
                          f"area:{first.get('WarningArea')}")
            except Exception as e:
                print(f"  QLD debug error: {e}")
        elif key == "sa":
            incidents = parse_sa_incidents(obj)
        else:
            incidents = []

        print(f"  {key.upper()} incidents: {len(incidents)} active")
        return incidents, None

    except requests.RequestException as e:
        print(f"  {key.upper()} incidents: fetch failed — {e}")
        return [], str(e)


def main():
    print(f"Fetching BOM warning feeds — {datetime.now(timezone.utc).isoformat()}")

    all_warnings = []
    errors = {}

    # ACT is included in the NSW feed — skip it as a separate fetch
    for state, url in BOM_WARNING_FEEDS.items():
        if state == "ACT":
            continue
        warnings, err = fetch_state(state, url)
        all_warnings.extend(warnings)
        if err:
            errors[state] = err

    # De-duplicate by product ID (ACT warnings appear in NSW feed)
    seen_pids = set()
    unique_warnings = []
    for w in all_warnings:
        key = w["pid"] if w["pid"] else w["title"] + w["state"]
        if key not in seen_pids:
            seen_pids.add(key)
            unique_warnings.append(w)

    # ── Fetch incident feeds ──────────────────────────────────────────────────
    print("Fetching incident feeds...")
    incidents = {}
    incident_errors = {}
    total_incidents = 0

    for key, feed_cfg in INCIDENT_FEEDS.items():
        inc_list, inc_err = fetch_incidents(key, feed_cfg)
        incidents[key] = inc_list
        total_incidents += len(inc_list)
        if inc_err:
            incident_errors[key] = inc_err

    # ── Fetch NSW RFS alert zones (CAP-AU polygons) ───────────────────────────
    print("Fetching NSW alert zones...")
    nsw_alert_zones, zones_err = fetch_nsw_alert_zones()
    if zones_err:
        incident_errors["nsw_zones"] = zones_err

    # Merge fetch errors
    errors.update(incident_errors)

    # Build output
    output = {
        "generated_utc":    datetime.now(timezone.utc).isoformat(),
        "warning_count":    len(unique_warnings),
        "warnings":         unique_warnings,
        "fetch_errors":     errors,
        "incidents":        incidents,       # keyed by state: "nsw","vic","qld","sa"
        "incident_count":   total_incidents,
        "nsw_alert_zones":  nsw_alert_zones, # CAP-AU polygons for site-risk intersection
    }

    with open("live_data.json", "w") as f:
        json.dump(output, f, indent=2)

    print(f"Done — {len(unique_warnings)} warnings, {total_incidents} incidents, {len(nsw_alert_zones)} NSW alert zones written to live_data.json")
    if errors:
        print(f"Fetch errors: {errors}")

if __name__ == "__main__":
    main()
