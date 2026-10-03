// Screen 2: live progress from /analyze/stream.
(function (SL) {

  const STEPS = [
    {id: "intent", title: "Understanding request"},
    {id: "location", title: "Resolving location"},
    {id: "evidence", title: "Identifying relevant evidence"},
    {id: "candidates", title: "Finding candidate sites"},
    {id: "scoring", title: "Scoring and ranking"},
    {id: "decision", title: "Generating decision"},
  ];

  // Backend error stage -> step it happened in.
  const STAGE_STEP = {
    rate_limit: "intent",
    deepseek: "intent", deepseek_json: "intent", location_extraction: "intent",
    geocoding: "location",
    building_data: "candidates", candidate_data: "candidates", imagery_data: "candidates",
    scoring: "scoring",
  };

  function errorMessage(error) {
    switch (error.stage) {
      case "rate_limit":
        return error.text || "You have reached the free question limit. Sign in for unlimited questions, or try again later.";
      case "deepseek":
        return "SkyLens could not reach its planning model (DeepSeek). Check the API key and try again.";
      case "deepseek_json":
        return "The planning model returned an unreadable answer. Try rephrasing the question.";
      case "location_extraction":
        return "SkyLens could not find a place name in your question. Add a location, e.g. \"in Adyar, Chennai\".";
      case "geocoding":
        return "SkyLens could not find that place on the map. Try a more specific location.";
      case "building_data":
      case "candidate_data":
        return "The map data provider (OpenStreetMap Overpass) is busy or rate-limiting. Wait about a minute and try again.";
      case "imagery_data":
        return "The satellite imagery provider (Microsoft Planetary Computer) didn't respond. Try again in a minute.";
      case "scoring":
        return "SkyLens could not rank the candidates.";
      case "timeout":
        return `The analysis took longer than ${SL.TIMEOUT_MS / 1000} seconds, so SkyLens stopped waiting. ` +
          "The map data provider is probably busy; try again in a minute. Queries that already succeeded are cached.";
      case "network":
        return `SkyLens could not reach the backend at ${SL.API || location.origin}. Check that it is running.`;
      default:
        return "Something went wrong during the analysis.";
    }
  }

  function detail(id, run) {
    const d = run.steps[id] || {};
    const done = d.status === "done";
    switch (id) {
      case "intent":
        return done ? (d.title || "Understood, but no analysis module yet") : "Reading your question";
      case "location":
        return done ? (d.description ? SL.cap(d.description) : SL.shortPlace(d.name))
          : run.steps.intent && run.steps.intent.location ? `Looking up ${run.steps.intent.location}`
          : "Finding the place on the map";
      case "evidence":
        return done
          ? d.layers.filter(l => l.available).map(l => SL.layerLabel(l.id)).join(" · ")
          : "Choosing the data to use";
      case "candidates": {
        const changes = (run.steps.intent || {}).use_case === "construction_progress";
        if ((run.steps.intent || {}).use_case === "infrastructure_outlook") {
          return done ? `${SL.fmt(d.count)} project${d.count === 1 ? "" : "s"} found` : "Reading mapped infrastructure";
        }
        return done ? `${SL.fmt(d.count)} ${changes ? "changed area" : "candidate"}${d.count === 1 ? "" : "s"} found`
          : changes ? "Comparing satellite images from two dates" : "Scanning satellite imagery and map data";
      }
      case "scoring":
        return done ? `Top ${d.ranked} ranked`
          : d.criteria ? `Evaluating ${d.criteria} criteria (${d.measured} with data)`
          : "Measuring each criterion";
      default:
        return done ? "Decision ready" : "Almost there…";
    }
  }

  SL.views.analyzing = {

    render(el) {

      const run = SL.run;

      if (!run) {
        location.hash = "#/";
        return;
      }

      el.innerHTML = `
        <div class="an-map"><div id="an-map"></div></div><div class="an-fade"></div>
        <section class="analyzing">
          <h1>SkyLens is analyzing<br>your request...</h1>
          <p class="lead">Turning your question into a decision.</p>
          <p class="an-query">"${SL.esc(run.query)}"</p>
          <ol class="stepper" id="stepper"></ol>
          <div id="an-error"></div>
          <div class="layers-panel" id="layers-panel" hidden>
            <div class="lp-title">Data layers being analyzed</div>
            <div class="lp-row" id="lp-row"></div>
          </div>
        </section>`;

      this.el = el;
      this.map = SL.maps.create(SL.$("#an-map", el), {scrollWheelZoom: false}).setView([13.0, 80.24], 11);
      this.drawnLocation = false;
      this.drawnPreview = false;
      this.listener = r => this.update(r);
      run.listeners.add(this.listener);
      this.update(run);
    },

    update(run) {

      const el = this.el;

      // ---- steps
      const doneIds = STEPS.filter(s => (run.steps[s.id] || {}).status === "done").map(s => s.id);
      const open = STEPS.find(s => !doneIds.includes(s.id));
      const failedId = run.error ? (STAGE_STEP[run.error.stage] || (open && open.id)) : null;

      SL.$("#stepper", el).innerHTML = STEPS.map(s => {
        let state = "pending";
        if (s.id === failedId) state = "failed";
        else if (doneIds.includes(s.id)) state = "done";
        else if (run.active && open && s.id === open.id) state = "running";
        const mark = state === "done" ? SL.icon("check", 16)
          : state === "failed" ? SL.icon("x", 16) : "";
        return `<li class="st st-${state}">
          <span class="st-icon">${mark}</span>
          <div><b>${s.title}</b><small>${SL.esc(detail(s.id, run))}</small></div>
        </li>`;
      }).join("");

      // ---- data layers
      const layers = Object.entries(run.layers);
      SL.$("#layers-panel", el).hidden = !layers.length;
      SL.$("#lp-row", el).innerHTML = layers.map(([id, status]) => {
        if (status === "pending" && !run.active) status = "unused";
        const note = {unavailable: "No data yet", failed: "Unavailable", unused: "Not used"}[status] || "";
        const mark = status === "pending" ? `<span class="spin"></span>`
          : status === "used" ? SL.icon("check", 12, "ok") : "";
        return `<div class="lp lp-${status}" title="${SL.esc(note || SL.cap(status))}">
          <span class="lp-icon">${SL.icon(SL.layerIcon(id), 20)}${mark}</span>
          <span>${SL.esc(SL.layerLabel(id))}</span>${note ? `<em>${note}</em>` : ""}
        </div>`;
      }).join("");

      // ---- map
      const loc = run.steps.location;
      if (loc && loc.status === "done" && !this.drawnLocation) {
        this.drawnLocation = true;
        const area = SL.maps.searchLayer(loc, loc.latitude, loc.longitude).addTo(this.map);
        SL.maps.placeTag(loc.latitude, loc.longitude, SL.shortPlace(loc.name).split(",")[0]).addTo(this.map);
        this.map.flyToBounds(area.getBounds(), {padding: [40, 40], duration: 1.2});
      }
      if (run.preview && !this.drawnPreview && this.drawnLocation) {
        this.drawnPreview = true;
        run.preview.preview.forEach(p => {
          const layer = SL.maps.parcel(p.geometry, false) ||
            L.circleMarker([p.latitude, p.longitude], {radius: 5, color: SL.LIME, weight: 2});
          layer.addTo(this.map);
        });
      }

      // ---- error
      SL.$("#an-error", el).innerHTML = run.error ? `
        <div class="an-error">
          <b>Analysis stopped${failedId ? " at " + STEPS.find(s => s.id === failedId).title.toLowerCase() : ""}</b>
          <p>${SL.esc(errorMessage(run.error))}</p>
          ${run.error.text && !["network", "timeout"].includes(run.error.stage)
            ? `<p class="muted sm">Detail: ${SL.esc(run.error.text)}</p>` : ""}
          <div class="row-gap">
            <button class="btn-lime" id="retry">${SL.icon("refresh", 16)} Try again</button>
            <button class="btn-outline" id="edit">Edit question</button>
            ${SL.feedback.button("result", "Report this problem")}
          </div>
        </div>` : "";

      if (run.error) {
        SL.$("#retry", el).onclick = () => SL.startAnalysis(run.query, run.radiusKm);
        SL.$("#edit", el).onclick = () => { SL.pendingQuery = run.query; SL.go("#/"); };
        SL.feedback.attach(SL.$("#an-error", el), {run});
      }
    },

    destroy() {
      if (SL.run && this.listener) SL.run.listeners.delete(this.listener);
      if (this.map) this.map.remove();
      this.map = null;
    },
  };

})(window.SL);
