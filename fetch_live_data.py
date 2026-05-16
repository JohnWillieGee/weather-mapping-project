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
import os
import time
import re
import csv
import io
from datetime import datetime, timezone

# ── FDR XML FEEDS (BOM anonymous FTP) ────────────────────────────────────────
# Free access via BOM anonymous FTP — no credentials required.
# Files overwritten each issue: twice daily during fire season.
# Product IDs confirmed May 2026.
# FTP: ftp://ftp.bom.gov.au/anon/gen/fwo/<product_id>.xml
FDR_XML_FEEDS = {
    "NSW": "IDN10016",
    "VIC": "IDV18555",
    "QLD": "IDQ13016",
    "SA":  "IDS10070",
    "WA":  "IDW15100",
    "TAS": "IDT13151",
    "NT":  "IDD10731",
}


# ── BOM RADAR CONFIG ─────────────────────────────────────────────────────────
# Frames to keep per product. FTP holds ~36 frames (~6hrs at 10min intervals).
# 12 frames = ~2hrs of animation history.
RADAR_FRAMES = 12

# National mosaic removed — bounds could not be accurately derived (no .map file).
# Replaced by full set of 512km individual radars with accurate .map file bounds.
RADAR_NATIONAL = None

# Individual radars — 512km range (suffix 1).
# Bounds derived from each radar's .map file (Gnomonic projection corners).
# Ordered: core BAI coverage sites first, then gap-fillers by region.
RADAR_SITES = [
    # ── Core sites — direct 512km equivalents of previous 128km set ──────────
    {"id": "IDR711",  "name": "Sydney",               "map": "IDR711.map"},
    {"id": "IDR021",  "name": "Melbourne",             "map": "IDR021.map"},
    {"id": "IDR401",  "name": "Canberra",              "map": "IDR401.map"},
    {"id": "IDR661",  "name": "Brisbane",              "map": "IDR661.map"},
    {"id": "IDR041",  "name": "Newcastle",             "map": "IDR041.map"},
    {"id": "IDR081",  "name": "Gympie",                "map": "IDR081.map"},
    {"id": "IDR681",  "name": "Bairnsdale",            "map": "IDR681.map"},
    {"id": "IDR331",  "name": "Ceduna",                "map": "IDR331.map"},
    {"id": "IDR701",  "name": "Perth",                 "map": "IDR701.map"},
    {"id": "IDR761",  "name": "Hobart",                "map": "IDR761.map"},
    {"id": "IDR281",  "name": "Grafton",               "map": "IDR281.map"},
    {"id": "IDR191",  "name": "Cairns",                "map": "IDR191.map"},
    {"id": "IDR641",  "name": "Adelaide",              "map": "IDR641.map"},
    {"id": "IDR781",  "name": "Weipa",                 "map": "IDR781.map"},
    {"id": "IDR1061", "name": "Townsville",            "map": "IDR1061.map"},
    # ── Gap-fillers — NSW inland / south ─────────────────────────────────────
    {"id": "IDR531",  "name": "Moree",                 "map": "IDR531.map"},
    {"id": "IDR551",  "name": "Wagga Wagga",           "map": "IDR551.map"},
    {"id": "IDR691",  "name": "Namoi",                 "map": "IDR691.map"},  # Blackjack Mtn — NW NSW
    {"id": "IDR931",  "name": "Brewarrina",            "map": "IDR931.map"},  # far NW NSW
    {"id": "IDR941",  "name": "Hillston",              "map": "IDR941.map"},  # central NSW
    {"id": "IDR961",  "name": "Yeoval",                "map": "IDR961.map"},  # central NSW
    # ── Gap-fillers — VIC inland ──────────────────────────────────────────────
    {"id": "IDR301",  "name": "Mildura",               "map": "IDR301.map"},
    {"id": "IDR491",  "name": "Yarrawonga",            "map": "IDR491.map"},
    {"id": "IDR951",  "name": "Rainbow",               "map": "IDR951.map"},  # NW VIC
    # ── Gap-fillers — QLD inland ──────────────────────────────────────────────
    {"id": "IDR721",  "name": "Emerald",               "map": "IDR721.map"},
    {"id": "IDR561",  "name": "Longreach",             "map": "IDR561.map"},
    {"id": "IDR751",  "name": "Mount Isa",             "map": "IDR751.map"},
    {"id": "IDR671",  "name": "Warrego",               "map": "IDR671.map"},  # SW QLD
    {"id": "IDR981",  "name": "Taroom",                "map": "IDR981.map"},  # central QLD
    {"id": "IDR1071", "name": "Richmond",              "map": "IDR1071.map"}, # NW QLD
    {"id": "IDR741",  "name": "Greenvale",             "map": "IDR741.map"},  # NQ inland
    # ── Gap-fillers — SA / NT ─────────────────────────────────────────────────
    {"id": "IDR271",  "name": "Woomera",               "map": "IDR271.map"},  # central SA
    {"id": "IDR141",  "name": "Mt Gambier",            "map": "IDR141.map"},  # SE SA
    {"id": "IDR251",  "name": "Alice Springs",         "map": "IDR251.map"},
    {"id": "IDR631",  "name": "Darwin",                "map": "IDR631.map"},
    {"id": "IDR421",  "name": "Katherine",             "map": "IDR421.map"},
    {"id": "IDR771",  "name": "Warruwi (Arafura)",     "map": "IDR771.map"},  # Arnhem Land coast
    # ── Gap-fillers — WA ─────────────────────────────────────────────────────
    {"id": "IDR171",  "name": "Broome",                "map": "IDR171.map"},
    {"id": "IDR161",  "name": "Port Hedland",          "map": "IDR161.map"},
    {"id": "IDR151",  "name": "Dampier",               "map": "IDR151.map"},
    {"id": "IDR061",  "name": "Geraldton",             "map": "IDR061.map"},
    {"id": "IDR311",  "name": "Albany",                "map": "IDR311.map"},
    {"id": "IDR321",  "name": "Esperance",             "map": "IDR321.map"},
    {"id": "IDR481",  "name": "Kalgoorlie",            "map": "IDR481.map"},
    {"id": "IDR381",  "name": "Newdegate",             "map": "IDR381.map"},  # SW WA inland
    {"id": "IDR581",  "name": "South Doodlakine",      "map": "IDR581.map"},  # wheatbelt WA
    {"id": "IDR441",  "name": "Giles",                 "map": "IDR441.map"},  # remote central WA
    {"id": "IDR391",  "name": "Halls Creek",           "map": "IDR391.map"},  # Kimberley
    {"id": "IDR1111", "name": "Karratha",              "map": "IDR1111.map"},
    # ── TAS extra ─────────────────────────────────────────────────────────────
    {"id": "IDR521",  "name": "NW Tasmania",           "map": "IDR521.map"},
]

FTP_RADAR_HOST = "ftp.bom.gov.au"
FTP_RADAR_DIR  = "/anon/gen/radar"
FTP_RADAR_MAPS = "/anon/gen/radar_transparencies/coordinates"

# ── COMPOSITE CANVAS CONFIG ───────────────────────────────────────────────────
# Full Australia geographic extent for the composite canvas.
# These bounds are used ONLY for the composite image — individual product bounds
# still come from their .map files.
COMPOSITE_BOUNDS = {
    "south": -47.0,
    "west":  112.0,
    "north":  -5.5,
    "east":  158.0,
}
# Canvas pixel dimensions — larger = more detail but bigger files.
# 4096×3072 gives ~7km/px resolution, adequate for weather overview.
COMPOSITE_WIDTH  = 4096
COMPOSITE_HEIGHT = 3072


