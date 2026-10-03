// Feedback on a result or one site: a form in a dialog, sent to POST
// /feedback with a snapshot of what the page showed, so each report can
// be traced back to the question, its interpretation and the site.
(function (SL) {

  // Same ids as backend/feedback.py TAGS (a test checks they match).
  SL.FEEDBACK_TAGS = [
    ["has_building", "Has a building"],
    ["in_use", "Campus, protected or in use"],
    ["wrong_size", "Wrong size"],
    ["wrong_location", "Wrong location"],
    ["flood_terrain", "Flood or terrain wrong"],
    ["infrastructure", "Infrastructure wrong"],
    ["changes", "Changes wrong"],
    ["web_findings", "Web findings wrong"],
    ["slow_error", "Slow or error"],
    ["other", "Other"],
  ];

  // Tags that make sense for a single site.
  const SITE_TAGS = new Set(["has_building", "in_use", "wrong_size", "wrong_location",
    "flood_terrain", "infrastructure", "changes", "other"]);

  const round = (x, digits = 6) => (typeof x === "number" ? +x.toFixed(digits) : x);

  // Geometry is kept (to check the site again later) unless it is huge.
  function geometryOf(c) {
    const g = c.geometry;
    if (!g) return null;
    return JSON.stringify(g).length <= 40000 ? g : {omitted: "too large", type: g.type};
  }

  function siteSnapshot(c) {
    const criteria = {};
    Object.entries(c.criteria || {}).forEach(([id, k]) => {
      criteria[id] = {label: k.label, state: k.state, available: k.available, score: k.score,
        weight: k.weight, evidence: k.evidence || null, note: k.note || null};
    });
    const a = c.assessment || {};
    return {
      candidate_id: c.candidate_id || null, osm_id: c.osm_id || null, rank: c.rank,
      name: c.name || null, site_type: c.site_type || null, building_type: c.building_type || null,
      change_type: c.change_type || null, discovered: !!c.discovered,
      latitude: round(c.latitude), longitude: round(c.longitude), area_m2: c.area_m2,
      score: c.score, confidence: c.confidence, evidence_coverage: c.evidence_coverage,
      reasons: c.reasons || [], measurements: c.measurements || {}, criteria,
      verify: (a.verify || []).map(v => v.id),
      unknown: (a.unknown || []).map(u => u.id || u.label || u),
      geometry: geometryOf(c),
    };
  }

  function siteKey(c) {
    return String(c.candidate_id || c.osm_id || `${round(c.latitude, 5)},${round(c.longitude, 5)}`);
  }

  // What the page showed for a result (no site detail beyond a summary).
  function resultSnapshot(r, resultId) {
    if (!r) return {};
    const loc = r.resolved_location || {};
    const area = r.search_area || {};
    const infra = r.infrastructure;
    const web = r.web_research;
    const cd = r.change_detection;
    return {
      result_id: resultId || null,
      query: r.query, status: r.status, message: r.message || null,
      use_case: (r.use_case || {}).id || null,
      analysis_spec: r.analysis_spec || null,
      location: {name: loc.name || r.location || null, latitude: round(loc.latitude), longitude: round(loc.longitude)},
      search_area: {name: area.name || null, description: area.description || null, area_km2: area.area_km2 || null},
      completeness: r.completeness || null,
      analysis: r.analysis || null,
      decision: r.decision ? {summary: r.decision.summary} : null,
      change_detection: cd ? {sensor: cd.sensor, before: cd.before && cd.before.date, after: cd.after && cd.after.date} : null,
      top_sites: (r.top_prospects || []).slice(0, 10).map(c => ({
        id: siteKey(c), rank: c.rank, latitude: round(c.latitude), longitude: round(c.longitude),
        score: c.score, area_m2: c.area_m2, site_type: c.site_type || c.building_type || c.change_type || null,
        confidence: c.confidence,
      })),
      infrastructure: infra ? {
        under_construction: infra.under_construction.map(p => [p.name || p.label, p.kind, p.distance_m]),
        proposed: infra.proposed.map(p => [p.name || p.label, p.kind, p.distance_m]),
        existing: infra.existing.map(e => [e.kind, e.name, e.distance_m]),
      } : null,
      web_research: web ? {status: web.status, findings: (web.findings || []).map(f => f.url)} : null,
    };
  }

  // An analysis that failed before a result existed.
  function errorSnapshot(run) {
    return {
      query: run.query, status: "error",
      error: {stage: run.error.stage, text: run.error.text || null, status: run.error.status || null},
      progress: run.steps ? Object.keys(run.steps) : null,
    };
  }

  const sentKey = (resultId, site) => `skylens.feedback.${resultId || "x"}.${site || "result"}`;

  function wasSent(resultId, site) {
    try { return !!localStorage.getItem(sentKey(resultId, site)); } catch (e) { return false; }
  }

  function markSent(resultId, site) {
    try { localStorage.setItem(sentKey(resultId, site), "1"); } catch (e) { /* not essential */ }
  }

  async function post(body) {
    let response;
    try {
      response = await fetch(SL.API + "/feedback", {
        method: "POST",
        headers: Object.assign({"Content-Type": "application/json"}, SL.auth.header()),
        body: JSON.stringify(body),
      });
    } catch (e) {
      throw "Couldn't reach SkyLens. Check your connection and try again.";
    }
    if (!response.ok) {
      const detail = ((await response.json().catch(() => null)) || {}).detail;
      throw (detail && detail.error) || `Feedback could not be sent (${response.status}).`;
    }
    return response.json();
  }

  // opts: {result, resultId, site (a candidate), run (failed analysis), onSent}
  function open(opts) {
    const site = opts.site || null;
    const scope = site ? "site" : "result";
    const tags = SL.FEEDBACK_TAGS.filter(([id]) => !site || SITE_TAGS.has(id));
    const title = site
      ? `What's wrong with ${SL.esc(SL.rankNoun((opts.result.use_case || {}).id).toLowerCase())} #${site.rank}?`
      : opts.run ? "Tell us what went wrong" : "How was this result?";

    const dialog = document.createElement("dialog");
    dialog.className = "fb-dialog";
    dialog.innerHTML = `
      <form method="dialog" class="fb-form">
        <div class="fb-head">
          <h2>${title}</h2>
          <button type="button" class="btn-icon fb-close" aria-label="Close">${SL.icon("x", 16)}</button>
        </div>
        ${site ? `<p class="muted sm">The site's location, scores and evidence are attached automatically.</p>`
          : `<p class="muted sm">Your question and what SkyLens showed are attached automatically.</p>`}
        ${opts.run ? "" : `
        <div class="fb-verdict" role="radiogroup" aria-label="Rating">
          <button type="button" data-verdict="up" role="radio" aria-checked="false">${SL.icon("thumbUp", 18)} Useful</button>
          <button type="button" data-verdict="down" role="radio" aria-checked="false">${SL.icon("thumbDown", 18)} Wrong or not useful</button>
        </div>`}
        <fieldset class="fb-tags">
          <legend class="muted sm">What's wrong? (optional)</legend>
          ${tags.map(([id, label]) => `
            <label class="fb-tag"><input type="checkbox" value="${id}"> <span>${SL.esc(label)}</span></label>`).join("")}
        </fieldset>
        <label class="fb-comment">
          <span class="muted sm">Details (optional)</span>
          <textarea maxlength="2000" rows="3" placeholder="${site
            ? "e.g. There's a house on this plot; it's part of a school"
            : "e.g. The top results are all inside a campus"}"></textarea>
        </label>
        <p class="notice fb-error" hidden></p>
        <div class="row-gap fb-actions">
          <button type="submit" class="btn-lime fb-send">Send feedback</button>
          <button type="button" class="btn-outline fb-cancel">Cancel</button>
        </div>
      </form>`;
    document.body.appendChild(dialog);

    let verdict = null;
    const close = () => { dialog.close(); dialog.remove(); };
    SL.$(".fb-close", dialog).onclick = close;
    SL.$(".fb-cancel", dialog).onclick = close;
    dialog.addEventListener("cancel", e => { e.preventDefault(); close(); });
    dialog.addEventListener("click", e => { if (e.target === dialog) close(); });

    SL.$$("[data-verdict]", dialog).forEach(b => b.addEventListener("click", () => {
      verdict = verdict === b.dataset.verdict ? null : b.dataset.verdict;
      SL.$$("[data-verdict]", dialog).forEach(x => {
        const on = x.dataset.verdict === verdict;
        x.classList.toggle("on", on);
        x.setAttribute("aria-checked", String(on));
      });
    }));

    const error = SL.$(".fb-error", dialog);
    const send = SL.$(".fb-send", dialog);

    SL.$("form", dialog).addEventListener("submit", async e => {
      e.preventDefault();
      const chosen = SL.$$(".fb-tags input:checked", dialog).map(i => i.value);
      const comment = SL.$("textarea", dialog).value.trim();
      if (!verdict && !chosen.length && !comment) {
        error.textContent = "Choose a rating or a tag, or write a few words.";
        error.hidden = false;
        return;
      }
      const context = opts.run ? errorSnapshot(opts.run) : resultSnapshot(opts.result, opts.resultId);
      if (site) context.site = siteSnapshot(site);
      send.disabled = true;
      send.textContent = "Sending…";
      try {
        await post({
          scope, verdict: opts.run ? "down" : verdict, tags: chosen, comment,
          site_id: site ? siteKey(site) : null, context,
        });
        markSent(opts.resultId, site && siteKey(site));
        close();
        SL.toast("Thanks, your feedback was sent.");
        if (opts.onSent) opts.onSent();
      } catch (message) {
        error.textContent = String(message);
        error.hidden = false;
        send.disabled = false;
        send.textContent = "Send feedback";
      }
    });

    dialog.showModal();
  }

  SL.feedback = {

    open,

    // Button markup; wire it with attach(). label overrides the text.
    button(kind = "result", label) {
      const text = label || (kind === "site" ? "Something wrong with this site?" : "Give feedback");
      return `<button type="button" class="btn-outline fb-button" data-feedback="${kind}">
        ${SL.icon("message", 16)} <span>${SL.esc(text)}</span></button>`;
    },

    // "Was this useful?" bar for the end of a result page.
    bar() {
      return `
        <div class="card fb-bar">
          <div><b>Is this result right?</b>
            <p class="muted sm">Tell us what's wrong (a building on a site, campus land, wrong size). We use it to fix SkyLens.</p></div>
          ${SL.feedback.button("result", "Give feedback")}
        </div>`;
    },

    // Wire every [data-feedback] button inside el.
    attach(el, {result, resultId, site, run} = {}) {
      SL.$$("[data-feedback]", el).forEach(b => {
        const forSite = b.dataset.feedback === "site" ? site : null;
        const mark = () => {
          b.classList.add("sent");
          SL.$("span", b).textContent = "Feedback sent · send more";
        };
        if (wasSent(resultId, forSite && siteKey(forSite))) mark();
        b.addEventListener("click", () => open({result, resultId, site: forSite, run, onSent: mark}));
      });
    },
  };

})(window.SL);
