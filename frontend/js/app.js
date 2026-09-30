// App shell: header, hash router and the analysis run controller.
(function (SL) {

  // ---------------------------------------------------------- header

  SL.renderHeader = () => {
    const left = SL.store.remaining();
    SL.$("#topbar").innerHTML = `
      <a class="brand" href="#/" aria-label="SkyLens home">
        <span class="wordmark">SKY<span>LENS</span></span>
        <span class="tagline">Reality Intelligence</span>
      </a>
      <nav class="top-actions">
        ${SL.unlimited()
          ? `<span class="pill" title="Signed in: no question limit">Unlimited · ${SL.esc(SL.session.username)}</span>`
          : `<a class="pill${left ? "" : " pill-out"}" href="#/upgrade"
               title="Demo counter, stored in this browser">Free intents: ${left} / ${SL.FREE_INTENTS}</a>`}
        <a class="btn-ghost about-link" href="#/about" aria-label="About SkyLens" title="About SkyLens">${SL.icon("info", 14)}<span>About</span></a>
        <a class="btn-ghost" href="#/projects">Projects</a>
        ${SL.unlimited()
          ? `<button class="btn-ghost" id="logout">Log out</button>`
          : `<a class="btn-ghost" href="#/login">${SL.icon("user", 14)} Log in</a>`}
      </nav>`;

    const logout = SL.$("#logout");
    if (logout) {
      logout.onclick = () => {
        SL.auth.clear();
        SL.renderHeader();
        SL.toast("Logged out.");
      };
    }
  };

  // ---------------------------------------------------------- results

  SL.getResult = id =>
    SL.store.result(id) || (SL.run && SL.run.resultId === id ? SL.run.result : null);

  SL.missingResult = el => {
    el.innerHTML = `
      <section class="page narrow empty-page">
        <h1>This analysis isn't available</h1>
        <p class="lead">Only your last few analyses are kept, and only in this browser.</p>
        <a class="btn-lime" href="#/">Ask a question ${SL.icon("arrowRight", 16)}</a>
      </section>`;
  };

  // ---------------------------------------------------------- router

  const ROUTES = [
    [/^#\/analyzing$/, "analyzing"],
    [/^#\/results\/(\w+)$/, "results"],
    [/^#\/site\/(\w+)\/(\d+)$/, "site"],
    [/^#\/next\/(\w+)$/, "next"],
    [/^#\/compare\/(\w+)$/, "compare"],
    [/^#\/report\/(\w+)$/, "report"],
    [/^#\/upgrade$/, "upgrade"],
    [/^#\/projects$/, "projects"],
    [/^#\/login$/, "login"],
    [/^#\/about$/, "about"],
  ];

  let current = null;

  function route() {
    const hash = location.hash;
    let name = "workspace";
    let params = [];
    for (const [pattern, view] of ROUTES) {
      const match = hash.match(pattern);
      if (match) { name = view; params = match.slice(1); break; }
    }
    if (current && current.destroy) current.destroy();
    current = SL.views[name];
    const el = SL.$("#view");
    el.innerHTML = "";
    el.className = "view view-" + name;
    document.body.dataset.view = name;
    SL.renderHeader();
    window.scrollTo(0, 0);
    current.render(el, ...params);
  }

  SL.go = hash => {
    if (location.hash === hash) route();
    else location.hash = hash;
  };

  // Check a stored login before the first screen: it is dropped if it
  // expired or the password in .env changed.
  async function start() {
    if (SL.auth.restore()) {
      try {
        const response = await fetch(SL.API + "/auth/me", {headers: SL.auth.header()});
        if (response.ok && !(await response.json()).unlimited) SL.auth.clear();
      } catch (e) { /* backend unreachable: keep the session until it can be checked */ }
    }
    window.addEventListener("hashchange", route);
    route();
  }

  document.addEventListener("DOMContentLoaded", start);

  // ---------------------------------------------------------- analysis run

  // One analysis at a time. Views subscribe via run.listeners.
  SL.run = null;

  SL.startAnalysis = (query, radiusKm) => {

    query = (query || "").trim();

    if (!query) return;

    if (SL.run && SL.run.active) {
      SL.toast("An analysis is already running.");
      SL.go("#/analyzing");
      return;
    }

    if (SL.store.remaining() <= 0) {
      SL.go("#/upgrade");
      return;
    }

    const run = SL.run = {
      query, radiusKm, active: true,
      steps: {}, layers: {}, preview: null,
      error: null, result: null, resultId: null,
      listeners: new Set(),
    };

    const notify = () => run.listeners.forEach(fn => fn(run));

    SL.go("#/analyzing");

    SL.analyzeStream(query, radiusKm, (event, data) => {
      if (event === "step") {
        run.steps[data.step] = Object.assign({}, run.steps[data.step], data);
        if (data.step === "evidence") {
          data.layers.forEach(l => {
            if (!run.layers[l.id]) run.layers[l.id] = l.available ? "pending" : "unavailable";
          });
        }
      } else if (event === "layer") {
        run.layers[data.id] = data.status;
      } else if (event === "candidates") {
        run.preview = data;
      }
      notify();
    }).then(result => {
      run.result = result;
      run.active = false;
      run.resultId = SL.store.add(result);
      SL.renderHeader();
      notify();
      if (location.hash === "#/analyzing") {
        setTimeout(() => {
          if (location.hash === "#/analyzing") location.hash = "#/results/" + run.resultId;
        }, 700);
      } else {
        SL.toast("Your analysis is ready. Open it from Projects.");
      }
    }).catch(error => {
      run.error = error;
      run.active = false;
      notify();
    });
  };

})(window.SL);
