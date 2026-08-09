// ============================================================================
// watchtower_nearme.js
// Mobile "Near Me" personal-risk view for WatchTower.
// ============================================================================

var NEARME_STATE = {
  radiusKm: 50,
  userLat: null,
  userLng: null,
  accuracy: null,        // GPS accuracy in metres, for the Info tab emergency-services block
  fixTime: null,         // Date object — when the current location fix was taken
  locStatus: 'idle',
  hazards: [],           // card list — filtered by radius + type/severity
  mapHazards: [],        // map markers — filtered by type/severity ONLY, no radius cutoff
  map: null,
  markersLayer: null,
  userMarker: null,
  radiusCircle: null,
  filters: null,
  activeTab: 'nearme',
  lastDataUpdate: null,   // Date — when live_data.json was last successfully loaded
  knownHazardKeys: null,  // snapshot of all hazard keys nationally, from the previous data refresh
  newHazardKeys: null     // keys present now but not in the previous refresh — flagged with a "NEW" badge
};

function nearMeShouldShow() {
  var stored = localStorage.getItem('wt_view_mode');
  if (stored === 'nearme') return true;
  if (stored === 'desktop') return false;
  return window.innerWidth <= 768;
}

function nearMeSetViewMode(mode) {
  if (mode === 'auto') localStorage.removeItem('wt_view_mode');
  else localStorage.setItem('wt_view_mode', mode);
  nearMeApplyViewMode();
}

function nearMeApplyViewMode() {
  var show = nearMeShouldShow();
  var nm = document.getElementById('nearme-view');
  var app = document.getElementById('app');
  if (!nm || !app) return;
  if (show) {
    nm.style.display = 'flex';
    app.style.display = 'none';
    nearMeInit();
  } else {
    nm.style.display = 'none';
    app.style.display = '';
  }
}

window.addEventListener('resize', function () {
  if (!localStorage.getItem('wt_view_mode')) nearMeApplyViewMode();
});

var _nearMeInitDone = false;
function nearMeInit() {
  if (_nearMeInitDone) {
    if (NEARME_STATE.map) setTimeout(function () { NEARME_STATE.map.invalidateSize(); }, 50);
    return;
  }
  _nearMeInitDone = true;
  nearMeLoadFilters();
  nearMeInitMap();
  nearMeRenderRadiusPills();
  nearMeRestoreSplit();
  NEARME_STATE.lastDataUpdate = new Date();
  nearMeSnapshotHazardKeys(); // baseline — nothing flagged "new" until the next refresh
  nearMeRenderUpdatedText();
  setInterval(nearMeRenderUpdatedText, 30000); // keep the "X min ago" text ticking over
  nearMeRequestLocation();
}

function nearMeSetSplit(pct) {
  pct = Number(pct);
  if (!pct || pct < 20) pct = 20;
  if (pct > 80) pct = 80;
  var mapEl = document.getElementById('nearme-map');
  var bodyEl = document.getElementById('nearme-body');
  if (mapEl) mapEl.style.flex = pct + ' 1 0';
  if (bodyEl) bodyEl.style.flex = (100 - pct) + ' 1 0';
  localStorage.setItem('wt_nearme_split', String(pct));
  if (NEARME_STATE.map) setTimeout(function () { NEARME_STATE.map.invalidateSize(); }, 260);
}

function nearMeRestoreSplit() {
  var saved = localStorage.getItem('wt_nearme_split');
  var pct = saved ? Number(saved) : 66; // default 2/3 map, 1/3 list
  var slider = document.getElementById('nearme-split-slider');
  if (slider) slider.value = pct;
  nearMeSetSplit(pct);
}

function nearMeRequestLocation() {
  NEARME_STATE.locStatus = 'requesting';
  nearMeUpdateStatusLine();

  if (!navigator.geolocation) {
    NEARME_STATE.locStatus = 'unsupported';
    nearMeUpdateStatusLine();
    nearMeFallbackNational();
    return;
  }

  navigator.geolocation.getCurrentPosition(
    function (pos) {
      NEARME_STATE.userLat = pos.coords.latitude;
      NEARME_STATE.userLng = pos.coords.longitude;
      NEARME_STATE.accuracy = pos.coords.accuracy;
      NEARME_STATE.fixTime = new Date(pos.timestamp);
      NEARME_STATE.locStatus = 'granted';
      nearMeUpdateStatusLine();
      nearMeComputeHazards();
      nearMeFitMapToRadius();
      if (NEARME_STATE.activeTab === 'info') nearMeRenderInfoTab();
    },
    function () {
      NEARME_STATE.locStatus = 'denied';
      nearMeUpdateStatusLine();
      nearMeFallbackNational();
      if (NEARME_STATE.activeTab === 'info') nearMeRenderInfoTab();
    },
    { enableHighAccuracy: false, timeout: 10000, maximumAge: 300000 }
  );
}

function nearMeFallbackNational() {
  var list = nearMeFlattenHazards(null, null, null);
  list.sort(function (a, b) { return b.severityRank - a.severityRank; });
  NEARME_STATE.hazards = list.slice(0, 15);
  NEARME_STATE.mapHazards = list; // map shows everything matching filters, uncapped
  nearMeRenderCards();
  nearMeUpdateMap();
}