def composite_radar_frames(meta, map_bounds):
    """Composite all individual radar PNGs into a single full-Australia PNG per frame.

    Canvas is built in Web Mercator projection to match Leaflet's imageOverlay
    rendering. Leaflet projects imageOverlay bounds through Mercator when
    rendering — an equirectangular canvas causes north/south offsets at
    mid-latitudes (~200km at Alice Springs). Building in Mercator eliminates this.

    Per-radar pre-processing:
      - Crop top 16px header (BOM copyright bar)
      - Pad 16px transparent at bottom (restores 512x512, keeps radar centre aligned)
      - Make black background transparent (threshold <30 per channel)

    Updates meta in-place with 'composite' product. Returns composite dict or None.
    """
    try:
        from PIL import Image
        import numpy as np
        import math
    except ImportError:
        print("  Composite: Pillow/numpy not installed — skipping. Run: pip install Pillow numpy")
        return None

    cb = COMPOSITE_BOUNDS
    cw, ch = COMPOSITE_WIDTH, COMPOSITE_HEIGHT
    HEADER_CROP_PX = 16
    BLACK_THRESHOLD = 30

    # Mercator helpers — canvas is built in Mercator space to match Leaflet
    def merc_y(lat):
        """WGS84 lat -> Mercator Y value (north is larger)"""
        return math.log(math.tan(math.pi / 4 + math.radians(max(-85.0, min(85.0, lat))) / 2))

    def merc_x(lon):
        return math.radians(lon)

    # Canvas Mercator extents
    mx_w = merc_x(cb["west"]);  mx_e = merc_x(cb["east"])
    my_n = merc_y(cb["north"]); my_s = merc_y(cb["south"])
    mx_span = mx_e - mx_w
    my_span = my_n - my_s  # positive

    def geo_to_px(lat, lon):
        """WGS84 lat/lon -> Mercator pixel on composite canvas."""
        x = int((merc_x(lon) - mx_w) / mx_span * cw)
        y = int((my_n - merc_y(lat)) / my_span * ch)
        return x, y

    def prepare_radar_image(path):
        """Crop header, pad bottom, make black transparent. Returns RGBA Image or None."""
        try:
            img = Image.open(path).convert("RGB")
        except Exception as e:
            print(f"  Composite: could not open {path} — {e}")
            return None
        w, h = img.size
        arr = np.array(img.crop((0, HEADER_CROP_PX, w, h))).astype(np.uint8)
        pad = np.zeros((HEADER_CROP_PX, w, 3), dtype=np.uint8)
        arr = np.concatenate([arr, pad], axis=0)  # restore to h rows
        rgba = np.zeros((h, w, 4), dtype=np.uint8)
        rgba[:, :, :3] = arr
        rgba[:, :, 3] = np.where(np.all(arr < BLACK_THRESHOLD, axis=2), 0, 255).astype(np.uint8)
        return Image.fromarray(rgba, "RGBA")

    composite_frames = []
    any_written = False

    for frame_idx in range(RADAR_FRAMES):
        canvas = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        pasted = 0

        for pid, product in meta.items():
            frames = product.get("frames", [])
            if frame_idx >= len(frames):
                continue
            bounds = map_bounds.get(pid)
            if not bounds:
                continue
            local_path = os.path.join("radar", f"{pid}_{frame_idx:02d}.png")
            if not os.path.exists(local_path):
                continue

            radar_img = prepare_radar_image(local_path)
            if radar_img is None:
                continue

            s, w, n, e = bounds
            # Convert bounds to Mercator pixel positions
            x0, y0 = geo_to_px(n, w)   # NW corner -> top-left
            x1, y1 = geo_to_px(s, e)   # SE corner -> bottom-right

            x0c = max(0, x0); y0c = max(0, y0)
            x1c = min(cw, x1); y1c = min(ch, y1)
            if x1c <= x0c or y1c <= y0c:
                continue
            dest_w = x1c - x0c
            dest_h = y1c - y0c
            if dest_w < 1 or dest_h < 1:
                continue

            try:
                resized  = radar_img.resize((x1 - x0, y1 - y0), Image.LANCZOS)
                cropped  = resized.crop((x0c - x0, y0c - y0,
                                         x0c - x0 + dest_w, y0c - y0 + dest_h))
                canvas.paste(cropped, (x0c, y0c), cropped)
                pasted += 1
            except Exception as e:
                print(f"  Composite [{pid}] frame {frame_idx}: paste failed — {e}")

        utc_str = ""
        for pid, product in meta.items():
            frames = product.get("frames", [])
            if frame_idx < len(frames):
                utc_str = frames[frame_idx].get("utc", "")
                break

        out_path = os.path.join("radar", f"composite_{frame_idx:02d}.png")
        try:
            canvas.save(out_path, "PNG", optimize=True, compress_level=9)
            file_kb = os.path.getsize(out_path) // 1024
            print(f"  Composite frame {frame_idx:02d}: {pasted} radars pasted -> "
                  f"{file_kb}KB  [{utc_str}]")
            any_written = True
        except Exception as e:
            print(f"  Composite frame {frame_idx:02d}: save failed — {e}")
            continue

        composite_frames.append({"file": f"radar/composite_{frame_idx:02d}.png", "utc": utc_str})

    if not any_written:
        return None

    composite_product = {
        "name":   "Composite (all radars)",
        "bounds": [cb["south"], cb["west"], cb["north"], cb["east"]],
        "frames": composite_frames,
    }
    meta["composite"] = composite_product
    print(f"  Composite: {len(composite_frames)} frames written to radar/composite_NN.png")
    return composite_product

def parse_map_file(content):
    """Parse a BOM .map file and return the correct geographic bounding box
    [south, west, north, east] for use as a Leaflet imageOverlay bounds.

    Background:
      BOM .map files use Gnomonic projection. The four corner points (lon0/lat0
      ..lon3/lat3) are the DIAGONAL corners of the square image, located at
      radius*sqrt(2) from the radar centre (~724km for a 512km radar). The
      actual radar circle only extends to 'radius' km from the centre.

      Using the diagonal corners as the image bounds causes the radar data to
      be stretched ~41% too large on the map and misregistered.

      The correct approach is to use the CARDINAL extents (N/S/E/W tips of the
      radar circle at exactly 'radius' km from the centre), which correspond to
      the true edge of the radar sweep and correctly frame the circular image.

    Strategy:
      1. Extract centre_lat and centre_lon from the .map file
      2. Derive radius from the corner points (distance from centre to corner
         divided by sqrt(2) gives the cardinal radius)
      3. Compute cardinal N/S/E/W extents from centre + radius
    """
    import math

    centre_lat = None
    centre_lon = None
    corner_lats = []
    corner_lons = []

    for line in content.splitlines():
        line = line.strip()
        if line.startswith("center_latitude"):
            try:
                centre_lat = float(line.split("=")[1].strip())
            except (IndexError, ValueError):
                pass
        elif line.startswith("center_longitude"):
            try:
                centre_lon = float(line.split("=")[1].strip())
            except (IndexError, ValueError):
                pass
        else:
            for i in range(4):
                if line.startswith(f"lon{i}"):
                    try:
                        corner_lons.append(float(line.split("=")[1].strip()))
                    except (IndexError, ValueError):
                        pass
                if line.startswith(f"lat{i}"):
                    try:
                        corner_lats.append(float(line.split("=")[1].strip()))
                    except (IndexError, ValueError):
                        pass

    if centre_lat is None or centre_lon is None or len(corner_lats) < 4:
        return None

    # Derive radar radius (km) from diagonal corner distance / sqrt(2)
    # Corner-to-centre distance = radius * sqrt(2) in Gnomonic projection
    # Use haversine to measure actual corner distances
    R_EARTH = 6371.0

    def haversine_km(lat1, lon1, lat2, lon2):
        import math
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = (math.sin(dlat/2)**2 +
             math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
             math.sin(dlon/2)**2)
        return 2 * R_EARTH * math.asin(math.sqrt(a))

    corner_dists = [
        haversine_km(centre_lat, centre_lon, corner_lats[i], corner_lons[i])
        for i in range(min(len(corner_lats), len(corner_lons)))
    ]
    if not corner_dists:
        return None

    diag_km   = sum(corner_dists) / len(corner_dists)  # avg corner distance
    radius_km = diag_km / math.sqrt(2)

    # Compute cardinal extents from centre + radius
    deg_per_km_lat = 1.0 / 111.32
    deg_per_km_lon = 1.0 / (111.32 * math.cos(math.radians(centre_lat)))

    north = centre_lat + radius_km * deg_per_km_lat
    south = centre_lat - radius_km * deg_per_km_lat
    east  = centre_lon + radius_km * deg_per_km_lon
    west  = centre_lon - radius_km * deg_per_km_lon

    return [south, west, north, east]


