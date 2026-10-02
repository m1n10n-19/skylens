// Printable report (Export Report / Generate a detailed report).
(function (SL) {

  SL.views.report = {

    render(el, id) {

      const r = SL.getResult(id);

      if (!r || r.status !== "success") return SL.missingResult(el);

      const top = r.top_prospects || [];
      const uc = r.use_case || {};
      const an = r.analysis || {};
      const loc = r.resolved_location;
      const date = new Date().toLocaleDateString("en-IN", {year: "numeric", month: "long", day: "numeric"});
      const verify = SL.evidence.mergedVerify(top.slice(0, 3));
      const dates = SL.evidence.dates(top);
      const done = r.completeness;
      const noun = SL.rankNoun(uc.id);
      const cd = r.change_detection;

      el.innerHTML = `
        <div class="report-bar no-print">
          <a class="back" href="#/results/${id}">${SL.icon("arrowLeft", 16)} Back to results</a>
          <button class="btn-lime" id="print">${SL.icon("download", 16)} Print / Save as PDF</button>
        </div>

        <article class="report">
          <header class="rp-head">
            <div class="wordmark">SKY<span>LENS</span></div>
            <div class="rp-meta">Site analysis report · ${date}</div>
          </header>

          <h1>${SL.esc(uc.title || "Analysis")}: ${SL.esc(SL.shortPlace(loc.name))}</h1>
          <p class="rp-q">"${SL.esc(r.query)}"</p>

          <h2>Summary</h2>
          <p>${SL.esc(r.decision.summary)}</p>
          <p><b>Recommended action:</b> ${SL.esc(r.decision.recommended_action)}</p>
          <table class="rp-table">
            <tr><th>Location</th><td>${SL.esc(loc.name)} (${loc.latitude.toFixed(5)}, ${loc.longitude.toFixed(5)})</td></tr>
            <tr><th>Search area</th><td>${r.search_area.description
              ? `${SL.esc(SL.cap(r.search_area.description))} (≈ ${SL.fmt(r.search_area.area_km2, 1)} km²)`
              : `${r.search_area.radius_km} km radius`}</td></tr>
            <tr><th>Candidates evaluated</th><td>${SL.fmt(an.total_candidates)} ${SL.esc(String(an.candidate_type || "").replace(/_/g, " "))}s</td></tr>
            <tr><th>Size filter</th><td>${SL.isNum(an.minimum_area_m2) ? "≥ " + SL.fmt(an.minimum_area_m2) + " m²" : "–"}
              ${SL.isNum(an.maximum_area_m2) ? " and ≤ " + SL.fmt(an.maximum_area_m2) + " m²" : ""}
              ${r.analysis_spec.area.as_stated ? ` (requested: ${SL.esc(r.analysis_spec.area.as_stated)})` : ""}</td></tr>
            ${cd ? `<tr><th>Imagery compared</th><td class="rp-scene">Sentinel-2 ${SL.esc(cd.before.id)}
              (${SL.evidence.day(cd.before.date)}, ${Math.round(cd.before.clear_fraction * 100)}% clear) and
              ${SL.esc(cd.after.id)} (${SL.evidence.day(cd.after.date)}, ${Math.round(cd.after.clear_fraction * 100)}% clear);
              ${cd.season_gap_days} days apart in the year. ${SL.esc(cd.method)}</td></tr>` : ""}
            <tr><th>Overall confidence</th><td>${SL.esc(SL.cap(r.decision.confidence))}</td></tr>
            ${done ? `<tr><th>Completeness</th><td>${done.status === "partial"
              ? `Partial: ${done.reasons.map(SL.esc).join(" ")}` : "Complete"}</td></tr>` : ""}
          </table>

          <h2>Method</h2>
          <p>Each candidate is scored 0–100 on the criteria below. The overall score is the
            weighted average of the criteria SkyLens could measure; criteria without data are
            excluded and listed as not assessed. No values are estimated.</p>
          <table class="rp-table">
            <thead><tr><th>Criterion</th><th>Weight</th><th>Measured</th></tr></thead>
            <tbody>${(uc.criteria || []).map(k => `<tr><td>${SL.esc(k.label)}</td>
              <td>${k.weight > 0 ? Math.round(k.weight * 100) + "%" : "Evidence only"}</td><td>${k.measured ? "Yes" : "No data"}</td></tr>`).join("")}</tbody>
          </table>

          <h2>Ranked ${noun.toLowerCase()}s</h2>
          ${top.length ? `<table class="rp-table">
            <thead><tr><th>#</th><th>Site</th><th>Area</th><th>Score</th><th>Confidence</th><th>Coordinates</th></tr></thead>
            <tbody>${top.map(c => `<tr><td>${c.rank}</td>
              <td>${SL.esc(c.name || SL.cap(c.site_type || c.building_type))}</td>
              <td>${SL.fmt(c.area_m2)} m²</td><td><b>${SL.fmt(c.score)}</b></td>
              <td>${SL.esc(SL.cap(c.confidence))}</td>
              <td>${(+c.latitude).toFixed(5)}, ${(+c.longitude).toFixed(5)}</td></tr>`).join("")}</tbody>
          </table>` : "<p>No candidates matched.</p>"}

          ${top.slice(0, 3).map(c => `
            <section class="rp-site">
              <h3>${noun} #${c.rank}: score ${SL.fmt(c.score)} / 100</h3>
              <div class="rp-site-grid">
                ${SL.maps.thumb(c, 360, 230)}
                <div>
                  ${SL.facts.rowsHTML(SL.facts.rows(c, r))}
                  <b>Why it ranked here</b>
                  <ul>${(c.reasons || []).map(x => `<li>${SL.esc(x)}</li>`).join("")}</ul>
                </div>
              </div>
            </section>`).join("")}

          ${verify.length ? `
          <h2>What to verify next</h2>
          <p>Remote data cannot settle these questions. They are ordered from the cheapest kind of
            check to the most involved. SkyLens does not carry out these checks.</p>
          <table class="rp-table">
            <thead><tr><th>Question to resolve</th><th>Check</th><th>Why</th><th>Sites</th></tr></thead>
            <tbody>${verify.map(({item, ranks}) => `<tr>
              <td>${SL.esc(item.question)}</td>
              <td>${SL.esc(SL.evidence.methodLabel(item.method_type))}</td>
              <td>${SL.esc(item.why)}</td>
              <td>${ranks.map(n => "#" + n).join(", ")}</td></tr>`).join("")}</tbody>
          </table>` : ""}

          <h2>Data used</h2>
          <ul>${(r.evidence || []).map(e => {
            const d = dates[e.id] || {};
            const asOf = d.data_as_of && SL.evidence.day(d.data_as_of);
            const got = d.retrieved_at && SL.evidence.day(d.retrieved_at);
            return `<li>${SL.esc(e.label)}: ${SL.esc(e.status.replace(/_/g, " "))}` +
              (e.source ? ` (${SL.esc(e.source)})` : "") +
              (asOf ? `; data as of ${asOf}` : "") + (got ? `, retrieved ${got}` : "") + "</li>";
          }).join("")}</ul>
          ${r.satellite && r.satellite.date ? `<p class="sm rp-scene">Satellite scene ${SL.esc(r.satellite.id)},
            acquired ${SL.evidence.day(r.satellite.date)}, ${SL.fmt(r.satellite.cloud_cover, 1)}% cloud cover
            (context only; not used in scoring).</p>` : ""}

          <h2>Not assessed</h2>
          <p>${(r.missing_data || []).map(m => SL.esc(SL.cap(m))).join(", ") || "–"}</p>

          <h2>Limitations</h2>
          <ul>${(r.limitations || []).map(l => `<li>${SL.esc(l)}</li>`).join("")}</ul>

          <footer class="rp-foot">Generated by SkyLens. Imagery &copy; Esri, Maxar, Earthstar Geographics.
            Map data &copy; OpenStreetMap contributors.</footer>
        </article>`;

      SL.$("#print", el).onclick = () => window.print();
    },
  };

})(window.SL);
