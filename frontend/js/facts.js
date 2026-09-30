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
