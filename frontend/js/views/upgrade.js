// Screen 6: upgrade after the free intents, and the Projects list.
// Demo only: counts and history live in this browser.
(function (SL) {

  SL.views.upgrade = {

    render(el) {

      const used = SL.store.used();
      const left = SL.store.remaining();
      const history = SL.store.history();
      const sum = key => history.reduce((n, h) => n + (h[key] || 0), 0);
      const useCases = new Set(history.filter(h => h.status === "success").map(h => h.title)).size;

      el.innerHTML = `
        <section class="upgrade">
          <div class="up-card">
            <div class="up-check">${SL.icon("check", 28)}</div>
            <h1>${left ? `You have ${left} free intent${left === 1 ? "" : "s"} left`
              : `You've explored ${used} decision${used === 1 ? "" : "s"}<br>with SkyLens.`}</h1>
            <p class="muted">Here's what you've discovered:</p>
            <div class="up-stats">
              <div>${SL.icon("grid", 20)}<b>${used}</b><small>intents analyzed</small></div>
              <div>${SL.icon("pin", 20)}<b>${SL.fmt(sum("total"))}</b><small>opportunities found</small></div>
              <div>${SL.icon("parcels", 20)}<b>${SL.fmt(sum("shortlisted"))}</b><small>sites shortlisted</small></div>
              <div>${SL.icon("layers", 20)}<b>${useCases}</b><small>use cases explored</small></div>
            </div>
            <h2>Keep investigating with a paid plan.</h2>
            <p class="muted">Continue analyzing locations, save projects, generate detailed reports
              and access advanced use cases.</p>
            <div class="up-cta">
              <button class="btn-lime lg" id="buy">Start with ₹50,000/month ${SL.icon("arrowRight", 16)}</button>
              <button class="btn-outline lg" id="talk">Talk to our team</button>
            </div>
            ${left ? `<p><a class="back" href="#/">${SL.icon("arrowLeft", 16)} Keep exploring for free</a></p>` : ""}
            <p class="fineprint">Demo: the free-intent counter is stored in this browser.
              <button class="link" id="reset">Reset demo</button></p>
          </div>
        </section>`;

      const soon = () => SL.toast("Payments and sales contact aren't connected in this demo.");
      SL.$("#buy", el).onclick = soon;
      SL.$("#talk", el).onclick = soon;
      SL.$("#reset", el).onclick = () => {
        SL.store.resetDemo();
        SL.toast(`Demo reset: ${SL.FREE_INTENTS} free intents available.`);
        SL.go("#/");
      };
    },
  };

  SL.views.projects = {

    render(el) {

      const history = SL.store.history();

      el.innerHTML = `
        <section class="page narrow">
          <a class="back" href="#/">${SL.icon("arrowLeft", 16)} Home</a>
          <div class="page-head">
            <div><h1>Projects</h1>
              <p class="lead">Your recent analyses, saved in this browser only.</p></div>
            ${history.length ? `<button class="btn-outline" id="clear">Clear history</button>` : ""}
          </div>
          ${history.length ? `<div class="projects">${history.map(h => {
            const stored = !!SL.store.result(h.id);
            return `<article class="card project">
              <div>
                <div class="tags"><span class="tag${h.status === "success" ? " tag-lime" : ""}">${SL.esc(h.title || "Analysis")}</span>
                  ${h.place ? `<span class="tag">${SL.esc(h.place)}</span>` : ""}</div>
                <p>"${SL.esc(h.query)}"</p>
                <small class="muted">${new Date(h.at).toLocaleString("en-IN")}
                  ${h.status === "success" ? ` · ${SL.fmt(h.total)} candidates, ${SL.fmt(h.shortlisted)} ranked` : ""}</small>
              </div>
              ${stored ? `<a class="btn-outline" href="#/results/${h.id}">Open</a>`
                : `<button class="btn-outline" data-q="${SL.esc(h.query)}">Ask again</button>`}
            </article>`;
          }).join("")}</div>` : `
          <div class="card empty-result"><p>No analyses yet.</p>
            <a class="btn-lime" href="#/">Ask a question ${SL.icon("arrowRight", 16)}</a></div>`}
        </section>`;

      const clear = SL.$("#clear", el);
      if (clear) clear.onclick = () => { SL.store.clearHistory(); SL.go("#/projects"); };

      SL.$$("[data-q]", el).forEach(b => b.addEventListener("click", () => {
        SL.pendingQuery = b.dataset.q;
        SL.go("#/");
      }));
    },
  };

})(window.SL);