def fetch_radar_frames():
    """Fetch BOM radar PNGs via anonymous FTP and save to radar/ subfolder.

    For each product (national mosaic + 12 individual radars):
      - Lists the FTP radar directory
      - Selects the RADAR_FRAMES most recent timestamped PNGs for that product
      - Downloads them to radar/<product_id>_NN.png (00 = newest)
      - Fetches the .map file once (if not already cached locally) for each
        individual radar to derive geo bounds

    Writes radar/radar_meta.json with frame timestamps and bounds for all
    products — consumed by index.html to build the animated overlay.

    Returns a summary dict for inclusion in the fetcher's console output,
    or None on complete FTP failure.
    """
    from ftplib import FTP, error_perm

    os.makedirs("radar", exist_ok=True)

    try:
        ftp = FTP(FTP_RADAR_HOST, timeout=30)
        ftp.login()
        print(f"  Radar FTP: connected to {FTP_RADAR_HOST}")
    except Exception as e:
        print(f"  Radar FTP: connection failed — {e}")
        return None

    # ── Step 1: get full directory listing once ───────────────────────────────
    try:
        ftp.cwd(FTP_RADAR_DIR)
        all_files = ftp.nlst()
        print(f"  Radar FTP: {len(all_files)} files in {FTP_RADAR_DIR}")
    except Exception as e:
        print(f"  Radar FTP: listing failed — {e}")
        try:
            ftp.quit()
        except Exception:
            pass
        return None

    # Build a set for fast lookup
    all_files_set = set(all_files)

    # ── Step 2: fetch .map files for individual radars (one-time cache) ───────
    # Only download if the local cached copy doesn't exist yet
    map_bounds = {}  # product_id -> [S, W, N, E]

    try:
        ftp.cwd(FTP_RADAR_MAPS)
    except Exception as e:
        print(f"  Radar FTP: cannot access maps dir — {e}")

    for site in RADAR_SITES:
        pid   = site["id"]
        mfile = site["map"]
        local_map = os.path.join("radar", mfile)

        if not os.path.exists(local_map):
            buf = io.BytesIO()
            try:
                ftp.retrbinary(f"RETR {mfile}", buf.write)
                buf.seek(0)
                content = buf.read().decode("utf-8", errors="replace")
                with open(local_map, "w") as f:
                    f.write(content)
                print(f"  Radar map: fetched {mfile}")
            except error_perm:
                print(f"  Radar map: {mfile} not found on FTP — skipping bounds")
                content = None
            except Exception as e:
                print(f"  Radar map: {mfile} fetch failed — {e}")
                content = None
        else:
            with open(local_map) as f:
                content = f.read()

        if content:
            bounds = parse_map_file(content)
            if bounds:
                map_bounds[pid] = bounds
            else:
                print(f"  Radar map: could not parse bounds from {mfile}")

    # National mosaic removed — no hardcoded bounds needed

    # ── Step 3: fetch frames for each product ─────────────────────────────────
    try:
        ftp.cwd(FTP_RADAR_DIR)
    except Exception as e:
        print(f"  Radar FTP: cannot return to radar dir — {e}")

    meta = {}   # product_id -> { "name", "bounds", "frames": [{"file", "utc"}, ...] }
    total_downloaded = 0
    total_skipped    = 0

    # RADAR_NATIONAL is None — national mosaic removed (no accurate .map file)
    all_products = [{"id": s["id"], "name": s["name"]} for s in RADAR_SITES]

    for product in all_products:
        pid  = product["id"]
        name = product["name"]

        # Find all timestamped PNGs for this product in the directory listing
        # Naming: IDRnnnx.T.yyyymmddhhmm.png  (national: IDR00004.T.yyyymmddhhmm.png)
        prefix  = pid + ".T."
        matches = sorted(
            [f for f in all_files_set if f.startswith(prefix) and f.endswith(".png")],
            reverse=True   # newest first (lexicographic sort works on yyyymmddhhmm)
        )

        if not matches:
            print(f"  Radar [{pid}]: no frames found in listing")
            continue

        selected = matches[:RADAR_FRAMES]
        frames   = []
        downloaded = 0
        skipped    = 0

        for i, fname in enumerate(selected):
            local_path = os.path.join("radar", f"{pid}_{i:02d}.png")

            # Parse UTC timestamp from filename: IDR00004.T.yyyymmddhhmm.png
            utc_str = ""
            m = re.search(r'\.T\.(\d{12})\.png$', fname)
            if m:
                ts = m.group(1)
                utc_str = f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]}T{ts[8:10]}:{ts[10:12]}Z"

            frames.append({"file": f"radar/{pid}_{i:02d}.png", "utc": utc_str})

            # Only download if we don't already have this exact frame
            # Use a sidecar .txt file to record which source file is cached
            stamp_path = local_path + ".src"
            if os.path.exists(local_path) and os.path.exists(stamp_path):
                with open(stamp_path) as sf:
                    if sf.read().strip() == fname:
                        skipped += 1
                        continue

            # Download
            buf = io.BytesIO()
            try:
                ftp.retrbinary(f"RETR {fname}", buf.write)
                buf.seek(0)
                with open(local_path, "wb") as f:
                    f.write(buf.read())
                with open(stamp_path, "w") as sf:
                    sf.write(fname)
                downloaded += 1
            except error_perm:
                print(f"  Radar [{pid}]: {fname} not found (race condition?) — skipping")
            except Exception as e:
                print(f"  Radar [{pid}]: download failed for {fname} — {e}")

        total_downloaded += downloaded
        total_skipped    += skipped
        print(f"  Radar [{pid}] {name}: {len(frames)} frames "
              f"({downloaded} downloaded, {skipped} cached)")

        meta[pid] = {
            "name":   name,
            "bounds": map_bounds.get(pid),
            "frames": frames,
        }

    try:
        ftp.quit()
    except Exception:
        pass

    # ── Composite step — runs after FTP closes ────────────────────────────────
    # Stitches all per-product PNGs into a single full-Australia PNG per frame.
    # Adds 'composite' key to meta if successful.
    # index.html will use 'composite' product if present (12 files vs 600).
    print("  Composite: building full-Australia composite frames...")
    composite_radar_frames(meta, map_bounds)

    # Write radar_meta.json — includes composite product if it was built
    radar_meta = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "frame_count":   RADAR_FRAMES,
        "products":      meta,
    }
    with open(os.path.join("radar", "radar_meta.json"), "w") as f:
        json.dump(radar_meta, f, indent=2)

    print(f"  Radar: {total_downloaded} downloaded, {total_skipped} served from cache — "
          f"radar_meta.json written ({len(meta)} products incl. composite)")
    return {
        "products":   len(meta),
        "downloaded": total_downloaded,
        "cached":     total_skipped,
    }


