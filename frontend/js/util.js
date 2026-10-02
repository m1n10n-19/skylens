// Shared helpers, icons, toast and browser storage for SkyLens.
window.SL = window.SL || {};

(function (SL) {

  // The local dev server (port 5500) talks to uvicorn on :8000;
  // anywhere else the backend serves this page, so use the same origin.
  SL.API = location.port === "5500" ? "http://127.0.0.1:8000" : "";

  SL.FREE_INTENTS = 3;

  // Signed-in team member ({token, username}) or null; see SL.auth.
  // Signed-in users have no question limit.
  SL.session = null;

  SL.unlimited = () => !!SL.session;

  // Screens register here (js/views/*.js); app.js routes to them.
  SL.views = {};

  // ---------------------------------------------------------- DOM / text

  SL.$ = (sel, root = document) => root.querySelector(sel);

  SL.$$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  SL.esc = s => String(s ?? "").replace(/[&<>"']/g, c => (
    {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]
  ));

  SL.isNum = v => v !== null && v !== undefined && v !== "" && Number.isFinite(+v);

  SL.fmt = (v, digits = 0) => SL.isNum(v)
    ? (+v).toLocaleString("en-IN", {maximumFractionDigits: digits})
    : "–";

  SL.cap = s => s
    ? String(s).replace(/_/g, " ").replace(/^./, c => c.toUpperCase())
    : "–";

  SL.dist = m => !SL.isNum(m) ? "–"
    : m >= 1000 ? (m / 1000).toFixed(1) + " km"
    : Math.round(m) + " m";

  SL.shortPlace = name => (name || "").split(",").slice(0, 2).join(",").trim();

  // Area in m², plus the unit the user asked in (cents, sq ft, acres...).
  const UNITS = [
    ["cent", 40.468564, "cents"],
    ["acre", 4046.8564, "acres"],
    ["hectare", 10000, "ha"],
    ["ground", 222.967, "grounds"],
    ["sq ft", 0.09290304, "sq ft"],
    ["sqft", 0.09290304, "sq ft"],
    ["square feet", 0.09290304, "sq ft"],
  ];

  SL.areaText = (m2, spec) => {
    if (!SL.isNum(m2)) return "–";
    const base = SL.fmt(m2) + " m²";
    const stated = ((spec && spec.area && spec.area.as_stated) || "").toLowerCase();
    const unit = UNITS.find(([key]) => stated.includes(key));
    if (!unit) return base;
    const value = m2 / unit[1];
    return `${base} (≈ ${SL.fmt(value, value < 10 ? 1 : 0)} ${unit[2]})`;
  };

  // "Weight 15%", or "Evidence only, not scored" for weight 0.
  SL.weightText = w => w > 0 ? `Weight ${Math.round(w * 100)}%` : "Evidence only, not scored";

  SL.confidenceBadge = c => {
    const level = String(c || "low").toLowerCase();
    return `<span class="badge badge-${SL.esc(level)}">${SL.esc(SL.cap(level))}</span>`;
  };

  // ---------------------------------------------------------- toast

  SL.toast = msg => {
    const el = document.createElement("div");
    el.className = "toast";
    el.textContent = msg;
    document.body.appendChild(el);
    setTimeout(() => el.classList.add("show"), 10);
    setTimeout(() => { el.classList.remove("show"); setTimeout(() => el.remove(), 300); }, 3200);
  };

  // ---------------------------------------------------------- icons

  const PATHS = {
    arrowRight: "M5 12h14M13 6l6 6-6 6",
    arrowLeft: "M19 12H5M11 6l-6 6 6 6",
    check: "M5 12.5l4.5 4.5L19 7",
    x: "M6 6l12 12M18 6L6 18",
    satellite: "M5 9l4-4 4 4-4 4zM11 15l4-4 4 4-4 4zM11 7l6 6M3 21c0-2.5 1.5-4 4-4M3 17c2 0 4 2 4 4",
    parcels: "M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z",
    building: "M4 21V5l8-3v19M12 8h8v13M3 21h18M8 8v.01M8 12v.01M8 16v.01M16 12v.01M16 16v.01",
    road: "M8 3L4 21M16 3l4 18M12 4v3M12 11v2M12 17v3",
    store: "M4 9l1.5-5h13L20 9M4 9v11h16V9M4 9h16M9 20v-6h6v6",
    bolt: "M13 2L4 14h7l-1 8 9-12h-7z",
    parking: "M5 3h14v18H5zM10 17V7h3a3 3 0 010 6h-3",
    shield: "M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z",
    users: "M9 11a4 4 0 100-8 4 4 0 000 8zM2 21c1-4 3.5-6 7-6s6 2 7 6M16 3.5a4 4 0 010 7.5M18 15c2 .8 3.4 2.8 4 6",
    layers: "M12 3l9 5-9 5-9-5zM3 13l9 5 9-5",
    file: "M14 3H6v18h12V7zM14 3v4h4M9 13h6M9 17h6",
    sun: "M12 7a5 5 0 100 10 5 5 0 000-10zM12 1v2M12 21v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M1 12h2M21 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4",
    folder: "M3 6h6l2 2h10v11H3z",
    pin: "M12 21s7-6.2 7-12a7 7 0 10-14 0c0 5.8 7 12 7 12zM12 11.5a2.5 2.5 0 100-5 2.5 2.5 0 000 5z",
    crane: "M3 21h9M7 21V4l13 3H7M17 7v6M15 13h4v3h-4z",
    leaf: "M5 21c0-9 5-15 16-16-1 10-7 15-16 16zM5 21l9-9",
    chart: "M4 20V10M10 20V4M16 20v-7M2 20h20",
    scale: "M12 3v18M5 7h14M5 7l-3 7a3.5 3.5 0 006 0zM19 7l-3 7a3.5 3.5 0 006 0zM8 21h8",
    share: "M4 13v7h16v-7M12 3v12M8 7l4-4 4 4",
    download: "M12 3v12M8 11l4 4 4-4M4 21h16",
    more: "M4 12a1.5 1.5 0 103 0 1.5 1.5 0 10-3 0M10.5 12a1.5 1.5 0 103 0 1.5 1.5 0 10-3 0M17 12a1.5 1.5 0 103 0 1.5 1.5 0 10-3 0",
    user: "M12 12a4 4 0 100-8 4 4 0 000 8zM4 21c1.5-4 4.5-6 8-6s6.5 2 8 6",
    search: "M11 18a7 7 0 100-14 7 7 0 000 14zM21 21l-5-5",
    grid: "M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z",
    copy: "M9 9h11v11H9zM5 15H4V4h11v1",
    refresh: "M20 12a8 8 0 11-2.3-5.7M20 4v5h-5",
    info: "M12 21a9 9 0 100-18 9 9 0 000 18zM12 11v5M12 8v.01",
  };

  SL.icon = (name, size = 18, cls = "") =>
    `<svg class="ic ${cls}" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" ` +
    `stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">` +
    `<path d="${PATHS[name] || ""}"/></svg>`;

  SL.LAYER_META = {
    satellite_imagery: ["Satellite imagery", "satellite"],
    land_parcels: ["Land parcels", "parcels"],
    building_footprints: ["Buildings", "building"],
    roads: ["Road access", "road"],
    points_of_interest: ["Commercial activity", "store"],
    ev_chargers: ["Existing EV chargers", "bolt"],
    parking: ["Parking", "parking"],
    flood_risk: ["Flood risk", "shield"],
    population: ["Population", "users"],
    zoning: ["Zoning", "layers"],
    ownership: ["Ownership", "file"],
    solar_irradiance: ["Solar irradiance", "sun"],
    shading: ["Shading", "sun"],
    historical_imagery: ["Historical imagery", "satellite"],
    terrain: ["Terrain", "chart"],
    site_registry: ["Your sites", "folder"],
  };

  SL.layerLabel = id => (SL.LAYER_META[id] || [SL.cap(id)])[0];

  SL.layerIcon = id => (SL.LAYER_META[id] || [null, "layers"])[1];

  // Short headline per use case, e.g. "Site #1 — EV charging opportunity".
  SL.OPPORTUNITY = {
    solar_prospecting: "Solar opportunity",
    ev_charging_site_selection: "EV charging opportunity",
    commercial_site_selection: "Commercial site opportunity",
    land_acquisition: "Land opportunity",
    construction_progress: "Detected change",
  };

  // What one ranked result is called: "Site #1" or "Change #1".
  SL.rankNoun = useCaseId => useCaseId === "construction_progress" ? "Change" : "Site";

  // ---------------------------------------------------------- storage

  // Demo-only: the free-intent counter and past analyses live in this
  // browser. Every access is guarded; the app works without storage.
  const KEY = "skylens.v1";
  const KEEP_RESULTS = 5;
  let memory = {used: 0, history: [], results: {}};

  function load() {
    try {
      const raw = localStorage.getItem(KEY);
      if (raw) memory = Object.assign({used: 0, history: [], results: {}}, JSON.parse(raw));
    } catch (e) { /* storage unavailable: keep in-memory state */ }
    return memory;
  }

  function save() {
    for (let attempt = 0; attempt < KEEP_RESULTS; attempt++) {
      try {
        localStorage.setItem(KEY, JSON.stringify(memory));
        return;
      } catch (e) {
        // Quota exceeded: drop the oldest stored result and retry.
        const ids = memory.history.map(h => h.id).filter(id => memory.results[id]);
        if (!ids.length) return;
        delete memory.results[ids[ids.length - 1]];
      }
    }
  }

  SL.store = {

    used: () => load().used,

    remaining: () => SL.unlimited() ? Infinity : Math.max(0, SL.FREE_INTENTS - load().used),

    history: () => load().history,

    result: id => load().results[id] || null,

    add(result) {
      load();
      const id = Date.now().toString(36);
      const an = result.analysis || {};
      // Only answered questions use up a free intent.
      if (result.status === "success" && !SL.unlimited()) memory.used += 1;
      memory.history.unshift({
        id,
        at: new Date().toISOString(),
        query: result.query,
        status: result.status,
        title: (result.use_case && result.use_case.title) || SL.cap(result.analysis_spec && result.analysis_spec.intent_type),
        place: SL.shortPlace(result.resolved_location && result.resolved_location.name) ||
          (result.analysis_spec && result.analysis_spec.location) || "",
        total: an.total_candidates || 0,
        shortlisted: an.shortlisted || 0,
      });
      memory.results[id] = result;
      memory.history.slice(KEEP_RESULTS).forEach(h => { delete memory.results[h.id]; });
      save();
      return id;
    },

    clearHistory() {
      load();
      memory.history = [];
      memory.results = {};
      save();
    },

    resetDemo() {
      load();
      memory.used = 0;
      save();
    },
  };

  // ---------------------------------------------------------- team login

  const SESSION_KEY = "skylens.session";

  SL.auth = {

    restore() {
      try {
        const session = JSON.parse(localStorage.getItem(SESSION_KEY));
        if (session && session.token) SL.session = session;
      } catch (e) { /* no stored session */ }
      return SL.session;
    },

    save(session) {
      SL.session = session;
      try { localStorage.setItem(SESSION_KEY, JSON.stringify(session)); } catch (e) { /* session lasts this page only */ }
    },

    clear() {
      SL.session = null;
      try { localStorage.removeItem(SESSION_KEY); } catch (e) { /* nothing stored */ }
    },

    header() {
      return SL.session ? {Authorization: "Bearer " + SL.session.token} : {};
    },
  };

})(window.SL);
