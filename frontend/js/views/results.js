// Screen 3: decision overview (and the "understood, not supported" variant).
(function (SL) {

  // Plural noun for the headline, e.g. "10 EV charging locations".
  const WHAT = {
    solar_prospecting: "solar prospects",
    ev_charging_site_selection: "EV charging locations",
    commercial_site_selection: "commercial sites",
    land_acquisition: "land parcels",
    construction_progress: "changed areas",
    infrastructure_outlook: "infrastructure projects",
  };

  // Queries that work today, offered when a question is not supported.
  const TRY = {
    solar_prospecting: "Find commercial roofs around Adyar suitable for solar",
    ev_charging_site_selection: "Find 10 cent empty land in Thoraipakkam for an EV charger",
    commercial_site_selection: "Find 4800 sq ft sites around Adyar suitable for a food court",
    land_acquisition: "Find vacant land above 1 acre near OMR with good road access",
    construction_progress: "What has changed around Thoraipakkam in the last year?",
    infrastructure_outlook: "What major infrastructure is coming near Velachery?",
  };

  function tooLarge(el, r) {
    el.innerHTML = `
      <section class="page narrow">
        <a class="back" href="#/">${SL.icon("arrowLeft", 16)} Ask another question</a>
        <h1>That area is too large to analyse at once</h1>
        <p class="q-echo">"${SL.esc(r.query)}"</p>
        <div class="card understood">
          <div class="tags">
            ${r.use_case ? `<span class="tag tag-lime">${SL.esc(r.use_case.title)}</span>` : ""}
            <span class="tag">${SL.icon("pin", 13)} ${SL.esc(r.location)}</span>
          </div>
          <p>${SL.esc(r.message)}</p>
          <p class="muted sm">SkyLens reads live map data for the area it searches, so each search is kept to
            about ${SL.fmt(r.max_area_km2)} km². A road is searched as a strip along it.</p>
          <div class="row-gap"><button class="btn-lime" id="edit">Edit question</button></div>
        </div>
      </section>`;
    SL.$("#edit", el).onclick = () => { SL.pendingQuery = r.query; SL.go("#/"); };
  }

  // A change analysis with no clear imagery for the period.
  function noImagery(el, r) {
    el.innerHTML = `
      <section class="page narrow">
        <a class="back" href="#/">${SL.icon("arrowLeft", 16)} Ask another question</a>
        <h1>No clear satellite imagery for that period</h1>
        <p class="q-echo">"${SL.esc(r.query)}"</p>
        <div class="card understood">
          <div class="tags">
            ${r.use_case ? `<span class="tag tag-lime">${SL.esc(r.use_case.title)}</span>` : ""}
            ${r.search_area ? `<span class="tag">${SL.icon("pin", 13)} ${SL.esc(r.search_area.name)}</span>` : ""}
          </div>
          <p>${SL.esc(r.message)}</p>
          <ul class="muted sm">${(r.suggestions || []).map(s => `<li>${SL.esc(s)}</li>`).join("")}</ul>
          <div class="row-gap"><button class="btn-lime" id="edit">Edit question</button></div>
        </div>
      </section>`;
    SL.$("#edit", el).onclick = () => { SL.pendingQuery = r.query; SL.go("#/"); };
  }

  function understood(el, r) {
    if (r.status === "area_too_large") return tooLarge(el, r);
    if (r.status === "data_unavailable") return noImagery(el, r);
    const spec = r.analysis_spec || {};
    const det = r.detected_requirements || {};
    const implemented = (r.supported_use_cases || []).filter(u => u.implemented);
    const list = (title, items) => items && items.length
      ? `<div class="det"><div class="muted sm">${title}</div><div class="tags">
          ${items.map(x => `<span class="tag">${SL.esc(SL.cap(x))}</span>`).join("")}</div></div>` : "";

    el.innerHTML = `
      <section class="page narrow">
        <a class="back" href="#/">${SL.icon("arrowLeft", 16)} Ask another question</a>
        <h1>SkyLens understood your question</h1>
        <p class="q-echo">"${SL.esc(r.query)}"</p>
        <div class="card understood">
          <div class="tags"><span class="tag tag-lime">${SL.esc(SL.cap(spec.intent_type))}</span>
            ${spec.location ? `<span class="tag">${SL.icon("pin", 13)} ${SL.esc(spec.location)}</span>` : ""}</div>
          <h2>${r.status === "not_yet_implemented" ? "This analysis isn't available yet" : "There is no analysis module for this yet"}</h2>
          <p>${SL.esc(r.message)}</p>
          ${list("What SkyLens would need to measure", det.criteria || spec.criteria)}
          ${list("Data it would need", det.data_needed || spec.data_needed)}
          ${list("Data SkyLens doesn't have yet", r.missing_data)}
        </div>
        <h2 class="section-title">What SkyLens can answer today</h2>
        <div class="next-grid">
          ${(implemented.length ? implemented : Object.keys(TRY).map(id => ({id, title: SL.cap(id)})))
            .filter(u => TRY[u.id]).map(u => `
            <button class="next-card" data-q="${SL.esc(TRY[u.id])}">
              <b>${SL.esc(u.title)}</b><small>"${SL.esc(TRY[u.id])}"</small></button>`).join("")}
        </div>
      </section>`;

    SL.$$("[data-q]", el).forEach(b => b.addEventListener("click", () => {
      SL.pendingQuery = b.dataset.q;
      SL.go("#/");
    }));
  }

  // "What's reported about this area": section markup and button.
  function webSection(r) {
    return `
          <section class="card web-card" id="web">
            <div class="card-head"><span>What's reported about this area</span>
              ${r.web_research ? "" : `<button class="btn-outline" id="web-run">${SL.icon("search", 16)} Check the web</button>`}</div>
            <div id="web-body">${r.web_research ? SL.webFindingsHTML(r.web_research)
              : `<p class="muted sm">Search news and government pages for infrastructure projects, flooding and
                  land issues around ${SL.esc(SL.shortPlace(r.resolved_location && r.resolved_location.name))}.
                  Results are quoted with their sources and are not verified. Uses one question.</p>`}</div>
          </section>`;
  }

  function attachWeb(el, r, id) {
    const webRun = SL.$("#web-run", el);
    if (!webRun) return;
    webRun.onclick = async () => {
      webRun.disabled = true;
      webRun.textContent = "Searching…";
      try {
        r.web_research = await SL.research(
          (r.analysis_spec && r.analysis_spec.location) || (r.resolved_location && r.resolved_location.name),
          (r.use_case || {}).id,
        );
        if (r.web_research.status === "success") SL.store.useQuestion();
        SL.store.update(id, r);
        SL.renderHeader();
        SL.$("#web-body", el).innerHTML = SL.webFindingsHTML(r.web_research);
        webRun.remove();
      } catch (error) {
        webRun.disabled = false;
        webRun.textContent = "Check the web";
        SL.$("#web-body", el).innerHTML = `<p class="notice">${SL.esc(error.text ||
          (error.stage === "rate_limit" ? "You have used all your free questions." : "Web research failed. Try again."))}</p>`;
      }
    };
  }

  function downloadJSON(result) {
    const blob = new Blob([JSON.stringify(result, null, 2)], {type: "application/json"});
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "skylens-analysis.json";
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }

  SL.views.results = {

    render(el, id) {

      const r = SL.getResult(id);

      if (!r) return SL.missingResult(el);

      if (r.status !== "success") return understood(el, r);

      if ((r.use_case || {}).output_format === "infrastructure_report") return this.renderInfra(el, r, id);

      const top = r.top_prospects || [];
      const uc = r.use_case || {};
      const an = r.analysis || {};
      const place = SL.shortPlace(r.resolved_location && r.resolved_location.name);
      const noun = String(an.candidate_type || "site").replace(/_/g, " ");
      const t = top[0];
      const partial = r.completeness && r.completeness.status === "partial" ? r.completeness.reasons : null;
      const toVerify = t && SL.evidence.has(t) ? t.assessment.verify.length : 0;
      const rankNoun = SL.rankNoun(uc.id);
      const cd = r.change_detection;

      const subtitle = top.length
        ? `${top.length} ${WHAT[uc.id] || "sites"} ranked in ${SL.esc(place)}, from
           ${SL.fmt(an.total_candidates)} candidate ${SL.esc(noun)}${an.total_candidates === 1 ? "" : "s"}.`
        : SL.esc(r.decision.summary);

      el.innerHTML = `
        <section class="page">
          <div class="page-head">
            <div>
              <a class="back" href="#/">${SL.icon("arrowLeft", 16)} Ask another question</a>
              <h1>SkyLens Decision</h1>
              <p class="lead">${subtitle}</p>
              ${r.search_area.description ? `<p class="searched">${SL.icon("pin", 14)} Searched ${SL.esc(r.search_area.description)}</p>` : ""}
              ${cd ? `<p class="searched">${SL.icon("satellite", 14)} Compared ${SL.esc(cd.sensor || "Sentinel-2")} images (${cd.resolution_m || 10} m) from
                ${SL.evidence.day(cd.before.date)} and ${SL.evidence.day(cd.after.date)}</p>` : ""}
              <p class="q-echo">"${SL.esc(r.query)}"</p>
              ${partial ? `
              <div class="partial" role="status">
                <b>Partial result.</b> Some data couldn't be collected, so this answer may be incomplete:
                <ul>${partial.map(x => `<li>${SL.esc(x)}</li>`).join("")}</ul>
              </div>` : ""}
            </div>
            <div class="head-actions">
              <a class="btn-outline" href="#/report/${id}">${SL.icon("file", 16)} Export Report</a>
              <button class="btn-outline" id="share">${SL.icon("share", 16)} Share</button>
              <div class="menu-wrap">
                <button class="btn-icon" id="more" aria-label="More actions" aria-expanded="false">${SL.icon("more", 20)}</button>
                <div class="menu" id="menu" hidden>
                  <a href="#/next/${id}">${SL.icon("arrowRight", 16)} What next?</a>
                  ${top.length > 1 ? `<a href="#/compare/${id}">${SL.icon("scale", 16)} Compare top 3</a>` : ""}
                  <button id="dl">${SL.icon("download", 16)} Download data (JSON)</button>
                  <a href="#/">${SL.icon("search", 16)} Ask another question</a>
                </div>
              </div>
            </div>
          </div>

          ${top.length ? `
          <div class="decision-grid">
            <article class="card top-card">
              <div class="card-head"><span>${cd ? "Most significant change" : "Top opportunity"}</span>${SL.confidenceBadge(t.confidence)}</div>
              <a href="#/site/${id}/1">${SL.maps.thumb(t, 480, 270)}</a>
              <div class="top-score">
                <span class="rank-badge">#1</span>
                ${t.name ? `<span class="top-name">${SL.esc(t.name)}</span>` : ""}
                <span class="score-big">Score <b>${SL.fmt(t.score)}</b><small>/ 100</small></span>
              </div>
              ${SL.facts.rowsHTML(SL.facts.rows(t, r, {brief: true}))}
              <a class="btn-lime" href="#/site/${id}/1">View details ${SL.icon("arrowRight", 16)}</a>
            </article>
            <div class="card map-card"><div id="res-map"></div></div>
          </div>

          <div class="strip" role="list">
            ${top.map(c => `
              <a class="strip-card${c.rank === 1 ? " sel" : ""}" role="listitem" href="#/site/${id}/${c.rank}">
                ${SL.maps.thumb(c, 240, 150)}
                <span class="strip-rank">#${c.rank}</span>
                <span class="strip-body"><b>${SL.fmt(c.score)}</b><small>/ 100</small>
                  <span class="muted sm">${SL.fmt(c.area_m2)} m²</span></span>
              </a>`).join("")}
          </div>` : `
          <div class="card empty-result">
            <h2>No candidates found</h2>
            <p>${SL.esc(r.decision.summary)}</p>
            <p class="muted">${SL.esc(r.decision.recommended_action)}</p>
            <div class="row-gap">
              ${SL.widenOption(r) ? `<button class="btn-lime" id="widen">${SL.icon("refresh", 16)} ${SL.esc(SL.widenOption(r).label)}</button>` : ""}
              <button class="btn-outline" id="edit">Edit question</button>
            </div>
          </div>
          <div class="card map-card empty-map"><div id="res-map"></div></div>`}

          <div class="decision-note card">
            <div>
              <div class="muted sm">Recommended action</div>
              <p>${SL.esc(r.decision.recommended_action)}</p>
              ${toVerify ? `<p class="sm">Before acting on ${rankNoun.toLowerCase()} #1, check
                <a class="link" href="#/site/${id}/1">${toVerify} thing${toVerify === 1 ? "" : "s"}</a>
                that remote data can't settle.</p>` : ""}
              <p class="muted sm">Scores use measured evidence only
                (${Math.round((an.evidence_coverage || 0) * 100)}% of the criteria weight for the top site).
                Anything SkyLens has no data for is listed below, never estimated.</p>
            </div>
            <a class="btn-outline" href="#/next/${id}">What next? ${SL.icon("arrowRight", 16)}</a>
          </div>

          ${r.infrastructure ? `
          <section class="card web-card">
            <div class="card-head"><span>Infrastructure around the area</span></div>
            ${SL.infraHTML(r.infrastructure, {compact: true})}
          </section>` : ""}

          ${webSection(r)}

          <details class="card limits">
            <summary>What this analysis does not tell you</summary>
            <ul>${(r.limitations || []).map(l => `<li>${SL.esc(l)}</li>`).join("")}</ul>
            ${(r.missing_data || []).length ? `<p class="sm"><b>Not assessed (no data):</b>
              ${r.missing_data.map(m => SL.esc(SL.cap(m))).join(", ")}</p>` : ""}
          </details>
        </section>`;

      // ---- actions
      SL.$("#share", el).onclick = () => {
        const text = SL.facts.summaryText(r);
        (navigator.clipboard ? navigator.clipboard.writeText(text) : Promise.reject())
          .then(() => SL.toast("Summary copied to the clipboard."))
          .catch(() => SL.toast("Couldn't access the clipboard."));
      };

      const menu = SL.$("#menu", el);
      const more = SL.$("#more", el);
      more.onclick = e => {
        e.stopPropagation();
        menu.hidden = !menu.hidden;
        more.setAttribute("aria-expanded", String(!menu.hidden));
      };
      this.closeMenu = () => { menu.hidden = true; more.setAttribute("aria-expanded", "false"); };
      document.addEventListener("click", this.closeMenu);
      SL.$("#dl", el).onclick = () => downloadJSON(r);

      attachWeb(el, r, id);

      // ---- map: the searched area, then the ranked sites
      const map = this.map = SL.maps.create(SL.$("#res-map", el), {zoomControl: true});
      map.zoomControl.setPosition("bottomright");
      const loc = r.resolved_location;
      const areaLayer = SL.maps.searchLayer(r.search_area, loc.latitude, loc.longitude).addTo(map);
      SL.maps.placeTag(loc.latitude, loc.longitude, place.split(",")[0]).addTo(map);

      if (!top.length) {
        map.fitBounds(areaLayer.getBounds(), {padding: [30, 30]});
        const widen = SL.$("#widen", el);
        if (widen) widen.onclick = () => SL.startAnalysis(r.query, SL.widenOption(r).radiusKm);
        SL.$("#edit", el).onclick = () => { SL.pendingQuery = r.query; SL.go("#/"); };
        return;
      }

      const points = [];
      top.slice().reverse().forEach(c => {
        const parcel = SL.maps.parcel(c.geometry, c.rank === 1);
        if (parcel) parcel.addTo(map);
        SL.maps.pin(c.latitude, c.longitude, c.rank, c.rank === 1)
          .bindTooltip(`#${c.rank} · ${SL.fmt(c.score)}/100`, {direction: "top", offset: [0, -14]})
          .on("click", () => { location.hash = `#/site/${id}/${c.rank}`; })
          .addTo(map);
        points.push([c.latitude, c.longitude]);
      });
      map.fitBounds(L.latLngBounds(points).extend(areaLayer.getBounds()), {padding: [30, 30]});
    },

    // "What infrastructure is coming near X?": an area report.
    renderInfra(el, r, id) {

      const report = r.infrastructure;
      const loc = r.resolved_location;
      const place = SL.shortPlace(loc.name);

      el.innerHTML = `
        <section class="page">
          <a class="back" href="#/">${SL.icon("arrowLeft", 16)} Ask another question</a>
          <h1>Infrastructure around ${SL.esc(place.split(",")[0])}</h1>
          <p class="lead">${SL.esc(r.decision.summary)}</p>
          ${r.search_area.description ? `<p class="searched">${SL.icon("pin", 14)} Searched ${SL.esc(r.search_area.description)}</p>` : ""}
          <p class="q-echo">"${SL.esc(r.query)}"</p>

          <div class="decision-grid">
            <article class="card">${SL.infraHTML(report)}</article>
            <div class="card map-card">
              <div id="res-map"></div>
              <div class="infra-legend sm">${Object.entries(SL.INFRA_COLORS).map(([status, color]) =>
                `<span><i class="status-dot" style="background:${color}"></i>${SL.esc(SL.cap(status))}</span>`).join("")}</div>
            </div>
          </div>

          <div class="decision-note card"><div>
            <div class="muted sm">Recommended action</div>
            <p>${SL.esc(r.decision.recommended_action)}</p>
          </div></div>

          ${webSection(r)}

          <details class="card limits">
            <summary>What this analysis does not tell you</summary>
            <ul>${(r.limitations || []).map(l => `<li>${SL.esc(l)}</li>`).join("")}</ul>
          </details>
        </section>`;

      attachWeb(el, r, id);

      const map = this.map = SL.maps.create(SL.$("#res-map", el), {zoomControl: true});
      map.zoomControl.setPosition("bottomright");
      const areaLayer = SL.maps.searchLayer(r.search_area, loc.latitude, loc.longitude).addTo(map);
      const group = L.featureGroup().addTo(map);
      const draw = (item, color) => {
        if (!item.geometry) return;
        L.geoJSON(item.geometry, {
          style: {color, weight: 4, opacity: 0.9, fillOpacity: 0.25},
          pointToLayer: (f, latlng) => L.circleMarker(latlng, {radius: 7, color: "#0b100e", weight: 1, fillColor: color, fillOpacity: 1}),
        }).bindTooltip(SL.esc(`${item.name || item.label} · ${item.label}`)).addTo(group);
      };
      report.existing.forEach(e => (e.nearest || [e]).forEach(n => draw(n, SL.INFRA_COLORS.existing)));
      report.under_construction.forEach(p => draw(p, SL.INFRA_COLORS.under_construction));
      report.proposed.forEach(p => draw(p, SL.INFRA_COLORS.proposed));
      const bounds = areaLayer.getBounds();
      const projects = report.under_construction.concat(report.proposed);
      if (projects.length) {
        const projectLayer = L.featureGroup();
        projects.forEach(p => p.geometry && L.geoJSON(p.geometry).addTo(projectLayer));
        bounds.extend(projectLayer.getBounds());
      }
      map.fitBounds(bounds, {padding: [30, 30]});
    },

    destroy() {
      if (this.closeMenu) document.removeEventListener("click", this.closeMenu);
      if (this.map) this.map.remove();
      this.map = null;
      this.closeMenu = null;
    },
  };

})(window.SL);