def fetch_fdr_xml():
    """
    Fetch FDR XML from BOM anonymous FTP for all states.
    Uses ftplib (Python stdlib) — no extra dependencies needed.
    FTP server: ftp.bom.gov.au  path: /anon/gen/fwo/<product_id>.xml
    Returns dict matching the live_data.json fdr structure:
      { "ratings": { <AAC>: { "1": {fd, fbi, st, et}, ... } },
        "times":   { "1": <start_time_local_str>, ... } }
    or None on complete failure.
    """
    import xml.etree.ElementTree as ET
    from ftplib import FTP, error_perm
    import io

    FTP_HOST = "ftp.bom.gov.au"
    FTP_DIR  = "/anon/gen/fwo"

    ratings = {}
    times   = {}
    errors  = []
    total   = 0

    # Open a single FTP connection and reuse it for all 7 state files
    try:
        ftp = FTP(FTP_HOST, timeout=30)
        ftp.login()           # anonymous login (user="anonymous", passwd="")
        ftp.cwd(FTP_DIR)
        print(f"  FDR FTP: connected to {FTP_HOST}{FTP_DIR}")
    except Exception as e:
        print(f"  FDR FTP: connection failed — {e}")
        return None

    for state, product_id in FDR_XML_FEEDS.items():
        filename = product_id + ".xml"
        buf = io.BytesIO()
        try:
            ftp.retrbinary(f"RETR {filename}", buf.write)
            buf.seek(0)
            content = buf.read()
            print(f"  FDR FTP [{state}]: retrieved {filename} ({len(content)} bytes)")
        except error_perm as e:
            print(f"  FDR FTP [{state}]: {filename} not on FTP — likely out of fire season, skipping")
            # Not an error — BOM only publishes FDR XML during each state's fire season
            continue
        except Exception as e:
            print(f"  FDR FTP [{state}]: retrieve failed — {e}")
            errors.append(state)
            continue

        try:
            root = ET.fromstring(content)
            forecast = root.find("forecast")
            if forecast is None:
                print(f"  FDR XML [{state}]: no <forecast> element found")
                errors.append(state)
                continue

            state_count = 0
            for area in forecast.findall("area"):
                aac   = area.get("aac", "")
                atype = area.get("type", "")
                if atype != "fire-district" or not aac:
                    continue

                if aac not in ratings:
                    ratings[aac] = {}

                # Collect all forecast-periods for this area first,
                # then determine the correct index offset.
                # Some state files (e.g. QLD) omit index 0 (the current partial day)
                # and start at index 1. We detect the minimum index present and
                # always map it to Day 1 so no districts are left without Day 1 data.
                fps = []
                for fp in area.findall("forecast-period"):
                    idx_raw = fp.get("index", "")
                    if idx_raw == "":
                        continue
                    try:
                        fps.append((int(idx_raw), fp))
                    except ValueError:
                        continue

                if not fps:
                    continue

                # Determine offset: min index present → Day 1
                min_idx = min(i for i, _ in fps)
                offset = 1 - min_idx  # e.g. min=0 → offset=1, min=1 → offset=0

                for idx_raw_int, fp in fps:
                    idx = idx_raw_int + offset  # remapped to 1-based
                    if idx < 1 or idx > 4:
                        continue

                    st_local = fp.get("start-time-local", "")
                    et_local = fp.get("end-time-local", "")

                    fbi_el = fp.find("element[@type='fire_behaviour_index']")
                    fd_el  = fp.find("text[@type='fire_danger']")

                    fbi = 0
                    fd  = "No Rating"
                    if fbi_el is not None and fbi_el.text:
                        try:
                            fbi = int(fbi_el.text.strip())
                        except ValueError:
                            pass
                    if fd_el is not None and fd_el.text:
                        fd = fd_el.text.strip()

                    idx_str = str(idx)
                    ratings[aac][idx_str] = {
                        "fd":  fd,
                        "fbi": fbi,
                        "st":  st_local,
                        "et":  et_local,
                    }

                    # Only set times if not already set by an earlier state,
                    # but prefer the entry with the earliest start time for Day 1
                    if idx_str not in times and st_local:
                        times[idx_str] = st_local

                    state_count += 1

            total += state_count
            print(f"  FDR XML [{state}]: {state_count} district-period entries")

        except ET.ParseError as e:
            print(f"  FDR XML [{state}]: XML parse error — {e}")
            errors.append(state)
        except Exception as e:
            print(f"  FDR XML [{state}]: error — {e}")
            errors.append(state)

    if not ratings:
        print("  FDR XML: all state fetches failed")
        return None

    try:
        ftp.quit()
    except Exception:
        pass

    print(f"  FDR FTP: {len(ratings)} districts, {len(times)} periods "
          f"({total} entries) — out of season/failed: {errors or 'none'}")
    return {"ratings": ratings, "times": times}


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

# BOM marine wind warning XML products — one per state, only present on FTP when active
# Fetched via anonymous FTP: /anon/gen/fwo/<product_id>.xml
MARINE_WARNING_PRODUCTS = {
    "QLD": "IDQ20085",
    "NSW": "IDN20400",
    "VIC": "IDV20600",
    "SA":  "IDS20201",
    "WA":  "IDW20100",
    "TAS": "IDT20100",
    "NT":  "IDD20105",
}

