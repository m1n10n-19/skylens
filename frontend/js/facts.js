// Human-readable facts about a ranked site, shared by the results,
// site, compare and report views. Values come only from the
// backend's measurements; unmeasured criteria say so.
(function (SL) {

  // Size and type criteria are shown as their own rows.
  const SKIP = new Set([
    "footprint_size", "parcel_size_fit", "site_size_fit", "parcel_size", "building_type",
  ]);

  const level = (score, words = ["High", "Medium", "Low"]) =>
    score >= 75 ? words[0] : score >= 45 ? words[1] : words[2];

  function criterionValue(id, crit, m) {

    if (!crit.available) {
      return `<span class="na">Not assessed: ${SL.esc(crit.note || "no data")}</span>`;
    }

    const s = crit.score;

    switch (id) {

      case "road_access":
      case "accessibility":
        return SL.isNum(m.nearest_road_m)
          ? `${level(s, ["Good", "Fair", "Poor"])} (${SL.dist(m.nearest_road_m)} to ${SL.esc(m.nearest_road)})`
          : "Poor (no mapped road within 500 m)";

      case "major_road_proximity":
        return SL.isNum(m.nearest_major_road_m)
          ? `${SL.dist(m.nearest_major_road_m)} to ${SL.esc(m.nearest_major_road)}`
          : "No major road within 1 km";

      case "demand_potential":
        return `${level(s)} (${SL.fmt(m.dwell_destinations_500m)} destinations within 500 m)`;

      case "commercial_activity":
        return `${level(s)} (${SL.fmt(m.businesses_500m)} businesses within 500 m)`;

      case "competition":
        if (SL.isNum(m.chargers_2km)) {
          return SL.isNum(m.nearest_charger_m)
            ? `Nearest charger ${SL.dist(m.nearest_charger_m)}; ${m.chargers_2km} within 2 km`
            : "No mapped chargers within 2 km";
        }
        if (SL.isNum(m.competitors_500m)) {
          return `${m.competitors_500m} similar business${m.competitors_500m === 1 ? "" : "es"} within 500 m`;
        }
        break;

      case "parking_potential":
        return `${level(s)} (${SL.fmt(m.parking_areas_300m)} mapped parking area(s) within 300 m)`;

      case "vacancy": {
        const tag = m.landuse_tag;
        return ["vacant", "brownfield", "greenfield"].includes(tag)
          ? `Likely vacant (tagged ${SL.esc(tag)})`
          : `Open land (tagged ${SL.esc(tag)}), verify on site`;
      }

      case "location":
        return `${SL.dist(m.distance_to_centre_m)} from the search centre`;
    }

    return SL.esc(crit.evidence || SL.fmt(s));
  }

  // How "find similar sites nearby" can widen this search, or null.
  // {label, radiusKm, text}; text also explains why it can't.
  SL.widenOption = result => {
    const area = result.search_area || {};
    if (area.kind === "place") return null;
    if (area.kind === "corridor") {
      const road = area.road || {};
      return road.half_width_m < 1000 ? {
        label: "Widen the strip to 1 km either side",
        radiusKm: 1,
        text: `Search 1 km either side of ${road.name} (this one used ${road.half_width_m} m).`,
      } : null;
    }
    const radius = area.radius_km || 1;
    return radius < 2 ? {
      label: "Search within 2 km",
      radiusKm: 2,
      text: `Run the same question over a 2 km radius (this one used ${radius} km).`,
    } : null;
  };

  SL.widenBlockedText = result => {
    const area = result.search_area || {};
    if (area.kind === "place") return `Already searching all of ${area.name}.`;
    if (area.kind === "corridor") return "Already searching 1 km either side of the road.";
    return "Already searching the maximum 2 km radius.";
  };

  // ---------------------------------------------------------- evidence

  // Verification method types from the backend, cheapest first.
  const METHODS = {
    records_check: "Records check",
    imagery_review: "Imagery review",
    field_visit: "Site visit",
    site_survey: "Specialist survey",
  };

  const BASIS = {
    measured: ["Measured", "basis-measured", "Computed by SkyLens from mapped geometry"],
    inferred: ["Inferred", "basis-inferred", "Derived from several observations"],
    observed: ["Map tag · unverified", "basis-observed", "Read from an OpenStreetMap tag; not verified on imagery or the ground"],
  };

  const STATE = {
    data_unavailable: "No data source",
    not_implemented: "Not measured yet",
    data_not_loaded: "Not loaded",
    not_measurable: "Couldn't measure",
    not_assessed: "Not assessed",
  };

  const day = iso => {
    const d = new Date(iso);
    return isNaN(d) ? null : d.toLocaleDateString("en-IN", {day: "numeric", month: "short", year: "numeric"});
  };

  // "OpenStreetMap (Overpass), data as of 30 Sep 2026" for one criterion.
  function sourceLine(crit) {
    const item = (crit.evidence_items || []).find(i => i.source);
    if (!item) return "";
    const asOf = item.data_as_of && day(item.data_as_of);
    return SL.esc(item.source) + (asOf ? `, data as of ${asOf}` : "");
  }

  SL.evidence = {

    has: candidate => !!(candidate && candidate.assessment),

    basisBadge(basis) {
      const [label, cls, title] = BASIS[basis] || BASIS.observed;
      return `<span class="basis ${cls}" title="${SL.esc(title)}">${label}</span>`;
    },

    methodLabel: type => METHODS[type] || SL.cap(type),

    // Known and unknown, for the site's Evidence tab.
    html(candidate) {
      if (!SL.evidence.has(candidate)) {
        return `<p class="muted">Detailed evidence isn't available for this analysis.
          Run the question again to see it.</p>`;
      }
      const a = candidate.assessment;
      const crits = candidate.criteria || {};
      return `
        <div class="ev">
          <h4>What SkyLens found <span class="muted sm">(${a.known.length})</span></h4>
          ${a.known.length ? `<ul class="ev-list">${a.known.map(k => `
            <li>
              <div class="ev-top"><span>${SL.esc(k.label)}</span>${SL.evidence.basisBadge(k.basis)}</div>
              <div class="ev-sub">${SL.esc(k.summary || "")}</div>
              ${crits[k.id] && sourceLine(crits[k.id]) ? `<div class="ev-src">${sourceLine(crits[k.id])}</div>` : ""}
            </li>`).join("")}</ul>` : `<p class="muted sm">Nothing could be measured for this site.</p>`}

          <h4>What SkyLens doesn't know <span class="muted sm">(${a.unknown.length})</span></h4>
          ${a.unknown.length ? `<ul class="ev-list ev-unknown">${a.unknown.map(u => `
            <li>
              <div class="ev-top"><span>${SL.esc(u.label)}</span><em>${SL.esc(STATE[u.state] || SL.cap(u.state))}</em></div>
              <div class="ev-sub">${SL.esc(u.reason || "")}</div>
            </li>`).join("")}</ul>` : `<p class="muted sm">Every criterion was measured.</p>`}

          <p class="muted sm">Unknowns are left out of the score, never estimated.</p>
        </div>`;
    },

    // The verify list for one site (already ordered cheapest first).
    verifyHTML(candidate) {
      const items = SL.evidence.has(candidate) ? candidate.assessment.verify : [];
      if (!items.length) return "";
      return `<ol class="verify">${items.map(v => `
        <li>
          <div class="v-q">${SL.esc(v.question)}</div>
          <div class="v-meta"><span class="v-method">${SL.esc(SL.evidence.methodLabel(v.method_type))}</span>
            ${SL.esc(v.why || "")}</div>
        </li>`).join("")}</ol>`;
    },

    // Verify items across several sites, merged by id and ordered by
    // method type (cheapest first): [{item, ranks: [1, 2]}].
    mergedVerify(sites) {
      const byId = new Map();
      sites.forEach(c => {
        if (!SL.evidence.has(c)) return;
        c.assessment.verify.forEach(v => {
          if (!byId.has(v.id)) byId.set(v.id, {item: v, ranks: []});
          byId.get(v.id).ranks.push(c.rank);
        });
      });
      const order = Object.keys(METHODS);
      return Array.from(byId.values()).sort(
        (a, b) => order.indexOf(a.item.method_type) - order.indexOf(b.item.method_type));
    },

    // Data dates per layer from the evidence items of these sites:
    // {layer: {data_as_of, retrieved_at}}.
    dates(sites) {
      const out = {};
      sites.forEach(c => Object.values(c.criteria || {}).forEach(crit =>
        (crit.evidence_items || []).forEach(i => {
          if (i.data_as_of || i.retrieved_at) {
            out[i.layer] = {data_as_of: i.data_as_of, retrieved_at: i.retrieved_at};
          }
        })));
      return out;
    },

    day,
  };

  SL.facts = {

    level,

    criterionValue(candidate, id) {
      const crit = (candidate.criteria || {})[id];
      return crit ? criterionValue(id, crit, candidate.measurements || {}) : "–";
    },

    typeLabel(candidate) {
      return candidate.site_type ? "Land use (inferred)" : "Building type";
    },

    typeValue(candidate) {
      return SL.esc(SL.cap(candidate.site_type || candidate.building_type));
    },

    // [label, html] rows for a site.
    rows(candidate, result, {brief = false} = {}) {
      const spec = result.analysis_spec;
      const rows = [
        ["Approx. area", SL.areaText(candidate.area_m2, spec)],
        [SL.facts.typeLabel(candidate), SL.facts.typeValue(candidate)],
      ];

      Object.entries(candidate.criteria || {}).forEach(([id, crit]) => {
        if (SKIP.has(id)) return;
        if (brief && !["road_access", "competition"].includes(id)) return;
        rows.push([SL.esc(crit.label), criterionValue(id, crit, candidate.measurements || {})]);
      });

      if (!brief) {
        rows.push(["Evidence coverage", `${Math.round((candidate.evidence_coverage || 0) * 100)}% of criteria weight`]);
      }
      rows.push(["Confidence", SL.confidenceBadge(candidate.confidence)]);
      return rows;
    },

    rowsHTML(rows) {
      return `<dl class="facts">${rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("")}</dl>`;
    },

    // Plain-text summary for "Share" (copied to the clipboard).
    summaryText(result) {
      const top = result.top_prospects || [];
      const lines = [
        `SkyLens: ${result.query}`,
        result.decision && result.decision.summary,
        "",
        ...top.slice(0, 5).map(c =>
          `#${c.rank} ${c.name || SL.cap(c.site_type || c.building_type)}: score ${c.score}/100, ` +
          `${SL.fmt(c.area_m2)} m², ${c.latitude}, ${c.longitude}`),
        "",
        `Recommended: ${result.decision && result.decision.recommended_action}`,
      ];
      return lines.filter(l => l !== undefined && l !== null).join("\n");
    },
  };

})(window.SL);
