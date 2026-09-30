// About: what SkyLens is and what it is for. Static content.
(function (SL) {

  const CONTACT = "hello@skylens.ai";

  const QUESTIONS = [
    "Find land that fits our requirements.",
    "Show me what changed on our sites.",
    "Track construction progress.",
    "Where are the elephants moving?",
    "What happened at this accident site?",
  ];

  const APPLICATIONS = [
    {label: "Real estate + construction", icon: "building",
     title: "Know the site without being everywhere.",
     text: "Help real estate and construction teams find land, inspect sites, track progress and understand physical inventory.",
     items: ["Land discovery & site assessment", "Construction progress tracking", "Change detection over time", "Spatial inventory"]},
    {label: "Conservation", icon: "leaf",
     title: "Understand movement across the landscape.",
     text: "Support forest teams with aerial, thermal and spatial intelligence for wildlife and protected areas.",
     items: ["Elephant movement monitoring", "Wildlife corridor intelligence", "Protected-area monitoring", "Support for anti-poaching teams"]},
    {label: "Insurance + industry", icon: "shield",
     title: "See the asset. Understand the risk.",
     text: "Create spatial evidence for underwriting, industrial inspection and accident assessment.",
     items: ["Pre-underwriting intelligence", "Industrial asset inspection", "Accident-site documentation", "Before / after evidence"]},
  ];

  const SENSORS = [
    {id: "sat", name: "Satellite", text: "Find candidates and context.", tier: "Explore · Free"},
    {id: "rgb", name: "Aerial RGB", text: "See what is actually there in detail.", tier: "Enterprise"},
    {id: "thermal", name: "Thermal", text: "Reveal heat, activity and anomalies.", tier: "Enterprise"},
    {id: "lidar", name: "LiDAR", text: "Measure shape, height and volume.", tier: "Enterprise"},
  ];

  const IMPACT = [
    {title: "Disaster intelligence",
     text: "After floods, cyclones, fires or earthquakes, identify what changed, who is affected and where intervention should happen first.",
     q: "Which 1,000 locations need attention first?"},
    {title: "Climate & water resilience",
     text: "Spot communities and landscapes moving toward water stress by combining terrain, vegetation, reservoirs, rainfall and physical change.",
     q: "Where should we intervene before water becomes a crisis?"},
    {title: "Wildlife corridors",
     text: "Understand how roads, fencing, settlements and development are changing animal movement and identify corridors worth protecting.",
     q: "Where is development about to cut off a critical corridor?"},
    {title: "Environmental protection",
     text: "Find emerging land disturbance, illegal dumping and ecological pressure across areas too large for continuous manual inspection.",
     q: "Where is physical evidence of harm appearing?"},
    {title: "Invisible infrastructure gaps",
     text: "Reveal communities losing practical access to roads, bridges, markets, water or essential services before the problem becomes visible in official reports.",
     q: "Which places are becoming harder to reach?"},
    {title: "Smarter public investment",
     text: "Help governments compare physical evidence across thousands of locations so limited budgets go toward the places where intervention can matter most.",
     q: "Where can one decision create the greatest public value?"},
  ];

  const PRINCIPLES = [
    ["Privacy by design", "Minimize unnecessary collection and exposure."],
    ["Purpose limitation", "Capture data for defined customer objectives."],
    ["Human oversight", "Keep consequential decisions subject to appropriate review."],
    ["Evidence & confidence", "Separate observation from inference and show uncertainty."],
    ["Responsible deployment", "Operate within applicable law, permissions and safety boundaries."],
    ["Data governance", "Define access, retention and security controls."],
  ];

  const signal = rows => `
    <dl class="ab-signal">
      ${rows.map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join("")}
    </dl>`;

  SL.views.about = {

    render(el) {

      el.innerHTML = `
        <div class="page about">

          <section class="ab-hero">
            <div class="ab-kicker">Physical intelligence for the real world</div>
            <h1>Ask a question about reality.<br>Get a decision.</h1>
            <p class="lead">SkyLens turns a business question about a place into verified spatial
              intelligence, using satellite data, autonomous drones, computer vision, thermal,
              LiDAR and AI.</p>
            <div class="row-gap">
              <a class="btn-lime lg" href="#/">Ask a question ${SL.icon("arrowRight", 16)}</a>
              <a class="btn-ghost" href="mailto:${CONTACT}?subject=SkyLens%20customer%20pilot">Talk to SkyLens</a>
            </div>
          </section>

          <section class="ab-section">
            <div class="ab-kicker">The thesis</div>
            <h2 class="ab-h2">The physical world is full of data.<br>
              <em>Most of it is still understood manually.</em></h2>
            <p class="lead">Site visits. Surveys. Aerial photographs. Inspection reports.
              Spreadsheets. Human interpretation. SkyLens is building the intelligence layer that
              connects these observations into a spatial understanding of reality.</p>
          </section>

          <section class="ab-section">
            <div class="ab-kicker">Why SkyLens</div>
            <h2 class="ab-h2">You don't buy a drone flight.<br>You ask a question.</h2>
            <div class="ab-split">
              <ul class="ab-questions">
                ${QUESTIONS.map(q => `<li>“${q}”</li>`).join("")}
              </ul>
              <div>
                <p class="ab-big">SkyLens decides what needs to be observed, captures it,
                  understands it, verifies the evidence and returns an answer.</p>
                <p class="muted">The customer doesn't have to choose the drone, sensor, flight path
                  or technical workflow. The system is organized around the intent, not around
                  the hardware.</p>
              </div>
            </div>
          </section>

          <section class="ab-section">
            <div class="ab-kicker">Initial markets</div>
            <h2 class="ab-h2">Start with valuable problems.<br>Build toward a general physical-world model.</h2>
            <div class="ab-grid ab-grid-3">
              ${APPLICATIONS.map((a, i) => `
                <article class="card ab-app">
                  <div class="ab-app-head">
                    <span class="ab-num">0${i + 1} · ${a.label}</span>
                    <span class="ab-app-icon">${SL.icon(a.icon, 18)}</span>
                  </div>
                  <h3>${a.title}</h3>
                  <p class="muted">${a.text}</p>
                  <ul class="ab-list">${a.items.map(x => `<li>${x}</li>`).join("")}</ul>
                </article>`).join("")}
            </div>
          </section>

          <section class="ab-section">
            <div class="ab-kicker">How it senses</div>
            <h2 class="ab-h2">Start from orbit.<br>Go on site only when the question needs it.</h2>
            <p class="lead">SkyLens escalates sensing one step at a time. Satellite data can answer
              simple questions for free; higher-confidence questions can trigger physical capture.</p>
            <div class="ab-grid ab-grid-4">
              ${SENSORS.map(s => `
                <div class="card ab-sensor">
                  <div class="ab-visual ab-${s.id}"></div>
                  <h3>${s.name}</h3>
                  <p class="muted sm">${s.text}</p>
                  <span class="tag${s.id === "sat" ? " tag-lime" : ""}">${s.tier}</span>
                </div>`).join("")}
            </div>
            <div class="ab-grid ab-grid-2">
              <div class="card">
                <h3>See → Understand → Act</h3>
                <p class="muted sm">Every observation becomes part of a spatial record: what is
                  there, where it is, what changed and what matters.</p>
                ${signal([["RGB + Thermal + LiDAR", "Sense"], ["3D + Computer Vision", "Understand"],
                          ["Reports + APIs + Workflows", "Act"]])}
              </div>
              <div class="card">
                <h3>Evidence, not just inference.</h3>
                <p class="muted sm">SkyLens separates observed evidence from inference, checks
                  findings against available data and surfaces uncertainty when the evidence
                  isn't enough.</p>
                ${signal([["Observed", "Evidence"], ["Inferred", "Reasoning"], ["Uncertain", "Human review"]])}
              </div>
            </div>
          </section>

          <section class="ab-section">
            <div class="ab-kicker">SkyLens for public good</div>
            <h2 class="ab-h2">Turn physical-world intelligence into public value.</h2>
            <p class="lead">The same intelligence layer that helps organizations understand sites
              can help governments, communities and conservation teams act earlier, allocate scarce
              resources better and protect places that are easy to overlook.</p>
            <div class="ab-grid ab-grid-3">
              ${IMPACT.map((c, i) => `
                <article class="card ab-impact${i === 0 ? " why" : ""}">
                  <span class="ab-num">0${i + 1}</span>
                  <h3>${c.title}</h3>
                  <p class="muted sm">${c.text}</p>
                  <p class="ab-q">“${c.q}”</p>
                </article>`).join("")}
            </div>
            <div class="ab-principle">
              <p class="ab-big">Observe broadly.<br><em>Intervene precisely.</em></p>
              <p class="muted">SkyLens is designed to understand places, not to identify or
                continuously track private individuals. Public-good deployments should operate with
                defined purpose, minimum necessary observation, appropriate permissions, human
                oversight and auditable evidence.</p>
            </div>
          </section>

          <section class="ab-section">
            <div class="ab-kicker">Responsible intelligence</div>
            <h2 class="ab-h2">Privacy and ethics are built into the way SkyLens works.</h2>
            <p class="lead">Physical intelligence should not become unrestricted surveillance.
              SkyLens is designed around defined purpose, minimum necessary observation, evidence,
              human oversight and responsible deployment.</p>
            <div class="ab-grid ab-grid-3">
              ${PRINCIPLES.map(([t, d]) => `
                <div class="card ab-principle-card">
                  <h3>${SL.icon("check", 16)} ${t}</h3>
                  <p class="muted sm">${d}</p>
                </div>`).join("")}
            </div>
          </section>

          <section class="ab-section">
            <div class="ab-kicker">The long-term vision</div>
            <h2 class="ab-h2">From drones that capture data to machines that understand the physical world.</h2>
            <p class="lead">The drone is the beginning, not the destination. SkyLens is building
              toward an intelligence layer that continuously observes physical environments,
              understands meaningful change and makes that knowledge available to humans and machines.</p>
            <blockquote class="ab-quote">AI transformed how we interact with digital information.
              SkyLens is building the bridge between intelligence and physical reality.</blockquote>
          </section>

          <section class="card ab-cta">
            <div class="ab-kicker">Work with SkyLens</div>
            <h2 class="ab-h2">Have a physical-world problem?</h2>
            <p class="lead">Tell us what you need to know. We're looking for environments that are
              large, complex, dynamic or difficult to inspect.</p>
            <div class="row-gap">
              <a class="btn-lime lg" href="mailto:${CONTACT}?subject=SkyLens%20customer%20pilot">Talk to SkyLens ${SL.icon("arrowRight", 16)}</a>
              <a class="btn-ghost" href="#/">Try it now</a>
            </div>
          </section>

        </div>`;
    },
  };

})(window.SL);