# Severity attribute codes -> human-readable warning level
MARINE_SEVERITY = {
    "STR": "Strong Wind Warning",
    "GAL": "Gale Warning",
    "STO": "Storm Force Warning",
    "HUR": "Hurricane Force Warning",
}

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
        "url":      "https://www.rfs.nsw.gov.au/feeds/majorIncidents.json",
        "url_xml":  "http://www.rfs.nsw.gov.au/feeds/majorIncidents.xml",
        "url_rss":  "https://www.rfs.nsw.gov.au/feeds/majorIncidents.georss",

        "url_arcgis": (
            "https://services.arcgis.com/cEku21QqeYtjx5kU/arcgis/rest/services/"
            "Current_Incidents/FeatureServer/0/query"
            "?where=1%3D1&outFields=*&f=geojson"
        ),
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
    "wa": {
        "url": "https://www.emergency.wa.gov.au/data/incident_FCAD.json",
        "url_rss": "https://www.emergency.wa.gov.au/data/incident_FCAD.rss",
        "label": "WA DFES",
        "source_url": "https://www.emergency.wa.gov.au/",
        "agency": "DFES",
    },
    "tas": {
        "url": "https://services.thelist.tas.gov.au/arcgis/rest/services/Public/EmergencyManagementPublic/MapServer/72/query?where=1%3D1&outFields=*&f=geojson",
        "label": "TAS TasALERT",
        "source_url": "https://alert.tas.gov.au/",
        "agency": "TFS",
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

    IMPORTANT: The NSW RFS feed embeds ALL incident data inside the 'description'
    property as HTML-encoded 'KEY: Value <br />' pairs. The top-level properties
    only reliably contain: title, category (alert level), pubDate, guid, link.
    All other fields (type, status, size, location, council, agency) must be
    parsed from the description HTML.

    Description format example:
      ALERT LEVEL: Advice <br />LOCATION: Warialda RD, Warialda 2402 <br />
      COUNCIL AREA: Gwydir <br />STATUS: Under control <br />
      TYPE: Grass Fire <br />SIZE: 0 ha <br />
      RESPONSIBLE AGENCY: Rural Fire Service <br />

    Excludes: Not Applicable alert level, planned/prescribed burns, resolved statuses.
    """
    import html as html_module
    incidents = []
    features = data.get("features", []) if isinstance(data, dict) else []

    for f in features:
        p = f.get("properties", {})
        geom = f.get("geometry") or {}

        # Centroid: use Point geometry, else average polygon ring coordinates
        lat, lng = 0.0, 0.0
        if geom.get("type") == "Point":
            coords = geom.get("coordinates", [])
            if len(coords) >= 2:
                lng, lat = float(coords[0]), float(coords[1])
        elif geom.get("type") in ("Polygon", "MultiPolygon"):
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

        # Alert level comes from the top-level 'category' property
        alert_level = normalise_alert_level(p.get("category") or "")

        # Exclude Not Applicable — non-actionable monitoring entries
        if (alert_level or "").lower() in ("not applicable", ""):
            continue

        # Parse description HTML — all the real incident data is here
        # Format: "KEY: Value <br />\nKEY: Value <br />"
        inc_type    = ""
        inc_status  = ""
        location    = ""
        council     = ""
        size        = ""
        agency      = "RFS"

        raw_desc = p.get("description") or ""
        if raw_desc:
            decoded = html_module.unescape(raw_desc)
            # Split on <br /> variants and extract key:value pairs
            for part in re.split(r'<br\s*/?>', decoded, flags=re.IGNORECASE):
                part = re.sub(r'<[^>]+>', '', part).strip()  # strip any remaining tags
                if ':' in part:
                    key, _, val = part.partition(':')
                    key = key.strip().upper()
                    val = val.strip()
                    if key == 'TYPE':
                        inc_type = val
                    elif key == 'STATUS':
                        inc_status = val
                    elif key == 'LOCATION':
                        location = val
                    elif key == 'COUNCIL AREA':
                        council = val
                    elif key == 'SIZE':
                        size = val
                    elif key == 'RESPONSIBLE AGENCY':
                        agency = val

        if is_excluded_incident(inc_type, inc_status):
            continue

        # Extract polygons for site-risk intersection (store as [lng,lat] pairs)
        polys = []
        if geom.get("type") == "Polygon":
            polys = [geom["coordinates"][0]]
        elif geom.get("type") == "MultiPolygon":
            polys = [ring[0] for ring in geom["coordinates"]]

        incidents.append({
            "title":       p.get("title") or "NSW Incident",
            "alertLevel":  alert_level,
            "status":      inc_status,
            "type":        inc_type,
            "size":        size,
            "agency":      agency,
            "updated":     p.get("pubDate") or "",
            "description": location,
            "council":     council,
            "lat":         lat,
            "lng":         lng,
            "polys":       polys,
            "state":       "NSW",
            "sourceUrl":   INCIDENT_FEEDS["nsw"]["source_url"],
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


NSW_BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-AU,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}

def make_rfs_session():
    """Create a requests Session pre-loaded with RFS cookies by visiting the page first."""
    s = requests.Session()
    s.headers.update(NSW_BROWSER_HEADERS)
    try:
        s.get("https://www.rfs.nsw.gov.au/fire-information/fires-near-me",
              timeout=15, allow_redirects=True)
    except Exception:
        pass  # If pre-fetch fails, still try with just headers
    return s


def fetch_nsw_alert_zones(session=None):
    """Fetch and parse NSW RFS IncidentAlerts.xml. Returns (list, error_or_None)."""
    try:
        s = session or make_rfs_session()
        r = s.get(NSW_ALERT_ZONES_URL, timeout=20, headers={
            "Accept": "application/xml, text/xml, */*",
            "Referer": "https://www.rfs.nsw.gov.au/fire-information/fires-near-me",
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


def parse_wa_json(data):
    """
    Parse WA DFES incident_FCAD.json.
    Schema unknown until confirmed — attempt common patterns.
    Expected: GeoJSON FeatureCollection or flat list with lat/lng fields.
    """
    incidents = []

    if isinstance(data, dict) and (data.get("type") == "FeatureCollection" or "features" in data):
        records = data.get("features", [])
        use_geojson = True
    elif isinstance(data, dict) and "incidents" in data:
        records = data["incidents"]
        use_geojson = False
    elif isinstance(data, list):
        records = data
        use_geojson = False
    else:
        print(f"  WA JSON: unexpected structure — keys: {list(data.keys()) if isinstance(data, dict) else type(data)}")
        return incidents

    print(f"  WA JSON: {len(records)} raw records")
    if records:
        first = records[0].get("properties", records[0]) if use_geojson else records[0]
        print(f"  WA JSON schema keys: {list(first.keys())[:15]}")

    for rec in records:
        if use_geojson:
            p = rec.get("properties", {})
            geom = rec.get("geometry") or {}
            coords = geom.get("coordinates", [])
            if geom.get("type") == "Point" and len(coords) >= 2:
                lng, lat = float(coords[0]), float(coords[1])
            else:
                lat = float(p.get("Latitude") or p.get("latitude") or p.get("lat") or 0)
                lng = float(p.get("Longitude") or p.get("longitude") or p.get("lng") or 0)
        else:
            p = rec if isinstance(rec, dict) else {}
            lat = float(p.get("Latitude") or p.get("latitude") or p.get("lat") or 0)
            lng = float(p.get("Longitude") or p.get("longitude") or p.get("lng") or 0)

        if not lat or not lng:
            continue

        inc_type   = (p.get("Type") or p.get("type") or p.get("IncidentType") or
                      p.get("incident_type") or p.get("category") or "")
        inc_status = (p.get("Status") or p.get("status") or p.get("IncidentStatus") or "")

        if is_excluded_incident(inc_type, inc_status):
            continue

        alert_level = normalise_alert_level(
            p.get("AlertLevel") or p.get("alert_level") or p.get("WarningLevel") or
            p.get("Severity") or p.get("severity") or ""
        )

        title = (p.get("Name") or p.get("name") or p.get("Title") or p.get("title") or
                 p.get("IncidentName") or p.get("Location") or p.get("location") or "WA Incident")
        updated = (p.get("LastUpdated") or p.get("last_updated") or p.get("UpdateDateTime") or
                   p.get("Updated") or p.get("updated") or "")

        incidents.append({
            "title":       title,
            "alertLevel":  alert_level,
            "status":      inc_status,
            "type":        inc_type,
            "size":        str(p.get("AreaBurnt") or p.get("Size") or p.get("size") or ""),
            "agency":      "DFES",
            "updated":     str(updated),
            "description": str(p.get("Location") or p.get("Suburb") or p.get("suburb") or ""),
            "lat":         lat,
            "lng":         lng,
            "polys":       [],
            "state":       "WA",
            "sourceUrl":   INCIDENT_FEEDS["wa"]["source_url"],
        })

    return incidents


def parse_wa_rss(xml_text):
    """
    Parse WA DFES incident_FCAD.rss — GeoRSS format.
    Expected: RSS 2.0 with <geo:lat>/<geo:long> or <georss:point> elements.
    Falls back to BOM-style RSS parsing if structure differs.
    """
    incidents = []
    try:
        root = ET.fromstring(xml_text.strip())
    except ET.ParseError as e:
        print(f"  WA RSS: XML parse error — {e}")
        return incidents

    GEO_NS    = "http://www.w3.org/2003/01/geo/wgs84_pos#"
    GEORSS_NS = "http://www.georss.org/georss"

    items = root.findall(".//item")
    print(f"  WA RSS: {len(items)} raw items")

    if items:
        # Log first item's child tags for schema discovery
        tags = [child.tag for child in items[0]]
        print(f"  WA RSS first item tags: {tags[:15]}")

    for item in items:
        def get(tag, ns=None):
            el = item.find(f"{{{ns}}}{tag}") if ns else item.find(tag)
            return el.text.strip() if el is not None and el.text else ""

        title   = get("title")
        link    = get("link") or get("guid")
        desc    = get("description")
        pub     = get("pubDate")

        if not title:
            continue

        # Extract coordinates — try geo:lat/geo:long, georss:point, then description
        lat, lng = 0.0, 0.0

        lat_el = item.find(f"{{{GEO_NS}}}lat")
        lng_el = item.find(f"{{{GEO_NS}}}long")
        if lat_el is not None and lng_el is not None:
            try:
                lat = float(lat_el.text)
                lng = float(lng_el.text)
            except (ValueError, TypeError):
                pass

        if not lat:
            point_el = item.find(f"{{{GEORSS_NS}}}point")
            if point_el is not None and point_el.text:
                parts = point_el.text.strip().split()
                if len(parts) >= 2:
                    try:
                        lat, lng = float(parts[0]), float(parts[1])
                    except ValueError:
                        pass

        if not lat or not lng:
            continue

        # Alert level from title — WA typically prefixes with warning level
        title_l = title.lower()
        alert_level = ("Emergency Warning" if "emergency warning" in title_l else
                       "Watch and Act"     if "watch and act"     in title_l else
                       "Advice"            if "advice"            in title_l else "")

        # Type from title or description
        inc_type = ""
        if "bushfire" in title_l or "fire" in title_l:
            inc_type = "Bush Fire"
        elif "flood" in title_l:
            inc_type = "Flood"
        elif "hazard reduction" in title_l or "prescribed burn" in title_l:
            inc_type = "hazard reduction burn"

        if is_excluded_incident(inc_type, ""):
            continue

        # Strip alert level prefix from title
        clean_title = title
        for prefix in ["Emergency Warning - ", "Watch and Act - ", "Advice - "]:
            if clean_title.startswith(prefix):
                clean_title = clean_title[len(prefix):]
                break

        incidents.append({
            "title":       clean_title or title,
            "alertLevel":  alert_level,
            "status":      "",
            "type":        inc_type,
            "size":        "",
            "agency":      "DFES",
            "updated":     pub,
            "description": desc[:200] if desc else "",
            "lat":         lat,
            "lng":         lng,
            "polys":       [],
            "state":       "WA",
            "sourceUrl":   INCIDENT_FEEDS["wa"]["source_url"],
        })

    return incidents


def fetch_wa_incidents():
    """
    Fetch WA DFES incidents.
    Strategy:
      1. Try JSON feed with Referer + Accept headers (EmergencyWA app-style request)
      2. If JSON redirects to HTML or fails, try RSS feed with same headers
      3. If both fail, return empty list — BOM WA warnings (IDZ00060) cover serious events
    Returns (list, method_used, error_or_None)
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; WeatherMap/1.0)",
        "Referer":    "https://www.emergency.wa.gov.au/",
        "Accept":     "application/json, text/javascript, */*",
        "X-Requested-With": "XMLHttpRequest",
    }

    # ── Attempt 1: JSON feed ─────────────────────────────────────────────────
    try:
        r = requests.get(INCIDENT_FEEDS["wa"]["url"], timeout=20, headers=headers,
                         allow_redirects=True)
        content_type = r.headers.get("Content-Type", "")
        print(f"  WA JSON: HTTP {r.status_code}, Content-Type: {content_type[:60]}")

        if r.status_code == 200 and "html" not in content_type.lower():
            text = r.text.strip().lstrip('\ufeff')
            obj = json.loads(text)
            incidents = parse_wa_json(obj)
            print(f"  WA incidents (JSON): {len(incidents)} active")
            return incidents, "json", None
        else:
            print("  WA JSON: redirected to HTML or wrong content-type — trying RSS")

    except (requests.RequestException, json.JSONDecodeError) as e:
        print(f"  WA JSON: failed — {e} — trying RSS")

    # ── Attempt 2: RSS feed ──────────────────────────────────────────────────
    rss_headers = {**headers, "Accept": "application/rss+xml, application/xml, text/xml, */*"}
    try:
        r = requests.get(INCIDENT_FEEDS["wa"]["url_rss"], timeout=20, headers=rss_headers,
                         allow_redirects=True)
        content_type = r.headers.get("Content-Type", "")
        print(f"  WA RSS: HTTP {r.status_code}, Content-Type: {content_type[:60]}")

        if r.status_code == 200 and "html" not in content_type.lower():
            incidents = parse_wa_rss(r.text)
            print(f"  WA incidents (RSS): {len(incidents)} active")
            return incidents, "rss", None
        else:
            print("  WA RSS: also redirected to HTML — both feeds inaccessible")
            return [], "none", "Both JSON and RSS feeds returned HTML (likely access-restricted)"

    except requests.RequestException as e:
        print(f"  WA RSS: failed — {e}")
        return [], "none", str(e)



