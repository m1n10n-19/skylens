// Team login: the username/password in the backend's .env.
// Signed-in users have no question limit.
(function (SL) {

  SL.views.login = {

    render(el) {

      if (SL.session) {
        el.innerHTML = `
          <section class="login">
            <div class="login-card">
              <h1>You're logged in</h1>
              <p class="muted">Signed in as <b>${SL.esc(SL.session.username)}</b>. You have no question limit.</p>
              <a class="btn-lime lg" href="#/">Ask a question ${SL.icon("arrowRight", 16)}</a>
            </div>
          </section>`;
        return;
      }

      el.innerHTML = `
        <section class="login">
          <form class="login-card" id="login-form" novalidate>
            <h1>Team login</h1>
            <p class="muted">Signed-in team members can ask unlimited questions.</p>
            <label>Username
              <input id="login-user" name="username" autocomplete="username" required>
            </label>
            <label>Password
              <input id="login-pass" name="password" type="password" autocomplete="current-password" required>
            </label>
            <p class="login-error" id="login-error" role="alert" hidden></p>
            <button class="btn-lime lg" type="submit" id="login-go">Log in</button>
            <a class="back" href="#/">${SL.icon("arrowLeft", 16)} Continue without logging in</a>
          </form>
        </section>`;

      const form = SL.$("#login-form", el);
      const user = SL.$("#login-user", el);
      const pass = SL.$("#login-pass", el);
      const error = SL.$("#login-error", el);
      const button = SL.$("#login-go", el);

      const fail = message => {
        error.textContent = message;
        error.hidden = false;
        button.disabled = false;
        button.textContent = "Log in";
      };

      form.addEventListener("submit", async e => {
        e.preventDefault();
        if (!user.value.trim() || !pass.value) return fail("Enter your username and password.");
        error.hidden = true;
        button.disabled = true;
        button.textContent = "Logging in…";

        let response;
        try {
          response = await fetch(SL.API + "/auth/login", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({username: user.value, password: pass.value}),
          });
        } catch (err) {
          return fail(`Can't reach the backend at ${SL.API || location.origin}. Check that it is running.`);
        }

        const body = await response.json().catch(() => null);

        if (!response.ok || !body || !body.token) {
          const detail = body && body.detail;
          return fail((detail && detail.error) || "Login failed. Try again.");
        }

        SL.auth.save({token: body.token, username: body.username});
        SL.toast(`Logged in as ${body.username}. No question limit.`);
        SL.go("#/");
      });

      user.focus();
    },
  };

})(window.SL);
