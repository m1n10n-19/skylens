// Screen 1: ask a question.
(function (SL) {

  const EXAMPLES = [
    "Find commercial roofs around Adyar suitable for solar",
    "Find 10 cent empty land in Thoraipakkam for an EV charger",
    "Find the best location for a food court in Adyar",
  ];

  // Card images are satellite crops of each place.
  const CATEGORIES = [
    {title: "Real Estate", text: "Find development opportunities", icon: "building",
     lat: 12.9010, lon: 80.2279, span: 0.012,
     query: "Find vacant land above 1 acre near OMR with good road access"},
    {title: "Energy", text: "Find solar / EV opportunities", icon: "sun",
     lat: 13.0108, lon: 80.2129, span: 0.006,
     query: "Find large roofs around Guindy Industrial Estate suitable for solar"},
    {title: "Infrastructure", text: "Compare potential sites", icon: "road",
     lat: 13.0067, lon: 80.2058, span: 0.008,
     query: "Where should I put an EV charging hub near Sholinganallur?"},
    {title: "Construction", text: "Monitor project progress", icon: "crane",
     lat: 12.8398, lon: 80.2266, span: 0.008,
     query: "Which of my construction sites have changed significantly since last month?"},
    {title: "Risk", text: "Find environmental or physical risks", icon: "leaf",
     lat: 12.9380, lon: 80.2140, span: 0.02,
     query: "Which areas near Pallikaranai marsh are at risk of flooding?"},
  ];

  SL.views.workspace = {

    render(el) {

      const prefill = SL.pendingQuery || "";
      SL.pendingQuery = null;

      const left = SL.store.remaining();

      el.innerHTML = `
        <div class="hero-bg"><div id="hero-map"></div><div class="hero-fade"></div></div>
        <section class="workspace">
          <h1>What do you want<br>SkyLens to find?</h1>
          <p class="lead">Ask a question about a place, property, infrastructure, risk or
            opportunity. SkyLens will figure out the data and analysis required.</p>

          <form class="ask" id="ask">
            <textarea id="q" rows="2" aria-label="Your question"
              placeholder="e.g. Find 10 cent empty land parcels in Thoraipakkam Chennai for an EV charging station">${SL.esc(prefill)}</textarea>
            <button class="go" type="submit" aria-label="Analyze">${SL.icon("arrowRight", 20)}</button>
          </form>
          ${left ? "" : `<p class="notice">You've used your ${SL.FREE_INTENTS} free intents.
            <a href="#/upgrade">See plans</a> or <a href="#/login">log in</a>.</p>`}

          <div class="examples">
            <div class="muted sm">Try an example:</div>
            <div class="chips">
              ${EXAMPLES.map(q => `<button class="chip" type="button" data-q="${SL.esc(q)}">${SL.esc(q)}</button>`).join("")}
            </div>
          </div>

          <h2 class="section-title">Not sure where to start?</h2>
          <div class="categories">
            ${CATEGORIES.map((c, i) => `
              <button class="cat" type="button" data-i="${i}">
                <span class="cat-img"><img src="${SL.maps.areaImage(c.lat, c.lon, c.span)}" alt="" loading="lazy">
                  <span class="cat-icon">${SL.icon(c.icon, 16)}</span></span>
                <span class="cat-body"><b>${c.title}</b><small>${c.text}</small></span>
              </button>`).join("")}
          </div>

          <p class="fineprint">Imagery &copy; Esri, Maxar, Earthstar Geographics.
            Map data &copy; OpenStreetMap contributors.</p>
        </section>`;

      this.map = SL.maps.background(SL.$("#hero-map", el), 13.0005, 80.2720, 14);

      const q = SL.$("#q", el);

      SL.$("#ask", el).addEventListener("submit", e => {
        e.preventDefault();
        SL.startAnalysis(q.value);
      });

      q.addEventListener("keydown", e => {
        if (e.key === "Enter" && !e.shiftKey) {
          e.preventDefault();
          SL.startAnalysis(q.value);
        }
      });

      const fill = text => { q.value = text; q.focus(); };

      SL.$$(".chip", el).forEach(b => b.addEventListener("click", () => fill(b.dataset.q)));

      SL.$$(".cat", el).forEach(b => b.addEventListener("click", () => fill(CATEGORIES[+b.dataset.i].query)));

      if (!prefill) q.focus();
    },

    destroy() {
      if (this.map) this.map.remove();
      this.map = null;
    },
  };

})(window.SL);
