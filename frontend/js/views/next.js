// Screen 5: follow-up actions, and the side-by-side comparison.
(function (SL) {

  SL.views.next = {

    render(el, id) {

      const r = SL.getResult(id);

      if (!r) return SL.missingResult(el);

      const success = r.status === "success";
      const top = (success && r.top_prospects) || [];
      const widen = success ? SL.widenOption(r) : null;

      el.innerHTML = `
        <section class="page narrow">
          <a class="back" href="#/results/${id}">${SL.icon("arrowLeft", 16)} Back to results</a>
          <h1>What would you like to do next?</h1>
          <p class="lead">Build on this analysis or explore new questions.</p>

          <div class="next-grid">
            ${top.length > 1 ? `
            <a class="next-card" href="#/compare/${id}">
              ${SL.icon("scale", 26)}<b>Compare the top ${Math.min(3, top.length)} sites</b>
              <small>See a side-by-side comparison of the best opportunities.</small></a>` : `
            <div class="next-card disabled" aria-disabled="true">
              ${SL.icon("scale", 26)}<b>Compare the top sites</b>
              <small>Needs at least two ranked sites.</small></div>`}

            <div class="next-card disabled" aria-disabled="true">
              ${SL.icon("chart", 26)}<b>Estimate commercial potential</b>
              <small>Needs demand and revenue data that SkyLens doesn't have yet.</small>
              <span class="soon">Coming soon</span></div>

            <button class="next-card" id="similar" ${widen ? "" : "disabled"}>
              ${SL.icon("pin", 26)}<b>Find similar sites nearby</b>
              <small>${SL.esc(widen ? widen.text : success ? SL.widenBlockedText(r) : "Needs a completed analysis.")}</small></button>

            <a class="next-card" href="#/report/${id}">
              ${SL.icon("file", 26)}<b>Generate a detailed report</b>
              <small>A printable report you can save as PDF.</small></a>
          </div>

          <form class="ask-dark" id="ask2">
            ${SL.icon("search", 18)}
            <input id="q2" placeholder="Ask another question..." aria-label="Ask another question">
            <button class="go" type="submit" aria-label="Analyze">${SL.icon("arrowRight", 18)}</button>
          </form>
        </section>`;

      const similar = SL.$("#similar", el);
      if (widen) similar.onclick = () => SL.startAnalysis(r.query, widen.radiusKm);

      SL.$("#ask2", el).addEventListener("submit", e => {
        e.preventDefault();
        SL.startAnalysis(SL.$("#q2", el).value);
      });
    },
  };

  SL.views.compare = {

    render(el, id) {

      const r = SL.getResult(id);
      const sites = ((r && r.top_prospects) || []).slice(0, 3);

      if (!r || !sites.length) return SL.missingResult(el);

      const crits = Object.entries(sites[0].criteria || {});

      // Highlight the best value in a numeric row.
      const row = (label, values, numbers) => {
        const best = numbers && Math.max(...numbers.filter(SL.isNum));
        return `<tr><th>${label}</th>${values.map((v, i) =>
          `<td class="${numbers && SL.isNum(numbers[i]) && numbers[i] === best && sites.length > 1 ? "best" : ""}">${v}</td>`
        ).join("")}</tr>`;
      };

      el.innerHTML = `
        <section class="page">
          <a class="back" href="#/next/${id}">${SL.icon("arrowLeft", 16)} Back</a>
          <h1>Compare the top ${sites.length} sites</h1>
          <p class="lead">Best value in each row is highlighted. "–" means SkyLens has no data for it.</p>
          <div class="card table-wrap">
            <table class="cmp">
              <thead><tr><th></th>${sites.map(c => `
                <th><a href="#/site/${id}/${c.rank}">${SL.maps.thumb(c, 240, 150)}
                  <span class="cmp-name">#${c.rank} ${SL.esc(c.name || SL.cap(c.site_type || c.building_type))}</span></a></th>`).join("")}
              </tr></thead>
              <tbody>
                ${row("Score", sites.map(c => `<b>${SL.fmt(c.score)}</b> / 100`), sites.map(c => c.score))}
                ${row("Confidence", sites.map(c => SL.confidenceBadge(c.confidence)))}
                ${row("Area", sites.map(c => SL.areaText(c.area_m2, r.analysis_spec)))}
                ${row(SL.facts.typeLabel(sites[0]), sites.map(SL.facts.typeValue))}
                ${row("Evidence coverage", sites.map(c => Math.round(c.evidence_coverage * 100) + "%"))}
                ${crits.map(([cid, k]) => row(
                  `${SL.esc(k.label)}<small>${SL.esc(SL.weightText(k.weight).toLowerCase())}</small>`,
                  sites.map(c => {
                    const x = c.criteria[cid];
                    if (!x || !x.available) return "–";
                    return x.weight === 0 ? `<small>${SL.esc(x.evidence || "")}</small>`
                      : `<b>${SL.fmt(x.score)}</b><small>${SL.esc(x.evidence || "")}</small>`;
                  }),
                  sites.map(c => c.criteria[cid] && c.criteria[cid].available && c.criteria[cid].weight > 0
                    ? c.criteria[cid].score : null),
                )).join("")}
              </tbody>
            </table>
          </div>
        </section>`;
    },
  };

})(window.SL);