function nearMeHaversine(lat1, lng1, lat2, lng2) {
  if (typeof haversineKm === 'function') return haversineKm(lat1, lng1, lat2, lng2);
  var R = 6371;
  var dLat = (lat2 - lat1) * Math.PI / 180;
  var dLng = (lng2 - lng1) * Math.PI / 180;
  var a = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
    Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
    Math.sin(dLng / 2) * Math.sin(dLng / 2);
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

function nearMeSeverityFromText(text) {
  var t = (text || '').toLowerCase();
  if (t.indexOf('emergency') !== -1) return 4;
  if (t.indexOf('watch') !== -1) return 3;
  if (t.indexOf('advice') !== -1) return 2;
  if (t.indexOf('information') !== -1) return 1; // genuine low-priority monitoring notices only
  if (t.indexOf('warning') !== -1) return 2; // standard BOM "Warning" products (severe weather, thunderstorm, flood, cyclone) — active hazard, not routine
  return 2; // unclassified — default to a real warning level rather than burying it as "Other/routine"
}

// Incidents use a separate, stricter classifier: only the properly
// normalised alertLevel field (Emergency Warning / Watch and Act / Advice)
// indicates real urgency. Raw containment status text ("Being Controlled",
// "Patrolled", "Under Control", "Going") describes progress, not risk, and
// must never be scanned for these words — it defaults to routine instead.
function nearMeIncidentSeverity(inc) {
  var text = inc.alertLevel || '';
  if (!text) return 1;
  var t = text.toLowerCase();
  if (t.indexOf('emergency') !== -1) return 4;
  if (t.indexOf('watch') !== -1) return 3;
  if (t.indexOf('advice') !== -1) return 2;
  return 1; // 'Information' or any unrecognised alertLevel text — routine
}

function nearMeTypeList() {
  var list = [
    { key: 'incident', label: 'Fire & emergency incidents', icon: '\uD83D\uDD25', color: '#ff6b35' },
    { key: 'fdr', label: 'Fire Danger Rating (Very High+)', icon: '\uD83D\uDD25', color: '#d32f2f' },
    { key: 'marine', label: 'Marine wind warnings', icon: '\uD83C\uDF0A', color: '#f0a500' }
  ];
  if (typeof BOM_FTP_PRODUCTS === 'object' && BOM_FTP_PRODUCTS) {
    Object.keys(BOM_FTP_PRODUCTS).forEach(function (k) {
      // Fire weather is shown, matching desktop: it's informational only here
      // (FDR above is still the primary fire-risk signal), not excluded.
      list.push({ key: k, label: BOM_FTP_PRODUCTS[k].label, icon: BOM_FTP_PRODUCTS[k].icon, color: BOM_FTP_PRODUCTS[k].color });
    });
  }
  return list;
}

function nearMeAllTypeKeys() { return nearMeTypeList().map(function (t) { return t.key; }); }

function nearMeLoadFilters() {
  var saved = null;
  try { saved = JSON.parse(localStorage.getItem('wt_nearme_filters') || 'null'); } catch (e) { saved = null; }
  if (!saved || !saved.types || !saved.severities) {
    saved = { types: nearMeAllTypeKeys(), severities: [1, 2, 3, 4] };
  }
  NEARME_STATE.filters = saved;
}

function nearMeSaveFilters() {
  localStorage.setItem('wt_nearme_filters', JSON.stringify(NEARME_STATE.filters));
}

function nearMeToggleSettings() {
  var panel = document.getElementById('nearme-settings-panel');
  if (!panel) return;
  var showing = panel.style.display !== 'none';
  if (showing) { panel.style.display = 'none'; return; }
  nearMeRenderSettingsPanel();
  panel.style.display = 'block';
}

function nearMeRenderSettingsPanel() {
  var inner = document.getElementById('nearme-settings-inner');
  if (!inner) return;

  var sevList = [
    { key: 4, label: 'Emergency Warning' },
    { key: 3, label: 'Watch and Act' },
    { key: 2, label: 'Advice / Warning' },
    { key: 1, label: 'Other / routine' }
  ];

  var html = '<div class="nm-settings-title">Alert types</div>';
  html += nearMeTypeList().map(function (t) {
    var checked = NEARME_STATE.filters.types.indexOf(t.key) !== -1;
    return '<label class="nm-check-row"><input type="checkbox"' + (checked ? ' checked' : '') +
      ' onchange="nearMeToggleTypeFilter(\'' + t.key + '\', this.checked)">' +
      '<span>' + t.icon + ' ' + nearMeEsc(t.label) + '</span></label>';
  }).join('');

  html += '<div class="nm-settings-title">Severity levels</div>';
  html += sevList.map(function (s) {
    var checked = NEARME_STATE.filters.severities.indexOf(s.key) !== -1;
    return '<label class="nm-check-row"><input type="checkbox"' + (checked ? ' checked' : '') +
      ' onchange="nearMeToggleSeverityFilter(' + s.key + ', this.checked)">' +
      '<span>' + s.label + '</span></label>';
  }).join('');

  html += '<button class="nm-settings-done" onclick="nearMeToggleSettings()">Done</button>';
  inner.innerHTML = html;
}

function nearMeToggleTypeFilter(key, checked) {
  var arr = NEARME_STATE.filters.types;
  var idx = arr.indexOf(key);
  if (checked && idx === -1) arr.push(key);
  if (!checked && idx !== -1) arr.splice(idx, 1);
  nearMeSaveFilters();
  nearMeRefreshCurrentView();
}

function nearMeToggleSeverityFilter(key, checked) {
  key = Number(key);
  var arr = NEARME_STATE.filters.severities;
  var idx = arr.indexOf(key);
  if (checked && idx === -1) arr.push(key);
  if (!checked && idx !== -1) arr.splice(idx, 1);
  nearMeSaveFilters();
  nearMeRefreshCurrentView();
}

function nearMeRefreshCurrentView() {
  if (NEARME_STATE.locStatus === 'granted') nearMeComputeHazards();
  else nearMeFallbackNational();
  if (NEARME_STATE.activeTab === 'alerts') nearMeRenderAlertsTab();
}

// Called from fetchBOMWarnings() once live_data.json has actually loaded.
// Near Me may have already rendered once against the still-empty
// allIncidents/bomWarnings arrays (it doesn't wait for the fetch), so this
// re-runs whatever computation matches the current location state.
function nearMeOnDataUpdated() {
  if (!_nearMeInitDone) return; // view never opened yet — nothing to refresh
  NEARME_STATE.lastDataUpdate = new Date();
  nearMeUpdateNewSet();
  nearMeRenderUpdatedText();
  nearMeRefreshCurrentView();
}

// ---------------------------------------------------------------------------
// Last-updated timestamp + "new since last check" tracking
// ---------------------------------------------------------------------------
function nearMeTimeAgo(date) {
  if (!date) return '';
  var mins = Math.round((Date.now() - date.getTime()) / 60000);
  if (mins < 1) return 'just now';
  if (mins === 1) return '1 min ago';
  if (mins < 60) return mins + ' min ago';
  return Math.round(mins / 60) + 'h ago';
}

function nearMeRenderUpdatedText() {
  var el = document.getElementById('nearme-updated');
  if (el) el.textContent = NEARME_STATE.lastDataUpdate ? ('Updated ' + nearMeTimeAgo(NEARME_STATE.lastDataUpdate)) : '';
}

// A stable-ish identity for a hazard across refreshes — no shared ID field
// exists across the different source feeds, so title+state+category is the
// best available fingerprint.
function nearMeHazardKey(h) {
  return h.kind + '|' + h.category + '|' + (h.state || '') + '|' + h.title + '|' + h.sub;
}

// Snapshot every hazard nationally (ignoring current type/severity filters,
// so toggling a filter never looks like "new" hazards appearing) as the
// baseline for next time's comparison.
function nearMeSnapshotHazardKeys() {
  var allFilters = { types: nearMeAllTypeKeys(), severities: [1, 2, 3, 4] };
  var fullList = nearMeFlattenHazards(null, null, null, allFilters);
  var keys = {};
  fullList.forEach(function (h) { keys[nearMeHazardKey(h)] = true; });
  NEARME_STATE.knownHazardKeys = keys;
}

function nearMeUpdateNewSet() {
  var allFilters = { types: nearMeAllTypeKeys(), severities: [1, 2, 3, 4] };
  var fullList = nearMeFlattenHazards(null, null, null, allFilters);
  var currentKeys = {};
  fullList.forEach(function (h) { currentKeys[nearMeHazardKey(h)] = true; });

  var newKeys = {};
  if (NEARME_STATE.knownHazardKeys) {
    Object.keys(currentKeys).forEach(function (k) {
      if (!NEARME_STATE.knownHazardKeys[k]) newKeys[k] = true;
    });
  }
  NEARME_STATE.newHazardKeys = newKeys;
  NEARME_STATE.knownHazardKeys = currentKeys;
}

// ---------------------------------------------------------------------------
// District polygon matching — AAC-first, mirrors the desktop fix (see
// index.html's renderWarningsMap()/drawAacMatches()): warnings from the
// dedicated per-type fetches (severe_weather, thunderstorm, coastal_hazard,
// heatwave) carry a precise w.areas AAC list, matched directly against each
// district's own "aac" property — no text/name guessing, and no same-state
// requirement, so a warning filed under one state that legitimately includes
// a district tagged with a different state (e.g. ACT's NSW_PW017 inside an
// NSW-filed Severe Weather Warning) resolves correctly. BOM_PW_DISTRICTS uses
// lowercase "aac"; FDR_GEO (fire districts) uses uppercase "AAC" — checking
// both covers either casing regardless of dataset.
// Legacy text-matching (state-gated name search) is kept ONLY as a fallback
// for warnings with no w.areas — currently none, since fire_weather is
// excluded from this view entirely and cyclone now has its own dedicated
// section below, but kept for safety if a new type is ever added to
// bomWarnings without a matching dedicated fetch yet.
// ---------------------------------------------------------------------------
function nearMeMatchDistrictPolygons(w) {
  var matches = [];

  if (w.areas && w.areas.length) {
    var wantedAacs = w.areas.map(function (a) { return a.aac; });
    function scanAac(collection) {
      if (!collection || !collection.features) return;
      collection.features.forEach(function (feat) {
        var faac = feat.properties && (feat.properties.aac || feat.properties.AAC);
        if (faac && wantedAacs.indexOf(faac) !== -1) matches.push(feat);
      });
    }
    if (typeof BOM_PW_DISTRICTS !== 'undefined') scanAac(BOM_PW_DISTRICTS);
    if (typeof BOM_ME_DISTRICTS !== 'undefined') scanAac(BOM_ME_DISTRICTS);
    // Fire Weather Warning AAC codes (NSW_FW0xx etc) live only in FDR_GEO —
    // included here for robustness even though fire_weather itself is
    // filtered out of this view's hazard list further down.
    if (typeof FDR_GEO !== 'undefined') scanAac(FDR_GEO);
    return matches;
  }

  // Legacy fallback — unchanged text-matching, only reached for a type with
  // no AAC area list at all.
  var searchText = ((w.title || '') + ' ' + (w.text || '')).toLowerCase().replace(/\s+&\s+/g, ' and ');
  function scan(collection) {
    if (!collection || !collection.features) return;
    collection.features.forEach(function (feat) {
      var dname = (feat.properties.name || '').toLowerCase().replace(/\s+&\s+/g, ' and ');
      var dstate = (feat.properties.state || '').toUpperCase();
      if (dstate === (w.state || '').toUpperCase() && dname.length > 3 && searchText.indexOf(dname) !== -1) {
        matches.push(feat);
      }
    });
  }
  if (typeof BOM_PW_DISTRICTS !== 'undefined') scan(BOM_PW_DISTRICTS);
  if (!matches.length && typeof BOM_ME_DISTRICTS !== 'undefined') scan(BOM_ME_DISTRICTS);
  return matches;
}

// Distance from a point to a polygon/multipolygon boundary — reuses the
// same vertex-based minDistToPolyKm() already defined in index.html.
function nearMeMinDistToGeometry(lat, lng, geometry) {
  if (!geometry || typeof minDistToPolyKm !== 'function') return Infinity;
  if (geometry.type === 'Polygon') {
    return minDistToPolyKm(lat, lng, geometry.coordinates[0]);
  }
  if (geometry.type === 'MultiPolygon') {
    var min = Infinity;
    geometry.coordinates.forEach(function (poly) {
      var d = minDistToPolyKm(lat, lng, poly[0]);
      if (d < min) min = d;
    });
    return min;
  }
  return Infinity;
}

function nearMePointInAnyPoly(lat, lng, feats) {
  if (typeof pointInGeoJSONPolygon !== 'function') return false;
  return feats.some(function (feat) { return pointInGeoJSONPolygon(lat, lng, feat.geometry); });
}

// Marine zones (MARINE_ZONES_GEOJSON) are keyed by AAC code — build a lookup
// once rather than scanning the whole feature list per hazard.
var _nearMeMarineIndex = null;
function nearMeMarineFeatureByAAC(aac) {
  if (!_nearMeMarineIndex) {
    _nearMeMarineIndex = {};
    if (typeof MARINE_ZONES_GEOJSON !== 'undefined' && MARINE_ZONES_GEOJSON && MARINE_ZONES_GEOJSON.features) {
      MARINE_ZONES_GEOJSON.features.forEach(function (feat) {
        if (feat.properties && feat.properties.AAC) _nearMeMarineIndex[feat.properties.AAC] = feat;
      });
    }
  }
  return _nearMeMarineIndex[aac] || null;
}

function nearMeGeomCentroid(geom) {
  if (!geom) return { lat: 0, lng: 0 };
  var ring = geom.type === 'Polygon' ? geom.coordinates[0]
    : (geom.type === 'MultiPolygon' ? geom.coordinates[0][0] : null);
  if (!ring) return { lat: 0, lng: 0 };
  var lats = 0, lngs = 0, n = 0;
  ring.forEach(function (c) { lngs += c[0]; lats += c[1]; n++; });
  return n ? { lat: lats / n, lng: lngs / n } : { lat: 0, lng: 0 };
}

function nearMeFlattenHazards(userLat, userLng, radiusKm, filtersOverride) {
  var out = [];
  var filters = filtersOverride || NEARME_STATE.filters || { types: nearMeAllTypeKeys(), severities: [1, 2, 3, 4] };

  if (typeof allIncidents === 'object' && allIncidents) {
    Object.keys(allIncidents).forEach(function (state) {
      (allIncidents[state] || []).forEach(function (inc) {
        if (typeof inc.lat !== 'number' || typeof inc.lng !== 'number' || (!inc.lat && !inc.lng)) return;
        if (filters.types.indexOf('incident') === -1) return;
        var sevRank = nearMeIncidentSeverity(inc);
        if (filters.severities.indexOf(sevRank) === -1) return;
        var dist = (userLat !== null) ? nearMeHaversine(userLat, userLng, inc.lat, inc.lng) : null;
        if (radiusKm !== null && dist !== null && dist > radiusKm) return;
        out.push({
          kind: 'incident',
          category: 'incident',
          state: state.toUpperCase(),
          title: (inc.title || inc.name || 'Incident') + (inc.status ? ' \u2014 ' + inc.status : ''),
          sub: (inc.location || inc.council || state.toUpperCase()) + (inc.agency ? ', ' + inc.agency : ''),
          severityRank: sevRank,
          distanceKm: dist,
          lat: inc.lat,
          lng: inc.lng,
          url: (typeof INC_FEEDS !== 'undefined' && INC_FEEDS[state] && INC_FEEDS[state].sourceUrl) || null,
          icon: '\uD83D\uDD25',
          color: '#ff6b35'
        });
      });
    });
  }

  if (typeof bomWarnings === 'object' && bomWarnings) {
    bomWarnings.forEach(function (w) {
      if (w.cancelled) return;
      if (!w.coords || w.coords.length < 2) return;
      var cat = w.type || 'other';
      // Cyclone is superseded by the dedicated tracking-data section below,
      // which uses the real Warning/Watch polygons instead of this feed's
      // flat single-point representation — skip the old flat entry here to
      // avoid a duplicate card. Fire weather is shown as informational only
      // (matches desktop): FDR is still the primary fire-risk signal, but
      // the warning itself is visible here, not hidden.
      if (cat === 'cyclone') return;
      if (filters.types.indexOf(cat) === -1) return;
      var sevRank = nearMeSeverityFromText(w.title);
      if (filters.severities.indexOf(sevRank) === -1) return;

      var wLat = w.coords[0], wLng = w.coords[1];
      var matchedPolys = nearMeMatchDistrictPolygons(w);
      var isInside = false;
      var dist = null;

      if (userLat !== null) {
        if (matchedPolys.length) {
          isInside = nearMePointInAnyPoly(userLat, userLng, matchedPolys);
          if (isInside) {
            dist = 0;
          } else {
            var minD = Infinity;
            matchedPolys.forEach(function (feat) {
              var d = nearMeMinDistToGeometry(userLat, userLng, feat.geometry);
              if (d < minD) minD = d;
            });
            dist = (minD === Infinity) ? nearMeHaversine(userLat, userLng, wLat, wLng) : minD;
          }
        } else {
          dist = nearMeHaversine(userLat, userLng, wLat, wLng);
        }
      }
      if (radiusKm !== null && dist !== null && dist > radiusKm) return;

      var typeInfo = (typeof BOM_FTP_PRODUCTS === 'object' && BOM_FTP_PRODUCTS[cat]) ? BOM_FTP_PRODUCTS[cat] : null;
      var warnUrl = w.direct_url || (typeof bomWarningUrl === 'function' ? bomWarningUrl(w.pid, w.type, w.link) : (w.link || null));
      out.push({
        kind: 'warning',
        category: cat,
        state: (w.state || '').toUpperCase(),
        title: w.title || 'BOM warning',
        sub: (w.districts || w.state || 'BOM'),
        severityRank: sevRank,
        distanceKm: dist,
        isInside: isInside,
        lat: wLat,
        lng: wLng,
        polys: matchedPolys.length ? matchedPolys.map(function (f) { return f.geometry; }) : null,
        url: warnUrl,
        icon: typeInfo ? typeInfo.icon : '\u26A0\uFE0F',
        color: typeInfo ? typeInfo.color : '#f0a500'
      });
    });
  }

  // Marine wind warnings — a separate feed/dataset from bomWarnings[], keyed
  // by state with hazards referencing marine zone AAC codes (MARINE_ZONES_GEOJSON).
  if (filters.types.indexOf('marine') !== -1 &&
      typeof marineWarningsData === 'object' && marineWarningsData &&
      typeof MARINE_ZONES_GEOJSON !== 'undefined' && MARINE_ZONES_GEOJSON) {
    var marineSevRank = { HUR: 4, STO: 3, GAL: 2, GALE: 2, STR: 2 };
    Object.keys(marineWarningsData).forEach(function (stateKey) {
      var stateWarning = marineWarningsData[stateKey] || {};
      (stateWarning.hazards || []).forEach(function (hazard) {
        var sevRank = marineSevRank[hazard.severity] !== undefined ? marineSevRank[hazard.severity] : 2;
        if (filters.severities.indexOf(sevRank) === -1) return;

        var aacCodes = hazard.aac_codes || [];
        var matchedFeats = [];
        aacCodes.forEach(function (aac) {
          var feat = nearMeMarineFeatureByAAC(aac);
          if (feat) matchedFeats.push(feat);
        });
        if (!matchedFeats.length) return;

        var isInside = false;
        var dist = null;
        if (userLat !== null) {
          isInside = nearMePointInAnyPoly(userLat, userLng, matchedFeats);
          if (isInside) {
            dist = 0;
          } else {
            var minD = Infinity;
            matchedFeats.forEach(function (feat) {
              var d = nearMeMinDistToGeometry(userLat, userLng, feat.geometry);
              if (d < minD) minD = d;
            });
            dist = (minD === Infinity) ? null : minD;
          }
        }
        if (radiusKm !== null && dist !== null && dist > radiusKm) return;

        var col = (typeof marineColor === 'function') ? marineColor(hazard.severity) : '#f0a500';
        var zoneNames = matchedFeats.map(function (f) { return f.properties.DIST_NAME || f.properties.AAC; }).join(', ');
        var anchor = nearMeGeomCentroid(matchedFeats[0].geometry);

        out.push({
          kind: 'marine',
          category: 'marine',
          state: stateKey.toUpperCase(),
          title: 'Marine wind warning \u2014 ' + (hazard.level || hazard.severity),
          sub: hazard.summary || zoneNames || stateKey.toUpperCase(),
          severityRank: sevRank,
          distanceKm: dist,
          isInside: isInside,
          lat: anchor.lat,
          lng: anchor.lng,
          polys: matchedFeats.map(function (f) { return f.geometry; }),
          url: 'https://www.bom.gov.au/australia/warnings/',
          icon: '\uD83C\uDF0A',
          color: col
        });
      });
    });
  }

  // Fire Danger Ratings — flags if you're standing inside a district
  // currently rated Very High or above (Very High / Extreme / Catastrophic).
  // Area-based, so this only applies once we actually know your location.
  if (userLat !== null && filters.types.indexOf('fdr') !== -1 &&
      typeof FDR_GEO !== 'undefined' && FDR_GEO && typeof fdrRatings === 'object' && fdrRatings) {
    FDR_GEO.features.forEach(function (feat) {
      if (!pointInGeoJSONPolygon(userLat, userLng, feat.geometry)) return;
      var aac = feat.properties.AAC;
      var rating = fdrRatings[aac] && fdrRatings[aac][1]; // period 1 = today
      if (!rating || !rating.FireDanger) return;
      var fd = rating.FireDanger.toLowerCase();
      var fdrRank = fd.indexOf('catastrophic') !== -1 ? 4 :
                    fd.indexOf('extreme') !== -1 ? 3 :
                    fd.indexOf('very high') !== -1 ? 2 : 0;
      if (fdrRank === 0) return; // below Very High — not flagged here
      if (filters.severities.indexOf(fdrRank) === -1) return;
      var fdrState = (feat.properties.state || '').toLowerCase();
      out.push({
        kind: 'fdr',
        category: 'fdr',
        state: fdrState.toUpperCase(),
        title: 'Fire Danger Rating: ' + rating.FireDanger,
        sub: 'You are in ' + (feat.properties.DIST_NAME || aac || 'this district') + ' today',
        severityRank: fdrRank,
        distanceKm: 0,
        isInside: true,
        lat: userLat,
        lng: userLng,
        polys: [feat.geometry],
        url: (typeof INC_FEEDS !== 'undefined' && INC_FEEDS[fdrState] && INC_FEEDS[fdrState].sourceUrl) || 'https://www.bom.gov.au/australia/warnings/',
        icon: '\uD83D\uDD25',
        color: fdrRank >= 4 ? '#4a148c' : (fdrRank >= 3 ? '#b71c1c' : '#d32f2f')
      });
    });
  }

  // Tropical cyclones — real-time GML/CXML tracking data (cycloneSystems,
  // a global populated by desktop's fetchBOMWarnings and shared across this
  // file's global scope). Matched against the real Warning/Watch threat-area
  // polygons rather than a single point, mirroring desktop's own polygon-
  // first risk tiering exactly (Warning area -> severity 4, Watch area ->
  // severity 3, no distance fallback into either tier — you're either in
  // the declared area or you're not). This replaces the old flat bomWarnings
  // entry for 'cyclone' (skipped above), which only had a single point and
  // no real threat-area awareness.
  if (filters.types.indexOf('cyclone') !== -1 &&
      typeof cycloneSystems !== 'undefined' && cycloneSystems && cycloneSystems.length &&
      typeof pointInAnyLatLonPolygon === 'function') {
    cycloneSystems.forEach(function (system) {
      var warningPolys = (system.areas && system.areas.warning) || [];
      var watchPolys = (system.areas && system.areas.watch) || [];
      var fix = (typeof cxmlLatestFix === 'function') ? cxmlLatestFix(system) : null;
      var fixLat = fix ? fix.lat : null, fixLng = fix ? fix.lon : null;
      var catNum = fix && fix.category ? parseInt(fix.category, 10) : NaN;

      var sevRank, isInside = false, dist = null;

      if (userLat !== null) {
        if (pointInAnyLatLonPolygon(userLat, userLng, warningPolys)) {
          sevRank = 4; isInside = true; dist = 0;
        } else if (pointInAnyLatLonPolygon(userLat, userLng, watchPolys)) {
          sevRank = 3; isInside = true; dist = 0;
        } else {
          // Outside both declared threat areas — still worth surfacing as a
          // "tracked nearby" card if within radius, using a rough nearest-
          // vertex distance across every ring (good enough for a proximity
          // read; not used for the isInside/tier decision above).
          var minD = Infinity;
          warningPolys.concat(watchPolys).forEach(function (ring) {
            ring.forEach(function (pt) {
              var d = nearMeHaversine(userLat, userLng, pt[0], pt[1]);
              if (d < minD) minD = d;
            });
          });
          if (minD === Infinity && fixLat !== null && fixLng !== null) {
            minD = nearMeHaversine(userLat, userLng, fixLat, fixLng);
          }
          dist = (minD === Infinity) ? null : minD;
          sevRank = 2;
        }
      } else {
        // No location yet (e.g. national fallback view) — rank by the
        // system's own reported category instead of proximity, so a major
        // cyclone still surfaces near the top nationally.
        sevRank = (catNum >= 3) ? 4 : (catNum >= 1) ? 3 : 2;
      }

      if (filters.severities.indexOf(sevRank) === -1) return;
      if (radiusKm !== null && dist !== null && dist > radiusKm) return;
      // Nothing to anchor this card to (no location, no fix, no areas) — skip
      // rather than guess at a position.
      if (fixLat === null && !warningPolys.length && !watchPolys.length) return;

      var anchorLat = (fixLat !== null) ? fixLat : userLat;
      var anchorLng = (fixLng !== null) ? fixLng : userLng;

      var toLngLat = function (ring) { return ring.map(function (pt) { return [pt[1], pt[0]]; }); };
      var polys = warningPolys.concat(watchPolys).map(function (ring) {
        return { type: 'Polygon', coordinates: [toLngLat(ring)] };
      });

      var color = (!isNaN(catNum) && typeof cycloneCatColor === 'function') ? cycloneCatColor(fix.category) : '#ff4757';
      var catLabel = !isNaN(catNum) ? ('Category ' + fix.category) : 'Tropical Low';

      out.push({
        kind: 'cyclone',
        category: 'cyclone',
        state: (system.region || '').toUpperCase(),
        title: (system.distName || 'Tropical Cyclone') + ' — ' + catLabel,
        sub: isInside ? (sevRank === 4 ? 'You are in the Warning area' : 'You are in the Watch area') : 'Tracking — ' + (system.region || 'monitor for updates'),
        severityRank: sevRank,
        distanceKm: dist,
        isInside: isInside,
        lat: anchorLat,
        lng: anchorLng,
        polys: polys.length ? polys : null,
        url: 'https://www.bom.gov.au/australia/warnings/',
        icon: '\uD83C\uDF00',
        color: color
      });
    });
  }

  return out;
}

function nearMeComputeHazards() {
  var list = nearMeFlattenHazards(NEARME_STATE.userLat, NEARME_STATE.userLng, NEARME_STATE.radiusKm);
  list.sort(function (a, b) {
    if (a.distanceKm !== b.distanceKm) return a.distanceKm - b.distanceKm;
    return b.severityRank - a.severityRank;
  });
  NEARME_STATE.hazards = list;

  // Map shows everything matching type/severity filters, regardless of the
  // radius chip — zooming out should reveal more, not be capped by it.
  var mapList = nearMeFlattenHazards(NEARME_STATE.userLat, NEARME_STATE.userLng, null);
  mapList.sort(function (a, b) { return a.distanceKm - b.distanceKm; });
  NEARME_STATE.mapHazards = mapList;

  nearMeRenderCards();
  nearMeUpdateMap();
}

function nearMeCurrentTile() {
  var theme = (typeof S !== 'undefined' && S && S.theme) ? S.theme
    : (typeof isDark !== 'undefined' && isDark ? 'dark' : 'light');
  if (theme === 'natural' && typeof NATURAL_TILE !== 'undefined') return { url: NATURAL_TILE, subdomains: null };
  if (theme === 'light' && typeof LIGHT_TILE !== 'undefined') return { url: LIGHT_TILE, subdomains: 'abcd' };
  if (typeof DARK_TILE !== 'undefined') return { url: DARK_TILE, subdomains: 'abcd' };
  return { url: 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', subdomains: 'abcd' };
}

function nearMeInitMap() {
  if (NEARME_STATE.map || typeof L === 'undefined') return;
  var el = document.getElementById('nearme-map');
  if (!el) return;
  var tile = nearMeCurrentTile();
  NEARME_STATE.map = L.map('nearme-map', { zoomControl: true, attributionControl: false })
    .setView([-27, 134], 4);
  var opts = { maxZoom: 19 };
  if (tile.subdomains) opts.subdomains = tile.subdomains;
  L.tileLayer(tile.url, opts).addTo(NEARME_STATE.map);
  NEARME_STATE.markersLayer = L.layerGroup().addTo(NEARME_STATE.map);
}

function nearMeUpdateMap() {
  if (!NEARME_STATE.map) return;

  if (NEARME_STATE.userLat !== null) {
    if (NEARME_STATE.userMarker) NEARME_STATE.map.removeLayer(NEARME_STATE.userMarker);
    NEARME_STATE.userMarker = L.circleMarker([NEARME_STATE.userLat, NEARME_STATE.userLng], {
      radius: 7, color: '#ffffff', weight: 2, fillColor: '#3b82f6', fillOpacity: 1
    }).addTo(NEARME_STATE.map);

    // Radius reference circle — shows what's "in range" for the Near Me card
    // list, even though the map itself now plots everything beyond it too.
    if (NEARME_STATE.radiusCircle) NEARME_STATE.map.removeLayer(NEARME_STATE.radiusCircle);
    var accentCol = (typeof getComputedStyle === 'function')
      ? getComputedStyle(document.documentElement).getPropertyValue('--accent').trim() || '#f0a500'
      : '#f0a500';
    NEARME_STATE.radiusCircle = L.circle([NEARME_STATE.userLat, NEARME_STATE.userLng], {
      radius: NEARME_STATE.radiusKm * 1000,
      color: accentCol, weight: 1.5, dashArray: '5 5', fillColor: accentCol, fillOpacity: 0.05
    }).addTo(NEARME_STATE.map);
  }

  if (!NEARME_STATE.markersLayer) return;
  NEARME_STATE.markersLayer.clearLayers();
  NEARME_STATE.mapHazards.forEach(function (h) {
    if (typeof h.lat !== 'number' || typeof h.lng !== 'number') return;
    var popupHtml = '<b>' + nearMeEsc(h.title) + '</b><br>' + nearMeEsc(h.sub) +
      (h.isInside ? '' : (h.distanceKm !== null ? '<br>' + nearMeRound1(h.distanceKm) + ' km away' : ''));

    if (h.polys && h.polys.length) {
      h.polys.forEach(function (geom) {
        L.geoJSON({ type: 'Feature', geometry: geom, properties: {} }, {
          style: {
            color: h.color || '#e0a000', weight: 1.5, opacity: 0.85,
            fillColor: h.color || '#e0a000', fillOpacity: h.kind === 'fdr' ? 0.22 : 0.15
          }
        }).bindPopup(popupHtml).addTo(NEARME_STATE.markersLayer);
      });
    }
    // Small marker on top even when a polygon is drawn — gives a clean,
    // clickable anchor point regardless of how large the polygon is.
    // Uses the same icon shown on the card, not a plain dot.
    if (h.kind !== 'fdr') {
      L.marker([h.lat, h.lng], { icon: nearMeMarkerIcon(h) })
        .bindPopup(popupHtml).addTo(NEARME_STATE.markersLayer);
    }
  });
}

// Small circular badge, matching the card icon/colour, used as the map marker.
function nearMeMarkerIcon(h) {
  var bg = h.color || '#e0a000';
  var html = '<div style="width:26px;height:26px;border-radius:50%;background:' + bg +
    ';border:2px solid #fff;box-shadow:0 1px 3px rgba(0,0,0,0.45);' +
    'display:flex;align-items:center;justify-content:center;font-size:14px;line-height:1;">' +
    h.icon + '</div>';
  return L.divIcon({ html: html, className: 'nm-map-marker', iconSize: [26, 26], iconAnchor: [13, 13], popupAnchor: [0, -13] });
}

function nearMeUpdateStatusLine() {
  var el = document.getElementById('nearme-subtitle');
  if (!el) return;
  var msg = {
    idle: 'Waiting for location\u2026',
    requesting: 'Finding your location\u2026',
    granted: 'Location found',
    denied: 'Location permission denied \u2014 showing national view',
    unsupported: 'Location not supported \u2014 showing national view'
  }[NEARME_STATE.locStatus] || '';
  el.textContent = msg;
}

function nearMeRenderRadiusPills() {
  var wrap = document.getElementById('nearme-radius-pills');
  if (!wrap) return;
  var options = [25, 50, 100, 200];
  wrap.innerHTML = options.map(function (r) {
    var active = r === NEARME_STATE.radiusKm;
    return '<button class="nm-pill' + (active ? ' nm-pill-active' : '') +
      '" onclick="nearMeSetRadius(' + r + ')">' + r + 'km</button>';
  }).join('');
}

function nearMeSetRadius(km) {
  NEARME_STATE.radiusKm = km;
  nearMeRenderRadiusPills();
  if (NEARME_STATE.locStatus === 'granted') nearMeComputeHazards();
  nearMeFitMapToRadius();
}

// Recentres and zooms the map to comfortably fit the current radius chip —
// called when a chip is clicked, and once after the first location fix.
function nearMeFitMapToRadius() {
  if (!NEARME_STATE.map || NEARME_STATE.userLat === null) return;
  var lat = NEARME_STATE.userLat, lng = NEARME_STATE.userLng;
  var radiusKm = NEARME_STATE.radiusKm;

  // Manual degree offsets (km per degree lat ~111.32, adjusted for longitude
  // convergence at this latitude) rather than relying on LatLng.toBounds(),
  // so the math is transparent and not dependent on how that helper scales.
  var margin = 1.25; // a bit beyond the radius so the dashed circle isn't flush against the edge
  var latDelta = (radiusKm * margin) / 111.32;
  var lngDelta = (radiusKm * margin) / (111.32 * Math.cos(lat * Math.PI / 180));
  var bounds = L.latLngBounds(
    [lat - latDelta, lng - lngDelta],
    [lat + latDelta, lng + lngDelta]
  );

  // The map container's cached size can go stale after the flexible
  // map/list split resize — refresh it before fitBounds computes the zoom,
  // otherwise the fit can silently compute against outdated dimensions.
  // Small delay to let any pending layout settle before measuring.
  setTimeout(function () {
    NEARME_STATE.map.invalidateSize();
    NEARME_STATE.map.fitBounds(bounds, { padding: [20, 20], animate: true, duration: 0.5 });
  }, 50);
}

function nearMeCardHtml(h) {
  var distStr = h.isInside ? 'You are here' : ((h.distanceKm !== null) ? nearMeRound1(h.distanceKm) + ' km' : '');
  var isSevere = h.severityRank >= 4;
  var tag = h.url ? 'a' : 'div';
  var openAttrs = h.url ? ' href="' + nearMeEsc(h.url) + '" target="_blank" rel="noopener"' : '';
  var isNew = NEARME_STATE.newHazardKeys && NEARME_STATE.newHazardKeys[nearMeHazardKey(h)];
  return (
    '<' + tag + ' class="nm-card' + (h.url ? ' nm-card-link' : '') + '"' + openAttrs + '>' +
      '<div class="nm-card-icon ' + (isSevere ? 'nm-icon-danger' : 'nm-icon-warning') + '">' + h.icon + '</div>' +
      '<div class="nm-card-body">' +
        '<div class="nm-card-top">' +
          '<span class="nm-card-title">' + nearMeEsc(h.title) + (isNew ? '<span class="nm-badge-new">New</span>' : '') + '</span>' +
          (distStr ? '<span class="nm-card-dist' + (isSevere ? ' nm-dist-danger' : '') + '">' + distStr + '</span>' : '') +
        '</div>' +
        '<div class="nm-card-sub">' + nearMeEsc(h.sub) + '</div>' +
      '</div>' +
      (h.url ? '<div class="nm-card-arrow">\u203A</div>' : '') +
    '</' + tag + '>'
  );
}

function nearMeRenderCards() {
  var countEl = document.getElementById('nearme-count');
  var listEl = document.getElementById('nearme-list');
  if (!listEl) return;

  var n = NEARME_STATE.hazards.length;
  if (countEl) countEl.textContent = n === 0 ? 'No hazards nearby' : (n + ' hazard' + (n > 1 ? 's' : '') + ' near you');

  listEl.innerHTML = n === 0
    ? '<div class="nm-empty">Nothing to report in this radius right now.</div>'
    : NEARME_STATE.hazards.map(nearMeCardHtml).join('');

  nearMeRenderSummary();
}

// Plain-language one-liner at the top of the Near Me tab — the single
// "am I actually at risk" takeaway, before the person reads any cards.
function nearMeCategoryPhrase(h) {
  if (h.kind === 'fdr') return (h.title.replace('Fire Danger Rating: ', '')) + ' fire danger';
  if (h.kind === 'marine') return 'marine wind warning';
  if (h.kind === 'incident') return 'an emergency incident';
  var label = (h.category && typeof BOM_FTP_PRODUCTS === 'object' && BOM_FTP_PRODUCTS[h.category])
    ? BOM_FTP_PRODUCTS[h.category].label.toLowerCase() : 'warning';
  return label;
}

function nearMeRenderSummary() {
  var el = document.getElementById('nearme-summary');
  if (!el) return;

  if (NEARME_STATE.locStatus !== 'granted') {
    el.className = 'nm-sum-info';
    el.textContent = NEARME_STATE.locStatus === 'requesting' || NEARME_STATE.locStatus === 'idle'
      ? 'Finding your location to check nearby risk\u2026'
      : 'Location unavailable \u2014 showing hazards nationally instead of based on where you are.';
    return;
  }

  var list = NEARME_STATE.hazards;
  if (!list.length) {
    el.className = 'nm-sum-clear';
    el.textContent = 'All clear \u2014 no active hazards within ' + NEARME_STATE.radiusKm + 'km of your location.';
    return;
  }

  var insideOnes = list.filter(function (h) { return h.isInside; });
  if (insideOnes.length) {
    var maxSev = Math.max.apply(null, insideOnes.map(function (h) { return h.severityRank; }));
    var phrases = insideOnes.map(nearMeCategoryPhrase);
    var uniquePhrases = phrases.filter(function (v, i) { return phrases.indexOf(v) === i; });
    el.className = maxSev >= 4 ? 'nm-sum-danger' : 'nm-sum-warning';
    el.textContent = 'You are currently in an area affected by ' + uniquePhrases.join(' and ') + '.';
    return;
  }

  var nearest = list[0]; // already distance-sorted
  el.className = nearest.severityRank >= 4 ? 'nm-sum-danger' : (nearest.severityRank >= 3 ? 'nm-sum-warning' : 'nm-sum-info');
  var distTxt = (nearest.distanceKm !== null) ? nearMeRound1(nearest.distanceKm) + 'km away' : 'nearby';
  el.textContent = 'Nearest: ' + nearMeCategoryPhrase(nearest) + ', ' + distTxt + '.';
}

function nearMeRenderAlertsTab() {
  var countEl = document.getElementById('nearme-alerts-count');
  var listEl = document.getElementById('nearme-alerts-list');
  if (!listEl) return;

  var list = nearMeFlattenHazards(null, null, null);
  list.sort(function (a, b) { return b.severityRank - a.severityRank; });

  var n = list.length;
  if (countEl) countEl.textContent = n === 0 ? 'No active alerts' : (n + ' active alert' + (n > 1 ? 's' : '') + ' nationally');

  if (n === 0) {
    listEl.innerHTML = '<div class="nm-empty">Nothing active right now.</div>';
    return;
  }

  var stateOrder = ['NSW', 'VIC', 'QLD', 'SA', 'WA', 'TAS', 'NT', 'ACT'];
  var groups = {};
  list.forEach(function (h) {
    var key = h.state || 'OTHER';
    if (!groups[key]) groups[key] = [];
    groups[key].push(h);
  });

  var orderedKeys = stateOrder.filter(function (k) { return groups[k]; });
  Object.keys(groups).forEach(function (k) { if (orderedKeys.indexOf(k) === -1) orderedKeys.push(k); });

  var html = '';
  orderedKeys.forEach(function (key) {
    var fullName = (typeof STATE_NAMES === 'object' && STATE_NAMES[key]) ? STATE_NAMES[key] : null;
    var label = fullName ? (key + ' \u2014 ' + fullName) : (key === 'OTHER' ? 'Other' : key);
    html += '<div class="nm-state-heading">' + nearMeEsc(label) + ' <span class="nm-state-count">' + groups[key].length + '</span></div>';
    html += groups[key].map(nearMeCardHtml).join('');
  });
  listEl.innerHTML = html;
}

function nearMeRenderInfoTab() {
  var el = document.getElementById('nearme-info-content');
  if (!el) return;

  var locBlockHtml;
  if (NEARME_STATE.locStatus === 'granted' && NEARME_STATE.userLat !== null) {
    var latStr = NEARME_STATE.userLat.toFixed(6);
    var lngStr = NEARME_STATE.userLng.toFixed(6);
    var accStr = (typeof NEARME_STATE.accuracy === 'number') ? '\u00B1' + Math.round(NEARME_STATE.accuracy) + ' m' : 'unknown';
    var timeStr = NEARME_STATE.fixTime ? NEARME_STATE.fixTime.toLocaleTimeString() : '';
    var mapsUrl = 'https://www.google.com/maps?q=' + latStr + ',' + lngStr;

    locBlockHtml =
      '<div class="nm-info-block">' +
        '<div class="nm-info-title">Your location \u2014 for emergency services</div>' +
        '<div class="nm-loc-coords">' + latStr + ', ' + lngStr + '</div>' +
        '<p style="margin-top:2px">Accuracy: ' + accStr + (timeStr ? ' &middot; fixed at ' + nearMeEsc(timeStr) : '') + '</p>' +
        '<div class="nm-loc-actions">' +
          '<button class="nm-loc-btn" onclick="nearMeCopyCoords()">Copy coordinates</button>' +
          '<a class="nm-loc-btn" href="' + mapsUrl + '" target="_blank" rel="noopener">Open in Maps</a>' +
        '</div>' +
        '<p id="nearme-copy-feedback" style="margin-top:6px;min-height:14px"></p>' +
        '<p style="margin-top:4px;color:var(--text3)">If you call 000, read out these coordinates \u2014 emergency services can locate you directly from them, which is often faster and more precise than a street address.</p>' +
      '</div>';
  } else {
    locBlockHtml =
      '<div class="nm-info-block">' +
        '<div class="nm-info-title">Your location \u2014 for emergency services</div>' +
        '<p>Not available yet \u2014 location hasn\u2019t been found (see status below).</p>' +
      '</div>';
  }

  el.innerHTML =
    '<div class="nm-info-block">' +
      '<div class="nm-info-title">About this view</div>' +
      '<p>Near Me shows hazards close to your current location, using the same live BOM warnings and state emergency incident feeds as the main WatchTower map.</p>' +
    '</div>' +
    locBlockHtml +
    '<div class="nm-info-block">' +
      '<div class="nm-info-title">Location status</div>' +
      '<p id="nearme-info-status"></p>' +
    '</div>';
  var statusText = {
    idle: 'Waiting for location.',
    requesting: 'Finding your location.',
    granted: 'Using your device location.',
    denied: 'Location permission denied \u2014 showing a national view instead.',
    unsupported: 'Location not supported on this device \u2014 showing a national view instead.'
  }[NEARME_STATE.locStatus] || '';
  var statusEl2 = document.getElementById('nearme-info-status');
  if (statusEl2) statusEl2.textContent = statusText;
}

function nearMeCopyCoords() {
  if (NEARME_STATE.userLat === null) return;
  var text = NEARME_STATE.userLat.toFixed(6) + ', ' + NEARME_STATE.userLng.toFixed(6);
  var feedback = document.getElementById('nearme-copy-feedback');
  function showResult(ok) {
    if (feedback) feedback.textContent = ok ? 'Copied.' : 'Could not copy \u2014 coordinates shown above.';
  }
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(function () { showResult(true); }, function () { showResult(false); });
  } else {
    showResult(false);
  }
}

function nearMeSetTab(tab) {
  NEARME_STATE.activeTab = tab;
  document.querySelectorAll('.nm-nav-item').forEach(function (el) {
    el.classList.toggle('nm-nav-active', el.getAttribute('data-tab') === tab);
  });

  ['nearme', 'alerts', 'info'].forEach(function (t) {
    var el = document.getElementById('nearme-tab-' + t);
    if (el) el.style.display = (t === tab) ? '' : 'none';
  });

  var mapEl = document.getElementById('nearme-map');
  var bodyEl = document.getElementById('nearme-body');
  var splitRow = document.getElementById('nearme-split-row');
  if (tab === 'map') {
    if (mapEl) { mapEl.style.display = ''; mapEl.classList.add('nm-map-expanded'); }
    if (bodyEl) bodyEl.style.display = 'none';
    if (splitRow) splitRow.style.display = 'none';
  } else if (tab === 'nearme') {
    if (mapEl) { mapEl.style.display = ''; mapEl.classList.remove('nm-map-expanded'); }
    if (bodyEl) bodyEl.style.display = '';
    if (splitRow) splitRow.style.display = '';
  } else {
    // Alerts / Info — full-height list, no map at all (Map tab already covers that view)
    if (mapEl) { mapEl.style.display = 'none'; mapEl.classList.remove('nm-map-expanded'); }
    if (bodyEl) bodyEl.style.display = '';
    if (splitRow) splitRow.style.display = 'none';
  }

  if (tab === 'alerts') nearMeRenderAlertsTab();
  if (tab === 'info') nearMeRenderInfoTab();

  setTimeout(function () { if (NEARME_STATE.map) NEARME_STATE.map.invalidateSize(); }, 260);
}

function nearMeRound1(n) { return Math.round(n * 10) / 10; }

function nearMeShareLocation() {
  if (NEARME_STATE.userLat === null) {
    alert('Location not available yet \u2014 try again once it\u2019s found.');
    return;
  }
  var latStr = NEARME_STATE.userLat.toFixed(6);
  var lngStr = NEARME_STATE.userLng.toFixed(6);
  var mapsUrl = 'https://www.google.com/maps?q=' + latStr + ',' + lngStr;

  var nearest = NEARME_STATE.hazards[0];
  var hazardLine;
  if (!nearest) {
    hazardLine = 'No active hazards nearby right now.';
  } else if (nearest.isInside) {
    hazardLine = 'I am currently in an area affected by ' + nearMeCategoryPhrase(nearest) + '.';
  } else if (nearest.distanceKm !== null) {
    hazardLine = 'Nearest hazard: ' + nearMeCategoryPhrase(nearest) + ', ' + nearMeRound1(nearest.distanceKm) + 'km away.';
  } else {
    hazardLine = 'Nearest hazard: ' + nearMeCategoryPhrase(nearest) + '.';
  }

  var text = 'My location: ' + latStr + ', ' + lngStr + '\n' + mapsUrl + '\n' + hazardLine;

  if (navigator.share) {
    navigator.share({ title: 'My location', text: text }).catch(function () { /* user cancelled — no-op */ });
  } else {
    // Fallback for browsers without the Web Share API — open the SMS
    // composer with the message prefilled, which works on most mobiles.
    window.location.href = 'sms:?body=' + encodeURIComponent(text);
  }
}

function nearMeEsc(s) {
  return String(s || '').replace(/[&<>"']/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
  });
}