def parse_tas_incidents(data):
    """
    Parse TAS TasALERT warnings from LIST Tasmania ArcGIS GeoJSON service.
    Endpoint: services.thelist.tas.gov.au/.../EmergencyManagementPublic/MapServer/72/query

    Confirmed fields from service metadata:
      ALERT_TYPE      — e.g. "Bushfire - Emergency Warning", "Bushfire - Watch and Act",
                          "Bushfire - Advice", "Flood - Emergency Warning", etc.
      SENDER_NAME     — issuing agency name
      TASALERT_LINK   — URL to full warning details
      EFFECTIVE_FROM_DATE, EXPIRES_DATE, REPLICATED_DATE — epoch ms timestamps

    Geometry: esriGeometryPoint — [lng, lat] GeoJSON standard
    """
    incidents = []
    features = data.get("features", []) if isinstance(data, dict) else []
    print(f"  TAS GeoJSON: {len(features)} raw features")

    if features:
        sample_p = features[0].get("properties", {})
        print(f"  TAS schema keys: {list(sample_p.keys())[:15]}")
        print(f"  TAS sample — alert_type:{sample_p.get('ALERT_TYPE')} sender:{sample_p.get('SENDER_NAME')}")

    for f in features:
        p = f.get("properties", {})
        geom = f.get("geometry") or {}
        coords = geom.get("coordinates", [])

        if geom.get("type") == "Point" and len(coords) >= 2:
            lng, lat = float(coords[0]), float(coords[1])
        else:
            continue

        if not lat or not lng:
            continue

        alert_type = p.get("ALERT_TYPE") or ""
        alert_lower = alert_type.lower()

        # Parse alert level and incident type from ALERT_TYPE field
        # Format: "Hazard Type - Alert Level" e.g. "Bushfire - Emergency Warning"
        alert_level = ("Emergency Warning" if "emergency warning" in alert_lower else
                       "Watch and Act"     if "watch and act"     in alert_lower else
                       "Advice"            if "advice"            in alert_lower else "")

        inc_type = ""
        if "bushfire" in alert_lower or "fire" in alert_lower:
            inc_type = "Bush Fire"
        elif "flood" in alert_lower:
            inc_type = "Flood"
        elif "storm" in alert_lower or "severe weather" in alert_lower:
            inc_type = "Severe Weather"

        # Skip if no actionable alert level
        if not alert_level:
            continue

        if is_excluded_incident(inc_type, ""):
            continue

        sender = p.get("SENDER_NAME") or "TFS"
        link = p.get("TASALERT_LINK") or INCIDENT_FEEDS["tas"]["source_url"]

        # Timestamps are epoch milliseconds
        updated = ""
        rep_date = p.get("REPLICATED_DATE") or p.get("EFFECTIVE_FROM_DATE")
        if rep_date:
            try:
                from datetime import datetime, timezone
                updated = datetime.fromtimestamp(int(rep_date) / 1000, tz=timezone.utc).isoformat()
            except (ValueError, TypeError):
                pass

        # Title: use ALERT_TYPE as it's the most descriptive field available
        title = alert_type or "TAS Incident"

        incidents.append({
            "title":       title,
            "alertLevel":  alert_level,
            "status":      "",
            "type":        inc_type,
            "size":        "",
            "agency":      sender,
            "updated":     updated,
            "description": "",
            "lat":         lat,
            "lng":         lng,
            "polys":       [],
            "state":       "TAS",
            "sourceUrl":   link or INCIDENT_FEEDS["tas"]["source_url"],
        })

    return incidents


def fetch_nsw_incidents_with_fallback(feed_cfg, rfs_session=None):
    """
    Fetch NSW RFS incidents with a 4-tier fallback chain:
      1. XML feed     — http://www.rfs.nsw.gov.au/feeds/majorIncidents.xml (plain HTTP)
      2. GeoRSS feed  — https://www.rfs.nsw.gov.au/feeds/majorIncidents.georss
      3. JSON feed    — https://www.rfs.nsw.gov.au/feeds/majorIncidents.json
      4. ArcGIS REST  — public ESRI endpoint, completely separate infrastructure
    Returns (incidents_list, method_used, error_or_None)
    Note: If RFS has a site-wide outage or WAF block, all rfs.nsw.gov.au tiers
    will fail together. ArcGIS is the only truly independent fallback.
    """
    import random
    # Small jitter delay (2–6s) before hitting RFS — avoids rate limiting on shared GitHub Actions IPs
    time.sleep(random.uniform(2, 6))

    browser_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "en-AU,en;q=0.9",
        "Cache-Control": "no-cache",
        "Pragma":        "no-cache",
    }
    last_err = None

    # ── Tier 1: XML (plain HTTP — may bypass Cloudflare HTTPS interception) ─
    xml_url = feed_cfg.get("url_xml")
    if xml_url:
        try:
            r = requests.get(xml_url, timeout=20, headers={
                **browser_headers,
                "Accept": "application/xml, text/xml, */*",
            })
            r.raise_for_status()
            if "html" in r.headers.get("Content-Type", ""):
                raise ValueError("WAF block — returned HTML")
            incidents = parse_nsw_georss(r.text)
            print(f"  NSW incidents: {len(incidents)} active (method: xml)")
            return incidents, "xml", None
        except Exception as e:
            last_err = str(e)
            print(f"  NSW XML: failed — {e}")
            time.sleep(random.uniform(1, 3))  # brief pause before next tier

    # ── Tier 2: GeoRSS ──────────────────────────────────────────────────────
    rss_url = feed_cfg.get("url_rss")
    if rss_url:
        try:
            r = requests.get(rss_url, timeout=20, headers={
                **browser_headers,
                "Accept": "application/rss+xml, application/xml, text/xml, */*",
                "Referer": "https://www.rfs.nsw.gov.au/fire-information/fires-near-me",
            })
            r.raise_for_status()
            if "html" in r.headers.get("Content-Type", ""):
                raise ValueError("WAF block — returned HTML")
            incidents = parse_nsw_georss(r.text)
            print(f"  NSW incidents: {len(incidents)} active (method: georss)")
            return incidents, "georss", None
        except Exception as e:
            last_err = str(e)
            print(f"  NSW GeoRSS: failed — {e}")
            time.sleep(random.uniform(1, 3))  # brief pause before next tier

    # ── Tier 3: JSON with session ────────────────────────────────────────────
    json_url = feed_cfg.get("url")
    if json_url:
        try:
            s = rfs_session or make_rfs_session()
            r = s.get(json_url, timeout=20, headers={
                "Accept": "application/json, */*",
                "Referer": "https://www.rfs.nsw.gov.au/fire-information/fires-near-me",
            })
            r.raise_for_status()
            if "html" in r.headers.get("Content-Type", ""):
                raise ValueError("WAF block — returned HTML")
            obj = json.loads(r.text.strip().lstrip("\ufeff"))
            incidents = parse_nsw_incidents(obj)
            print(f"  NSW incidents: {len(incidents)} active (method: json)")
            return incidents, "json", None
        except Exception as e:
            last_err = str(e)
            print(f"  NSW JSON: failed — {e}")
            time.sleep(random.uniform(1, 3))

    # ── Tier 4: ArcGIS public REST (separate infrastructure from rfs.nsw.gov.au) ─
    arcgis_url = feed_cfg.get("url_arcgis")
    if arcgis_url:
        try:
            r = requests.get(arcgis_url, timeout=20, headers={
                **browser_headers,
                "Accept": "application/json, */*",
            })
            r.raise_for_status()
            obj = json.loads(r.text.strip())
            incidents = parse_nsw_incidents(obj)
            print(f"  NSW incidents: {len(incidents)} active (method: arcgis)")
            return incidents, "arcgis", None
        except Exception as e:
            last_err = str(e)
            print(f"  NSW ArcGIS: failed — {e}")

    # ── Manual cache fallback (Option A) ────────────────────────────────────
    # If all live tiers fail, try reading cached_nsw.xml from the repo.
    # This file must be manually saved and committed — it is NOT auto-updated.
    # Data will be stale; logged clearly so operators know it's not live.
    cache_path = os.path.join(os.path.dirname(__file__), "cached_nsw.xml")
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cached_xml = f.read()
            incidents = parse_nsw_georss(cached_xml)
            cache_mtime = os.path.getmtime(cache_path)
            cache_age_hrs = (time.time() - cache_mtime) / 3600
            print(f"  NSW incidents: {len(incidents)} active (method: MANUAL CACHE — {cache_age_hrs:.1f}h old — NOT LIVE DATA)")
            return incidents, "manual_cache", f"Using cached_nsw.xml ({cache_age_hrs:.1f}h old) — all live feeds blocked"
        except Exception as e:
            print(f"  NSW cache read failed — {e}")

    print(f"  NSW incidents: 0 active (all feeds unavailable — likely RFS site outage)")
    return [], "none", f"All NSW feeds unavailable: {last_err}"


