// Screen 4: one site, with overview, score breakdown and nearby context.
(function (SL) {

  const COLORS = {road: "#5ad1ff", poi: "#ffd166", parking: "#8fb3ff", charger: SL.LIME};

  function breakdownHTML(c) {
    const crits = Object.values(c.criteria || {});
    return `
      <div class="breakdown">
        ${crits.map(k => k.available && k.weight === 0 ? `
          <div class="bd">
            <div class="bd-top"><span>${SL.esc(k.label)}</span><em>Evidence only</em></div>
            <div class="bd-meta">${SL.esc(k.evidence || "")}</div>
          </div>` : k.available ? `
          <div class="bd">
            <div class="bd-top"><span>${SL.esc(k.label)}</span><b>${SL.fmt(k.score)}</b></div>
            <div class="bar"><i style="width:${Math.max(2, k.score)}%"></i></div>
            <div class="bd-meta">${SL.weightText(k.weight)} · ${SL.esc(k.evidence || "")}</div>
          </div>` : `
          <div class="bd bd-na">
            <div class="bd-top"><span>${SL.esc(k.label)}</span><em>Not measured</em></div>
            <div class="bd-meta">${SL.weightText(k.weight)} · ${SL.esc(k.note || "No data")}</div>
          </div>`).join("")}
        <p class="muted sm">Score = weighted average of the measured criteria
          (${Math.round((c.evidence_coverage || 0) * 100)}% of the weight). Criteria without data are
          left out, not guessed.</p>
      </div>`;
  }

  function nearbyHTML(c, useCaseTitle) {
    const n = c.nearby;
    if (!n) {
      return `<p class="muted">Surrounding map data (roads, businesses, chargers) isn't collected
        for ${SL.esc(useCaseTitle.toLowerCase())} yet.</p>`;
    }
    const counts = {};
    n.pois.forEach(p => { counts[p.category] = (counts[p.category] || 0) + 1; });
    const cats = Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 8);
    const road = n.nearest_road;
    const dot = color => `<i class="dot" style="background:${color}"></i>`;
    return `
      <dl class="facts">
        <dt>${dot(COLORS.road)} Nearest road</dt>
        <dd>${road ? `${road.name ? `${SL.esc(road.name)} (${SL.esc(road.type)})` : SL.esc(SL.cap(road.type) + " road")},
          ${SL.dist(road.distance_m)}` : "No mapped road within 500 m"}</dd>
        <dt>${dot(COLORS.poi)} Businesses & amenities</dt>
        <dd>${n.pois.length} within 500 m</dd>
        <dt>${dot(COLORS.parking)} Parking</dt>
        <dd>${n.parking.length} mapped within 300 m</dd>
        <dt>${dot(COLORS.charger)} EV chargers</dt>
        <dd>${n.chargers.length
          ? n.chargers.slice().sort((a, b) => a.distance_m - b.distance_m).slice(0, 4)
              .map(ch => `${SL.esc(ch.name || "Charger")} (${SL.dist(ch.distance_m)})`).join("<br>")
          : "None mapped within 2 km"}</dd>
      </dl>
      ${cats.length ? `<div class="tags">${cats.map(([k, v]) =>
        `<span class="tag">${SL.esc(SL.cap(k))} · ${v}</span>`).join("")}</div>` : ""}
      <p class="muted sm">From OpenStreetMap, which may not list every business or charger.</p>`;
  }

  function nearbyLayer(c) {
    const group = L.layerGroup();
    const n = c.nearby;
    if (!n) return group;
    L.circle([c.latitude, c.longitude], {
      radius: 500, color: "#fff", weight: 1, dashArray: "4 6", fill: false, interactive: false,
    }).addTo(group);
    if (n.nearest_road && n.nearest_road.geometry) {
      L.geoJSON(n.nearest_road.geometry, {style: {color: COLORS.road, weight: 4, opacity: 0.9}}).addTo(group);
    }
    const point = (p, color, radius) => L.circleMarker([p.lat, p.lon], {
      radius, color: "#0b100e", weight: 1, fillColor: color, fillOpacity: 1,
    }).bindTooltip(SL.esc(p.name || SL.cap(p.category))).addTo(group);
    n.pois.forEach(p => point(p, COLORS.poi, 4));
    n.parking.forEach(p => point(p, COLORS.parking, 5));
    n.chargers.forEach(p => point(p, COLORS.charger, 7));
    return group;
  }

  SL.views.site = {

    render(el, id, rankText) {

      const r = SL.getResult(id);
      const top = (r && r.top_prospects) || [];
      const rank = +rankText;
      const c = top[rank - 1];

      if (!r || !c) return SL.missingResult(el);

      const uc = r.use_case || {};
      const title = SL.OPPORTUNITY[uc.id] || "Site";
      const place = SL.shortPlace(r.resolved_location.name);
      const recommended = rank <= 3 && c.score >= 60;
      const noun = SL.rankNoun(uc.id);
      // Reasons starting "Warning:" are cautions, shown apart from the reasons to pick a site.
      const isWarning = x => /^Warning:\s*/.test(x);
      const warnings = (c.reasons || []).filter(isWarning).map(x => SL.cap(x.replace(/^Warning:\s*/, "")));
      const reasons = (c.reasons || []).filter(x => !isWarning(x));

      el.innerHTML = `
        <section class="page">
          <div class="site-head">
            <a class="back" href="#/results/${id}">${SL.icon("arrowLeft", 16)} Back to results</a>
            <div class="site-nav">
              ${rank > 1 ? `<a class="btn-icon" href="#/site/${id}/${rank - 1}" aria-label="Previous site">${SL.icon("arrowLeft", 16)}</a>` : ""}
              <span class="muted sm">${noun} ${rank} of ${top.length}</span>
              ${rank < top.length ? `<a class="btn-icon" href="#/site/${id}/${rank + 1}" aria-label="Next site">${SL.icon("arrowRight", 16)}</a>` : ""}
            </div>
          </div>
          <div class="site-title">
            <div>
              <h1>${noun} #${rank} — ${SL.esc(title)}</h1>
              <p class="muted">${SL.icon("pin", 15)} ${SL.esc(c.name ? c.name + ", " : "")}${SL.esc(place)}
                · ${(+c.latitude).toFixed(5)}, ${(+c.longitude).toFixed(5)}</p>
            </div>
            <span class="score-pill">Score: ${SL.fmt(c.score)} / 100</span>
          </div>

          <div class="site-grid">
            <div class="card map-card tall"><div id="site-map"></div></div>
            <div class="site-side">
              <div class="card">
                <div class="tabs" role="tablist">
                  <button role="tab" data-tab="overview" class="active">Overview</button>
                  <button role="tab" data-tab="evidence">Evidence</button>
                  <button role="tab" data-tab="breakdown">Score breakdown</button>
                  <button role="tab" data-tab="nearby">Nearby context</button>
                </div>
                <div id="tab-body"></div>
              </div>
              ${warnings.length ? `
              <div class="partial" role="note">
                <b>Warning.</b> ${warnings.length === 1 ? SL.esc(warnings[0]) + "." : ""}
                ${warnings.length > 1 ? `<ul>${warnings.map(x => `<li>${SL.esc(x)}</li>`).join("")}</ul>` : ""}
              </div>` : ""}
              <div class="card why">
                <h3>${recommended && noun === "Site" ? "Why this site is recommended" : `Why SkyLens ranked it #${rank}`}</h3>
                <ul>${reasons.map(x => `<li>${SL.esc(x)}</li>`).join("") || "<li>No specific reasons recorded.</li>"}</ul>
              </div>
              ${SL.evidence.verifyHTML(c) ? `
              <div class="card verify-card">
                <h3>${noun === "Change" ? "What to verify about this change" : "What to verify before committing"}</h3>
                <p class="muted sm">Remote data can't settle these. Cheapest checks first; SkyLens doesn't carry them out.</p>
                ${SL.evidence.verifyHTML(c)}
              </div>` : ""}
              <div class="row-gap">
                <a class="btn-outline" href="#/next/${id}">What next? ${SL.icon("arrowRight", 16)}</a>
                <a class="btn-outline" href="https://www.google.com/maps/search/?api=1&query=${c.latitude},${c.longitude}"
                   target="_blank" rel="noopener">Open in Google Maps</a>
              </div>
            </div>
          </div>
        </section>`;

      // ---- map
      const map = this.map = SL.maps.create(SL.$("#site-map", el), {zoomControl: true});
      map.zoomControl.setPosition("bottomright");
      L.control.scale({imperial: false, position: "bottomleft"}).addTo(map);
      const parcel = SL.maps.parcel(c.geometry, true);
      if (parcel) parcel.addTo(map);
      else SL.maps.pin(c.latitude, c.longitude, rank, true).addTo(map);
      const siteBounds = L.latLngBounds(SL.maps.bounds(c));
      map.fitBounds(siteBounds, {padding: [70, 70], maxZoom: 18});
      const nearby = nearbyLayer(c);

      // ---- tabs
      const body = SL.$("#tab-body", el);
      const show = tab => {
        SL.$$(".tabs button", el).forEach(b => b.classList.toggle("active", b.dataset.tab === tab));
        if (tab === "overview") body.innerHTML = SL.facts.rowsHTML(SL.facts.rows(c, r));
        if (tab === "evidence") body.innerHTML = SL.evidence.html(c);
        if (tab === "breakdown") body.innerHTML = breakdownHTML(c);
        if (tab === "nearby") body.innerHTML = nearbyHTML(c, uc.title || "this analysis");
        if (tab === "nearby" && c.nearby) {
          nearby.addTo(map);
          map.fitBounds(L.latLng(c.latitude, c.longitude).toBounds(1100), {padding: [20, 20]});
        } else if (map.hasLayer(nearby)) {
          map.removeLayer(nearby);
          map.fitBounds(siteBounds, {padding: [70, 70], maxZoom: 18});
        }
      };
      SL.$$(".tabs button", el).forEach(b => b.addEventListener("click", () => show(b.dataset.tab)));
      show("overview");
    },

    destroy() {
      if (this.map) this.map.remove();
      this.map = null;
    },
  };

})(window.SL);
