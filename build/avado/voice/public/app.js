// AVADO voice page for Home Assistant. Plain JS, no dependencies; strict CSP (script-src 'self').
(function () {
  "use strict";

  var params = new URLSearchParams(location.search);
  var theme = params.get("theme");
  if (theme !== "light" && theme !== "dark") {
    theme = window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  }
  document.documentElement.setAttribute("data-theme", theme);

  var state = null;
  var timer = null;
  var busy = false;

  function $(id) {
    return document.getElementById(id);
  }
  function show(id, on) {
    $(id).hidden = !on;
  }
  function schedule(ms) {
    clearTimeout(timer);
    timer = setTimeout(load, ms);
  }

  function load() {
    fetch("/avado/api/status", { credentials: "same-origin", cache: "no-store" })
      .then(function (r) {
        if (!r.ok) throw new Error("status " + r.status);
        return r.json();
      })
      .then(function (s) {
        state = s;
        render();
        schedule(s.working ? 2000 : 10000);
      })
      .catch(function () {
        $("headline").textContent = "Home Assistant is starting";
        $("detail").textContent = "This page tries again by itself in a few seconds.";
        schedule(5000);
      });
  }

  function send(on) {
    if (busy) return;
    busy = true;
    $("problem").hidden = true;
    fetch("/avado/api/voice", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-Avado-Request": "1" },
      body: JSON.stringify({ on: on }),
    })
      .then(function (r) {
        return r.json().then(function (j) {
          if (!r.ok) throw new Error(j && j.error ? j.error : "That did not work. Please try again.");
          return j;
        });
      })
      .then(function (s) {
        state = s;
        state.working = true;
        render();
        schedule(1500);
      })
      .catch(function (e) {
        $("problem").textContent = e.message;
        $("problem").hidden = false;
        render();
      })
      .then(function () {
        busy = false;
      });
  }

  function step(n, done) {
    $("step-" + n).classList.toggle("done", !!done);
    if ($("tick-" + n)) $("tick-" + n).textContent = done ? "✓ Done" : "";
  }

  function render() {
    var s = state;
    var a = s.assistant || {};
    var ready = a.found === true && a.has_model;
    var headline, detail, level = "info";
    if (s.working) {
      headline = s.on ? "Switching voice off…" : "Switching voice on…";
      detail = s.step ? s.step + "." : "";
    } else if (s.on) {
      headline = "Voice uses your AVADO";
      detail = "Listening, answering and speaking all happen on this box. Nothing goes to the cloud.";
      level = "ok";
    } else if (a.found === false) {
      headline = "Get the Private Assistant first";
      detail = "It is the AI on your AVADO that answers you.";
    } else if (a.found && !a.has_model) {
      headline = "One step left in your Private Assistant";
      detail = "Open it and get its AI model, then come back here.";
    } else if (s.ha && s.ha.onboarded === false) {
      headline = "Finish Home Assistant's set-up first";
      detail = "Open Home Assistant and create your account, then come back here.";
    } else {
      headline = "Voice is off";
      detail = "Switch it on to talk to Home Assistant through your AVADO's own assistant.";
    }
    $("headline").textContent = headline;
    $("detail").textContent = detail;
    $("status").setAttribute("data-level", level);

    if (s.problem && !s.working) {
      $("problem").textContent = s.problem;
      $("problem").hidden = false;
    }

    step(1, ready);
    $("step-1-text").textContent = ready
      ? "Installed, with " + (a.model_name || "its model") + "."
      : a.found === false
      ? "Not on this box yet. Install it from the AVADO store."
      : a.found
      ? "Installed, but it has no AI model yet."
      : a.problem || "Checking…";
    show("get-assistant", a.found === false);
    show("open-assistant", !!a.found && !a.has_model);

    var sw = $("voice");
    sw.checked = s.working ? !s.on : !!s.on;
    sw.disabled = !!s.working || busy || (!s.on && !ready);
    step(2, s.on && !s.working);
    $("step-2-text").textContent = s.working
      ? "This takes up to a few minutes the first time."
      : s.on
      ? "On. Uses about 0.6 GB of memory."
      : ready
      ? "Speech to text, the answer and the spoken reply all run on this AVADO."
      : "Opens when step 1 is done.";
  }

  $("voice").addEventListener("change", function (e) {
    var on = e.target.checked;
    if (!on && !window.confirm("Switch off voice with your AVADO? Home Assistant goes back to the voice assistant you had before.")) {
      e.target.checked = true;
      return;
    }
    send(on);
  });

  load();
})();
