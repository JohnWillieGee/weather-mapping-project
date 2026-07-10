// ============================================================================
// watchtower_nearme.js
// Mobile "Near Me" personal-risk view for WatchTower.
// Centres on the user's GPS location and shows nearby hazards from the same
// live_data.json feed the desktop map already uses. Does NOT touch site data,
// allIncidents, warningsData, or any existing desktop rendering functions.
// ============================================================================

var NEARME_STATE = {
  radiusKm: 50,          // default filter radius
  userLat: null,
  userLng: null,
  locStatus: 'idle',     // idle | requesting | granted | denied | unsupported
  hazards: []            // flattened, distance-sorted list for current radius
};

// ---------------------------------------------------------------------------
// View-mode decision: auto (screen width) vs manual override (localStorage)
// ---------------------------------------------------------------------------
function nearMeShouldShow() {
  var stored = localStorage.getItem('wt_view_mode'); // 'auto' | 'nearme' | 'desktop'
  if (stored === 'nearme') return true;
  if (stored === 'desktop') return false;
  return window.innerWidth <= 768;
}

function nearMeSetViewMode(mode) {
  // mode: 'auto' | 'nearme' | 'desktop'
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

// Re-check on resize/orientation change, but only when in 'auto' mode
window.addEventListener('resize', function () {
  if (!localStorage.getItem('wt_view_mode')) nearMeApplyViewMode();
});

// ---------------------------------------------------------------------------
// Init — runs once when the Near Me view becomes visible
// ---------------------------------------------------------------------------
var _nearMeInitDone = false;
function nearMeInit() {
  if (_nearMeInitDone) return;
  _nearMeInitDone = true;
  nearMeRequestLocation();
  nearMeRenderRadiusPills();
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
      NEARME_STATE.locStatus = 'granted';
      nearMeUpdateStatusLine();
      nearMeComputeHazards();
    },
    function (err) {
      NEARME_STATE.locStatus = 'denied';
      nearMeUpdateStatusLine();
      nearMeFallbackNational();
    },
    { enableHighAccuracy: false, timeout: 10000, maximumAge: 300000 }
  );
}

function nearMeFallbackNational() {
  // No location permission — show a flat list of the most severe hazards
  // nationally rather than a distance-sorted list.
  var list = nearMeFlattenHazards(null, null, null); // null radius = no distance filter
  list.sort(function (a, b) { return b.severityRank - a.severityRank; });
  NEARME_STATE.hazards = list.slice(0, 15);
  nearMeRenderCards();
}

// ---------------------------------------------------------------------------
// Distance calc — reuses the existing haversineKm() already defined in
// index.html. Falls back to a local copy if it's ever missing.
// ---------------------------------------------------------------------------
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

var NEARME_SEVERITY_RANK = {
  'emergency warning': 4, 'emergency': 4,
  'watch and act': 3,
  'advice': 2,
  'warning': 2,
  'other': 1
};

function nearMeSeverityRank(levelText) {
  var key = (levelText || '').toLowerCase().trim();
  return NEARME_SEVERITY_RANK[key] || 1;
}

// ---------------------------------------------------------------------------
// Flatten allIncidents{} + bomWarnings[] into one array, computing distance
// from the user when lat/lng is available. Reads existing globals only —
// never mutates them.
// ---------------------------------------------------------------------------
function nearMeFlattenHazards(userLat, userLng, radiusKm) {
  var out = [];

  // Incidents (fire etc.) — allIncidents.{state}[] with .lat / .lng
  if (typeof allIncidents === 'object' && allIncidents) {
    Object.keys(allIncidents).forEach(function (state) {
      (allIncidents[state] || []).forEach(function (inc) {
        if (typeof inc.lat !== 'number' || typeof inc.lng !== 'number' || (!inc.lat && !inc.lng)) return;
        var dist = (userLat !== null) ? nearMeHaversine(userLat, userLng, inc.lat, inc.lng) : null;
        if (radiusKm !== null && dist !== null && dist > radiusKm) return;
        out.push({
          kind: 'incident',
          title: (inc.title || inc.name || 'Incident') + (inc.status ? ' \u2014 ' + inc.status : ''),
          sub: (inc.location || inc.council || state.toUpperCase()) + (inc.agency ? ', ' + inc.agency : ''),
          level: inc.status || inc.alertLevel || '',
          severityRank: nearMeSeverityRank(inc.status || inc.alertLevel),
          distanceKm: dist,
          icon: 'ti-flame'
        });
      });
    });
  }

  // BOM warnings — bomWarnings[] with .coords = [lat, lng]
  if (typeof bomWarnings === 'object' && bomWarnings) {
    bomWarnings.forEach(function (w) {
      if (w.cancelled) return;
      if (!w.coords || w.coords.length < 2) return;
      var wLat = w.coords[0], wLng = w.coords[1];
      var dist = (userLat !== null) ? nearMeHaversine(userLat, userLng, wLat, wLng) : null;
      if (radiusKm !== null && dist !== null && dist > radiusKm) return;
      out.push({
        kind: 'warning',
        title: w.title || 'BOM warning',
        sub: (w.districts || w.state || 'BOM'),
        level: w.type || '',
        severityRank: nearMeSeverityRank(w.type === 'fire_weather' ? 'warning' : w.type),
        distanceKm: dist,
        icon: w.type === 'fire_weather' ? 'ti-flame' : 'ti-cloud-storm'
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
  nearMeRenderCards();
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------
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
}

function nearMeRenderCards() {
  var countEl = document.getElementById('nearme-count');
  var listEl = document.getElementById('nearme-list');
  if (!listEl) return;

  var n = NEARME_STATE.hazards.length;
  if (countEl) {
    countEl.textContent = n === 0 ? 'No hazards nearby' : (n + ' hazard' + (n > 1 ? 's' : '') + ' near you');
  }

  if (n === 0) {
    listEl.innerHTML = '<div class="nm-empty">Nothing to report in this radius right now.</div>';
    return;
  }

  listEl.innerHTML = NEARME_STATE.hazards.map(function (h) {
    var distStr = (h.distanceKm !== null) ? nearMeRound1(h.distanceKm) + ' km' : '';
    var isSevere = h.severityRank >= 4;
    return (
      '<div class="nm-card">' +
        '<div class="nm-card-icon ' + (isSevere ? 'nm-icon-danger' : 'nm-icon-warning') + '">' +
          '<i class="ti ' + h.icon + '" aria-hidden="true"></i>' +
        '</div>' +
        '<div class="nm-card-body">' +
          '<div class="nm-card-top">' +
            '<span class="nm-card-title">' + nearMeEsc(h.title) + '</span>' +
            (distStr ? '<span class="nm-card-dist' + (isSevere ? ' nm-dist-danger' : '') + '">' + distStr + '</span>' : '') +
          '</div>' +
          '<div class="nm-card-sub">' + nearMeEsc(h.sub) + '</div>' +
        '</div>' +
      '</div>'
    );
  }).join('');
}

function nearMeRound1(n) { return Math.round(n * 10) / 10; }

function nearMeEsc(s) {
  return String(s || '').replace(/[&<>"']/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
  });
}

// ---------------------------------------------------------------------------
// Bottom nav (Near Me / Map / Alerts / Info) — Map/Alerts/Info are stubs for
// now; Near Me is the only live tab in this first pass.
// ---------------------------------------------------------------------------
function nearMeSetTab(tab) {
  document.querySelectorAll('.nm-nav-item').forEach(function (el) {
    el.classList.toggle('nm-nav-active', el.getAttribute('data-tab') === tab);
  });
  // Future: swap #nearme-body content per tab. Only 'nearme' has content today.
}
