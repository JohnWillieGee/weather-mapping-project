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
  activeTab: 'nearme'
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

function nearMeTypeList() {
  var list = [
    { key: 'incident', label: 'Fire & emergency incidents', icon: '\uD83D\uDD25', color: '#ff6b35' },
    { key: 'fdr', label: 'Fire Danger Rating (Very High+)', icon: '\uD83D\uDD25', color: '#d32f2f' },
    { key: 'marine', label: 'Marine wind warnings', icon: '\uD83C\uDF0A', color: '#f0a500' }
  ];
  if (typeof BOM_FTP_PRODUCTS === 'object' && BOM_FTP_PRODUCTS) {
    Object.keys(BOM_FTP_PRODUCTS).forEach(function (k) {
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
  nearMeRefreshCurrentView();
}

// ---------------------------------------------------------------------------
// District polygon matching — mirrors the exact logic desktop's
// renderWarningsMap() uses, so mobile draws/measures against the same real
// BOM district shapes instead of a single centroid point.
// ---------------------------------------------------------------------------
function nearMeMatchDistrictPolygons(w) {
  var matches = [];
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

function nearMeFlattenHazards(userLat, userLng, radiusKm) {
  var out = [];
  var filters = NEARME_STATE.filters || { types: nearMeAllTypeKeys(), severities: [1, 2, 3, 4] };

  if (typeof allIncidents === 'object' && allIncidents) {
    Object.keys(allIncidents).forEach(function (state) {
      (allIncidents[state] || []).forEach(function (inc) {
        if (typeof inc.lat !== 'number' || typeof inc.lng !== 'number' || (!inc.lat && !inc.lng)) return;
        if (filters.types.indexOf('incident') === -1) return;
        var sevText = inc.alertLevel || inc.status || '';
        var sevRank = nearMeSeverityFromText(sevText);
        if (filters.severities.indexOf(sevRank) === -1) return;
        var dist = (userLat !== null) ? nearMeHaversine(userLat, userLng, inc.lat, inc.lng) : null;
        if (radiusKm !== null && dist !== null && dist > radiusKm) return;
        out.push({
          kind: 'incident',
          category: 'incident',
          title: (inc.title || inc.name || 'Incident') + (inc.status ? ' \u2014 ' + inc.status : ''),
          sub: (inc.location || inc.council || state.toUpperCase()) + (inc.agency ? ', ' + inc.agency : ''),
          severityRank: sevRank,
          distanceKm: dist,
          lat: inc.lat,
          lng: inc.lng,
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
      out.push({
        kind: 'warning',
        category: cat,
        title: w.title || 'BOM warning',
        sub: (w.districts || w.state || 'BOM'),
        severityRank: sevRank,
        distanceKm: dist,
        isInside: isInside,
        lat: wLat,
        lng: wLng,
        polys: matchedPolys.length ? matchedPolys.map(function (f) { return f.geometry; }) : null,
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
          title: 'Marine wind warning \u2014 ' + (hazard.level || hazard.severity),
          sub: hazard.summary || zoneNames || stateKey.toUpperCase(),
          severityRank: sevRank,
          distanceKm: dist,
          isInside: isInside,
          lat: anchor.lat,
          lng: anchor.lng,
          polys: matchedFeats.map(function (f) { return f.geometry; }),
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
      out.push({
        kind: 'fdr',
        category: 'fdr',
        title: 'Fire Danger Rating: ' + rating.FireDanger,
        sub: 'You are in ' + (feat.properties.DIST_NAME || aac || 'this district') + ' today',
        severityRank: fdrRank,
        distanceKm: 0,
        isInside: true,
        lat: userLat,
        lng: userLng,
        polys: [feat.geometry],
        icon: '\uD83D\uDD25',
        color: fdrRank >= 4 ? '#4a148c' : (fdrRank >= 3 ? '#b71c1c' : '#d32f2f')
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
    if (h.kind !== 'fdr') {
      L.circleMarker([h.lat, h.lng], {
        radius: 6, color: '#000', weight: 1, fillColor: h.color || '#e0a000', fillOpacity: 0.85
      }).bindPopup(popupHtml).addTo(NEARME_STATE.markersLayer);
    }
  });
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
  var options = [50, 100, 200];
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
  return (
    '<div class="nm-card">' +
      '<div class="nm-card-icon ' + (isSevere ? 'nm-icon-danger' : 'nm-icon-warning') + '">' + h.icon + '</div>' +
      '<div class="nm-card-body">' +
        '<div class="nm-card-top">' +
          '<span class="nm-card-title">' + nearMeEsc(h.title) + '</span>' +
          (distStr ? '<span class="nm-card-dist' + (isSevere ? ' nm-dist-danger' : '') + '">' + distStr + '</span>' : '') +
        '</div>' +
        '<div class="nm-card-sub">' + nearMeEsc(h.sub) + '</div>' +
      '</div>' +
    '</div>'
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
}

function nearMeRenderAlertsTab() {
  var countEl = document.getElementById('nearme-alerts-count');
  var listEl = document.getElementById('nearme-alerts-list');
  if (!listEl) return;

  var list = nearMeFlattenHazards(null, null, null);
  list.sort(function (a, b) { return b.severityRank - a.severityRank; });

  var n = list.length;
  if (countEl) countEl.textContent = n === 0 ? 'No active alerts' : (n + ' active alert' + (n > 1 ? 's' : '') + ' nationally');
  listEl.innerHTML = n === 0
    ? '<div class="nm-empty">Nothing active right now.</div>'
    : list.map(nearMeCardHtml).join('');
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

function nearMeEsc(s) {
  return String(s || '').replace(/[&<>"']/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
  });
}