def parse_nsw_georss(xml_text):
    """
    Parse NSW RFS GeoRSS feed into the same normalised incident format as parse_nsw_incidents().
    GeoRSS uses RSS 2.0 + georss:point/polygon extensions.
    Fields in <description> are the same HTML key:value pairs as the JSON feed.
    """
    import html as html_module
    import xml.etree.ElementTree as ET

    incidents = []
    NS = {
        "georss": "http://www.georss.org/georss",
        "geo":    "http://www.w3.org/2003/01/geo/wgs84_pos#",
    }

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        print(f"  NSW GeoRSS parse error: {e}")
        return incidents

    channel = root.find("channel")
    items = channel.findall("item") if channel is not None else root.findall(".//item")

    for item in items:
        title    = (item.findtext("title") or "").strip()
        category = (item.findtext("category") or "").strip()
        link     = (item.findtext("link") or "").strip()
        pub_date = (item.findtext("pubDate") or "").strip()
        desc_raw = item.findtext("description") or ""

        # Alert level is in <category>
        alert_level = normalise_alert_level(category)
        if (alert_level or "").lower() in ("not applicable", ""):
            continue

        # Parse description HTML for key:value pairs
        import re
        decoded = html_module.unescape(desc_raw)
        inc_type = inc_status = location = council = size = agency = ""
        agency = "RFS"
        for part in re.split(r'<br\s*/?>', decoded, flags=re.IGNORECASE):
            part = re.sub(r'<[^>]+>', '', part).strip()
            if ':' in part:
                k, _, v = part.partition(':')
                k, v = k.strip().upper(), v.strip()
                if k == 'TYPE':           inc_type   = v
                elif k == 'STATUS':       inc_status = v
                elif k == 'LOCATION':     location   = v
                elif k == 'COUNCIL AREA': council    = v
                elif k == 'SIZE':         size       = v
                elif k == 'RESPONSIBLE AGENCY': agency = v

        if is_excluded_incident(inc_type, inc_status):
            continue

        # Coordinates: <georss:point>lat lng</georss:point>
        lat = lng = None
        polys = []
        pt = item.find("georss:point", NS)
        if pt is not None and pt.text:
            parts = pt.text.strip().split()
            if len(parts) == 2:
                try:
                    lat, lng = float(parts[0]), float(parts[1])
                except ValueError:
                    pass

        poly_el = item.find("georss:polygon", NS)
        if poly_el is not None and poly_el.text:
            coords = poly_el.text.strip().split()
            try:
                ring = [[float(coords[i+1]), float(coords[i])]
                        for i in range(0, len(coords)-1, 2)]
                polys = [ring]
                if lat is None and ring:
                    lng, lat = ring[0][0], ring[0][1]
            except (ValueError, IndexError):
                pass

        if lat is None:
            continue

        incidents.append({
            "title":       title or "NSW Incident",
            "alertLevel":  alert_level,
            "status":      inc_status,
            "type":        inc_type,
            "size":        size,
            "agency":      agency,
            "updated":     pub_date,
            "description": location,
            "council":     council,
            "lat":         lat,
            "lng":         lng,
            "polys":       polys,
            "state":       "NSW",
            "sourceUrl":   INCIDENT_FEEDS["nsw"]["source_url"],
        })

    return incidents


