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
    "NT":  "http://www.bom.gov.au/fwo/IDZ00061.warnings_nt.xml",
    "ACT": "http://www.bom.gov.au/fwo/IDZ00054.warnings_nsw.xml",  # ACT included in NSW feed
}

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


def parse_qld_incidents(data):
    """
    Parse QLD QFD bushfire JSON into normalised incident list.

    The QLD S3 feed has been observed in these formats:
      Format A — legacy flat:  {"Incidents": [{Longitude, Latitude, IncidentName, ...}]}
      Format B — GeoJSON:      {"type":"FeatureCollection","features":[{geometry,properties}]}
      Format C — bare list:    [{...}]

    In Format A, all fields are top-level on each incident object (no "properties" wrapper).
    This is the most common live format — always try it first.
    """
    incidents = []

    # Determine record list and whether we have a GeoJSON feature wrapper
    use_geojson_wrapper = False
    if isinstance(data, dict) and "Incidents" in data:
        # Format A — legacy flat list (most common live format)
        records = data["Incidents"]
        use_geojson_wrapper = False
    elif isinstance(data, dict) and data.get("type") == "FeatureCollection":
        # Format B — GeoJSON FeatureCollection
        records = data.get("features", [])
        use_geojson_wrapper = True
    elif isinstance(data, list):
        # Format C — bare array
        records = data
        use_geojson_wrapper = False
    elif isinstance(data, dict) and "features" in data:
        # Format B variant — FeatureCollection without explicit type field
        records = data["features"]
        use_geojson_wrapper = True
    elif isinstance(data, dict) and "results" in data:
        records = data["results"]
        use_geojson_wrapper = False
    else:
        print(f"  QLD: unexpected JSON structure — top-level keys: {list(data.keys()) if isinstance(data, dict) else type(data)}")
        return incidents

    print(f"  QLD: {len(records)} raw records (format={'geojson' if use_geojson_wrapper else 'flat'})")

    for rec in records:
        if use_geojson_wrapper:
            # GeoJSON: coords in geometry, fields in properties
            p = rec.get("properties", {}) if isinstance(rec, dict) else {}
            geom = rec.get("geometry") or {}
            coords = geom.get("coordinates", [])
            if geom.get("type") == "Point" and len(coords) >= 2:
                lng, lat = float(coords[0]), float(coords[1])
            else:
                lat = float(p.get("Latitude") or p.get("latitude") or 0)
                lng = float(p.get("Longitude") or p.get("longitude") or 0)
        else:
            # Flat format — all fields directly on the record
            p = rec if isinstance(rec, dict) else {}
            lat = float(p.get("Latitude") or p.get("latitude") or 0)
            lng = float(p.get("Longitude") or p.get("longitude") or 0)

        if not lat or not lng:
            continue

        inc_type   = (p.get("IncidentType") or p.get("Type") or p.get("incidentType") or "")
        inc_status = (p.get("IncidentStatus") or p.get("Status") or "")

        if is_excluded_incident(inc_type, inc_status):
            continue

        alert_level = normalise_alert_level(
            p.get("CurrentSituation") or p.get("AlertLevel") or p.get("alertLevel") or ""
        )
        updated = parse_compact_timestamp(
            p.get("UpdateDateTime") or p.get("LastUpdate") or ""
        )

        # Title: try all known QLD field names
        title = (p.get("IncidentName") or p.get("Name") or p.get("name") or
                 p.get("Title") or p.get("title") or "QLD Incident")

        incidents.append({
            "title":       title,
            "alertLevel":  alert_level,
            "status":      inc_status,
            "type":        inc_type,
            "size":        str(p.get("AreaBurnt") or p.get("areaBurnt") or ""),
            "agency":      "QFD",
            "updated":     updated,
            "description": (p.get("AreaDescription") or p.get("LocalGovernmentArea") or ""),
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

        if key == "qld":
            incidents = parse_qld_incidents(obj)
            # Debug: log first raw record's field names so we can verify schema in Action log
            try:
                raw_records = (obj.get("Incidents") or obj.get("features") or
                               (obj if isinstance(obj, list) else []))
                if raw_records:
                    first = raw_records[0]
                    # For GeoJSON, unwrap properties
                    if "properties" in first:
                        first = first["properties"]
                    print(f"  QLD schema keys: {list(first.keys())[:15]}")
                    print(f"  QLD sample — name:{first.get('IncidentName') or first.get('Name') or first.get('name')} "
                          f"type:{first.get('IncidentType') or first.get('Type')} "
                          f"situation:{first.get('CurrentSituation')} "
                          f"status:{first.get('IncidentStatus') or first.get('Status')}")
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

    # Merge fetch errors
    errors.update(incident_errors)

    # Build output
    output = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "warning_count": len(unique_warnings),
        "warnings": unique_warnings,
        "fetch_errors": errors,
        "incidents": incidents,          # keyed by state code: "qld", "sa", etc.
        "incident_count": total_incidents,
    }

    with open("live_data.json", "w") as f:
        json.dump(output, f, indent=2)

    print(f"Done — {len(unique_warnings)} warnings, {total_incidents} incidents written to live_data.json")
    if errors:
        print(f"Fetch errors: {errors}")

if __name__ == "__main__":
    main()
