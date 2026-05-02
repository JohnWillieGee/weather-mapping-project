#!/usr/bin/env python3
"""
fetch_live_data.py
Fetches BOM state warning XML feeds and writes live_data.json
Run by GitHub Actions every 15 minutes — no CORS restrictions server-side.
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

def extract_pid_from_link(link):
    """Extract product ID from BOM warning link URL.
    e.g. http://www.bom.gov.au/qld/warnings/flood/diamantina-river.shtml -> no ID in URL
    e.g. http://www.bom.gov.au/products/IDN21000.shtml -> IDN21000
    """
    # Try direct product ID in URL
    m = re.search(r'\b(ID[A-Z]\d{5,6})\b', link)
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

    # Build output
    output = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "warning_count": len(unique_warnings),
        "warnings": unique_warnings,
        "fetch_errors": errors,
        # Placeholder for future data sources
        "incidents": {},
    }

    with open("live_data.json", "w") as f:
        json.dump(output, f, indent=2)

    print(f"Done — {len(unique_warnings)} total warnings written to live_data.json")
    if errors:
        print(f"Fetch errors: {errors}")

if __name__ == "__main__":
    main()