def fetch_incidents(key, feed_cfg, rfs_session=None):
    """Fetch and parse one state's incident feed. Returns (list, error_or_None)."""
    # WA has its own dedicated fetch function with JSON->RSS->fallback logic
    if key == "wa":
        return [], None

    # NSW uses its own multi-tier fallback chain
    if key == "nsw":
        incidents, method, err = fetch_nsw_incidents_with_fallback(feed_cfg, rfs_session)
        return incidents, err

    url = feed_cfg["url"]
    try:
        r = requests.get(url, timeout=20, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "application/json, application/xml, text/xml, */*",
            "Accept-Language": "en-AU,en;q=0.9",
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

        if key == "vic":
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
        elif key == "tas":
            incidents = parse_tas_incidents(obj)
        else:
            incidents = []

        print(f"  {key.upper()} incidents: {len(incidents)} active")
        return incidents, None

    except requests.RequestException as e:
        print(f"  {key.upper()} incidents: fetch failed — {e}")
        return [], str(e)


def parse_marine_warning_xml(xml_text, state):
    """Parse a BOM marine wind warning XML product (e.g. IDQ20085.xml).

    Extracts today's hazards only (forecast-period index="0") from the
    <warning> block. For each hazard returns:
      - level:      human-readable warning level (e.g. "Strong Wind Warning")
      - severity:   raw BOM severity code (e.g. "STR")
      - zones:      list of affected zone descriptions
      - aac_codes:  list of AAC zone identifiers
      - summary:    single-line summary text from warning_areas element

    Returns a dict with keys "state", "product", "issued_utc", "hazards",
    or None if the XML cannot be parsed or contains no today hazards.
    """
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        print(f"  Marine [{state}]: XML parse error — {e}")
        return None

    # Extract issue time from amoc block
    issued_utc = ""
    issue_el = root.find(".//issue-time-utc")
    if issue_el is not None and issue_el.text:
        issued_utc = issue_el.text.strip()

    product_id = ""
    id_el = root.find(".//identifier")
    if id_el is not None and id_el.text:
        product_id = id_el.text.strip()

    # Extract warning summary text (human-readable overview)
    summary_lines = []
    for p in root.findall(".//warning-summary/p"):
        if p.text:
            summary_lines.append(p.text.strip())

    # Extract today's hazards — look for any forecast-period with an index attribute
    # Note: BOM XML structure varies by state:
    #   - Most states: forecast-period[@index='0'] is today, direct child of <warning>
    #   - WA: forecast-period[@index='1'] is the first hazard day, nested inside <area>
    #   - Use .// to search all descendants, and accept any indexed period that has MWW hazards
    hazards = []
    seen_aacs = set()  # deduplicate across periods

    for fp in root.findall(".//forecast-period[@index]"):
        for hazard in fp.findall("hazard"):
            # Only process marine wind warning hazards
            if hazard.get("type") != "MWW":
                continue

            severity_code = hazard.get("severity", "")
            level = MARINE_SEVERITY.get(severity_code, severity_code)

            # Collect affected zones
            zones = []
            aac_codes = []
            for area in hazard.findall(".//area-list/area"):
                desc = area.get("description", "").strip()
                aac  = area.get("aac", "").strip()
                if desc:
                    zones.append(desc)
                if aac and aac not in seen_aacs:
                    aac_codes.append(aac)
                    seen_aacs.add(aac)

            # Short summary text from warning_areas element
            areas_text = ""
            for txt in hazard.findall("text"):
                if txt.get("type") == "warning_areas" and txt.text:
                    areas_text = txt.text.strip()

            if zones:
                hazards.append({
                    "level":      level,
                    "severity":   severity_code,
                    "zones":      zones,
                    "aac_codes":  aac_codes,
                    "summary":    areas_text,
                })

    if not hazards:
        return None

    return {
        "state":       state,
        "product":     product_id,
        "issued_utc":  issued_utc,
        "overview":    summary_lines,
        "hazards":     hazards,
    }


def fetch_marine_warnings():
    """Fetch BOM marine wind warning XML products via anonymous FTP.

    Files live at /anon/gen/fwo/<product_id>.xml and are only present
    on the FTP when a warning is active for that state — a 550 error
    means no current warning, which is normal and handled gracefully.

    Returns a dict keyed by state code containing parsed warning data,
    only for states with active warnings. Empty dict if FTP unreachable.
    """
    from ftplib import FTP, error_perm
    import io

    results = {}

    try:
        ftp = FTP(FTP_RADAR_HOST, timeout=30)
        ftp.login()
        ftp.cwd("/anon/gen/fwo")
    except Exception as e:
        print(f"  Marine warnings FTP: connection failed — {e}")
        return results

    for state, product_id in MARINE_WARNING_PRODUCTS.items():
        filename = f"{product_id}.xml"
        buf = io.BytesIO()
        try:
            ftp.retrbinary(f"RETR {filename}", buf.write)
            buf.seek(0)
            xml_text = buf.read().decode("utf-8", errors="replace")
            parsed = parse_marine_warning_xml(xml_text, state)
            if parsed:
                results[state] = parsed
                n = len(parsed["hazards"])
                print(f"  Marine [{state}]: {n} hazard(s) — "
                      f"{', '.join(h['level'] for h in parsed['hazards'])}")
            else:
                print(f"  Marine [{state}]: file present but no today hazards")
        except error_perm:
            # 550 = file not found = no active warning for this state — normal
            print(f"  Marine [{state}]: no active warning")
        except Exception as e:
            print(f"  Marine [{state}]: fetch error — {e}")

    try:
        ftp.quit()
    except Exception:
        pass

    print(f"  Marine warnings: {len(results)} state(s) with active warnings")
    return results


def _ensure_radar_gitignore():
    """Ensure the repo-level .gitignore excludes individual radar PNGs and .map files.
    Only composite_*.png and radar_meta.json are committed to the repo.
    Appends missing rules only — never rewrites existing content.
    Called once per run before fetch_radar_frames().
    """
    gitignore_path = ".gitignore"
    required_rules = {
        "radar/IDR*.png": "# Individual radar product PNGs — local working files only",
        "radar/*.map":    "# Radar .map files — cached locally after first FTP fetch",
        "radar/*.src":    "# Radar .src sidecar cache markers",
    }
    existing = ""
    if os.path.exists(gitignore_path):
        with open(gitignore_path, encoding='utf-8') as f:
            existing = f.read()

    lines_to_add = []
    for rule, comment in required_rules.items():
        if rule not in existing:
            lines_to_add.append(comment)
            lines_to_add.append(rule)

    if lines_to_add:
        with open(gitignore_path, "a", encoding='utf-8') as f:
            f.write("\n# --- Radar working files (added by fetch_live_data.py) ---\n")
            f.write("\n".join(lines_to_add) + "\n")
        added = [l for l in lines_to_add if not l.startswith("#")]
        print(f"  .gitignore updated — added: {added}")
    # else: all rules already present — nothing to do


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

    # Create one shared RFS session (visits RFS page once to get cookies,
    # reused for both majorIncidents.json and IncidentAlerts.xml)
    print("  Establishing RFS session...")
    rfs_session = make_rfs_session()

    for key, feed_cfg in INCIDENT_FEEDS.items():
        if key == "wa":
            continue  # WA handled separately below
        inc_list, inc_err = fetch_incidents(key, feed_cfg, rfs_session=rfs_session if key == "nsw" else None)
        incidents[key] = inc_list
        total_incidents += len(inc_list)
        if inc_err:
            incident_errors[key] = inc_err

    # ── Fetch WA incidents (JSON→RSS→fallback) ────────────────────────────────
    print("Fetching WA incidents...")
    wa_list, wa_method, wa_err = fetch_wa_incidents()
    incidents["wa"] = wa_list
    total_incidents += len(wa_list)
    if wa_err:
        incident_errors["wa"] = wa_err
        print(f"  WA fallback: BOM WA warnings (IDZ00060) cover serious events")
    print(f"  WA incidents: {len(wa_list)} active (method: {wa_method})")

    # ── Fetch NSW RFS alert zones (CAP-AU polygons) ───────────────────────────
    print("Fetching NSW alert zones...")
    nsw_alert_zones, zones_err = fetch_nsw_alert_zones(session=rfs_session)
    if zones_err:
        incident_errors["nsw_zones"] = zones_err

    # Merge fetch errors
    errors.update(incident_errors)

    # ── Fetch BOM marine wind warnings via FTP ───────────────────────────────
    print("Fetching BOM marine wind warnings...")
    marine_warnings = fetch_marine_warnings()

    # ── Fetch BOM radar frames via FTP ───────────────────────────────────────
    print("Fetching BOM radar frames...")

    # Ensure individual product PNGs and .map files are gitignored —
    # only composite_*.png and radar_meta.json are committed to the repo.
    _ensure_radar_gitignore()

    radar_result = fetch_radar_frames()
    if radar_result:
        print(f"  Radar: {radar_result['products']} products ready")
    else:
        print("  Radar: fetch failed or skipped")
        errors["radar"] = "fetch failed"

    # ── Fetch FDR XML from BOM anonymous FTP ─────────────────────────────────
    print("Fetching FDR ratings from BOM FTP XML...")
    fdr_data = fetch_fdr_xml()
    if fdr_data:
        print(f"  FDR: {len(fdr_data['ratings'])} districts ready")
    else:
        print("  FDR: fetch failed")
        errors["fdr_xml"] = "fetch failed"

    # Build output
    now_utc = datetime.now(timezone.utc).isoformat()
    output = {
        "generated_utc":      now_utc,
        "last_checked_utc":   now_utc,   # Always updated — ensures git always has a change to commit
        "warning_count":      len(unique_warnings),
        "warnings":           unique_warnings,
        "fetch_errors":       errors,
        "incidents":          incidents,
        "incident_count":     total_incidents,
        "nsw_alert_zones":    nsw_alert_zones,
        "wa_feed_method":     wa_method,
        "fdr":                fdr_data,
        "marine_warnings":    marine_warnings,
    }

    with open("live_data.json", "w") as f:
        json.dump(output, f, indent=2)

    print(f"Done — {len(unique_warnings)} warnings, {total_incidents} incidents, "
          f"{len(nsw_alert_zones)} NSW alert zones, "
          f"{len(marine_warnings)} state(s) with marine warnings written to live_data.json")
    if errors:
        print(f"Fetch errors: {errors}")

    # ── Git commit and push ───────────────────────────────────────────────────
    git_push()


def git_push():
    """Commit live_data.json and push to GitHub.
    Requires git to be installed and the repo already cloned with credentials cached.
    Safe to run repeatedly — skips commit if there are no changes.
    """
    import subprocess

    def run(cmd, check=True):
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True
        )
        if result.stdout.strip():
            print(f"  git: {result.stdout.strip()}")
        if result.stderr.strip():
            # stderr isn't always an error for git — filter real problems
            for line in result.stderr.strip().splitlines():
                if any(w in line.lower() for w in ["error", "fatal", "rejected"]):
                    print(f"  git ERROR: {line}")
        if check and result.returncode != 0:
            # Don't raise — just warn, so a git failure doesn't break the whole run
            print(f"  git: command failed (exit {result.returncode}): {cmd}")
        return result

    print("Pushing live_data.json and radar composites to GitHub...")

    # Make sure we're on the right branch and up to date
    run("git pull --rebase --autostash", check=False)

    # Stage live_data.json
    run("git add live_data.json")

    # Stage composite PNGs and radar_meta.json only.
    # Individual product PNGs (IDRxxx_NN.png) and .map files stay local —
    # they are working files used to build the composites, not served to the browser.
    # .src sidecar files are local cache markers only — never committed.
    run("git add radar/composite_*.png radar/radar_meta.json", check=False)

    # Commit with timestamp
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    run(f'git commit -m "chore: update live_data + radar composites {ts} [skip ci]"')

    # Push
    result = run("git push", check=False)
    if result.returncode == 0:
        print("  git: pushed successfully")
    else:
        print("  git: push failed — check credentials or network")


if __name__ == "__main__":
    main()
