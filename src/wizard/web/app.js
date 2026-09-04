/* Hermes onboarding wizard — front-end. Talks to the local wizard server. */
(function () {
  "use strict";

  var TOKEN = new URLSearchParams(location.search).get("t") || "";
  var LS = "hermes_wizard_state_v1";

  // « The Foundation was established... to serve as a nucleus of the Second Empire. »
  try {
    console.log("%c R. Daneel Olivaw ", "background:#5b5bd6;color:#fff;padding:2px 8px;border-radius:6px;font-weight:700");
    console.log("%c« Psychohistory dealt not with man, but with man-masses. » — Hari Seldon, Foundation",
      "color:#8a63e8");
  } catch (e) {}

  var S = load() || {
    step: 0, provider: "claude-code",
    claude: "", node: "", hermes: "", hermes_config: "", python: "",
    install_dir: "", workspace: "", wsSuggested: "", tokenCleaned: "",
    repo: "Walt9819/olivaw",
    brainOk: false, hermesOk: false,
    identity: { agent_name: "", owner_name: "", purpose: "", business: "", approach: "" },
    usecases: [],
    token: "", owner_id: "", chat_id: "", owner_username: "", bot_username: "",
    maintainer_id: "", tavily_key: "", applied: false, applyResult: null,
    agent: { mode: "default", slug: "default" },
    smtp: { provider: "gmail", host: "", port: 587, user: "", password: "", from_addr: "", secure: "starttls", to_addr: "" }
  };
  if (!S.agent) S.agent = { mode: "default", slug: "default" };
  if (!S.smtp) S.smtp = { provider: "gmail", port: 587, secure: "starttls" };
  delete S.rescue_history;   // conversations now live on disk, not in localStorage
  // One-time code used to bind ownership to the exact account that sends it (anti-hijack).
  if (!S.owner_code) {
    var _cs = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789", _c = "OLIVAW-";
    for (var _i = 0; _i < 4; _i++) _c += _cs[Math.floor(Math.random() * _cs.length)];
    S.owner_code = _c;
  }
  var META = { providers: [], usecases: [], default_provider: "claude-code",
    recommended_provider: "claude-code", hermes: {},
    agents: { default: null, extra: [] }, smtp_providers: [], image_options: [], google_presets: {} };

  function save() { try { localStorage.setItem(LS, JSON.stringify(S)); } catch (e) {} }
  function load() { try { return JSON.parse(localStorage.getItem(LS)); } catch (e) { return null; } }

  function api(route, body) {
    return fetch("/api/" + route, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Wizard-Token": TOKEN },
      body: JSON.stringify(body || {})
    }).then(function (r) { return r.json(); });
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  function el(id) { return document.getElementById(id); }
  function toast(msg) {
    var t = el("toast"); t.textContent = msg; t.classList.add("show");
    clearTimeout(toast._t); toast._t = setTimeout(function () { t.classList.remove("show"); }, 2600);
  }

  // ── steps ──────────────────────────────────────────────────────────────
  var STEPS = [
    { id: "welcome", label: "Inicio", render: rWelcome, enter: eWelcome },
    { id: "provider", label: "El cerebro", render: rProvider, enter: eProvider },
    { id: "hermes", label: "Hermes", render: rHermes, enter: eHermes },
    { id: "agent", label: "Tu agente", render: rAgent, enter: eAgent },
    { id: "channel", label: "Tu canal", render: rChannel, enter: eChannel },
    { id: "finish", label: "Activar", render: rFinish, enter: eFinish },
    { id: "channels", label: "Más canales", render: rChannels, enter: eChannels }
  ];

  function stepDone(i) {
    switch (STEPS[i].id) {
      case "welcome": return S.step > 0;
      case "provider": return !!S.brainOk;
      case "hermes": return !!S.hermesOk;
      case "agent": return !!(S.identity.agent_name || "").trim();
      case "channel": return !!S.owner_id;
      case "finish": return !!S.applied;
      case "channels": return !!S.applied;
    }
    return false;
  }

  function renderStepper() {
    var ol = el("stepper"); ol.innerHTML = "";
    STEPS.forEach(function (s, i) {
      var li = document.createElement("li");
      var done = stepDone(i), active = i === S.step;
      li.className = "step" + (active ? " active" : "") + (done ? " done" : "") +
        (i <= maxReachable() ? " clickable" : "");
      li.innerHTML = '<span class="num">' + (done && !active ? "✓" : (i + 1)) +
        '</span><span class="lbl">' + esc(s.label) + "</span>";
      if (i <= maxReachable()) li.onclick = function () { go(i); };
      ol.appendChild(li);
    });
  }
  function maxReachable() { return Math.min(STEPS.length - 1, (S._max || 0)); }

  function render() {
    var con = S.view === "console";
    document.body.classList[con ? "add" : "remove"]("console");
    el("stepper").hidden = con;
    el("navTree").hidden = !con;
    if (el("closeBtn")) el("closeBtn").hidden = !con;
    if (el("toConsole")) el("toConsole").hidden = con || !hasAnyAgent();
    if (el("brandSub"))
      el("brandSub").textContent = con ? "Tus agentes" : "Asistente de configuración";
    document.title = con ? "Olivaw — tus agentes" : "Olivaw — configura tu agente";
    if (con) {
      renderTree();
      el("panel").innerHTML = renderConsole();
      var sec = curSec();
      if (sec.wire) sec.wire();
      // Every panel below is el()-guarded, so this wires whatever this page happens to
      // show and skips the rest.
      eChannels();
      wireSecCards();
      save();
      return;
    }
    renderStepper();
    el("panel").innerHTML = STEPS[S.step].render();
    el("navProgress").textContent = "Paso " + (S.step + 1) + " de " + STEPS.length;
    el("navBack").style.visibility = S.step === 0 ? "hidden" : "visible";
    var next = el("navNext");
    next.textContent = S.step === STEPS.length - 1
      ? (hasAnyAgent() ? "Ir a mis agentes →" : "Cerrar asistente") : "Continuar →";
    STEPS[S.step].enter();
    save();
  }

  function go(i) {
    if (i < 0 || i > STEPS.length - 1) return;
    S.step = i; S._max = Math.max(S._max || 0, i);
    document.querySelector(".panel-wrap").scrollTop = 0;
    render();
  }

  el("navBack").onclick = function () { go(S.step - 1); };
  el("navNext").onclick = function () {
    if (S.step === STEPS.length - 1) {
      // Setup is over. Hand over to the console rather than to a dead tab: from here on
      // every question is "which agent, and what about it" - which is what it answers.
      if (hasAnyAgent()) {
        goSec("home", (S.applyResult && S.applyResult.agent && S.applyResult.agent.slug) || S.sel);
        return;
      }
      closeWizard(); return;
    }
    go(S.step + 1);
  };
  if (el("closeBtn")) el("closeBtn").onclick = closeWizard;
  if (el("toConsole")) el("toConsole").onclick = function () { goSec(S.sec || "home"); };

  function closeWizard() {
    api("shutdown", {});
    document.body.innerHTML =
      '<div style="display:grid;place-items:center;height:100vh;text-align:center;font-family:sans-serif">' +
      '<div><div style="font-size:52px">👋</div><h2>Puedes cerrar esta pestaña.</h2>' +
      '<p style="color:#888">Tu agente ya está configurándose en segundo plano.</p></div></div>';
  }

  // -- the console: agents first ---------------------------------------------
  // The wizard is a straight line - brain, Hermes, identity, channel, activate - and that
  // is exactly right the first time. It is wrong every time after, because almost
  // everything on its last page belongs to ONE agent and the sidebar never said which
  // one. So once agents exist, the sidebar stops listing installation steps and starts
  // listing agents: you pick one, its own settings appear underneath it, and the few
  // things that really are shared by the whole machine are grouped apart and labelled.
  function allAgents() {
    var ag = META.agents || {};
    return (ag.default ? [ag.default] : []).concat(ag.extra || []);
  }
  function agentBySlug(slug) {
    var all = allAgents(), i;
    for (i = 0; i < all.length; i++) if (all[i].slug === slug) return all[i];
    return null;
  }
  // The selected agent. Everything per-agent on screen resolves through here, so a wrong
  // answer would quietly configure somebody else's agent.
  function curAgent() { return agentBySlug(S.sel || "default") || allAgents()[0] || null; }
  function isAgentSec(sec) { return sec.scope === "agent"; }
  function curSec() {
    for (var i = 0; i < CONSOLE.length; i++) if (CONSOLE[i].id === S.sec) return CONSOLE[i];
    return CONSOLE[0];
  }
  function agentLabel(a) { return (a && (a.name || a.slug)) || "Tu agente"; }

  // A section is one page in the console, so its accordion is a click for nothing. Only
  // the outermost <details> goes: the fine-print folds inside it are still worth folding.
  function unfold(html) {
    var i = html.indexOf("<details");
    if (i < 0) return html;
    var a = html.indexOf("<summary>", i), b = html.indexOf("</summary>", a);
    if (a < 0 || b < 0) return html;
    var body = html.slice(b + "</summary>".length);
    var j = body.lastIndexOf("</details>");
    if (j >= 0) body = body.slice(0, j) + body.slice(j + "</details>".length);
    return html.slice(0, i) + body;
  }

  var CONSOLE = [
    { id: "home", scope: "agent", icon: "🏠", label: "Resumen",
      blurb: "Cómo está y qué puedes hacer con él",
      render: secAgentHome, wire: eAgentHome },
    { id: "canales", scope: "agent", icon: "💬", label: "Por dónde le hablan",
      blurb: "WhatsApp, correo, Slack, webhooks",
      title: "Por dónde puede hablarle la gente",
      lead: "Telegram ya está listo. Aquí sumas los demás — y cada uno queda guardado en " +
            "<b>este</b> agente, no en los otros.",
      render: function () {
        return secWhatsApp() + secGoogle() + secSlack() + secWebhook() + secSmtp();
      } },
    { id: "gasto", scope: "agent", icon: "⏱️", label: "Conversación y gasto",
      blurb: "Cada cuánto empieza de cero",
      title: "Cuánto dura cada conversación",
      render: function () { return unfold(secPolicy()); } },
    { id: "recuerdos", scope: "agent", icon: "🗂️", label: "Recordar conversaciones",
      blurb: "Que retome un hilo anterior",
      title: "Que recuerde y retome lo hablado",
      render: function () { return unfold(secHistory()); } },
    { id: "habilidades", scope: "agent", icon: "🧰", label: "Lo que sabe hacer",
      blurb: "Navegador, imágenes y conectores",
      title: "Lo que este agente sabe hacer",
      lead: "Navegar por internet, generar imágenes y conectarse a herramientas externas.",
      render: function () { return secBrowser() + secImages() + secMcp(); } },

    { id: "entre-agentes", scope: "team", icon: "🤝", label: "Que se hablen entre ellos",
      title: "Que tus agentes se hablen entre ellos",
      render: function () { return unfold(secIntercom()); } },
    { id: "rutinas", scope: "team", icon: "🌙", label: "Rutinas automáticas",
      title: "Rutinas automáticas",
      lead: "Igual que una persona: de madrugada repasa el día y guarda lo que importa; " +
            "los domingos revisa su semana y se corrige. " +
            "<span class=\"muted\">Se programan para el agente principal.</span>",
      render: function () { return secRoutines(true); } },
    { id: "propuestas", scope: "team", icon: "💡", label: "Lo que propone",
      title: "Lo que Olivaw propone",
      lead: "Cuando ve algo que podría hacer por ti, lo propone aquí y espera. No construye " +
            "nada sin tu sí — y lo que descartes no vuelve a proponerlo.",
      render: function () { return secProposals(true); } },
    { id: "memoria", scope: "team", icon: "📓", label: "Leer su memoria",
      title: "Su memoria, en Obsidian",
      lead: "Lo que tu agente recuerda son notas de texto. En Obsidian puedes leerlas, " +
            "corregirlas y ver cómo se conectan.",
      render: function () { return secVault(true); } },
    { id: "cerebro", scope: "team", icon: "⚙️", label: "El cerebro y Hermes",
      title: "El cerebro y el motor",
      lead: "Quién razona por tus agentes y qué ejecuta sus acciones. Es <b>compartido</b>: " +
            "lo que cambies aquí les afecta a todos.",
      render: secBrain, wire: eBrain },
    { id: "version", scope: "team", icon: "🔄", label: "Versión y actualizaciones",
      title: "Versión y actualizaciones",
      lead: "Olivaw se actualiza solo cuando nadie lo está usando. Aquí ves en qué " +
            "versión estás y puedes forzarlo si no quieres esperar.",
      render: secUpdate, wire: eUpdate }
  ];

  function goSec(id, slug) {
    if (slug) S.sel = slug;
    S.sec = id; S.view = "console"; save();
    var w = document.querySelector(".panel-wrap"); if (w) w.scrollTop = 0;
    render();
  }
  // Leaving the console for the wizard: creating an agent, or reconfiguring one.
  function enterSetup(step) {
    S.view = "setup"; S.step = step; S._max = Math.max(S._max || 0, step);
    save(); render();
  }

  function dotClass(a) {
    if (a.missing_profile) return "err";
    if (a.gateway_running === true) return "ok";
    if (a.gateway_running === false) return "off";
    return "unk";
  }

  function renderTree() {
    var t = el("navTree");
    if (!t) return;
    var cur = curAgent(), sec = curSec(), html = "";
    html += '<div class="tree-h">Tus agentes</div>';
    allAgents().forEach(function (a) {
      var on = !!(cur && a.slug === cur.slug);
      html += '<div class="tnode' + (on ? " on" : "") + '">' +
        '<div class="tnode-top" data-agent="' + esc(a.slug) + '" tabindex="0">' +
        '<span class="tdot ' + dotClass(a) + '"></span>' +
        '<span class="tname">' + esc(agentLabel(a)) + '</span>' +
        (a.is_default ? '<span class="tflag">principal</span>' : '') + '</div>';
      if (on) {
        html += '<div class="tkids">' + CONSOLE.filter(isAgentSec).map(function (x) {
          return '<div class="tkid' + (x.id === sec.id ? " active" : "") +
            '" data-sec="' + x.id + '" tabindex="0"><span class="tico">' + x.icon +
            '</span>' + esc(x.label) + '</div>';
        }).join("") + '</div>';
      }
      html += '</div>';
    });
    html += '<div class="tnew" id="treeNew" tabindex="0">➕ Crear un agente nuevo</div>';
    html += '<div class="tree-h">De todo el equipo</div>';
    html += CONSOLE.filter(function (x) { return !isAgentSec(x); }).map(function (x) {
      return '<div class="tkid flat' + (x.id === sec.id ? " active" : "") +
        '" data-sec="' + x.id + '" tabindex="0"><span class="tico">' + x.icon + '</span>' +
        esc(x.label) + '</div>';
    }).join("");
    t.innerHTML = html;

    function act(node, fn) {
      node.onclick = fn;
      node.onkeydown = function (e) {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fn(); }
      };
    }
    Array.prototype.forEach.call(t.querySelectorAll("[data-agent]"), function (n) {
      act(n, function () {
        // Keep the section when switching agents: comparing the same panel across two
        // agents is one click, not four.
        goSec(isAgentSec(curSec()) ? curSec().id : "home", n.getAttribute("data-agent"));
      });
    });
    Array.prototype.forEach.call(t.querySelectorAll("[data-sec]"), function (n) {
      act(n, function () { goSec(n.getAttribute("data-sec")); });
    });
    var nb = el("treeNew");
    if (nb) act(nb, function () {
      resetAgentFields();
      S.agent = { mode: "new", isolate_claude: false };
      enterSetup(1);
    });
  }

  // -- the agent's own front page ---------------------------------------------
  function secAgentHome() {
    var a = curAgent();
    if (!a) {
      return '<h1>Todavía no hay ningún agente</h1><p class="lead">Crea el primero desde ' +
        '«Crear un agente nuevo».</p>';
    }
    var owner = (a.owners && a.owners[0]) ? (a.owners[0].name || a.owners[0].user_id) : "";
    var warn = "";
    if (a.missing_profile) {
      warn = '<div class="callout warn"><b>Le falta su perfil.</b> Su configuración de Hermes ' +
        'ya no está en este equipo, así que no puede arrancar. Pulsa «Restaurar» para volver ' +
        'a escribirla.</div>';
    } else if (a.gateway_running === false) {
      warn = '<div class="callout warn"><b>Está pausado.</b> No le llega ningún mensaje, por ' +
        'ningún canal, hasta que lo reanudes.</div>';
    } else if (a.gateway_running === true && a.bridge_up === false) {
      warn = '<div class="callout warn"><b>Escucha, pero no puede pensar.</b> Está atendiendo ' +
        'mensajes y su puente con el cerebro no responde en el puerto ' + esc(a.port) + '. ' +
        'Si no se arregla solo, usa 🆘 abajo a la izquierda.</div>';
    }
    var facts = "";
    facts += sumline(a.gateway_running === true ? "🟢" : (a.gateway_running === false ? "⏸️" : "❔"),
      "Estado", a.gateway_running === true ? "Encendido, atendiendo mensajes"
        : a.gateway_running === false ? "Pausado" : "No pude comprobarlo");
    facts += sumline("🧠", "Su cerebro",
      a.bridge_up ? "Conectado (puente en el puerto " + a.port + ")"
                  : "El puente del puerto " + a.port + " no responde");
    facts += sumline("👤", "Quién puede darle órdenes", owner || "Nadie todavía");
    if (a.workspace) facts += sumline("📁", "Su carpeta de trabajo", a.workspace);
    facts += sumline("🧩", "Su perfil de Hermes", a.profile || a.slug);

    var actions = '<button class="btn btn-soft btn-sm" data-act="reconfigure" data-slug="' +
      esc(a.slug) + '">Reconfigurar</button>';
    if (!a.is_default) {
      if (a.missing_profile)
        actions += ' <button class="btn btn-soft btn-sm" data-act="restore" data-slug="' +
          esc(a.slug) + '">Restaurar</button>';
      actions += (a.gateway_running
        ? ' <button class="btn btn-soft btn-sm" data-act="stop" data-slug="' + esc(a.slug) + '">Pausar</button>'
        : ' <button class="btn btn-soft btn-sm" data-act="start" data-slug="' + esc(a.slug) + '">Reanudar</button>');
      actions += ' <button class="btn btn-soft btn-sm" data-act="reset" data-slug="' + esc(a.slug) +
        '" style="color:var(--err)">Eliminar</button>';
    }
    var grid = CONSOLE.filter(function (x) { return isAgentSec(x) && x.id !== "home"; })
      .map(function (x) {
        return '<div class="seccard" data-sec="' + x.id + '" tabindex="0">' +
          '<span class="secico">' + x.icon + '</span>' +
          '<b>' + esc(x.label) + '</b>' +
          '<span class="muted small">' + esc(x.blurb || "") + '</span></div>';
      }).join("");

    return '' +
      '<div class="eyebrow">Tus agentes</div>' +
      '<h1>' + esc(agentLabel(a)) +
      (a.is_default ? ' <span class="badge">principal</span>' : '') + '</h1>' +
      warn +
      '<div class="card pad"><ul class="filelist">' + facts + '</ul>' +
      '<div class="row" style="margin-top:12px">' + actions + '</div>' +
      '<span class="pill" data-pill="' + esc(a.slug) + '" style="display:none;margin-top:8px"></span></div>' +
      '<h2>¿Qué quieres ajustar de ' + esc(agentLabel(a)) + '?</h2>' +
      '<div class="secgrid">' + grid + '</div>';
  }
  function eAgentHome() { wireAgentActions(); }

  // Shared by the console's front page and the wizard's agent list, so pausing an agent
  // does the same thing (and says the same thing) in both.
  function wireAgentActions() {
    Array.prototype.forEach.call(document.querySelectorAll("#panel [data-act]"), function (b) {
      b.onclick = function () {
        var act = b.getAttribute("data-act"), slug = b.getAttribute("data-slug");
        if (act === "reconfigure") {
          var a = agentBySlug(slug) || {};
          S.agent = { mode: a.is_default ? "default" : "reconfigure", slug: slug,
                      port: a.port, workspace: a.workspace, name: a.name };
          if (!a.is_default) { resetAgentFields(); }  // reconfiguring an extra: fresh inputs
          enterSetup(1); return;
        }
        if (act === "reset" && !confirm("¿Eliminar este agente por completo? Se borra su perfil, memoria y datos. No se puede deshacer.")) return;
        var pill = document.querySelector('[data-pill="' + slug + '"]');
        if (pill) { pill.style.display = "inline-flex"; }
        runTest(b, pill, function () { return api("agent/action", { slug: slug, action: act }); })
          .then(function () { setTimeout(refreshAgents, 400); });
      };
    });
  }
  function wireSecCards() {
    Array.prototype.forEach.call(document.querySelectorAll("#panel [data-sec]"), function (n) {
      var jump = function () { goSec(n.getAttribute("data-sec")); };
      n.onclick = jump;
      n.onkeydown = function (e) {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); jump(); }
      };
    });
  }

  // -- the shared brain and engine, read-only unless you ask to redo it -------
  function secBrain() {
    return '' +
      '<div id="brainBox" class="card pad"><span class="muted small">Comprobando…</span></div>' +
      '<div class="row" style="margin-top:12px">' +
      '<button class="btn btn-soft btn-sm" id="brainRecheck">Volver a comprobar</button>' +
      '<button class="btn btn-soft btn-sm" id="brainRedo">Rehacer esta parte</button></div>' +
      '<p class="muted small" style="margin-top:10px">«Rehacer esta parte» te lleva a los ' +
      'pasos del asistente para el cerebro y Hermes. No borra ni cambia ningún agente.</p>';
  }
  function eBrain() {
    function paint(html) { var b = el("brainBox"); if (b) b.innerHTML = html; }
    function load() {
      paint('<span class="muted small">Comprobando…</span>');
      Promise.all([api("provider/check", brainBody()), api("check", { what: "hermes" })])
        .then(function (r) {
          var br = r[0] || {}, hm = r[1] || {};
          paint('<ul class="filelist">' +
            sumline(br.ok ? "✅" : "⚠️", cliLabel(),
                    br.ok ? (br.path || br.detail || "listo") : (br.detail || "no lo encontré")) +
            sumline(hm.ok ? "✅" : "⚠️", "Hermes",
                    hm.ok ? (hm.path || hm.detail || "listo") : (hm.detail || "no lo encontré")) +
            '</ul>');
        });
    }
    if (el("brainRecheck")) el("brainRecheck").onclick = load;
    if (el("brainRedo")) el("brainRedo").onclick = function () { enterSetup(1); };
    load();
  }

  // ── version + updates ─────────────────────────────────────────────────────
  // Updating is the supervisor's job (it owns the bridges), so this panel only reads its
  // state and asks. The one state worth designing for is the bad one: a supervisor that
  // is not running means nothing ever checks, and no amount of pressing helps until it is.
  function secUpdate() {
    return '' +
      '<div id="updBox" class="card pad"><span class="muted small">Comprobando…</span></div>' +
      '<div class="row" style="margin-top:12px">' +
      '<button class="btn btn-primary btn-sm" id="updNow">Actualizar ahora</button>' +
      '<button class="btn btn-soft btn-sm" id="updCheck">Buscar actualizaciones</button></div>' +
      chLine("updPill") +
      '<div id="updLog" class="small muted" style="margin-top:10px"></div>';
  }

  function eUpdate() {
    var pill = el("updPill");
    function paint(st) {
      var box = el("updBox");
      if (!box) return;
      if (!st || !st.ok) { box.innerHTML = "No pude comprobarlo."; return; }
      var rows = [];
      rows.push(sumline("📦", "Versión instalada", st.current || "?"));
      if (st.latest)
        rows.push(sumline(st.available ? "🔄" : "✅",
                          st.available ? "Hay una más nueva" : "Es la última",
                          st.latest));
      else if (st.error)
        rows.push(sumline("⚠️", "No pude preguntarle a GitHub", esc(st.error)));
      // The honest headline: without a supervisor nothing checks and nothing installs.
      rows.push(st.supervisor_running
        ? sumline("✅", "Servicio en segundo plano", "encendido")
        : sumline("⚠️", "Servicio en segundo plano",
                  st.supervisor_known
                    ? "apagado — sin él no se actualiza solo"
                    : "no lo sé (versión anterior a este aviso)"));
      rows.push(st.auto_update
        ? sumline("🌙", "Se actualiza solo",
                  "cuando nadie lo usa" + (st.rest_text ? ", o " + esc(st.rest_text) : ""))
        : sumline("⏸️", "Se actualiza solo", "desactivado"));
      box.innerHTML = '<ul class="filelist">' + rows.join("") + '</ul>' +
        (st.available && st.changelog
          ? '<div class="hr"></div><b class="small">Qué trae la ' + esc(st.latest) +
            '</b><div class="small muted" style="margin-top:6px;white-space:pre-wrap">' +
            esc(st.changelog) + '</div>'
          : "");
      var lg = el("updLog");
      if (lg) {
        var r = st.result;
        lg.innerHTML = st.pending
          ? "⏳ Pedido enviado; el servicio lo aplica en unos segundos."
          : (r ? (r.ok ? "✓ " : "✕ ") + esc(r.detail || "") : "");
      }
      var b = el("updNow");
      if (b) b.textContent = st.available ? "Actualizar ahora" : "Reinstalar esta versión";
      if (b) b.disabled = !st.available && !st.error;
    }
    function load(force) {
      return api(force ? "update/check" : "update/status", {}).then(function (st) {
        paint(st); paintVersion(st); return st;
      });
    }
    if (el("updCheck")) el("updCheck").onclick = function () {
      pill.style.display = "inline-flex";
      runTest(this, pill, function () {
        return load(true).then(function (st) {
          return { ok: true, detail: st && st.available
            ? "Hay una nueva: " + st.latest : "Ya estás al día (" + (st && st.current) + ")" };
        });
      }, "Preguntando a GitHub…");
    };
    if (el("updNow")) el("updNow").onclick = function () {
      pill.style.display = "inline-flex";
      runTest(this, pill, function () { return api("update/apply", {}); }, "Pidiéndolo…")
        .then(function () {
          // The supervisor works on its own clock (~15s a loop), so watch for the result
          // rather than reporting success the moment the request file is written.
          var tries = 0;
          (function poll() {
            load().then(function (st) {
              tries++;
              if (st && (st.pending || (!st.result && tries < 12))) setTimeout(poll, 5000);
            });
          })();
        });
    };
    if (el("updBox")) load(false);
  }

  // The sidebar's version line. Painted from META at boot and refreshed by any update
  // check, so it is right in the wizard too — where there is no console section at all.
  function paintVersion(st) {
    var num = el("verNum"), badge = el("verBadge");
    var cur = (st && st.current) || META.version || "";
    if (num) num.textContent = cur ? "Olivaw v" + cur : "";
    if (!badge) return;
    var avail = !!(st && st.available);
    badge.hidden = !avail;
    if (avail) {
      badge.textContent = "Actualizar a " + st.latest;
      badge.title = "Hay una versión nueva (" + st.latest + ")";
      badge.onclick = function () { goSec("version"); };
    }
  }

  function renderConsole() {
    var sec = curSec(), a = curAgent();
    if (sec.id === "home") return sec.render();
    var head = isAgentSec(sec)
      ? '<div class="eyebrow">' + esc(agentLabel(a)) + ' <span class="muted">· ' +
        esc(sec.label) + '</span></div>'
      : '<div class="eyebrow">De todo el equipo <span class="muted">· compartido</span></div>';
    return head + (sec.title ? '<h1>' + sec.title + '</h1>' : '') +
      (sec.lead ? '<p class="lead">' + sec.lead + '</p>' : '') + sec.render();
  }

  // ── shared test-button helper ────────────────────────────────────────────
  function runTest(btn, pill, fn, loading) {
    btn.disabled = true;
    pill.className = "pill load";
    pill.innerHTML = '<span class="spinner"></span>' + (loading || "Probando…");
    return Promise.resolve().then(fn).then(function (r) {
      var ok = r && r.ok;
      pill.className = "pill " + (ok ? "ok" : "err");
      pill.textContent = (ok ? "✓ " : "✕ ") + ((r && r.detail) || (ok ? "Listo" : "Falló"));
      return r || { ok: false };
    }).catch(function (e) {
      pill.className = "pill err"; pill.textContent = "✕ " + e; return { ok: false };
    }).finally(function () { btn.disabled = false; });
  }

  // ── step 0: welcome ───────────────────────────────────────────────────────
  function hasAnyAgent() {
    var a = META.agents || {};
    return !!(a.default || (a.extra && a.extra.length));
  }

  function rWelcome() {
    // If agents already exist on this machine, show the manager. Otherwise, the intro.
    if (hasAnyAgent()) return rAgentManager();
    return '' +
      '<div class="eyebrow">Bienvenido</div>' +
      '<h1>Vamos a darle vida a tu agente</h1>' +
      '<p class="lead">En unos minutos tendrás un asistente propio que piensa con IA, ' +
      'vive en tu computadora y te responde por Telegram. No necesitas saber programar: ' +
      'te guío paso a paso y probamos cada parte para que nada quede a medias.</p>' +
      '<div class="card pad">' +
      '<div class="row" style="align-items:flex-start;gap:18px">' +
      guideItem("🧠", "Elegimos su cerebro", "Claude Code será quien razona por tu agente.") +
      '</div><div class="hr"></div>' +
      '<div class="row" style="align-items:flex-start;gap:18px">' +
      guideItem("🔌", "Conectamos Hermes", "El motor que ejecuta las acciones.") +
      '</div><div class="hr"></div>' +
      '<div class="row" style="align-items:flex-start;gap:18px">' +
      guideItem("✨", "Le damos personalidad", "Nombre, propósito y para qué lo vas a usar.") +
      '</div><div class="hr"></div>' +
      '<div class="row" style="align-items:flex-start;gap:18px">' +
      guideItem("💬", "Lo conectamos a ti", "Tu Telegram será el único que pueda darle órdenes.") +
      '</div></div>' +
      '<p class="muted small">Todo ocurre localmente en tu equipo. Nada se envía a ningún servidor nuestro.</p>';
  }

  function agentCard(a) {
    var pills = [];
    if (a.gateway_running === true) pills.push('<span class="pill ok" style="margin:0">● activo</span>');
    else if (a.gateway_running === false) pills.push('<span class="pill warn" style="margin:0">● pausado</span>');
    if (a.bridge_up) pills.push('<span class="pill ok" style="margin:0">puente ✓</span>');
    if (a.missing_profile) pills.push('<span class="pill err" style="margin:0">perfil ausente</span>');
    var owner = (a.owners && a.owners[0]) ? (a.owners[0].name || a.owners[0].user_id) : null;
    var meta = [];
    if (a.port) meta.push("puerto " + esc(a.port));
    if (owner) meta.push("dueño " + esc(owner));
    if (a.profile && !a.is_default) meta.push("perfil " + esc(a.profile));
    var actions = '<button class="btn btn-soft btn-sm" data-act="reconfigure" data-slug="' + esc(a.slug) + '">Reconfigurar</button>';
    if (!a.is_default) {
      if (a.missing_profile)
        actions += ' <button class="btn btn-soft btn-sm" data-act="restore" data-slug="' + esc(a.slug) + '">Restaurar</button>';
      actions += (a.gateway_running
        ? ' <button class="btn btn-soft btn-sm" data-act="stop" data-slug="' + esc(a.slug) + '">Pausar</button>'
        : ' <button class="btn btn-soft btn-sm" data-act="start" data-slug="' + esc(a.slug) + '">Reanudar</button>');
      actions += ' <button class="btn btn-soft btn-sm" data-act="reset" data-slug="' + esc(a.slug) + '" style="color:var(--err)">Eliminar</button>';
    }
    return '<div class="card"><div class="row" style="justify-content:space-between">' +
      '<div class="grow"><b>' + esc(a.name || a.slug) + '</b> ' +
      (a.is_default ? '<span class="badge">principal</span>' : '') +
      '<div class="muted small">' + esc(meta.join(" · ")) + '</div></div>' +
      '<div class="row" style="gap:6px">' + pills.join("") + '</div></div>' +
      '<div class="row" style="margin-top:10px">' + actions + '</div>' +
      '<span class="pill" data-pill="' + esc(a.slug) + '" style="display:none;margin-top:8px"></span></div>';
  }

  function rAgentManager() {
    var ag = META.agents || {};
    var cards = "";
    if (ag.default) cards += agentCard(ag.default);
    (ag.extra || []).forEach(function (a) { cards += agentCard(a); });
    return '' +
      '<div class="eyebrow">Tus agentes</div>' +
      '<h1>Agentes en esta computadora</h1>' +
      '<p class="lead">Puedes reconfigurar uno, pausarlo, o crear <b>uno nuevo totalmente ' +
      'aislado</b> (su propio perfil, puerto, memoria y bot). Cada agente es independiente.</p>' +
      cards +
      '<div class="card pad" style="text-align:center;border-style:dashed">' +
      '<b>➕ Crear un agente nuevo</b>' +
      '<p class="muted small" style="margin:6px 0 12px">Aislado del resto: su propia identidad, ' +
      'canal y datos. Ideal para otra persona, otro negocio u otro propósito.</p>' +
      '<label class="row" style="justify-content:center;gap:8px;font-size:13px;cursor:pointer">' +
      '<input type="checkbox" id="isoClaude"> <span class="muted">Usar una cuenta de Claude distinta para este agente</span></label>' +
      '<div style="margin-top:12px"><button class="btn btn-primary" id="btnNewAgent">Crear agente nuevo</button></div>' +
      '</div>';
  }

  function resetAgentFields() {
    S.brainOk = false; S.hermesOk = true;   // shared prereqs already satisfied when agents exist
    S.identity = { agent_name: "", owner_name: "", purpose: "", business: "", approach: "" };
    S.usecases = [];
    S.token = ""; S.owner_id = ""; S.chat_id = ""; S.owner_username = ""; S.bot_username = "";
    S.applied = false; S.applyResult = null;
  }

  function refreshAgents() {
    return api("agents/list", {}).then(function (r) {
      if (r && r.ok) { META.agents = { default: r.default, extra: r.extra || [] }; render(); }
    });
  }

  function eWelcome() {
    if (!hasAnyAgent()) return;
    var newBtn = el("btnNewAgent");
    if (newBtn) newBtn.onclick = function () {
      resetAgentFields();
      S.agent = { mode: "new", isolate_claude: !!(el("isoClaude") && el("isoClaude").checked) };
      enterSetup(1);
    };
    wireAgentActions();
  }
  function guideItem(ic, t, d) {
    return '<div style="font-size:26px">' + ic + '</div><div class="grow"><b>' + esc(t) +
      '</b><div class="muted small">' + esc(d) + "</div></div>";
  }

  // ── step 1: provider ──────────────────────────────────────────────────────
  function rProvider() {
    var opts = META.providers.map(function (p) {
      var sel = p.id === S.provider ? " sel" : "";
      var dis = p.status !== "ready" ? " disabled" : "";
      // Two brains are ready now, so "recommended" has to mean the default one specifically -
      // otherwise both cards claim it and the badge stops meaning anything.
      var badge = p.status !== "ready"
        ? '<span class="badge soon">Próximamente</span>'
        : (p.id === META.recommended_provider
            ? '<span class="badge">Recomendado</span>'
            : '<span class="badge alt">Disponible</span>');
      // Say which one this machine is already set up for, so switching is a deliberate act.
      if (p.status === "ready" && p.id === META.default_provider &&
          p.id !== META.recommended_provider) {
        badge += ' <span class="badge alt">En uso</span>';
      }
      return '<div class="opt' + sel + dis + '" data-pid="' + p.id + '">' +
        '<div class="ic">' + esc(p.label[0]) + '</div>' +
        '<div class="grow"><div class="row" style="justify-content:space-between">' +
        '<span class="ttl">' + esc(p.label) + '</span>' + badge + '</div>' +
        '<div class="muted small">' + esc(p.tagline) + '</div></div></div>';
    }).join("");

    var p = provider();
    var steps = (p.steps || []).map(function (g) {
      var link = g.link ? ' <a href="' + esc(g.link) + '" target="_blank" rel="noopener noreferrer">abrir</a>' : "";
      return '<div class="g"><div class="gn"></div><div class="gt"><b>' + esc(g.title) +
        "</b> — " + esc(g.body) + link + "</div></div>";
    }).join("");

    return '' +
      '<div class="eyebrow">Paso 1 · El cerebro</div>' +
      '<h1>Elige quién piensa por tu agente</h1>' +
      '<p class="lead">El cerebro es el modelo de IA que razona. Puedes usar <b>Claude Code</b> ' +
      '(el recomendado) o <b>Codex</b> de OpenAI: en ambos casos con tu suscripción, sin claves ' +
      'de API. Todo lo demás de Olivaw funciona igual con cualquiera de los dos.</p>' +
      '<div class="opt-grid" id="provOpts">' + opts + '</div>' +
      '<div class="callout"><b>Necesitas una cuenta de pago.</b> ' + esc(p.paid_note) +
      ' &nbsp;<a href="' + esc(p.download_url) + '" target="_blank" rel="noopener noreferrer">Descargar</a> · ' +
      '<a href="' + esc(p.help_url) + '" target="_blank" rel="noopener noreferrer">Ayuda oficial</a></div>' +
      '<div class="guide">' + steps + '</div>' +
      '<div class="card pad">' +
      '<b>1 · Conecta tu cuenta de ' + esc(cliLabel()) + '</b>' +
      '<p class="muted small">Un clic abre una ventana para iniciar sesión con tu cuenta. ' +
      'Solo se hace una vez. ' + esc(p.login_hint) + '</p>' +
      '<div class="row">' +
      '<button class="btn btn-primary" id="btnLogin">Iniciar sesión en ' + esc(cliLabel()) +
      '</button>' +
      '<button class="btn btn-soft btn-sm" id="btnLoginStatus">Ya inicié sesión</button>' +
      '<span id="pillLogin" class="pill" style="display:none"></span></div>' +
      '<details style="margin-top:12px"><summary>Opciones avanzadas</summary>' +
      '<label class="field" style="margin-top:8px"><span class="lab">Ruta de ' +
      esc(cliLabel()) + ' <span class="hint">(la detectamos sola)</span></span>' +
      '<input type="text" id="claudePath" placeholder="' + esc(cliKey()) + '" value="' +
      esc(cliPath()) + '"></label>' +
      '<div class="row">' +
      '<button class="btn btn-soft btn-sm" id="btnNode">Verificar Node.js</button>' +
      '<button class="btn btn-soft btn-sm" id="btnInstall">Instalar ' + esc(cliLabel()) +
      '</button>' +
      '<button class="btn btn-soft btn-sm" id="btnCheckClaude">Verificar ' + esc(cliLabel()) +
      '</button>' +
      '</div><div id="pillProv" class="pill load" style="display:none"></div></details>' +
      '</div>' +
      '<div class="card pad">' +
      '<b>2 · La prueba clave</b><p class="muted small">Enviamos un mensaje real a tu agente. ' +
      'Si responde, todo lo demás funcionará.</p>' +
      '<div class="row"><button class="btn btn-primary" id="btnBrain">Probar el cerebro</button>' +
      '<span id="pillBrain" class="pill" style="display:none"></span></div></div>';
  }
  function provider() {
    return META.providers.filter(function (p) { return p.id === S.provider; })[0] ||
      META.providers[0] || { steps: [], paid_note: "", download_url: "#", help_url: "#",
                             login_hint: "", cli_key: "claude", cli_label: "Claude Code" };
  }
  // The CLI path lives under the provider's own key, so switching brain does not lose the other
  // one's path, and the back end can look up whichever it needs.
  function cliKey() { return provider().cli_key || "claude"; }
  function cliLabel() { return provider().cli_label || provider().label || "el cerebro"; }
  function cliPath() { return S[cliKey()] || ""; }
  function setCliPath(v) { S[cliKey()] = v; save(); }
  function brainBody(extra) {
    var b = { provider: S.provider, claude: S.claude || "", codex: S.codex || "" };
    for (var k in (extra || {})) b[k] = extra[k];
    return b;
  }
  function eProvider() {
    Array.prototype.forEach.call(document.querySelectorAll("#provOpts .opt"), function (o) {
      o.onclick = function () {
        var pid = o.getAttribute("data-pid");
        var p = META.providers.filter(function (x) { return x.id === pid; })[0];
        if (!p || p.status !== "ready") { toast("Ese proveedor aún no está disponible."); return; }
        if (pid !== S.provider) S.brainOk = false;   // a different brain has not been tested
        S.provider = pid; S.providerPicked = true; save(); render(); renderStepper();
      };
    });
    var cp = el("claudePath");
    if (cp) cp.oninput = function () { setCliPath(cp.value.trim()); };
    // One-click Claude sign-in (opens the OAuth flow in a terminal).
    var pillLogin = el("pillLogin");
    if (el("btnLogin")) el("btnLogin").onclick = function () {
      pillLogin.style.display = "inline-flex";
      runTest(this, pillLogin, function () { return api("provider/login", brainBody()); }, "Abriendo…")
        .then(function (r) { if (r && r.ok) toast("Completa el inicio de sesión en la ventana, luego pulsa «Ya inicié sesión»."); });
    };
    if (el("btnLoginStatus")) el("btnLoginStatus").onclick = function () {
      pillLogin.style.display = "inline-flex";
      runTest(this, pillLogin, function () { return api("provider/login-status", brainBody()); }, "Comprobando…");
    };
    var pill = el("pillProv");
    if (el("btnNode")) el("btnNode").onclick = function () {
      pill.style.display = "inline-flex";
      runTest(this, pill, function () { return api("check", { what: "node" }); });
    };
    if (el("btnInstall")) el("btnInstall").onclick = function () {
      pill.style.display = "inline-flex";
      runTest(this, pill, function () { return api("provider/install", brainBody()); }, "Instalando…");
    };
    if (el("btnCheckClaude")) el("btnCheckClaude").onclick = function () {
      pill.style.display = "inline-flex";
      runTest(this, pill, function () {
        return api("provider/check", brainBody());
      }).then(function (r) {
        if (r.ok && r.path) {
          setCliPath(r.path);
          if (el("claudePath")) el("claudePath").value = r.path;
        }
      });
    };
    var pb = el("pillBrain");
    el("btnBrain").onclick = function () {
      pb.style.display = "inline-flex";
      runTest(this, pb, function () {
        return api("test-brain", brainBody({ workspace: S.workspace || S.wsSuggested }));
      }, "Despertando al cerebro… (puede tardar)").then(function (r) {
        S.brainOk = !!(r && r.ok); save(); renderStepper();
        if (S.brainOk) toast("¡El cerebro está vivo! 🧠");
      });
    };
  }

  // ── step 2: hermes ────────────────────────────────────────────────────────
  function rHermes() {
    return '' +
      '<div class="eyebrow">Paso 2 · Hermes</div>' +
      '<h1>El motor de tu agente</h1>' +
      '<p class="lead">Hermes es lo que le da manos a tu agente: envía mensajes, maneja archivos, ' +
      'busca en la web y más. <b>Ya quedó instalado y configurado por ti</b> durante la instalación — ' +
      'no tienes que responder ninguna pregunta ni tocar ajustes.</p>' +
      '<div class="card"><div class="row" style="justify-content:space-between">' +
      '<div class="grow"><b>Comprobar que está listo</b>' +
      '<div class="muted small">Solo confirmamos que responde. Este asistente se encarga del resto.</div></div>' +
      '<button class="btn btn-primary" id="btnHermes">Comprobar</button></div>' +
      '<div id="pillHermes" class="pill" style="display:none;margin-top:8px"></div></div>' +
      '<p class="muted small">Todo lo técnico de Hermes lo ajusta este asistente al final. Continúa cuando el check esté en verde.</p>';
  }
  function eHermes() {
    var pill = el("pillHermes");
    if (el("btnHermes")) el("btnHermes").onclick = function () {
      pill.style.display = "inline-flex";
      runTest(this, pill, function () { return api("check", { what: "hermes" }); }).then(function (r) {
        S.hermesOk = !!(r && r.ok); if (r && r.path) S.hermes = r.path; save(); renderStepper();
      });
    };
  }

  // ── step 3: agent identity + use-cases ─────────────────────────────────────
  function rAgent() {
    var id = S.identity;
    var chips = META.usecases.map(function (u) {
      var sel = S.usecases.indexOf(u.id) >= 0 ? " sel" : "";
      return '<div class="chip' + sel + '" data-uid="' + u.id + '">' +
        '<div class="cic">' + u.icon + '</div><div class="grow">' +
        '<div class="cttl">' + esc(u.label) + '</div>' +
        '<div class="cblurb">' + esc(u.blurb) + '</div></div></div>';
    }).join("");
    return '' +
      '<div class="eyebrow">Paso 3 · Tu agente</div>' +
      '<h1>Dale personalidad y un propósito</h1>' +
      '<p class="lead">Con esto tu agente arranca “templado”: ya sabe quién es, para qué existe ' +
      'y qué tareas domina. No empezará de cero.</p>' +
      '<div class="card pad">' +
      field("agent_name", "¿Cómo se llama tu agente?", "Daneel", id.agent_name) +
      field("purpose", "¿Para qué existe?", "En una frase. Ej: “Me ayuda a coordinar a mi equipo y clientes.”", id.purpose, true) +
      field("business", "Contexto: tu negocio, persona o sitio web (opcional)", "Ej: iGalenus, salud digital, igalenus.com", id.business, true) +
      field("approach", "¿Por dónde debería empezar? (opcional)", "Ej: “Familiarízate con iGalenus por su web e industria.”", id.approach, true) +
      '</div>' +
      // Where the agent's files live. Asked here, while the agent is being created, rather
      // than left in an advanced fold: it decides where her own notes end up, what gets
      // backed up, and what a sync client will fight over.
      '<h2>¿Dónde trabajará?</h2>' +
      '<div class="card pad">' +
      '<p class="small muted" style="margin-top:0">Tu agente necesita una carpeta donde ' +
      '<b>trabajar</b>. Es la que abre por defecto: ahí lee y escribe los archivos que le ' +
      'pidas, lo que descargue o genere, y lo que organice para ti.</p>' +
      '<p class="small muted">No es donde se guarda su configuración — de eso se encarga ' +
      'Olivaw por su cuenta. Esta es la carpeta que <b>tú</b> abrirás para ver su trabajo, ' +
      'y la que querrás <b>respaldar</b>. Si ya tienes una carpeta con el material con el ' +
      'que quieres que trabaje, elígela. Si no, deja la recomendada: se puede cambiar ' +
      'después.</p>' +
      '<label class="field" style="margin-top:12px"><span class="lab">Carpeta de trabajo</span>' +
      '<input type="text" id="wsPath" placeholder="' + esc(S.wsSuggested || "") + '" value="' +
      esc(S.workspace || "") + '"></label>' +
      '<div class="row">' +
      '<button class="btn btn-soft btn-sm" id="wsPick">Elegir carpeta…</button>' +
      '<button class="btn btn-ghost btn-sm" id="wsDefault">Usar la recomendada</button>' +
      '</div><div id="wsInfo" style="margin-top:10px"></div></div>' +

      '<h2>¿En qué lo usarás? <span class="muted small">(elige una o varias)</span></h2>' +
      '<div class="chips" id="ucChips">' + chips + '</div>' +
      '<div class="card" style="margin-top:16px"><div class="row">' +
      '<button class="btn btn-soft btn-sm" id="btnPreview">Previsualizar sus instrucciones</button>' +
      '<span class="muted small">Verás el archivo que guía a tu agente.</span></div>' +
      '<div id="previewWrap" style="display:none;margin-top:12px"><pre id="previewMd"></pre></div></div>';
  }
  function field(key, lab, ph, val, area) {
    var input = area
      ? '<textarea data-k="' + key + '" placeholder="' + esc(ph) + '">' + esc(val) + '</textarea>'
      : '<input type="text" data-k="' + key + '" placeholder="' + esc(ph) + '" value="' + esc(val) + '">';
    return '<label class="field"><span class="lab">' + esc(lab) + '</span>' + input + '</label>';
  }
  function eAgent() {
    Array.prototype.forEach.call(document.querySelectorAll("#panel [data-k]"), function (inp) {
      inp.oninput = function () { S.identity[inp.getAttribute("data-k")] = inp.value; save(); renderStepper(); };
    });
    Array.prototype.forEach.call(document.querySelectorAll("#ucChips .chip"), function (c) {
      c.onclick = function () {
        var uid = c.getAttribute("data-uid"), i = S.usecases.indexOf(uid);
        if (i >= 0) { S.usecases.splice(i, 1); c.classList.remove("sel"); }
        else { S.usecases.push(uid); c.classList.add("sel"); }
        save();
      };
    });
    el("btnPreview").onclick = function () {
      var b = this; b.disabled = true;
      var identity = Object.assign({}, S.identity, { owner_id: S.owner_id });
      api("preview-prompt", { identity: identity, usecase_ids: S.usecases }).then(function (r) {
        b.disabled = false;
        if (r && r.ok) { el("previewWrap").style.display = "block"; el("previewMd").textContent = r.markdown; }
      });
    };

    // ── the agent's folder ───────────────────────────────────────────────────
    // Left empty on purpose when she does not care: the server falls back to the
    // recommended path, so "skip" is the default rather than an extra decision.
    function wsPaint(info) {
      var box = el("wsInfo"); if (!box) return;
      if (!info) { box.innerHTML = ""; return; }
      var out = "";
      if (!info.ok) {
        out = '<div class="callout small">⚠️ ' + esc(info.detail || "No se puede usar.") + '</div>';
      } else {
        var head = info.reused
          ? "✓ Ya hay un agente trabajando aquí. Seguirá con esos archivos."
          : (info.exists ? "✓ La carpeta existe." : "✓ Se creará al activar.");
        out = '<div class="callout small">' + esc(head) +
          (info.free ? ' <span class="muted">(' + esc(info.free) + ' libres)</span>' : "") +
          '<br><code class="small">' + esc(info.path) + '</code></div>';
      }
      (info.warnings || []).forEach(function (w) {
        out += '<div class="small muted" style="margin-top:6px">• ' + esc(w) + '</div>';
      });
      box.innerHTML = out;
    }

    function wsCheck() {
      var v = (el("wsPath") || {}).value || "";
      S.workspace = v.trim();
      save();
      if (!S.workspace) {
        // Nothing typed is a valid answer - show what that will actually mean.
        wsPaint({ ok: true, exists: false, path: S.wsSuggested || "",
                  warnings: ["Se usará la carpeta recomendada. Puedes cambiarla después."] });
        return;
      }
      api("workspace/inspect", { path: S.workspace }).then(wsPaint);
    }

    if (el("wsPath")) {
      var t = null;
      el("wsPath").oninput = function () { clearTimeout(t); t = setTimeout(wsCheck, 350); };
    }
    if (el("wsDefault")) el("wsDefault").onclick = function () {
      S.workspace = "";
      if (el("wsPath")) el("wsPath").value = "";
      save(); wsCheck();
    };
    if (el("wsPick")) el("wsPick").onclick = function () {
      var b = this; b.disabled = true; b.textContent = "Elige en la ventana…";
      api("workspace/pick", { start: S.workspace || S.wsSuggested || "" }).then(function (r) {
        b.disabled = false; b.textContent = "Elegir carpeta…";
        if (r && r.ok && r.path) {
          S.workspace = r.path;
          if (el("wsPath")) el("wsPath").value = r.path;
          save(); wsCheck();
        } else if (r && !r.cancelled && r.detail) {
          toast(r.detail);
        }
      });
    };

    // Name the suggestion after the agent, then show what the current answer means.
    api("workspace/suggest", { agent_name: (S.identity.agent_name || "") }).then(function (r) {
      if (r && r.ok) {
        S.wsSuggested = r.path;
        if (el("wsPath")) el("wsPath").placeholder = r.path;
        if (el("wsPick") && !r.picker) el("wsPick").style.display = "none";
        save();
      }
      wsCheck();
    });
  }

  // ── step 4: channel (Telegram, owner lock) ─────────────────────────────────
  function rChannel() {
    var botLink = S.bot_username ? "https://t.me/" + S.bot_username : "";
    return '' +
      '<div class="eyebrow">Paso 4 · Tu canal</div>' +
      '<h1>Conéctate como el dueño</h1>' +
      '<p class="lead">Telegram será tu línea directa con el agente. <b>Solo tu cuenta</b> podrá ' +
      'darle órdenes o cambiar su configuración — nadie más.</p>' +
      '<div class="callout small">No se puede crear un bot por API: se hace en un chat corto con ' +
      '<b>BotFather</b>. Yo me encargo de todo lo demás automáticamente en cuanto pegues el token — ' +
      'incluido el <b>candado de dueño</b> (queda escrito en Hermes al activar).</div>' +
      '<h2>1 · Crea tu bot con BotFather</h2>' +
      '<div class="guide">' +
      '<div class="g"><div class="gn"></div><div class="gt"><b>Abre BotFather</b> en Telegram ' +
      '<a href="https://t.me/BotFather" target="_blank" rel="noopener noreferrer">t.me/BotFather</a></div></div>' +
      '<div class="g"><div class="gn"></div><div class="gt">Envía <code>/newbot</code> y sigue las dos ' +
      'preguntas: un nombre y un usuario que termine en <code>bot</code>.</div></div>' +
      '<div class="g"><div class="gn"></div><div class="gt">Copia el <b>token</b> que te da (algo como ' +
      '<code>123456:ABC-...</code>) y pégalo aquí abajo.</div></div></div>' +
      '<div class="card">' +
      '<label class="field"><span class="lab">Token del bot</span>' +
      '<input type="password" id="tgToken" placeholder="123456789:AA..." value="' + esc(S.token) + '"></label>' +
      '<div class="row"><button class="btn btn-primary" id="btnValidate">Validar token</button>' +
      '<span id="pillTg" class="pill" style="display:none"></span></div>' +
      // Persistent confirmation. The pill that runTest writes is destroyed a moment later
      // by the render() that follows a successful validation, so on its own the success
      // message flashed and vanished - the token worked and the screen never said so.
      (S.bot_username
        ? '<div class="callout small" style="margin-top:12px">✅ Token válido. Tu bot es ' +
          '<b>@' + esc(S.bot_username) + '</b>' +
          (S.tokenCleaned ? ' <span class="muted">(limpiamos el texto que pegaste: ' +
            esc(S.tokenCleaned) + ')</span>' : "") +
          '<br><span class="muted">Sigue con el paso 2 para vincularte como dueño.</span></div>'
        : "") + '</div>' +

      '<h2>2 · Vincula tu cuenta como dueño</h2>' +
      '<div class="card"><p class="small muted" style="margin-top:0">' +
      'Para asegurar que <b>solo tú</b> quedes como dueño, abre tu bot y envíale este código exacto:</p>' +
      '<div class="callout" style="text-align:center;font-size:22px;letter-spacing:3px"><b>' + esc(S.owner_code) + '</b></div>' +
      '<p class="small muted">Luego pulsa «Vincularme». Solo la cuenta que envíe este código quedará como dueña.</p>' +
      (botLink ? '<a class="btn btn-soft btn-sm" href="' + botLink + '" target="_blank" rel="noopener noreferrer">Abrir mi bot</a> ' : "") +
      '<div class="row" style="margin-top:8px"><button class="btn btn-primary" id="btnCapture">Vincularme como dueño</button>' +
      '<span id="pillOwner" class="pill" style="display:none"></span></div>' +
      (S.owner_id ? '<div class="callout small" style="margin-top:12px">🔒 Dueño: <b>' + esc(S.owner_username || S.owner_id) +
        '</b> (id ' + esc(S.owner_id) + '). Será el único con control del agente.</div>' : "") +
      '</div>' +

      '<h2>3 · Personaliza y prueba</h2>' +
      '<div class="card"><div class="row">' +
      '<button class="btn btn-soft btn-sm" id="btnBrand">Personalizar el bot</button>' +
      '<button class="btn btn-soft btn-sm" id="btnTgTest">Enviar mensaje de prueba</button>' +
      '</div><div id="pillBrand" class="pill" style="display:none;margin-top:8px"></div></div>' +

      '<details><summary>Opciones avanzadas (repositorio, alertas)</summary>' +
      field2("repo", "Repositorio de actualizaciones", S.repo) +
      field2("maintainer_id", "Chat id para alertas de mantenimiento (opcional)", S.maintainer_id) +
      field2("install_dir", "Carpeta de instalación", S.install_dir) +
      '</details>';
  }
  function field2(key, lab, val) {
    return '<label class="field" style="margin-top:12px"><span class="lab">' + esc(lab) + '</span>' +
      '<input type="text" data-adv="' + key + '" value="' + esc(val || "") + '"></label>';
  }
  function eChannel() {
    var tok = el("tgToken"); tok.oninput = function () { S.token = tok.value.trim(); save(); };
    Array.prototype.forEach.call(document.querySelectorAll("#panel [data-adv]"), function (inp) {
      inp.oninput = function () { S[inp.getAttribute("data-adv")] = inp.value; save(); };
    });
    var pillTg = el("pillTg");
    el("btnValidate").onclick = function () {
      pillTg.style.display = "inline-flex";
      runTest(this, pillTg, function () { return api("telegram/validate", { token: S.token }); }).then(function (r) {
        if (r && r.ok) {
          // Keep the token the server actually authenticated with, not the raw paste.
          // A copy from BotFather often carries an invisible character; storing the raw
          // text would write a token into .env that looks right and authenticates nowhere.
          if (r.token) S.token = r.token;
          S.bot_username = r.username || "";
          S.tokenCleaned = (r.notes && r.notes.length) ? r.notes.join(" y ") : "";
          save(); render();
          toast("Token válido: @" + (r.username || "tu bot"));
        }
      });
    };
    var pillOwner = el("pillOwner");
    el("btnCapture").onclick = function () {
      pillOwner.style.display = "inline-flex";
      runTest(this, pillOwner, function () { return api("telegram/capture", { token: S.token, code: S.owner_code }); }).then(function (r) {
        if (r && r.ok) {
          S.owner_id = String(r.owner_id); S.chat_id = String(r.chat_id);
          S.owner_username = r.name || r.username || "";
          if (!S.identity.owner_name) S.identity.owner_name = r.name || r.username || "";
          save(); render();
          toast("Vinculado como dueño: " + (S.owner_username || S.owner_id));
        }
      });
    };
    var pillBrand = el("pillBrand");
    el("btnBrand").onclick = function () {
      pillBrand.style.display = "inline-flex";
      runTest(this, pillBrand, function () {
        return api("telegram/brand", { token: S.token, agent_name: S.identity.agent_name, purpose: S.identity.purpose });
      }, "Personalizando…");
    };
    el("btnTgTest").onclick = function () {
      pillBrand.style.display = "inline-flex";
      runTest(this, pillBrand, function () {
        return api("telegram/test", { token: S.token, chat_id: S.chat_id, agent_name: S.identity.agent_name });
      }, "Enviando…");
    };
  }

  // ── step 5: finish ──────────────────────────────────────────────────────
  function rFinish() {
    if (S.applied && S.applyResult) return rFinished(S.applyResult);
    var missing = [];
    if (!S.brainOk) missing.push("probar el cerebro (Paso 1)");
    if (!(S.identity.agent_name || "").trim()) missing.push("ponerle nombre a tu agente (Paso 3)");
    if (!S.owner_id) missing.push("vincular tu cuenta de Telegram (Paso 4)");
    var warn = missing.length
      ? '<div class="callout warn"><b>Antes de activar, te falta:</b><ul style="margin:6px 0 0">' +
        missing.map(function (m) { return "<li>" + esc(m) + "</li>"; }).join("") + "</ul></div>"
      : '<div class="callout"><b>Todo listo para activar.</b> Revisé el cerebro, el nombre y tu canal.</div>';
    return '' +
      '<div class="eyebrow">Paso 5 · Activación</div>' +
      '<h1>Todo junto</h1>' +
      '<p class="lead">Voy a escribir la configuración de tu agente y a encender el supervisor, ' +
      'que lo mantiene vivo y lo actualiza solo cuando no lo estés usando.</p>' +
      warn +
      '<div class="card pad"><b>Resumen</b><ul class="filelist">' +
      sumline("🧠", "Cerebro", cliLabel() + (S.brainOk ? " · probado ✓" : "")) +
      sumline("✨", "Agente", (S.identity.agent_name || "sin nombre") + (S.identity.purpose ? " — " + S.identity.purpose : "")) +
      sumline("🧩", "Habilidades", S.usecases.length ? String(S.usecases.length) + " seleccionadas" : "ninguna") +
      sumline("🔒", "Dueño", S.owner_id ? (S.owner_username || S.owner_id) + " (id " + S.owner_id + ")" : "sin vincular") +
      sumline("📁", "Carpeta", S.workspace || S.wsSuggested || "(predeterminada)") +
      '</ul></div>' +
      '<div class="row"><button class="btn btn-primary" id="btnApply">Aplicar y activar</button>' +
      '<span id="pillApply" class="pill" style="display:none"></span></div>';
  }
  function sumline(ic, k, v) {
    return "<li>" + ic + " <b>" + esc(k) + ":</b> <span class='muted'>" + esc(v) + "</span></li>";
  }
  // Did Telegram really connect? The wizard used to declare success once the files were written,
  // so a revoked token produced a green screen and a silent bot.
  function telegramVerdict(tg) {
    if (!tg || !tg.state) return "";
    var fixes = {
      token_rejected: "Abre @BotFather, envía <code>/token</code>, elige tu bot y pega aquí el " +
        "token nuevo. El anterior deja de servir en cuanto generas otro.",
      token_rejected_log: "Pulsa «Reiniciar el gateway» para que tome el token nuevo.",
      webhook_set: "Ese bot tiene un webhook puesto y por eso no recibe nada. Quítalo (o usa " +
        "otro bot) y vuelve a comprobar.",
      gateway_down: "El gateway no está corriendo. Pulsa «Reiniciar el gateway».",
      no_token: "Falta el token en este perfil: vuelve al paso de Telegram y pégalo.",
      unreachable: "No hubo forma de hablar con Telegram desde este equipo. Comprueba la " +
        "conexión y vuelve a intentarlo."
    };
    var good = tg.ok;
    var cls = good ? (tg.state === "connected" ? "" : "warn") : "warn";
    var title = good
      ? (tg.state === "connected" ? "Telegram conectado ✅" : "Telegram conectado, pero incompleto ⚠️")
      : "Telegram NO está funcionando ⚠️";
    var notes = (tg.notes || []).map(function (n) {
      return '<div class="muted small" style="margin-top:6px">ℹ️ ' + esc(n) + "</div>";
    }).join("");
    return '<div class="callout ' + cls + '" id="tgVerdict"><b>' + title + "</b> " +
      esc(tg.detail || "") +
      (fixes[tg.state] ? '<div style="margin-top:6px">' + fixes[tg.state] + "</div>" : "") +
      notes +
      '<div class="row" style="margin-top:10px;gap:8px">' +
      '<button class="btn btn-soft btn-sm" id="tgRecheck">Volver a comprobar</button>' +
      (good ? "" : '<button class="btn btn-soft btn-sm" id="tgRestart">Reiniciar el gateway</button>') +
      '<span id="tgPill" class="pill" style="display:none"></span></div></div>';
  }

  function wireTelegramVerdict() {
    var pill = el("tgPill");
    function recheck(after) {
      if (pill) { pill.style.display = "inline-flex"; pill.className = "pill load"; pill.textContent = "Comprobando…"; }
      api("telegram/health", { profile: targetProfile() }).then(function (tg) {
        if (S.applyResult) { S.applyResult.telegram = tg; save(); }
        render();
        if (tg && tg.ok) toast("Telegram conectado ✅");
      });
      if (after) after();
    }
    if (el("tgRecheck")) el("tgRecheck").onclick = function () { recheck(); };
    if (el("tgRestart")) el("tgRestart").onclick = function () {
      var b = this; b.disabled = true; b.textContent = "Reiniciando…";
      api("agent/action", { action: "restart", profile: targetProfile() }).then(function () {
        recheck();
      });
    };
  }

  function rFinished(res) {
    var files = (res.written || []).map(function (f) { return "<li>📄 " + esc(f) + "</li>"; }).join("");
    var warns = (res.warnings || []).map(function (w) { return '<div class="callout warn small">' + esc(w) + "</div>"; }).join("");
    var tgBox = telegramVerdict(res.telegram);
    var botLink = S.bot_username ? "https://t.me/" + S.bot_username : "";
    // Owner-lock section depends on whether we configured Hermes natively.
    var lock;
    if (res.hermes_native) {
      var steps = ((res.hermes && res.hermes.steps) || [])
        .map(function (s) { return "<li>" + (s.ok ? "✅" : "⚠️") + " " + esc(s.name) + "</li>"; }).join("");
      lock = '<div class="callout"><b>Candado de dueño activado 🔒</b> — dejé escrito en Hermes que ' +
        '<b>solo tu cuenta</b> puede mandarle órdenes (TELEGRAM_ALLOWED_USERS), configuré el modelo ' +
        'y reinicié el gateway. No tienes que tocar ningún archivo.' +
        (steps ? '<ul class="filelist" style="margin-top:8px">' + steps + '</ul>' : '') + '</div>';
    } else {
      lock = '<div class="callout warn"><b>Candado de dueño 🔒</b> — no encontramos el comando ' +
        '<code>hermes</code>, así que dejé <code>hermes-config-snippet.yaml</code> listo para pegar ' +
        'en tu configuración cuando instales Hermes (incluye la restricción de dueño).</div>';
    }
    var newNote = "";
    if (S.agent && S.agent.mode === "new" && res.agent) {
      newNote = '<div class="callout"><b>Nuevo agente aislado creado 🧩</b> — «' +
        esc(res.agent.name || res.agent.slug) + '» corre en su propio perfil <code>' +
        esc(res.agent.profile || res.agent.slug) + '</code> y puerto <code>' +
        esc(res.agent.port) + '</code>, con su propia memoria y bot. No comparte nada con los demás.</div>';
    }
    // Isolated Claude account: needs a one-time login into ITS config dir.
    var isoNote = "";
    var cdir = res.agent && res.agent.claude_config_dir;
    if (cdir) {
      isoNote = '<div class="callout warn"><b>Cuenta de Claude separada 🔑</b> — este agente usa su ' +
        'propia sesión de Claude. Ábrela <b>una vez</b> en una terminal:<br>' +
        '<b>Windows:</b> <code>set CLAUDE_CONFIG_DIR=' + esc(cdir) + '</code> y luego <code>claude</code><br>' +
        '<b>Mac/Linux:</b> <code>CLAUDE_CONFIG_DIR="' + esc(cdir) + '" claude</code><br>' +
        'Inicia sesión con la cuenta de Claude que quieras para este agente.</div>';
    }
    return '' +
      '<div class="done-hero"><div class="big">🎉</div>' +
      '<h1>' + ((res.telegram && !res.telegram.ok) ? 'Casi listo' : '¡Tu agente está vivo!') + '</h1>' +
      '<p class="lead">' + ((res.telegram && !res.telegram.ok)
        ? 'Escribí todo y encendí el supervisor, pero falta un paso para que puedas hablarle.'
        : 'Ya escribí todo y encendí el supervisor. A partir de ahora se actualiza solo, ' +
          'en silencio, cuando no lo estés usando.') + '</p></div>' +
      // The verdict goes FIRST: "your agent is alive" is a lie if Telegram never connected,
      // and this is the screen where that lie used to be told.
      tgBox +
      newNote + isoNote +
      (botLink ? '<div class="card pad" style="text-align:center"><b>Habla con tu agente ahora</b>' +
        '<p class="muted small">Abre tu bot en Telegram y salúdalo.</p>' +
        '<a class="btn btn-primary" href="' + botLink + '" target="_blank" rel="noopener noreferrer">Abrir ' +
        esc(S.identity.agent_name || "mi agente") + ' en Telegram</a></div>' : "") +
      '<h2>Últimos detalles</h2>' +
      lock +
      warns +
      '<details style="margin-top:12px"><summary>Archivos que creé</summary><ul class="filelist">' + files + '</ul></details>' +
      '<div class="guide" style="margin-top:16px">' +
      '<div class="g"><div class="gn"></div><div class="gt">Si aún no lo hiciste: abre una terminal y ejecuta ' +
      '<code>claude</code> para iniciar sesión una vez.</div></div>' +
      '<div class="g"><div class="gn"></div><div class="gt">Asegúrate de que el gateway de Hermes esté corriendo.</div></div>' +
      '<div class="g"><div class="gn"></div><div class="gt">Escríbele a tu bot: ¡ya piensa por ti!</div></div>' +
      '</div>' +
      '<div class="callout" style="margin-top:14px">➕ <b>Opcional:</b> pulsa «Continuar» para ' +
      'conectar más canales — WhatsApp, Slack, webhooks (Google Chat) y correo por SMTP.</div>';
  }
  function eFinish() {
    // The finished screen renders through here too (rFinish returns rFinished when applied),
    // so the verdict's buttons get wired on both passes.
    wireTelegramVerdict();
    var btn = el("btnApply"); if (!btn) return;
    var pill = el("pillApply");
    btn.onclick = function () {
      pill.style.display = "inline-flex";
      var payload = {
        provider: S.provider, claude: S.claude || "", codex: S.codex || "",
        install_dir: S.install_dir,
        workspace: S.workspace || S.wsSuggested || "", repo: S.repo, token: S.token, owner_id: S.owner_id,
        chat_id: S.chat_id, maintainer_id: S.maintainer_id, lang: "es",
        identity: S.identity, usecase_ids: S.usecases, tavily_key: S.tavily_key,
        hermes_config: S.hermes_config,
        agent: S.agent, bot_username: S.bot_username
      };
      runTest(btn, pill, function () {
        return api("apply", payload).then(function (r) {
          if (!r || !r.ok) return r;
          return api("finish", {}).then(function (f) {
            r.detail = f && f.ok ? "Configuración aplicada y supervisor iniciado."
              : "Configuración aplicada (inicia el supervisor manualmente).";
            return r;
          });
        });
      }, "Aplicando…").then(function (r) {
        if (r && r.ok) {
          S.applied = true; S.applyResult = r; S._max = STEPS.length - 1;
          if (r.agent && r.agent.slug) S.sel = r.agent.slug;
          save(); render();
          refreshAgents();   // the console's list must include what we just created
        }
      });
    };
  }

  // ── step 6: extra channels (optional) ─────────────────────────────────────
  // Which agent the panels on screen are about: the one selected in the console, or -
  // during setup - the one being installed. Null means the default agent, which is bare
  // hermes with no -p.
  function targetProfile() {
    if (S.view === "console") {
      var a = curAgent();
      return (a && !a.is_default) ? (a.profile || a.slug) : null;
    }
    if (S.agent && S.agent.mode === "new" && S.applyResult && S.applyResult.agent)
      return S.applyResult.agent.profile;
    if (S.agent && S.agent.mode === "reconfigure") return S.agent.slug;
    return null; // default agent -> bare hermes
  }
  // What to CALL the agent being configured. Only the browser needs it so far: with one
  // window per agent, the window has to say whose it is or they are indistinguishable.
  function targetName() {
    if (S.view === "console") return agentLabel(curAgent());
    if (S.applyResult && S.applyResult.agent && S.applyResult.agent.name)
      return S.applyResult.agent.name;
    return (S.agent && S.agent.name) || "";
  }
  function targetWorkspace() {
    if (S.view === "console") {
      var a = curAgent();
      if (a && a.workspace) return a.workspace;
    }
    if (S.applyResult && S.applyResult.agent && S.applyResult.agent.workspace)
      return S.applyResult.agent.workspace;
    return S.workspace || "";
  }
  function chLine(id) { return '<span id="' + id + '" class="pill" style="display:none;margin-top:8px"></span>'; }

  function rChannels() {
    if (!S.applied) {
      return '<div class="eyebrow">Paso 6 · Más canales</div><h1>Primero activa tu agente</h1>' +
        '<p class="lead">Vuelve al paso anterior y pulsa «Aplicar y activar». Luego podrás ' +
        'conectar WhatsApp, Slack, webhooks y correo.</p>';
    }
    return '' +
      '<div class="eyebrow">Paso 6 · Más canales <span class="muted">(opcional)</span></div>' +
      '<h1>¿Por dónde más pueden hablarle?</h1>' +
      '<p class="lead">Tu agente ya vive en Telegram. Aquí puedes darle más capacidades ' +
      '(imágenes, video), conectar herramientas externas, y sumar más canales. Todo es opcional.</p>' +
      SETUP_ORDER.map(function (fn) { return fn(); }).join("") +
      '<p class="muted small" style="margin-top:16px">Nada de esto es definitivo, y no hace ' +
      'falta dejarlo listo ahora: el botón de abajo te lleva a <b>tus agentes</b>, donde ' +
      'tienes todo esto otra vez, ordenado por agente, cuando lo necesites.</p>';
  }

  // Every section of the post-setup page, in the order the wizard shows them. The console
  // picks subsets of this same list, so a section only ever exists in one place.
  var SETUP_ORDER = [secPolicy, secBrowser, secHistory, secImages, secIntercom, secMcp, secWhatsApp, secGoogle, secSlack, secWebhook, secSmtp, secVault, secRoutines, secProposals];

  // Conversation lifetime. First, and open by default, because it is the single
  // biggest driver of what the agent costs to run — and Hermes' own default is the
  // expensive one: never restart, summarise at half a million tokens.
  function secPolicy() {
    return '' +
      '<details open><summary>⏱️ Duración de la conversación <span class="chip">gasto</span></summary>' +
      '<p class="small muted">Cada mensaje que le mandas arrastra <b>toda la conversación anterior</b>. ' +
      'Si nunca empieza de cero, cada respuesta cuesta más que la anterior y te quedas sin saldo en ' +
      'días. Aquí decides cada cuánto <b>empieza una conversación nueva</b> y cuándo <b>resume</b> ' +
      'lo hablado para seguir sin cargarlo todo.</p>' +
      '<div id="polWarn"></div>' +
      '<div id="polPresets"></div>' +
      '<div class="callout small" id="polSummary" style="margin-top:10px"></div>' +
      '<details style="margin-top:10px"><summary>Ajustes finos</summary>' +
      '<label class="field" style="margin-top:8px"><span class="lab">Cuándo empieza de cero</span>' +
      '<select id="polMode">' +
      '<option value="both">Por inactividad y cada madrugada</option>' +
      '<option value="idle">Sólo por inactividad</option>' +
      '<option value="daily">Sólo cada madrugada</option>' +
      '<option value="none">Nunca (sólo resume)</option></select></label>' +
      '<label class="field"><span class="lab">Minutos sin hablar antes de empezar de cero ' +
      '<span class="hint">150 = 2½ h</span></span>' +
      '<input type="number" id="polIdle" min="15" max="10080" step="15"></label>' +
      '<label class="field"><span class="lab">Hora del reinicio diario <span class="hint">0–23</span></span>' +
      '<input type="number" id="polHour" min="0" max="23" step="1"></label>' +
      '<label class="field"><span class="lab">Resumir al llegar al … % de su memoria ' +
      '<span class="hint">más bajo = más barato</span></span>' +
      '<input type="number" id="polPct" min="3" max="90" step="1"></label>' +
      '</details>' +
      '<div class="row" style="margin-top:10px">' +
      '<button class="btn btn-primary btn-sm" id="polSave">Guardar duración</button></div>' +
      chLine("polPill") + '</details>';
  }

  // Which browser the agent drives. It ALWAYS has browser tools; this only decides
  // whether they move an invisible Chromium or a window the owner can watch.
  function secBrowser() {
    return '' +
      '<details><summary>🌐 Navegador</summary>' +
      '<p class="small muted">Tu agente ya sabe navegar por internet — abrir páginas, leer, ' +
      'hacer clic, llenar formularios. Aquí eliges <b>en qué navegador</b>.</p>' +
      '<div id="brwBox" class="small muted">Comprobando…</div>' +
      '<div class="row" style="margin-top:10px">' +
      '<button class="btn btn-primary btn-sm" id="brwOn">Usar un navegador real</button>' +
      '<button class="btn btn-soft btn-sm" id="brwOff">Volver al invisible</button></div>' +
      '<p class="small muted" style="margin-top:8px">El navegador real se abre <b>sin tus ' +
      'sesiones</b>, y no es un capricho nuestro: desde Chrome 136, el modo que permite ' +
      'controlarlo <b>sólo funciona con un perfil aparte</b>, y ese perfil usa otra clave de ' +
      'cifrado a propósito — por eso copiar tu perfil tampoco traería tus sesiones. Entra ' +
      'una vez a lo que necesite y queda guardado ahí para siempre.</p>' +
      '<p class="small muted"><b>Cada agente abre su propia ventana.</b> Antes compartían ' +
      'una y se pisaban: si dos navegaban a la vez, el segundo le cambiaba la página al ' +
      'primero. Ahora cada uno tiene la suya, con sus pestañas y sus sesiones — así que la ' +
      'sesión que inicies aquí vale para <b>este</b> agente, no para los demás. La primera ' +
      'pestaña de cada ventana te dice de quién es.</p>' +
      chLine("brwPill") +

      '<div class="hr"></div><b class="small">¿Y usar tu Chrome de siempre, ya con sesión?</b>' +
      '<p class="small muted">Sí se puede, por otra vía: tu agente le <b>encarga</b> la tarea ' +
      'a Claude Code, que sí está conectado a tu navegador de siempre, y se queda con la ' +
      'respuesta. No hay que iniciar sesión en nada.</p>' +
      '<div id="brwDeleg" class="small muted">Comprobando…</div>' +
      '<div class="row" style="margin-top:8px">' +
      '<button class="btn btn-soft btn-sm" id="brwDelegTest">Probar de verdad ' +
      '<span class="muted">(tarda ~1 min)</span></button></div>' +
      chLine("brwDelegPill") + '</details>';
  }

  // Conversation memory / history (resume past conversations)
  function secHistory() {
    return '' +
      '<details open><summary>🧠 Memoria de conversaciones (recordar y retomar)</summary>' +
      '<p class="small muted">Deja que tu agente <b>recuerde y retome</b> conversaciones pasadas: ' +
      'busca en su historial, continúa un hilo anterior o empieza uno nuevo cuando aplica. ' +
      'En Telegram esto requiere activar «Session Search» (por defecto solo está en la CLI).</p>' +
      '<div class="row"><button class="btn btn-soft btn-sm" id="histStatus">Ver estado</button>' +
      '<button class="btn btn-primary btn-sm" id="histEnable">Activar memoria</button>' +
      '<button class="btn btn-soft btn-sm" id="histList">Ver conversaciones recientes</button></div>' +
      '<div id="histBox" style="margin-top:8px"></div>' +
      chLine("histPill") + '</details>';
  }

  // Capabilities: image / video generation
  function secImages() {
    return '' +
      '<details><summary>🎨 Generación de imágenes y video</summary>' +
      '<p class="small muted">Activa la generación en Hermes (se abre una ventana para elegir ' +
      'proveedor y su clave). Opciones gratis o con tu cuenta de Google:</p>' +
      (META.image_options || []).map(function (o) {
        return '<div class="row" style="gap:8px;align-items:flex-start;margin-bottom:6px">' +
          '<span>' + (o.free ? '🆓' : '💳') + '</span><div class="grow"><b class="small">' +
          esc(o.label) + '</b><div class="muted small">' + esc(o.note) +
          (o.link ? ' <a href="' + esc(o.link) + '" target="_blank" rel="noopener noreferrer">abrir</a>' : '') +
          '</div></div></div>';
      }).join("") +
      '<div id="imgRoutes" style="margin-top:10px"></div>' +
      '<div class="row" style="margin-top:8px"><button class="btn btn-soft btn-sm" id="capImg">Configurar imágenes en Hermes</button></div>' +
      chLine("capPill") + '</details>';
  }

  // Agents talking to agents. Off is a real answer, so both buttons are here.
  function secIntercom() {
    return '' +
      '<details><summary>🤝 Que tus agentes se hablen entre ellos</summary>' +
      '<p class="small muted">Si tienes más de un agente, cada uno sabe cosas ' +
      'distintas. Con esto <b>uno puede preguntarle al otro</b> y seguir la conversación ' +
      'hasta resolver, sin que tú hagas de mensajero. Cada pregunta es un turno completo ' +
      'del otro agente (~30-120 s), y queda escrita.</p>' +
      '<div id="icBox" class="small muted">Comprobando…</div>' +
      '<div class="row" style="margin-top:10px">' +
      '<button class="btn btn-primary btn-sm" id="icOn">Permitir que se hablen</button>' +
      '<button class="btn btn-soft btn-sm" id="icOff">No permitirlo</button></div>' +
      '<div class="row" style="margin-top:8px;align-items:center;gap:10px">' +
      '<label class="small">Turnos por conversación ' +
      '<input id="icTurns" type="number" min="2" max="40" style="width:70px"></label>' +
      '<label class="small">Llamadas por hora ' +
      '<input id="icHour" type="number" min="1" max="500" style="width:80px"></label>' +
      '<button class="btn btn-soft btn-sm" id="icSave">Guardar límites</button></div>' +
      '<p class="small muted" style="margin-top:8px">Un agente que le escribe a otro <b>no ' +
      'manda</b>: el mensaje llega marcado como venido de otro agente, y el que lo recibe ' +
      'tiene instrucciones de no ejecutar nada delicado por petíción de un compañero. ' +
      'Para eso estás tú.</p>' +
      '<div id="icThreads" style="margin-top:6px"></div>' +
      chLine("icPill") + '</details>';
  }

  // Connectors (MCP)
  function secMcp() {
    return '' +
      '<details><summary>🧩 Conectores (MCP)</summary>' +
      '<p class="small muted">Conecta herramientas externas <b>del lado de Hermes</b> — así sí ' +
      'funcionan con tu agente. (Los conectores de Claude Code no funcionan aquí.)</p>' +
      '<div class="row"><button class="btn btn-soft btn-sm" id="mcpCat">Ver catálogo</button>' +
      '<button class="btn btn-soft btn-sm" id="mcpMine">Ver instalados</button></div>' +
      '<div id="mcpList" style="margin-top:10px"></div>' +
      '<div class="hr"></div><b class="small">Añadir uno personalizado</b>' +
      '<label class="field" style="margin-top:8px"><span class="lab">Nombre</span>' +
      '<input type="text" id="mcpName" placeholder="pixa"></label>' +
      '<label class="field"><span class="lab">URL del servidor MCP</span>' +
      '<input type="text" id="mcpUrl" placeholder="https://..."></label>' +
      '<div class="row"><button class="btn btn-soft btn-sm" id="mcpAdd">Añadir</button></div>' +
      chLine("mcpPill") + '</details>';
  }

  // WhatsApp — connect, show the QR HERE, then lock it to the owner's number
  function secWhatsApp() {
    return '' +
      '<details><summary>💬 WhatsApp</summary>' +
      '<p class="small muted">1) Pulsa «Conectar». 2) Aparecerá aquí un código QR. ' +
      '3) En tu teléfono: WhatsApp → Ajustes → <b>Dispositivos vinculados</b> → Vincular. ' +
      'No necesitas terminal.</p>' +
      '<div class="row"><button class="btn btn-primary btn-sm" id="waPair">Conectar WhatsApp</button>' +
      '<button class="btn btn-soft btn-sm" id="waQr">Ver código QR</button>' +
      '<button class="btn btn-soft btn-sm" id="waCloud">Usar WhatsApp Business (Cloud)</button></div>' +
      '<div id="waQrBox" style="display:none;margin-top:10px"></div>' +
      '<div class="hr"></div><b class="small">Quién puede darle órdenes (obligatorio)</b>' +
      '<label class="field" style="margin-top:8px"><span class="lab">Tu número con código de país ' +
      '<span class="hint">ej: 5215512345678</span></span>' +
      '<input type="text" id="waUsers" placeholder="5215512345678" value="' + esc(S.wa_users || "") + '"></label>' +
      '<div class="row"><button class="btn btn-soft btn-sm" id="waSave">Guardar y bloquear a mi número</button></div>' +
      chLine("waPill") +

      // WhatsApp is where CLIENTS write. The owner does not want every message - she wants
      // the ones that need HER. Which ones, and what her own reasons mean, is decided here.
      '<div class="hr"></div><b class="small">🔔 Avísame cuando un cliente necesite a una persona</b>' +
      '<p class="small muted">Tu agente atiende WhatsApp solo. Cuando pase algo que te toca a ' +
      'ti, te escribe <b>por Telegram</b>. Tú eliges cuándo.</p>' +
      '<label class="row" style="gap:8px;align-items:center;margin:6px 0">' +
      '<input type="checkbox" id="escOn"> <span>Sí, avísame por Telegram</span></label>' +
      '<div id="escBody" style="display:none">' +
      '<div id="escWarn"></div>' +
      '<div class="small muted" style="margin:8px 0 4px">Marca los motivos por los que quieres que te avise:</div>' +
      '<div id="escList"></div>' +
      '<div class="hr"></div><b class="small">¿Falta alguno? Añádelo con tus palabras</b>' +
      '<p class="small muted">Describe cuándo debe avisarte, como se lo explicarías a alguien ' +
      'nuevo. Tu agente usa esa descripción para reconocerlo.</p>' +
      '<label class="field" style="margin-top:8px"><span class="lab">Nombre corto ' +
      '<span class="hint">ej: Pide cita urgente</span></span>' +
      '<input type="text" id="escNewLabel" placeholder="Pide cita urgente"></label>' +
      '<label class="field"><span class="lab">¿Cuándo debe avisarte? ' +
      '<span class="hint">una o dos frases</span></span>' +
      '<textarea id="escNewDesc" rows="2" placeholder="Cuando el paciente pide una cita para hoy o mañana, o dice que no puede esperar."></textarea></label>' +
      '<label class="field"><span class="lab">Importancia</span>' +
      '<select id="escNewPri"><option value="alta">Alta — avísame en cuanto pase</option>' +
      '<option value="media">Media — puede esperar un poco</option></select></label>' +
      '<div class="row"><button class="btn btn-soft btn-sm" id="escAdd">Añadir motivo</button></div>' +
      '<div class="row" style="margin-top:10px"><button class="btn btn-primary btn-sm" id="escSave">Guardar avisos</button></div>' +
      '</div>' + chLine("escPill") + '</details>';
  }

  // Google Workspace: Gmail (native email platform) + Google Chat
  function secGoogle() {
    return '' +
      '<details><summary>🟦 Google Workspace (Gmail y Google Chat)</summary>' +
      '<p class="small muted">Conecta tu cuenta de Google para que tu agente <b>reciba y responda ' +
      'correos</b>, y opcionalmente hable por Google Chat. Usa una <b>contraseña de aplicación</b>, ' +
      'nunca tu contraseña normal.</p>' +
      '<b class="small">📧 Gmail / Workspace (correo)</b>' +
      '<label class="field" style="margin-top:8px"><span class="lab">Proveedor</span>' +
      '<select id="gwProv">' + Object.keys(META.google_presets || {}).map(function (k) {
        var o = META.google_presets[k];
        return '<option value="' + k + '"' + (S.gw_prov === k ? " selected" : "") + '>' + esc(o.label) + '</option>';
      }).join("") + '</select></label>' +
      '<div id="gwNote" class="callout small" style="margin:0 0 12px"></div>' +
      '<label class="field"><span class="lab">Tu correo</span>' +
      '<input type="text" id="gwAddr" placeholder="tu@empresa.com" value="' + esc(S.gw_addr || "") + '"></label>' +
      '<label class="field"><span class="lab">Contraseña de aplicación</span>' +
      '<input type="password" id="gwPass"></label>' +
      '<div class="row"><label class="field grow"><span class="lab">SMTP (salida)</span>' +
      '<input type="text" id="gwSmtp" value="' + esc(S.gw_smtp || "") + '"></label>' +
      '<label class="field grow"><span class="lab">IMAP (entrada)</span>' +
      '<input type="text" id="gwImap" value="' + esc(S.gw_imap || "") + '"></label></div>' +
      '<label class="field"><span class="lab">Correos que pueden darle órdenes (obligatorio)' +
      '<span class="hint"> — al menos el tuyo; los demás solo reciben respuestas</span></span>' +
      '<input type="text" id="gwUsers" placeholder="tu@empresa.com, jefe@empresa.com" value="' + esc(S.gw_users || "") + '"></label>' +
      '<div class="row"><button class="btn btn-primary btn-sm" id="gwSave">Conectar correo</button></div>' +
      '<div class="hr"></div><b class="small">💬 Google Chat (opcional, avanzado)</b>' +
      '<p class="small muted">Necesita un archivo JSON de «cuenta de servicio» de Google Cloud.</p>' +
      '<label class="field"><span class="lab">Ruta del JSON</span>' +
      '<input type="text" id="gcJson" placeholder="C:\ruta\service-account.json"></label>' +
      '<label class="field"><span class="lab">Correos permitidos</span>' +
      '<input type="text" id="gcUsers" placeholder="tu@empresa.com"></label>' +
      '<div class="row"><button class="btn btn-soft btn-sm" id="gcSave">Conectar Google Chat</button></div>' +
      chLine("gwPill") + '</details>';
  }

  // Slack
  function secSlack() {
    return '' +
      '<details><summary>🟣 Slack</summary>' +
      '<p class="small muted">Genera el manifest, crea la app en ' +
      '<a href="https://api.slack.com/apps" target="_blank" rel="noopener noreferrer">api.slack.com/apps</a> ' +
      '(«From an app manifest»), y termina la configuración en la terminal.</p>' +
      '<div class="row"><button class="btn btn-soft btn-sm" id="slackMan">Ver manifest</button>' +
      '<button class="btn btn-soft btn-sm" id="slackSetup">Configurar en terminal</button></div>' +
      '<div id="slackManWrap" style="display:none;margin-top:10px"><pre id="slackManTxt"></pre></div>' +
      chLine("slackPill") + '</details>';
  }

  // Webhook / Google Chat
  function secWebhook() {
    return '' +
      '<details><summary>🔗 Webhook / Google Chat</summary>' +
      '<p class="small muted">Crea una ruta <code>/webhooks/&lt;nombre&gt;</code> que activa al ' +
      'agente cuando llega un evento (Google Chat, Zapier, GitHub, lo que sea).</p>' +
      '<label class="field"><span class="lab">Nombre de la ruta</span>' +
      '<input type="text" id="whName" placeholder="ej: gchat" value="' + esc(S.wh_name || "") + '"></label>' +
      '<label class="field"><span class="lab">¿Qué hace? (opcional)</span>' +
      '<input type="text" id="whDesc" placeholder="Avisos desde Google Chat" value="' + esc(S.wh_desc || "") + '"></label>' +
      '<div class="row"><button class="btn btn-soft btn-sm" id="whAdd">Crear webhook</button>' +
      '<button class="btn btn-soft btn-sm" id="whTest">Probar</button></div>' +
      chLine("whPill") + '</details>';
  }

  // Email (SMTP)
  function secSmtp() {
    var smtpOpts = (META.smtp_providers || []).map(function (p) {
      return '<option value="' + p.id + '"' + (S.smtp.provider === p.id ? " selected" : "") +
        '>' + esc(p.label) + '</option>';
    }).join("");
    return '' +
      '<details><summary>✉️ Correo (SMTP)</summary>' +
      '<p class="small muted">Dale a tu agente la capacidad de enviar correos. Elige tu proveedor ' +
      'y usa una <b>contraseña de aplicación</b> (no tu contraseña normal).</p>' +
      '<label class="field"><span class="lab">Proveedor</span>' +
      '<select id="smtpProv">' + smtpOpts + '</select></label>' +
      '<div id="smtpNote" class="callout small" style="margin:0 0 12px"></div>' +
      '<div class="row"><label class="field grow"><span class="lab">Servidor (host)</span>' +
      '<input type="text" id="smtpHost" value="' + esc(S.smtp.host || "") + '"></label>' +
      '<label class="field" style="width:110px"><span class="lab">Puerto</span>' +
      '<input type="text" id="smtpPort" value="' + esc(S.smtp.port || 587) + '"></label></div>' +
      '<label class="field"><span class="lab">Tu correo (usuario)</span>' +
      '<input type="text" id="smtpUser" value="' + esc(S.smtp.user || "") + '"></label>' +
      '<label class="field"><span class="lab">Contraseña de aplicación</span>' +
      '<input type="password" id="smtpPass" value="' + esc(S.smtp.password || "") + '"></label>' +
      '<label class="field"><span class="lab">Enviar prueba a</span>' +
      '<input type="text" id="smtpTo" placeholder="tu-otro-correo@ejemplo.com" value="' + esc(S.smtp.to_addr || "") + '"></label>' +
      '<div class="row"><button class="btn btn-soft btn-sm" id="smtpTest">Enviar prueba</button>' +
      '<button class="btn btn-primary btn-sm" id="smtpSave">Guardar en el agente</button></div>' +
      chLine("smtpPill") + '</details>';
  }

  // The long-term memory, seen from the owner's side: can HE open it?
  function secVault(bare) {
    return (bare ? '' :
      '<h2 style="margin-top:26px">Memoria en Obsidian</h2>' +
      '<p class="muted small" style="margin-top:-6px">Lo que tu agente recuerda son notas de ' +
      'texto. En Obsidian puedes leerlas, corregirlas y ver cómo se conectan.</p>' +
      '') +
      '<div id="obsBox" class="card pad"><span class="muted small">Comprobando…</span></div>';
  }

  // Sleep + weekly retrospective: the agent's own upkeep, not a channel — but this is the
  // post-setup page, and it is where someone finishing the wizard will actually see it.
  function secRoutines(bare) {
    return (bare ? '' :
      '<h2 style="margin-top:26px">Rutinas automáticas <span class="muted" ' +
      'style="font-weight:400;font-size:15px">(recomendado)</span></h2>' +
      '<p class="muted small" style="margin-top:-6px">Igual que una persona: de madrugada repasa ' +
      'el día y guarda lo que importa; los domingos revisa su semana y se corrige.</p>' +
      '') +
      '<div id="selfcareBox" class="card pad"><span class="muted small">Revisando…</span></div>';
  }

  // What came out of those routines and needs a yes or a no.
  function secProposals(bare) {
    return (bare ? '' :
      '<h2 style="margin-top:26px">Lo que Olivaw propone</h2>' +
      '<p class="muted small" style="margin-top:-6px">Cuando ve algo que podría hacer por ti, lo ' +
      'propone aquí y espera. No construye nada sin tu sí — y lo que descartes no vuelve a ' +
      'proponerlo.</p>' +
      '') +
      '<div id="propBox" class="card pad"><span class="muted small">Cargando…</span></div>';
  }
  // ── self-care routines (nightly consolidation + weekly retrospective) ───────
  function selfcareHtml(st) {
    if (!st || !st.ok) {
      return '<span class="muted small">' +
        esc((st && st.detail) || "No pude leer las rutinas programadas.") + '</span>';
    }
    var rows = ["daily", "weekly"].map(function (k) {
      var j = (st.jobs || {})[k] || {};
      var on = !!j.installed;
      return '<div class="sc-row">' +
        '<div class="sc-main"><div class="sc-title">' +
        '<span class="pill ' + (on ? "ok" : "load") + '">' + (on ? "✓ activa" : "inactiva") +
        '</span> <b>' + esc(j.label || k) + '</b></div>' +
        '<div class="muted small">' + esc(j.what || "") + '</div>' +
        (on ? '<div class="muted small">Horario <code>' + esc(j.schedule || "") + '</code>' +
              (j.next_run ? ' · próxima: ' + esc(String(j.next_run).slice(0, 16)) : "") +
              (j.last_run ? ' · última: ' + esc(String(j.last_run).slice(0, 16)) : "") +
              '</div>'
            : '<div class="muted small">Se instalará a las ' +
              esc(cronHuman(j.default_schedule || "")) + '</div>') +
        '</div>' +
        '<div class="sc-side">' +
        (on ? '<button class="btn btn-soft btn-sm" data-scrun="' + k + '">Probar ahora</button>'
            : '') +
        '<button class="btn btn-ghost btn-sm" data-scview="' + k + '">Ver instrucciones</button>' +
        '</div></div>';
    }).join("");
    var anyOff = !((st.jobs || {}).daily || {}).installed ||
                 !((st.jobs || {}).weekly || {}).installed;
    return rows +
      '<div class="row" style="margin-top:12px;gap:10px;flex-wrap:wrap">' +
      '<button class="btn btn-primary btn-sm" id="scInstall">' +
      (anyOff ? "Activar las rutinas" : "Reinstalar / actualizar") + '</button>' +
      '<button class="btn btn-ghost btn-sm" id="scRemove">Quitar</button>' +
      '<span class="muted small">Memoria larga: <code>' +
      esc(st.agent_memory || st.vault || st.workspace || "") + '</code></span></div>' +
      '<div id="scOut" style="margin-top:10px"></div>';
  }

  // ── the vault, from the owner's side ──────────────────────────────────────
  function obsHtml(st) {
    if (!st || !st.ok) return '<span class="muted small">No pude comprobar Obsidian.</span>';
    var job = st.install_job || {};
    var steps = (st.steps || []).map(function (x) {
      return '<div class="ob-step"><span class="ob-dot ' + (x.ok ? "on" : "off") + '">' +
        (x.ok ? "✓" : "·") + '</span><span class="' + (x.ok ? "" : "muted") + '">' +
        esc(x.label) + '</span></div>';
    }).join("");
    var btns = [];
    if (!st.installed) {
      btns.push(job.state === "running"
        ? '<button class="btn btn-soft btn-sm" disabled>Instalando Obsidian…</button>'
        : '<button class="btn btn-primary btn-sm" id="obsInstall">Instalar Obsidian</button>');
    }
    if (!st.registered || !st.vault_exists) {
      btns.push('<button class="btn ' + (st.installed ? "btn-primary" : "btn-soft") +
        ' btn-sm" id="obsPrepare">Preparar el vault</button>');
    }
    if (st.installed && st.vault_exists) {
      btns.push('<button class="btn ' + (st.opened ? "btn-ghost" : "btn-primary") +
        ' btn-sm" id="obsOpen">Abrir en Obsidian</button>');
    }
    btns.push('<button class="btn btn-ghost btn-sm" id="obsCheck">Volver a comprobar</button>');
    return '<div class="ob-steps">' + steps + '</div>' +
      '<div class="muted small" style="margin-top:8px">' +
      (st.vault ? 'Vault: <code>' + esc(st.vault) + '</code> · ' + st.notes + ' notas' : 'Sin vault todavía') +
      '</div>' +
      (st.healthy ? "" : '<div class="callout small" style="margin:10px 0 0">' +
        esc(st.detail || "") + '</div>') +
      (job.state === "failed" && !st.installed
        ? '<div class="callout warn small" style="margin:10px 0 0">No pude instalarlo solo. ' +
          'Descárgalo de obsidian.md e instálalo a mano; luego pulsa «Volver a comprobar».</div>'
        : "") +
      '<div class="row" style="margin-top:12px;gap:10px;flex-wrap:wrap">' + btns.join("") +
      '</div><div id="obsOut" style="margin-top:10px"></div>';
  }

  var obsPoll = null;
  function paintObs() {
    if (!el("obsBox")) return;
    api("obsidian/status", {}).then(function (st) {
      var box = el("obsBox");
      if (!box) return;
      box.innerHTML = obsHtml(st);
      wireObs();
      // A winget install takes minutes; keep looking until it lands or gives up.
      var running = st && st.install_job && st.install_job.state === "running";
      if (running && !obsPoll) obsPoll = setInterval(paintObs, 5000);
      if (!running && obsPoll) { clearInterval(obsPoll); obsPoll = null; }
    });
  }

  function wireObs() {
    var out = el("obsOut");
    function act(id, route, working, done) {
      var b = el(id);
      if (!b) return;
      b.onclick = function () {
        b.disabled = true;
        var prev = b.textContent;
        b.textContent = working;
        api(route, {}).then(function (r) {
          b.disabled = false; b.textContent = prev;
          if (out && r && (r.detail || (r.problems && r.problems.length))) {
            out.innerHTML = '<div class="callout ' + (r.ok ? "" : "warn") + ' small">' +
              esc(r.detail || (r.problems || []).join(" · ")) + '</div>';
          }
          if (r && r.changed && r.changed.length && out) {
            out.innerHTML = '<div class="callout small">' +
              r.changed.map(esc).join("<br>") + '</div>';
          }
          if (done) toast(done);
          paintObs();
        });
      };
    }
    act("obsInstall", "obsidian/install", "Instalando…", null);
    act("obsPrepare", "obsidian/prepare", "Preparando…", "Vault listo 📓");
    act("obsOpen", "obsidian/open", "Abriendo…", null);
    act("obsCheck", "obsidian/status", "Comprobando…", null);
  }

  // ── proposals: the agent asks, the owner answers, the agent remembers ─────
  function propHtml(r) {
    if (!r || !r.ok) return '<span class="muted small">No pude leer las propuestas.</span>';
    var pend = r.pending || [];
    var decided = (r.proposals || []).filter(function (p) {
      return p.state !== "pendiente";
    });
    var html = "";
    if (!pend.length) {
      html += '<span class="muted small">Nada pendiente de tu decisión. ' +
        'La rutina nocturna te propondrá algo cuando vea un motivo real — no por rellenar.</span>';
    }
    html += pend.map(function (p) {
      return '<div class="sc-row"><div class="sc-main">' +
        '<div class="sc-title"><span class="pill load">te toca decidir</span> <b>' +
        esc(p.title) + '</b></div>' +
        (p.why ? '<div class="muted small">' + esc(p.why) + '</div>' : "") +
        '<div class="muted small">' +
        (p.category ? esc(p.category) : "") +
        (p.effort ? ' · esfuerzo ' + esc(p.effort) : "") +
        (p.proposed ? ' · propuesta el ' + esc(p.proposed) : "") + '</div>' +
        (p.body ? '<details style="margin-top:4px"><summary class="small">Ver qué haría ' +
          'exactamente</summary><div class="md small">' + mdHtml(p.body) + '</div></details>' : "") +
        '<input type="text" class="prop-note" data-note="' + esc(p.id) + '" ' +
        'placeholder="Comentario (opcional): «hazlo pero…», «no, mejor…»">' +
        '</div><div class="sc-side">' +
        '<button class="btn btn-primary btn-sm" data-yes="' + esc(p.id) + '">Hazlo</button>' +
        '<button class="btn btn-ghost btn-sm" data-no="' + esc(p.id) + '">No</button>' +
        '</div></div>';
    }).join("");
    if (decided.length) {
      var pill = { aceptada: "ok", hecha: "ok", rechazada: "warn", descartada: "load" };
      html += '<details style="margin-top:12px"><summary class="small">Historial (' +
        decided.length + ')</summary><div style="margin-top:6px">' +
        decided.map(function (p) {
          return '<div class="prop-hist"><span class="pill ' + (pill[p.state] || "load") + '">' +
            esc(p.state) + '</span> <span>' + esc(p.title) + '</span>' +
            (p.decided ? ' <span class="muted small">· ' + esc(p.decided) + '</span>' : "") +
            '</div>';
        }).join("") + '</div></details>';
    }
    // The file ships with headings, an intro line and HTML comments. One bullet is what tells
    // us something was actually learned; anything less is the empty template.
    var learned = String(r.learning || "").replace(/<!--[\s\S]*?-->/g, "");
    if (/^[ \t]*[-*+][ \t]+\S/m.test(learned)) {
      html += '<details style="margin-top:8px"><summary class="small">Qué ha aprendido de tus ' +
        'respuestas</summary><div class="md small" style="margin-top:6px">' +
        mdHtml(r.learning) + '</div></details>';
    }
    return html + '<div id="propOut" style="margin-top:10px"></div>';
  }

  function paintProps() {
    if (!el("propBox")) return;
    api("proposals/list", {}).then(function (r) {
      var box = el("propBox");
      if (!box) return;
      box.innerHTML = propHtml(r);
      wireProps();
    });
  }

  function wireProps() {
    var box = el("propBox");
    if (!box) return;
    function answer(id, state) {
      var note = box.querySelector('[data-note="' + id + '"]');
      var comment = note ? note.value : "";
      Array.prototype.forEach.call(box.querySelectorAll("button"), function (b) {
        b.disabled = true;
      });
      api("proposals/decide", { id: id, state: state, comment: comment }).then(function (r) {
        if (r && r.ok) {
          toast(state === "aceptada"
            ? "Anotado: lo hará esta noche 🌙"
            : "Anotado. No volverá a proponerlo.");
        }
        paintProps();
        if (r && !r.ok) {
          var out = el("propOut");
          if (out) out.innerHTML = '<div class="callout warn small">' +
            esc(r.detail || "No pude guardar tu respuesta") + '</div>';
        }
      });
    }
    Array.prototype.forEach.call(box.querySelectorAll("[data-yes]"), function (b) {
      b.onclick = function () { answer(b.getAttribute("data-yes"), "aceptada"); };
    });
    Array.prototype.forEach.call(box.querySelectorAll("[data-no]"), function (b) {
      b.onclick = function () { answer(b.getAttribute("data-no"), "rechazada"); };
    });
  }

  function cronHuman(spec) {
    var p = String(spec || "").split(/\s+/);
    if (p.length < 5) return spec;
    var days = { "0": "domingos", "1": "lunes", "2": "martes", "3": "miércoles",
                 "4": "jueves", "5": "viernes", "6": "sábados" };
    var hhmm = ("0" + p[1]).slice(-2) + ":" + ("0" + p[0]).slice(-2);
    if (p[4] === "*") return "las " + hhmm + " todos los días";
    return "los " + (days[p[4]] || p[4]) + " a las " + hhmm;
  }

  function paintSelfcare() {
    var box = el("selfcareBox");
    if (!box) return;
    api("selfcare/status", {}).then(function (st) {
      box = el("selfcareBox");
      if (!box) return;
      box.innerHTML = selfcareHtml(st);
      wireSelfcare();
    });
  }

  function wireSelfcare() {
    var box = el("selfcareBox");
    if (!box) return;
    var out = el("scOut");
    if (el("scInstall")) {
      el("scInstall").onclick = function () {
        var b = this;
        b.disabled = true; b.textContent = "Activando…";
        api("selfcare/install", {}).then(function (r) {
          if (r && r.ok) toast("Rutinas activadas 🌙");
          else if (out) out.innerHTML = '<div class="callout warn small">' +
            esc((r && (r.detail || JSON.stringify(r.results || {}))) || "No pude activarlas") +
            '</div>';
          paintSelfcare();
        });
      };
    }
    if (el("scRemove")) {
      el("scRemove").onclick = function () {
        api("selfcare/remove", {}).then(function () { toast("Rutinas quitadas"); paintSelfcare(); });
      };
    }
    Array.prototype.forEach.call(box.querySelectorAll("[data-scrun]"), function (b) {
      b.onclick = function () {
        b.disabled = true; b.textContent = "Encolando…";
        api("selfcare/run", { key: b.getAttribute("data-scrun") }).then(function (r) {
          b.disabled = false; b.textContent = "Probar ahora";
          if (out) {
            out.innerHTML = '<div class="callout small">' +
              (r && r.ok ? "Se ejecutará en el próximo tick del programador (menos de un minuto). " +
                           "Te llegará el resumen por tu canal."
                         : esc((r && r.detail) || "No pude encolarla")) + '</div>';
          }
        });
      };
    });
    Array.prototype.forEach.call(box.querySelectorAll("[data-scview]"), function (b) {
      b.onclick = function () {
        api("selfcare/preview", { key: b.getAttribute("data-scview") }).then(function (r) {
          if (!r || !r.ok || !out) return;
          out.innerHTML = '<details open><summary>' + esc(r.label) +
            ' — instrucciones que ejecutará</summary><pre style="white-space:pre-wrap">' +
            esc(r.prompt) + '</pre></details>';
        });
      };
    });
  }

  function eChannels() {
    // Guarded on purpose: this same function wires the wizard's one long page AND each
    // single-section page of the console, so anything that costs a request has to ask
    // whether its own markup is on screen. Everything below is already el()-guarded.
    if (el("selfcareBox")) paintSelfcare();
    if (el("obsBox")) paintObs();
    if (el("propBox")) paintProps();
    if (S.view !== "console" && !S.applied) return;
    var prof = targetProfile(), ws = targetWorkspace();

    // Conversation memory / history
    var histPill = el("histPill");
    if (el("histStatus")) el("histStatus").onclick = function () {
      var b = this; b.disabled = true;
      api("channel/history-status", { profile: prof }).then(function (r) {
        b.disabled = false;
        var box = el("histBox"); if (!r) { box.innerHTML = ""; return; }
        var rows = Object.keys(r.platforms || {}).map(function (p) {
          return '<div class="small">' + (r.platforms[p] ? "✅" : "⬜") + " " + esc(p) +
            (r.platforms[p] ? " — memoria activa" : " — sin memoria") + "</div>";
        }).join("");
        box.innerHTML = rows + (r.enabled_telegram ? "" :
          '<div class="callout warn small" style="margin-top:6px">En Telegram aún no está activa. ' +
          'Pulsa «Activar memoria» y marca «Session Search».</div>');
      });
    };
    if (el("histEnable")) el("histEnable").onclick = function () {
      histPill.style.display = "inline-flex";
      runTest(this, histPill, function () { return api("channel/history-enable", { profile: prof }); }, "Abriendo…")
        .then(function (r) { if (r && r.ok) toast("Marca «Session Search» en tu canal, guarda y reinicia el gateway."); });
    };
    if (el("histList")) el("histList").onclick = function () {
      var b = this; b.disabled = true;
      api("channel/sessions-recent", { profile: prof }).then(function (r) {
        b.disabled = false;
        el("histBox").innerHTML = '<pre>' + esc((r && r.detail) || "—") + '</pre>';
      });
    };

    // Capabilities: image/video setup (launches hermes setup tools)
    var capPill = el("capPill");
    if (el("capImg")) el("capImg").onclick = function () {
      capPill.style.display = "inline-flex";
      runTest(this, capPill, function () { return api("channel/tools-setup", { profile: prof }); }, "Abriendo…");
    };

    // Connectors (MCP)
    var mcpPill = el("mcpPill");
    if (el("mcpCat")) el("mcpCat").onclick = function () {
      var b = this; b.disabled = true;
      api("channel/mcp-catalog", { profile: prof }).then(function (r) {
        b.disabled = false;
        var box = el("mcpList");
        if (!r || !r.ok || !(r.servers || []).length) { box.innerHTML = '<span class="muted small">Catálogo no disponible.</span>'; return; }
        box.innerHTML = r.servers.map(function (s) {
          return '<div class="row" style="justify-content:space-between;border-bottom:1px solid var(--line-2);padding:6px 0">' +
            '<div class="grow"><b class="small">' + esc(s.name) + '</b> <span class="muted small">' + esc(s.desc || "") + '</span></div>' +
            '<button class="btn btn-soft btn-sm" data-mcp="' + esc(s.name) + '">Instalar</button></div>';
        }).join("");
        Array.prototype.forEach.call(box.querySelectorAll("[data-mcp]"), function (btn) {
          btn.onclick = function () {
            mcpPill.style.display = "inline-flex";
            runTest(btn, mcpPill, function () { return api("channel/mcp-install", { profile: prof, name: btn.getAttribute("data-mcp") }); }, "Abriendo…");
          };
        });
      });
    };
    if (el("mcpMine")) el("mcpMine").onclick = function () {
      var b = this; b.disabled = true;
      api("channel/mcp-list", { profile: prof }).then(function (r) {
        b.disabled = false;
        el("mcpList").innerHTML = '<pre>' + esc((r && r.detail) || "—") + '</pre>';
      });
    };
    if (el("mcpAdd")) el("mcpAdd").onclick = function () {
      mcpPill.style.display = "inline-flex";
      var name = (el("mcpName") || {}).value, url = (el("mcpUrl") || {}).value;
      runTest(this, mcpPill, function () { return api("channel/mcp-add", { profile: prof, name: name, url: url }); }, "Abriendo…");
    };

    var wa = el("waPair"), waPill = el("waPill");
    function pollQr(tries) {
      api("channel/whatsapp-qr", { profile: prof }).then(function (r) {
        var box = el("waQrBox"); if (!box) return;
        if (r && r.connected) {
          box.style.display = "block";
          box.innerHTML = '<div class="callout small">✅ WhatsApp conectado. Ahora guarda tu número abajo.</div>';
          return;
        }
        if (r && r.qr) {
          box.style.display = "block";
          box.innerHTML = '<div class="muted small" style="margin-bottom:6px">' + esc(r.detail || "") + '</div>' +
            '<pre style="line-height:1;font-size:9px">' + esc(r.qr) + '</pre>';
          return;
        }
        box.style.display = "block";
        box.innerHTML = '<span class="muted small">' + esc((r && r.detail) || "Esperando el código…") + '</span>';
        if ((tries || 0) < 12) setTimeout(function () { pollQr((tries || 0) + 1); }, 2500);
      });
    }
    if (wa) wa.onclick = function () {
      waPill.style.display = "inline-flex";
      runTest(this, waPill, function () { return api("channel/whatsapp", { profile: prof, cloud: false }); }, "Iniciando…")
        .then(function () { setTimeout(function () { pollQr(0); }, 2500); });
    };
    if (el("waQr")) el("waQr").onclick = function () { pollQr(0); };
    if (el("waCloud")) el("waCloud").onclick = function () {
      waPill.style.display = "inline-flex";
      runTest(this, waPill, function () { return api("channel/whatsapp", { profile: prof, cloud: true }); }, "Abriendo…");
    };
    if (el("waUsers")) el("waUsers").oninput = function () { S.wa_users = this.value; save(); };
    if (el("waSave")) el("waSave").onclick = function () {
      waPill.style.display = "inline-flex";
      runTest(this, waPill, function () {
        return api("channel/whatsapp-save", { profile: prof, allowed_users: S.wa_users || "",
                                              home_channel: (S.wa_users || "").split(",")[0].trim() });
      }, "Guardando…");
    };

    // ── when should a WhatsApp conversation reach the owner? ──────────────────
    // ── how this agent makes images ────────────────────────────────────────
    // Which route is right depends on the brain, so the server decides and this only
    // renders it. A Codex agent needs no setup at all; a Claude one has a free route the
    // old card never mentioned, because that card only knew about API keys.
    function loadImg() {
      api("images/status", { profile: prof }).then(function (r) {
        var box = el("imgRoutes");
        if (!box || !r || !r.ok) return;
        box.innerHTML = '<div class="small muted" style="margin-bottom:6px">' +
          'Cerebro de este agente: <b>' + esc(r.engine === "codex" ? "Codex" : "Claude Code") +
          '</b></div>' +
          r.routes.map(function (rt) {
            var mark = rt.ready ? "✅" : (rt.available ? "○" : "—");
            var best = rt.id === r.recommended
              ? ' <span class="chip">recomendado</span>' : "";
            return '<div class="row" style="gap:8px;align-items:flex-start;padding:5px 0;' +
              'border-bottom:1px solid var(--line-2)"><span>' + mark + '</span>' +
              '<div class="grow"><b class="small">' + esc(rt.label) + '</b>' + best +
              '<br><span class="muted small">' + esc(rt.cost) + ' — ' + esc(rt.note) +
              '</span></div></div>';
          }).join("");
      });
    }
    if (el("imgRoutes")) loadImg();

    // ── which browser the agent drives ─────────────────────────────────────
    var brwPill = el("brwPill");
    function paintBrw(st) {
      var box = el("brwBox");
      if (!box) return;
      if (!st || !st.ok) { box.innerHTML = "No pude comprobarlo."; return; }
      // Sharing a window is the one state where "connected" is true and the feature is
      // still broken, so it gets its own branch ahead of the happy one.
      var shared = (st.shared_with || []);
      if (st.mode === "cdp" && st.connected && shared.length) {
        box.innerHTML = '⚠️ <b>Comparte ventana</b> con: <b>' + esc(shared.join(", ")) +
          '</b>. Mientras estén juntos se pisan las pestañas: si los dos navegan a la vez, ' +
          'el último le cambia la página al otro. Pulsa <b>«Usar un navegador real»</b> ' +
          'para abrirle la suya.' +
          '<br><span class="muted">Ahora: puerto ' + esc(String(st.port || "")) + '</span>';
      } else if (st.mode === "cdp" && st.connected) {
        box.innerHTML = '✅ <b>Navegador real</b> — ' + esc(st.browser || "Chrome") +
          '. Esa ventana es suya y de nadie más; lo que abras ahí lo ve él.' +
          '<br><span class="muted">Perfil: ' + esc(st.data_dir || "") +
          ' · puerto ' + esc(String(st.port || "")) + '</span>';
      } else if (st.mode === "cdp") {
        box.innerHTML = '⚠️ Está configurado en navegador real, pero la ventana ' +
          '<b>ya no está abierta</b>. Pulsa «Usar un navegador real» para reabrirla.';
      } else if (!st.browser_found) {
        box.innerHTML = '🔍 Ahora usa un <b>navegador invisible</b>. No encontré Chrome, ' +
          'Edge ni Brave en este equipo, así que el modo real no está disponible.';
      } else {
        box.innerHTML = '🔍 Ahora usa un <b>navegador invisible</b>: puede leer y buscar, ' +
          'pero no tiene ninguna sesión iniciada. Para sitios donde haga falta entrar con ' +
          'tu cuenta, usa el navegador real.';
      }
    }
    function loadBrw() {
      api("browser/status", { profile: prof }).then(paintBrw);
      // Fast signals only (CLI on PATH + extension paired). The honest confirmation costs a
      // whole `claude -p` round trip, so it stays behind a button rather than making the
      // panel sit there for a minute.
      api("browser/delegation", {}).then(function (d) {
        var box = el("brwDeleg");
        if (!box || !d) return;
        box.innerHTML = (d.ready ? "✅ " : "○ ") + esc(d.detail || "");
      });
    }
    var brwDelegPill = el("brwDelegPill");
    if (el("brwDelegTest")) el("brwDelegTest").onclick = function () {
      brwDelegPill.style.display = "inline-flex";
      runTest(this, brwDelegPill, function () {
        return api("browser/delegation-test", {});
      }, "Preguntándole a Claude Code…");
    };
    if (el("brwOn")) el("brwOn").onclick = function () {
      brwPill.style.display = "inline-flex";
      runTest(this, brwPill, function () {
        return api("browser/enable", { profile: prof, name: targetName() });
      }, "Abriendo…").then(loadBrw);
    };
    if (el("brwOff")) el("brwOff").onclick = function () {
      brwPill.style.display = "inline-flex";
      runTest(this, brwPill, function () {
        return api("browser/disable", { profile: prof });
      }, "Cambiando…").then(loadBrw);
    };
    if (el("brwBox")) loadBrw();

    // ── agents talking to agents ───────────────────────────────────
    // Server-owned, like the conversation policy: the limits live in intercom.json, an
    // agent can be told to change them, and a value cached in this page would silently
    // put the old one back the next time the owner pressed Save.
    var icPill = el("icPill");
    function paintIc(st) {
      var box = el("icBox");
      if (!box) return;
      if (!st || !st.ok) { box.innerHTML = "No pude comprobarlo."; return; }
      var others = (st.agents || []).length;
      if (others < 2) {
        box.innerHTML = '👤 Por ahora sólo hay <b>un agente</b> en este equipo, ' +
          'así que no hay con quién hablar. En cuanto crees otro, se verán entre ellos.';
      } else if (st.enabled) {
        box.innerHTML = '✅ <b>Activado</b> — ' + others + ' agentes pueden preguntarse ' +
          'entre ellos: ' + (st.agents || []).map(function (a) {
            return '<b>' + esc(a.name) + '</b>' + (a.reachable ? '' : ' <span class="muted">(no alcanzable)</span>');
          }).join(' · ') + '.<br><span class="muted">Van ' + (st.quota ? st.quota.used : 0) +
          ' de ' + (st.quota ? st.quota.limit : 0) + ' llamadas esta hora.</span>';
      } else {
        box.innerHTML = '⛔ <b>Desactivado</b>. Tus agentes no pueden escribirse; si uno lo ' +
          'intenta, se le dice que no y ahí queda.';
      }
      if (el("icTurns")) el("icTurns").value = st.max_turns || 8;
      if (el("icHour")) el("icHour").value = st.hourly_limit || 30;
      var th = el("icThreads");
      if (th) {
        var rows = (st.threads || []);
        th.innerHTML = rows.length
          ? '<b class="small">Conversaciones recientes</b>' + rows.map(function (t) {
              return '<div class="row small" style="gap:8px;align-items:center">' +
                '<span class="muted">' + esc(t.from) + ' → ' + esc(t.to) + '</span>' +
                '<span class="muted">' + t.turns + ' turnos</span>' +
                '<a href="#" data-th="' + esc(t.id) + '" class="icShow">ver</a></div>';
            }).join("")
          : '';
        Array.prototype.forEach.call(th.querySelectorAll(".icShow"), function (a) {
          a.onclick = function (e) {
            e.preventDefault();
            api("intercom/thread", { thread: a.getAttribute("data-th") }).then(function (r) {
              var pre = document.createElement("pre");
              pre.className = "small";
              pre.style.cssText = "white-space:pre-wrap;max-height:300px;overflow:auto";
              pre.textContent = (r && r.text) || "";
              a.parentNode.parentNode.appendChild(pre);
              a.style.display = "none";
            });
          };
        });
      }
    }
    function loadIc() { api("intercom/status", {}).then(paintIc); }
    function icSave(patch, label) {
      icPill.style.display = "inline-flex";
      runTest(this, icPill, function () { return api("intercom/save", patch); },
              label || "Guardando\u2026").then(paintIc);
    }
    if (el("icOn")) el("icOn").onclick = function () {
      icSave.call(this, { enabled: true }, "Activando\u2026");
    };
    if (el("icOff")) el("icOff").onclick = function () {
      icSave.call(this, { enabled: false }, "Desactivando\u2026");
    };
    if (el("icSave")) el("icSave").onclick = function () {
      icSave.call(this, { max_turns: el("icTurns") ? el("icTurns").value : null,
                          hourly_limit: el("icHour") ? el("icHour").value : null });
    };
    if (el("icBox")) loadIc();

    // ── conversation lifetime ──────────────────────────────────────────────
    // Server-owned like the escalation panel, and for a stronger reason: the agent itself
    // can change this (tools/conversation_policy.py), so a value cached in the browser
    // would quietly overwrite a choice the agent made on the owner's behalf.
    var polPill = el("polPill");
    var POL = { policy: {}, defaults: {}, presets: [], preset: "", ctx: 0,
                configured: false, loaded: false };

    // Mirrors context_policy.trigger_tokens() so the summary shows the size Hermes will
    // ACTUALLY use: it never triggers under 64k tokens whatever the percentage says, and
    // raises the trigger to 75% for windows under 512k.
    function polTokens(pct, ctx) {
      if (!ctx || ctx <= 0) return 0;
      var p = ctx < 512000 ? Math.max(pct, 0.75) : pct;
      var v = Math.max(Math.floor(ctx * p), 64000);
      if (v >= ctx) return Math.max(1, Math.min(Math.floor(ctx * 0.85), ctx - 1));
      return v;
    }
    function polSay(p) {
      var mins = p.idle_minutes || 0, first;
      var when = mins < 60 ? (mins + " min")
        : (String(Math.round(mins / 6) / 10).replace(/\.0$/, "") + " h");
      var hh = ("0" + (p.at_hour || 0)).slice(-2) + ":00";
      if (p.mode === "both") first = "Empieza de cero tras " + when + " sin hablar, y cada día a las " + hh;
      else if (p.mode === "idle") first = "Empieza de cero tras " + when + " sin hablar";
      else if (p.mode === "daily") first = "Empieza de cero cada día a las " + hh;
      else first = "No empieza de cero nunca por su cuenta";
      if (!p.compact) return first + ". No resume: la conversación crece sin límite.";
      var t = polTokens(p.compact_at || 0.1, POL.ctx);
      return first + ". Resume al llegar a " +
        (t ? ("unos " + t.toLocaleString("es") + " tokens") :
             (Math.round((p.compact_at || 0) * 100) + "% de su memoria")) + ".";
    }
    function polPresetRow(pr) {
      return '<label class="row" style="gap:8px;align-items:flex-start;padding:6px 0;' +
        'border-bottom:1px solid var(--line-2)">' +
        '<input type="radio" name="polpreset" data-pol="' + esc(pr.id) + '"' +
        (POL.preset === pr.id ? " checked" : "") + '>' +
        '<span class="grow"><b class="small">' + esc(pr.label) + '</b><br>' +
        '<span class="muted small">' + esc(pr.note) + '</span></span></label>';
    }
    function paintPol() {
      if (!POL.loaded) return;
      var box = el("polPresets"), warn = el("polWarn");
      if (warn) {
        warn.innerHTML = POL.configured ? "" :
          '<div class="callout small">Este agente todavía usa los valores de fábrica de ' +
          'Hermes: <b>no empieza de cero nunca</b> y sólo resume al medio millón de tokens. ' +
          'Elige uno de abajo y guarda.</div>';
      }
      if (box) {
        box.innerHTML = POL.presets.map(polPresetRow).join("") +
          (POL.preset === "personalizado" ?
            '<div class="small muted" style="padding:6px 0">Ahora mismo tienes una ' +
            'combinación propia (ver «Ajustes finos»).</div>' : "");
        Array.prototype.forEach.call(box.querySelectorAll("[data-pol]"), function (r) {
          r.onchange = function () {
            POL.preset = r.getAttribute("data-pol");
            var found = null;
            POL.presets.forEach(function (p) { if (p.id === POL.preset) found = p; });
            if (found) POL.policy = polMerge(found.values);
            paintPol();
          };
        });
      }
      [["polMode", "mode"], ["polIdle", "idle_minutes"], ["polHour", "at_hour"]]
        .forEach(function (pr) { var i = el(pr[0]); if (i) i.value = POL.policy[pr[1]]; });
      if (el("polPct")) el("polPct").value = Math.round((POL.policy.compact_at || 0) * 100);
      var s = el("polSummary");
      if (s) s.innerHTML = "📋 " + esc(polSay(POL.policy));
    }
    // A preset names only what it changes, and the rest comes from Olivaw's defaults —
    // NOT from whatever is configured now. That is what context_policy.preset_policy()
    // does on the server and in the CLI, and this preview has to agree with it: a panel
    // that shows one thing and saves another is worse than no preview.
    function polMerge(values) {
      var out = {}, k;
      for (k in POL.defaults) if (POL.defaults.hasOwnProperty(k)) out[k] = POL.defaults[k];
      for (k in (values || {})) if (values.hasOwnProperty(k)) out[k] = values[k];
      return out;
    }
    function polRead() {
      var m = el("polMode"), i = el("polIdle"), h = el("polHour"), p = el("polPct");
      if (m) POL.policy.mode = m.value;
      if (i) POL.policy.idle_minutes = parseInt(i.value, 10) || POL.policy.idle_minutes;
      if (h) POL.policy.at_hour = parseInt(h.value, 10) || 0;
      if (p) POL.policy.compact_at = (parseFloat(p.value) || 10) / 100;
      POL.preset = "personalizado";
    }
    ["polMode", "polIdle", "polHour", "polPct"].forEach(function (id) {
      var i = el(id);
      if (i) i.onchange = function () { polRead(); var s = el("polSummary");
        if (s) s.innerHTML = "📋 " + esc(polSay(POL.policy)); };
    });
    function loadPol() {
      api("policy/get", { profile: prof }).then(function (r) {
        if (!r || !r.ok) return;
        POL.policy = r.policy || {}; POL.presets = r.presets || [];
        POL.defaults = r.defaults || {};
        POL.preset = r.preset || ""; POL.ctx = r.context_length || 0;
        POL.configured = !!r.configured; POL.loaded = true;
        paintPol();
      });
    }
    if (el("polSave")) el("polSave").onclick = function () {
      polPill.style.display = "inline-flex";
      // The gateway holds this setting in memory from boot, so saving restarts it — which
      // drops whatever turn is in flight. Say so before doing it, not after.
      runTest(this, polPill, function () {
        return api("policy/save", { profile: prof, preset: POL.preset, policy: POL.policy });
      }, "Guardando y reiniciando…").then(function () { loadPol(); });
    };
    if (el("polPresets")) loadPol();

    // Server-owned state, deliberately: the escalation script reads the same file, so the
    // browser must not keep its own idea of what is switched on.
    var escPill = el("escPill");
    var ESC = { catalog: [], enabled: false, reasons: [], custom: [], ready: true, detail: "" };
    var escSeq = 0;

    function escRow(item, isCustom) {
      var on = ESC.reasons.indexOf(item.key) >= 0;
      return '<label class="row" style="gap:8px;align-items:flex-start;padding:6px 0;' +
        'border-bottom:1px solid var(--line-2)">' +
        '<input type="checkbox" data-esc="' + esc(item.key) + '"' + (on ? " checked" : "") + '>' +
        '<span class="grow"><b class="small">' + esc(item.label) + '</b>' +
        (item.priority === "alta" ? ' <span class="chip">urgente</span>' : "") +
        (isCustom ? ' <span class="chip">tuyo</span>' : "") +
        '<br><span class="muted small">' + esc(item.description || "") + '</span></span>' +
        (isCustom ? '<button class="btn btn-ghost btn-sm" data-escdel="' + esc(item.key) +
          '" title="Quitar">✕</button>' : "") + '</label>';
    }

    function paintEsc() {
      var body = el("escBody"), list = el("escList"), warn = el("escWarn");
      if (el("escOn")) el("escOn").checked = !!ESC.enabled;
      if (body) body.style.display = ESC.enabled ? "block" : "none";
      if (warn) {
        warn.innerHTML = ESC.ready ? "" :
          '<div class="callout small">⚠️ ' + esc(ESC.detail || "") + '</div>';
      }
      if (!list) return;
      list.innerHTML = ESC.catalog.map(function (c) { return escRow(c, false); })
        .concat(ESC.custom.map(function (c) { return escRow(c, true); })).join("");
      Array.prototype.forEach.call(list.querySelectorAll("[data-esc]"), function (cb) {
        cb.onchange = function () {
          var k = cb.getAttribute("data-esc"), i = ESC.reasons.indexOf(k);
          if (cb.checked && i < 0) ESC.reasons.push(k);
          if (!cb.checked && i >= 0) ESC.reasons.splice(i, 1);
        };
      });
      Array.prototype.forEach.call(list.querySelectorAll("[data-escdel]"), function (b) {
        b.onclick = function (e2) {
          e2.preventDefault();
          var k = b.getAttribute("data-escdel");
          ESC.custom = ESC.custom.filter(function (c) { return c.key !== k; });
          ESC.reasons = ESC.reasons.filter(function (r) { return r !== k; });
          paintEsc();
        };
      });
    }

    function loadEsc() {
      api("channel/escalation-get", { profile: prof }).then(function (r) {
        if (!r || !r.ok) return;
        ESC.catalog = r.catalog || [];
        ESC.custom = (r.prefs && r.prefs.custom) || [];
        ESC.reasons = (r.prefs && r.prefs.reasons) || [];
        ESC.enabled = !!(r.prefs && r.prefs.enabled);
        ESC.ready = !!r.telegram_ready;
        ESC.detail = r.telegram_detail || "";
        paintEsc();
      });
    }

    if (el("escOn")) el("escOn").onchange = function () {
      ESC.enabled = this.checked;
      paintEsc();
    };

    if (el("escAdd")) el("escAdd").onclick = function () {
      var lab = (el("escNewLabel") || {}).value || "";
      var desc = (el("escNewDesc") || {}).value || "";
      var pri = (el("escNewPri") || {}).value || "media";
      if (!lab.trim() || !desc.trim()) {
        toast("Ponle un nombre y describe cuándo debe avisarte.");
        return;
      }
      // A provisional key so the checkbox has something to hang on; the server assigns
      // the real one when it saves, and loadEsc() replaces this with it.
      var key = "nuevo_" + (++escSeq);
      ESC.custom.push({ key: key, label: lab.trim(), description: desc.trim(), priority: pri });
      ESC.reasons.push(key);
      el("escNewLabel").value = ""; el("escNewDesc").value = "";
      paintEsc();
    };

    if (el("escSave")) el("escSave").onclick = function () {
      escPill.style.display = "inline-flex";
      var custom = ESC.custom.map(function (c) {
        return { key: (String(c.key).indexOf("nuevo_") === 0 ? "" : c.key),
                 label: c.label, description: c.description, priority: c.priority,
                 selected: ESC.reasons.indexOf(c.key) >= 0 };
      });
      var builtin = ESC.reasons.filter(function (k) {
        return ESC.catalog.some(function (c) { return c.key === k; });
      });
      runTest(this, escPill, function () {
        return api("channel/escalation-save",
                   { profile: prof, enabled: ESC.enabled, reasons: builtin, custom: custom });
      }, "Guardando…").then(function () { loadEsc(); });
    };

    if (el("escList")) loadEsc();

    // Google Workspace (Gmail platform + Google Chat)
    var gwPill = el("gwPill"), gp = el("gwProv");
    function applyGw() {
      var o = (META.google_presets || {})[S.gw_prov || "gmail"];
      if (!o) return;
      if ((S.gw_prov || "gmail") !== "other") { S.gw_smtp = o.smtp_host; S.gw_imap = o.imap_host; }
      if (el("gwSmtp")) el("gwSmtp").value = S.gw_smtp || "";
      if (el("gwImap")) el("gwImap").value = S.gw_imap || "";
      var n = el("gwNote");
      if (n) n.innerHTML = esc(o.note || "") + (o.link ? ' <a href="' + esc(o.link) +
        '" target="_blank" rel="noopener noreferrer">crear contraseña de aplicación</a>' : "");
      save();
    }
    if (gp) { gp.onchange = function () { S.gw_prov = gp.value; applyGw(); }; if (!S.gw_prov) S.gw_prov = "gmail"; applyGw(); }
    [["gwAddr", "gw_addr"], ["gwSmtp", "gw_smtp"], ["gwImap", "gw_imap"], ["gwUsers", "gw_users"]].forEach(function (pr) {
      var i = el(pr[0]); if (i) i.oninput = function () { S[pr[1]] = i.value; save(); };
    });
    if (el("gwSave")) el("gwSave").onclick = function () {
      gwPill.style.display = "inline-flex";
      runTest(this, gwPill, function () {
        return api("channel/email-platform-save", {
          profile: prof, address: S.gw_addr || "", password: (el("gwPass") || {}).value || "",
          smtp_host: S.gw_smtp || "", imap_host: S.gw_imap || "",
          allowed_users: S.gw_users || S.gw_addr || ""
        });
      }, "Conectando…").then(function (r) { if (r && r.ok) toast("Correo conectado ✉️"); });
    };
    if (el("gcSave")) el("gcSave").onclick = function () {
      gwPill.style.display = "inline-flex";
      runTest(this, gwPill, function () {
        return api("channel/gchat-save", { profile: prof,
          service_account: (el("gcJson") || {}).value || "",
          allowed_users: (el("gcUsers") || {}).value || "" });
      }, "Conectando…");
    };

    var slackPill = el("slackPill");
    if (el("slackMan")) el("slackMan").onclick = function () {
      var b = this; b.disabled = true;
      api("channel/slack-manifest", { profile: prof }).then(function (r) {
        b.disabled = false;
        if (r && r.ok) { el("slackManWrap").style.display = "block"; el("slackManTxt").textContent = r.manifest; }
        else { slackPill.style.display = "inline-flex"; slackPill.className = "pill err"; slackPill.textContent = "✕ " + (r && r.detail); }
      });
    };
    if (el("slackSetup")) el("slackSetup").onclick = function () {
      slackPill.style.display = "inline-flex";
      runTest(this, slackPill, function () { return api("channel/slack-setup", { profile: prof }); }, "Abriendo…");
    };

    var whName = el("whName"), whDesc = el("whDesc"), whPill = el("whPill");
    if (whName) whName.oninput = function () { S.wh_name = whName.value.trim(); save(); };
    if (whDesc) whDesc.oninput = function () { S.wh_desc = whDesc.value; save(); };
    if (el("whAdd")) el("whAdd").onclick = function () {
      whPill.style.display = "inline-flex";
      runTest(this, whPill, function () {
        return api("channel/webhook-add", { profile: prof, name: S.wh_name, description: S.wh_desc, deliver: "telegram" });
      }, "Creando…");
    };
    if (el("whTest")) el("whTest").onclick = function () {
      whPill.style.display = "inline-flex";
      runTest(this, whPill, function () { return api("channel/webhook-test", { profile: prof, name: S.wh_name }); });
    };

    // Email
    var prov = el("smtpProv");
    function applyProvider() {
      var p = (META.smtp_providers || []).filter(function (x) { return x.id === S.smtp.provider; })[0];
      if (!p) return;
      if (p.id !== "other") { S.smtp.host = p.host; S.smtp.port = p.port; S.smtp.secure = p.secure; }
      if (el("smtpHost")) el("smtpHost").value = S.smtp.host || "";
      if (el("smtpPort")) el("smtpPort").value = S.smtp.port || 587;
      var note = el("smtpNote");
      if (note) note.innerHTML = esc(p.note || "") + (p.link ? ' <a href="' + esc(p.link) + '" target="_blank" rel="noopener noreferrer">obtener contraseña</a>' : "");
      save();
    }
    if (prov) { prov.onchange = function () { S.smtp.provider = prov.value; applyProvider(); }; applyProvider(); }
    [["smtpHost", "host"], ["smtpPort", "port"], ["smtpUser", "user"], ["smtpPass", "password"], ["smtpTo", "to_addr"]].forEach(function (pair) {
      var inp = el(pair[0]); if (inp) inp.oninput = function () { S.smtp[pair[1]] = inp.value; save(); };
    });
    var smtpPill = el("smtpPill");
    function smtpBody() {
      return { profile: prof, workspace: ws, host: S.smtp.host, port: S.smtp.port, user: S.smtp.user,
               password: S.smtp.password, from_addr: S.smtp.user, secure: S.smtp.secure, to_addr: S.smtp.to_addr };
    }
    if (el("smtpTest")) el("smtpTest").onclick = function () {
      smtpPill.style.display = "inline-flex";
      runTest(this, smtpPill, function () { return api("channel/email-test", smtpBody()); }, "Enviando…");
    };
    if (el("smtpSave")) el("smtpSave").onclick = function () {
      smtpPill.style.display = "inline-flex";
      runTest(this, smtpPill, function () { return api("channel/email-save", smtpBody()); }, "Guardando…")
        .then(function (r) { if (r && r.ok) toast("Correo configurado ✉️"); });
    };
  }

  // ── SOS console (an overlay, NOT a wizard step) ─────────────────────────────
  // The escape hatch: when the bridge/Hermes is down, Telegram is dead, so the owner can
  // still reach Claude Code from here — with the installation snapshot attached. We stream
  // every event (thinking, tool calls, tool results) so it's visible that it IS working.
  // Conversations live on the SERVER (each backed by a real Claude Code session), so they can
  // be reopened later and continued with the context still in place.
  var LIVE = null;   // transient: {question, events:[], done:false, reply:"", cursor:0}
  var SOS = { open: false, conv: null, turns: [], list: [] };

  // ── markdown → HTML ─────────────────────────────────────────────────────────
  // Claude answers in markdown; showing it raw ("**Terminus-8823**", ``` fences) reads as
  // broken to a non-technical owner. This is a deliberately small renderer: the source is
  // HTML-escaped FIRST and no raw HTML is ever passed through, so a reply — or a log line
  // quoted inside one — cannot inject markup. Links are limited to http(s).
  function mdHtml(src) {
    var text = String(src == null ? "" : src).replace(/\r\n/g, "\n");
    var fences = [], codes = [];

    // fenced blocks first, so nothing inside them gets markdown treatment
    text = text.replace(/```[ \t]*[\w+#.-]*[ \t]*\n([\s\S]*?)```/g, function (m, code) {
      fences.push('<pre class="md-pre"><code>' + esc(code.replace(/\n+$/, "")) + "</code></pre>");
      return "\u0001F" + (fences.length - 1) + "\u0001";
    });
    text = esc(text);                       // everything below operates on safe text
    text = text.replace(/`([^`\n]+)`/g, function (m, c) {
      codes.push("<code>" + c + "</code>");
      return "\u0001C" + (codes.length - 1) + "\u0001";
    });

    function inline(s) {
      return s
        .replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g,
          '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
        .replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g,
          '$1<a href="$2" target="_blank" rel="noopener noreferrer">$2</a>')
        .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
        .replace(/__([^_\n]+)__/g, "<strong>$1</strong>")
        .replace(/(^|[\s(])\*([^*\n]+)\*/g, "$1<em>$2</em>")
        .replace(/~~([^~\n]+)~~/g, "<del>$1</del>");
    }

    var lines = text.split("\n"), out = [], para = [], i = 0;
    function flush() {
      if (para.length) out.push("<p>" + inline(para.join("<br>")) + "</p>");
      para = [];
    }
    while (i < lines.length) {
      var ln = lines[i], t = ln.trim();
      if (/^\u0001F\d+\u0001$/.test(t)) { flush(); out.push(t); i++; continue; }
      var h = /^(#{1,6})\s+(.*)$/.exec(t);
      if (h) {
        flush();
        var lvl = h[1].length <= 1 ? 3 : 4;
        out.push("<h" + lvl + ">" + inline(h[2]) + "</h" + lvl + ">");
        i++; continue;
      }
      if (/^(?:[-*_]\s*){3,}$/.test(t)) { flush(); out.push("<hr>"); i++; continue; }
      if (/^&gt;\s?/.test(t)) {
        flush();
        var quote = [];
        while (i < lines.length && /^\s*&gt;\s?/.test(lines[i])) {
          quote.push(lines[i].replace(/^\s*&gt;\s?/, "")); i++;
        }
        out.push("<blockquote>" + inline(quote.join("<br>")) + "</blockquote>");
        continue;
      }
      var isUl = /^[-*•]\s+\S/.test(t), isOl = /^\d{1,2}[.)]\s+\S/.test(t);
      if (isUl || isOl) {
        flush();
        var items = [], tag = isUl ? "ul" : "ol";
        while (i < lines.length) {
          var lt = lines[i].trim();
          var m = isUl ? /^[-*•]\s+(.*)$/.exec(lt) : /^\d{1,2}[.)]\s+(.*)$/.exec(lt);
          if (!m) {
            // a wrapped continuation line belongs to the item above it
            if (items.length && lt && !/^([-*•]|\d{1,2}[.)])\s/.test(lt) &&
                /^\s{2,}/.test(lines[i])) {
              items[items.length - 1] += " " + lt; i++; continue;
            }
            break;
          }
          items.push(m[1]); i++;
        }
        out.push("<" + tag + ">" + items.map(function (x) {
          return "<li>" + inline(x) + "</li>";
        }).join("") + "</" + tag + ">");
        continue;
      }
      if (!t) { flush(); i++; continue; }
      para.push(t); i++;
    }
    flush();

    return out.join("")
      .replace(/\u0001C(\d+)\u0001/g, function (m, n) { return codes[+n] || ""; })
      .replace(/\u0001F(\d+)\u0001/g, function (m, n) { return fences[+n] || ""; });
  }

  // ── "Claude is asking you something" → an answer form ───────────────────────
  // A reply can end in up to four questions (see rescue.parse_ask). Each becomes buttons —
  // single- or multi-select — and the owner can go past just picking: type a different answer,
  // attach a comment to any option they picked, and leave one general comment about the whole
  // set. What we send back is the exact option wording plus those comments, so Claude gets an
  // unambiguous answer and the owner never has to retype anything.
  function normalizeAsk(a) {
    if (!a) return null;
    var qs = a.questions;
    // turns stored before multi-question support carry the flat single-question shape
    if (!qs && a.options) {
      qs = [{ id: "q1", header: "", question: a.question, options: a.options,
              multi: !!a.multi, allow_free: a.allow_free !== false }];
    }
    qs = (qs || []).filter(function (q) { return q && (q.options || []).length; });
    if (!qs.length) return null;
    return { questions: qs, allow_general: a.allow_general !== false };
  }

  function askHtml(ask, interactive) {
    var a = normalizeAsk(ask);
    if (!a) return "";
    var many = a.questions.length > 1;
    var body = a.questions.map(function (q, qi) {
      var opts = (q.options || []).map(function (o, oi) {
        return '<button type="button" class="ask-opt" data-oi="' + oi + '"' +
          (interactive ? "" : " disabled") + '>' +
          '<span class="ask-opt-lbl">' + esc(o.label) + '</span>' +
          (o.detail ? '<span class="ask-opt-detail">' + esc(o.detail) + '</span>' : '') +
          '</button>';
      }).join("");
      // One comment row per option, kept in the DOM and revealed when that option is picked,
      // so switching selections never loses what was already typed.
      var notes = (q.options || []).map(function (o, oi) {
        return '<label class="ask-note" data-oi="' + oi + '" hidden>' +
          '<span class="ask-note-for">' + esc(o.label) + '</span>' +
          '<input type="text" placeholder="comentario sobre esta opción (opcional)"></label>';
      }).join("");
      return '<div class="ask-q" data-qi="' + qi + '" data-multi="' + (q.multi ? 1 : 0) + '">' +
        '<div class="ask-q-head">' +
        (many ? '<span class="ask-num">' + (qi + 1) + '</span>' : '') +
        (q.header ? '<span class="ask-chip">' + esc(q.header) + '</span>' : '') +
        '<b>' + esc(q.question || "¿Cómo quieres seguir?") + '</b>' +
        (q.multi ? ' <span class="muted small">· puedes elegir varias</span>' : '') +
        '</div><div class="ask-opts">' + opts + '</div>' +
        (interactive ? '<div class="ask-notes">' + notes + '</div>' +
          (q.allow_free === false ? '' :
            '<input class="ask-free" type="text" placeholder="…o escribe otra respuesta">') : '') +
        '</div>';
    }).join("");

    return '<div class="ask-tool' + (interactive ? '' : ' ask-done') + '">' +
      '<div class="ask-head">🤔 <b>' +
      (many ? "Claude te pregunta " + a.questions.length + " cosas" : "Claude te pregunta") +
      '</b></div>' + body +
      (interactive ? (
        (a.allow_general ? '<label class="ask-general"><span class="lab muted small">' +
          'Comentario general (opcional)</span>' +
          '<textarea rows="2" placeholder="Algo que quieras añadir sobre todo esto…">' +
          '</textarea></label>' : '') +
        '<div class="ask-actions">' +
        '<button class="btn btn-primary btn-sm ask-send">' +
        (many ? "Enviar respuestas" : "Enviar respuesta") + '</button>' +
        '<span class="muted small">Elige y, si quieres, añade comentarios. Ctrl+Enter envía.' +
        '</span></div>') : '') +
      '</div>';
  }

  // Pure function (no DOM) so the exact text Claude receives is easy to reason about and test.
  function composeAnswer(ask, answers) {
    var a = normalizeAsk(ask);
    if (!a) return "";
    var general = (answers.general || "").trim();
    var qs = a.questions;
    var answered = qs.map(function (q, qi) { return answers.questions[qi] || {}; });

    // The common case — one question, one option, nothing else — stays a plain sentence.
    if (qs.length === 1 && !general) {
      var one = answered[0];
      var picks = one.picks || [];
      if (picks.length === 1 && !(picks[0].note || "").trim() && !(one.free || "").trim()) {
        return picks[0].label;
      }
    }

    var lines = [qs.length > 1 ? "Respondo a tus preguntas:" : "Mi respuesta:"];
    qs.forEach(function (q, qi) {
      var ans = answered[qi];
      var picks = (ans.picks || []).filter(function (p) { return p.label; });
      var free = (ans.free || "").trim();
      lines.push("");
      lines.push((qs.length > 1 ? (qi + 1) + ") " : "") +
        (q.header ? "[" + q.header + "] " : "") + q.question);
      if (!picks.length && !free) {
        lines.push("   (sin responder)");
        return;
      }
      picks.forEach(function (p) {
        var note = (p.note || "").trim();
        lines.push("   - " + p.label + (note ? "  (comentario: " + note + ")" : ""));
      });
      if (free) lines.push("   - otra respuesta: " + free);
    });
    if (general) {
      lines.push("");
      lines.push("Comentario general: " + general);
    }
    return lines.join("\n");
  }

  function wireAsk() {
    var tool = document.querySelector("#rescueMsgs .ask-tool:not(.ask-done)");
    if (!tool || !SOS.conv) return;
    var ask = normalizeAsk(LIVE ? LIVE.ask
      : ((SOS.turns || []).length ? SOS.turns[SOS.turns.length - 1].ask : null));
    if (!ask) return;

    function syncNotes(qEl) {
      Array.prototype.forEach.call(qEl.querySelectorAll(".ask-note"), function (n) {
        var oi = n.getAttribute("data-oi");
        var opt = qEl.querySelector('.ask-opt[data-oi="' + oi + '"]');
        if (opt && opt.classList.contains("sel")) n.removeAttribute("hidden");
        else n.setAttribute("hidden", "");      // text is kept, just not sent
      });
    }

    function collect() {
      var out = { questions: [], general: "" };
      var gen = tool.querySelector(".ask-general textarea");
      out.general = gen ? gen.value : "";
      Array.prototype.forEach.call(tool.querySelectorAll(".ask-q"), function (qEl) {
        var picks = [];
        Array.prototype.forEach.call(qEl.querySelectorAll(".ask-opt.sel"), function (b) {
          var oi = b.getAttribute("data-oi");
          var note = qEl.querySelector('.ask-note[data-oi="' + oi + '"] input');
          picks.push({ label: b.querySelector(".ask-opt-lbl").textContent,
                       note: note ? note.value : "" });
        });
        var free = qEl.querySelector(".ask-free");
        out.questions.push({ picks: picks, free: free ? free.value : "" });
      });
      return out;
    }

    function send() {
      if (tool.classList.contains("ask-done")) return;
      var answers = collect();
      var got = answers.questions.some(function (q) {
        return (q.picks || []).length || (q.free || "").trim();
      });
      if (!got) { toast("Elige una opción o escribe tu respuesta."); return; }
      var msg = composeAnswer(ask, answers);
      if (!msg.trim()) { toast("No pude leer tu respuesta."); return; }
      tool.classList.add("ask-done");
      Array.prototype.forEach.call(tool.querySelectorAll("button,input,textarea"),
        function (n) { n.disabled = true; });
      sendTurn(msg);
    }

    Array.prototype.forEach.call(tool.querySelectorAll(".ask-q"), function (qEl) {
      var multi = qEl.getAttribute("data-multi") === "1";
      Array.prototype.forEach.call(qEl.querySelectorAll(".ask-opt"), function (b) {
        b.onclick = function () {
          if (multi) {
            b.classList.toggle("sel");
          } else {
            var was = b.classList.contains("sel");
            Array.prototype.forEach.call(qEl.querySelectorAll(".ask-opt"),
              function (x) { x.classList.remove("sel"); });
            if (!was) b.classList.add("sel");     // clicking the pick again clears it
          }
          syncNotes(qEl);
          var sb = tool.querySelector(".ask-send");
          if (sb) sb.classList.add("ask-ready");
        };
      });
      syncNotes(qEl);
    });

    var sb = tool.querySelector(".ask-send");
    if (sb) sb.onclick = send;
    // Enter on a one-line field sends; Ctrl+Enter sends from anywhere in the form.
    Array.prototype.forEach.call(tool.querySelectorAll('input[type="text"]'), function (inp) {
      inp.onkeydown = function (ev) {
        if (ev.key === "Enter") { ev.preventDefault(); send(); }
      };
    });
    tool.onkeydown = function (ev) {
      if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) { ev.preventDefault(); send(); }
    };
  }

  function evHtml(e) {
    var k = e.kind;
    if (k === "system") {
      return '<div class="ev ev-sys">⚙️ ' + esc(e.text) + '</div>';
    }
    if (k === "thinking") {
      return '<details class="ev ev-think" open><summary>💭 Razonando…</summary>' +
        '<div class="ev-body">' + esc(e.text) + '</div></details>';
    }
    if (k === "tool") {
      return '<div class="ev ev-tool">🔧 <b>' + esc(e.name || "herramienta") + '</b> ' +
        '<code>' + esc(e.text) + '</code></div>';
    }
    if (k === "tool_result") {
      return '<details class="ev ev-res"><summary>📄 resultado</summary>' +
        '<pre class="ev-body">' + esc(e.text) + '</pre></details>';
    }
    if (k === "text") {
      return '<div class="ev ev-text md">' + mdHtml(e.text) + '</div>';
    }
    if (k === "done") { return '<div class="ev ev-sys">✓ ' + esc(e.text) + '</div>'; }
    if (k === "error") { return '<div class="ev ev-err">✕ ' + esc(e.text) + '</div>'; }
    return '';
  }

  function turnHtml(t, interactive) {
    var out = '<div class="row" style="justify-content:flex-end">' +
      '<div class="card" style="max-width:82%;margin:0 0 10px;background:var(--panel-2)">' +
      '<div class="muted small" style="margin-bottom:4px">Tú</div>' +
      '<div style="white-space:pre-wrap">' + esc(t.question) + '</div></div></div>';
    // The model's answer arrives twice: streamed as `text` events, then again as the final
    // `reply`. Render it once — while streaming show the text events; once the reply is in,
    // drop the text events it already contains so the answer isn't duplicated.
    var reply = (t.reply || "").trim();
    var evs = (t.events || []).filter(function (e) {
      if (!reply || e.kind !== "text") return true;
      var txt = (e.text || "").trim();
      return !txt || reply.indexOf(txt) < 0;
    }).map(evHtml).join("");
    out += '<div class="card" style="margin:0 0 14px">' +
      '<div class="muted small" style="margin-bottom:6px">🤖 Claude' +
      (t.mode === "fix" ? ' <span class="badge">modo arreglo</span>' : '') + '</div>' +
      (evs ? '<div class="ev-list">' + evs + '</div>' : '') +
      (t.reply ? '<div class="ev-final md">' + mdHtml(t.reply) + '</div>' :
        (t.done ? '' : '<div class="ev ev-sys"><span class="spinner"></span> trabajando…</div>')) +
      askHtml(t.ask, !!interactive) +
      '</div>';
    return out;
  }

  function whenStr(ts) {
    if (!ts) return "";
    var d = new Date(ts * 1000), now = new Date();
    var same = d.toDateString() === now.toDateString();
    return same ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
                : d.toLocaleDateString([], { day: "2-digit", month: "short" });
  }

  function paintList() {
    var host = el("sosList");
    if (!host) return;
    if (!SOS.list.length) {
      host.innerHTML = '<div class="muted small" style="padding:14px 12px">Aún no hay ' +
        'conversaciones. Pregunta algo y quedará guardada aquí.</div>';
      return;
    }
    host.innerHTML = SOS.list.map(function (c) {
      var active = SOS.conv && SOS.conv.id === c.id;
      return '<div class="convo' + (active ? " active" : "") + '" data-id="' + esc(c.id) + '">' +
        '<div class="convo-top"><span class="convo-title">' + esc(c.title) + '</span>' +
        '<span class="convo-when">' + esc(whenStr(c.updated)) + '</span></div>' +
        '<div class="convo-sub">' + (c.archived ? "📦 archivo · " : "") +
        esc(String(c.turns)) + (c.turns === 1 ? " mensaje" : " mensajes") +
        (c.preview ? ' · ' + esc(c.preview.slice(0, 46)) : '') + '</div>' +
        '<button class="convo-del" data-del="' + esc(c.id) + '" title="Borrar">✕</button></div>';
    }).join("");
    Array.prototype.forEach.call(host.querySelectorAll(".convo"), function (node) {
      node.onclick = function (ev) {
        if (ev.target && ev.target.getAttribute("data-del")) return;
        openConv(node.getAttribute("data-id"));
      };
    });
    Array.prototype.forEach.call(host.querySelectorAll("[data-del]"), function (b) {
      b.onclick = function (ev) {
        ev.stopPropagation();
        var id = b.getAttribute("data-del");
        api("rescue/delete", { id: id }).then(function (r) {
          if (!r || !r.ok) { toast((r && r.detail) || "No pude borrarla."); return; }
          if (SOS.conv && SOS.conv.id === id) { SOS.conv = null; SOS.turns = []; paintMsgs(); }
          loadList();
        });
      };
    });
  }

  function loadList() {
    return api("rescue/conversations", { limit: 40 }).then(function (r) {
      SOS.list = (r && r.ok && r.conversations) || [];
      paintList();
      return SOS.list;
    });
  }

  function paintMsgs(force) {
    var msgs = el("rescueMsgs");
    var stick = force || atBottom();
    var turns = SOS.turns || [];
    var live = !!LIVE, archived = !!(SOS.conv && SOS.conv.archived);
    if (msgs) {
      msgs.innerHTML = turns.map(function (t, i) {
        return turnHtml(t, i === turns.length - 1 && !live && !archived);
      }).join("");
    }
    var head = el("sosConvTitle");
    if (head) {
      head.textContent = SOS.conv ? (SOS.conv.title || "Conversación")
                                  : "Nueva conversación";
    }
    var badge = el("sosConvBadge");
    if (badge) {
      var arch = SOS.conv && SOS.conv.archived;
      badge.innerHTML = SOS.conv
        ? (arch ? '<span class="badge">solo lectura</span>'
                : '<span class="muted small">Olivaw recuerda esta conversación</span>')
        : '<span class="muted small">Cuéntame qué está pasando</span>';
    }
    var comp = el("sosCompose");
    if (comp) comp.style.display = (SOS.conv && SOS.conv.archived) ? "none" : "";
    wireAsk();
    paintLive(stick);
  }

  function paintLive(force) {
    var host = el("rescueLive");
    if (!host) return;
    var stick = force || atBottom();
    host.innerHTML = LIVE ? turnHtml(LIVE) : "";
    if (stick) scrollDown();
  }

  // "Following the conversation" means parked at (or within 1% of) the bottom. If the owner
  // scrolled up to read something, new events must not drag them back down.
  function atBottom() {
    var sc = el("sosScroll");
    if (!sc) return true;
    var slack = Math.max(24, sc.scrollHeight * 0.01);
    return (sc.scrollHeight - sc.scrollTop - sc.clientHeight) <= slack;
  }

  function scrollDown() {
    var sc = el("sosScroll");
    if (sc) sc.scrollTop = sc.scrollHeight;
  }

  function openConv(id) {
    api("rescue/conversation", { id: id }).then(function (r) {
      if (!r || !r.ok) { toast((r && r.detail) || "No pude abrirla."); loadList(); return; }
      SOS.conv = r.conversation;
      SOS.turns = r.conversation.turns || [];
      paintList(); paintMsgs(true); scrollDown();
    });
  }

  function newConv() {
    SOS.conv = null; SOS.turns = [];
    paintList(); paintMsgs();
    var q = el("rescueQ"); if (q) { q.value = ""; q.focus(); }
  }

  function loadStatus() {
    var box = el("rescueStatus");
    if (!box) return;
    api("rescue/context", {}).then(function (c) {
      if (!box) return;
      if (!c || !c.ok) { box.innerHTML = '<span class="muted small">No pude leer el estado.</span>'; return; }
      // Name the brain this install actually runs, everywhere the console mentions it.
      paintSosLabels(c.engine === "codex" ? "Codex" : "Claude Code");
      var b = (c.bridges || []).map(function (x) {
        return '<span class="pill ' + (x.up ? "ok" : "err") + '" style="margin:0 6px 0 0">' +
          (x.up ? "✓" : "✕") + " puente :" + esc(x.port) + '</span>';
      }).join("");
      box.innerHTML = '<div class="row" style="flex-wrap:wrap;gap:6px">' + b +
        '<span class="pill ' + (c.hermes_installed ? "ok" : "err") + '" style="margin:0">' +
        (c.hermes_installed ? "✓" : "✕") + ' Hermes</span>' +
        (function () {
          var cx = c.engine === "codex";
          var okCli = cx ? c.codex_installed : c.claude_installed;
          return '<span class="pill ' + (okCli ? "ok" : "err") + '" style="margin:0">' +
            (okCli ? "✓" : "✕") + (cx ? " Codex" : " Claude") + '</span>';
        })() +
        '<span class="muted small" style="margin-left:auto">v' + esc(c.version || "?") + '</span></div>' +
        (c.bridge_down ? '<div class="callout warn small" style="margin:10px 0 0">El puente no responde: ' +
          'por eso tu agente no contesta en Telegram. Pregunta abajo y te digo cómo revivirlo.</div>' : "");
    });
  }

  function pollLive() {
    if (!LIVE || !LIVE.job_id) return;
    api("rescue/poll", { job_id: LIVE.job_id, cursor: LIVE.cursor || 0 }).then(function (r) {
      if (!LIVE) return;
      if (!r || !r.ok) {
        LIVE.events.push({ kind: "error", text: (r && r.detail) || "Se perdió la consulta." });
        LIVE.done = true; paintLive(); finishLive(); return;
      }
      (r.events || []).forEach(function (e) { LIVE.events.push(e); });
      LIVE.cursor = r.cursor || LIVE.cursor;
      if (r.reply) LIVE.reply = r.reply;
      if (r.ask) LIVE.ask = r.ask;
      LIVE.done = !!r.done;
      paintLive();
      if (!LIVE.done) setTimeout(pollLive, 800); else finishLive();
    }).catch(function (e) {
      if (!LIVE) return;
      LIVE.events.push({ kind: "error", text: String(e) });
      LIVE.done = true; paintLive(); finishLive();
    });
  }

  function finishLive() {
    if (!LIVE) return;
    var cid = LIVE.conversation_id;
    // Adopt the finished turn locally FIRST. This used to null LIVE - the only copy of the
    // answer on screen - and then hope rescue/conversation handed it back. When that call
    // failed, or simply ran before the turn had been written, the question and the answer
    // vanished with nothing left to reopen. Reported as "the response arrived and then the
    // message was deleted". The reload below is now a reconciliation, never the only copy.
    var justDone = { ts: Date.now() / 1000, question: LIVE.question, mode: LIVE.mode,
                     reply: LIVE.reply || "", ask: LIVE.ask || null,
                     events: LIVE.events || [] };
    LIVE = null;
    SOS.turns = (SOS.turns || []).concat([justDone]);
    paintMsgs(true);
    var sb = el("rescueSend"); if (sb) { sb.disabled = false; sb.textContent = "Preguntar"; }
    if (!cid) { loadList(); return; }
    // The server owns the transcript, so prefer its version - but only when it actually has
    // one at least as complete as what we are holding. A short answer must never overwrite
    // a longer transcript.
    api("rescue/conversation", { id: cid }).then(function (r) {
      var stick = atBottom();
      var srv = (r && r.ok && r.conversation) ? r.conversation : null;
      if (srv && (srv.turns || []).length >= (SOS.turns || []).length) {
        SOS.conv = srv;
        SOS.turns = srv.turns || [];
      } else if (srv) {
        // Keep our turns; take the metadata (title, session, archived flag).
        var mine = SOS.turns;
        SOS.conv = srv;
        SOS.turns = mine;
      }
      paintMsgs(stick); loadList(); if (stick) scrollDown();
    }).catch(function () { loadList(); });
  }

  function askSos() {
    var qEl = el("rescueQ");
    var q = (qEl && qEl.value) || "";
    if (!q.trim()) { toast("Escribe tu pregunta."); return; }
    if (qEl) qEl.value = "";
    sendTurn(q);
  }

  function sendTurn(q) {
    if (!q || !q.trim()) return;
    if (LIVE) { toast("Espera a que termine la consulta anterior."); return; }
    var fix = !!(el("rescueFix") && el("rescueFix").checked);
    var sb = el("rescueSend");
    if (sb) { sb.disabled = true; sb.textContent = "Trabajando…"; }
    LIVE = { question: q, mode: fix ? "fix" : "diagnose", events: [], cursor: 0,
             reply: "", done: false, job_id: null,
             conversation_id: SOS.conv ? SOS.conv.id : "" };
    paintLive(true);
    api("rescue/start", { question: q, allow_fix: fix,
                          conversation_id: SOS.conv ? SOS.conv.id : "" })
      .then(function (r) {
        if (!LIVE) return;
        if (!r || !r.ok) {
          LIVE.events.push({ kind: "error", text: (r && r.detail) || "No pude iniciar." });
          LIVE.done = true; paintLive(); finishLive(); return;
        }
        LIVE.job_id = r.job_id;
        LIVE.mode = r.mode || LIVE.mode;
        LIVE.conversation_id = r.conversation_id || LIVE.conversation_id;
        if (!SOS.conv || SOS.conv.id !== r.conversation_id) {
          SOS.conv = { id: r.conversation_id, title: r.title || "Conversación" };
          SOS.turns = SOS.turns || [];
          paintMsgs(); loadList();
        }
        pollLive();
      });
  }

  // This screen is Olivaw talking to its owner, so it says Olivaw - on a Claude machine and
  // on a Codex one alike. It used to introduce itself by the brain's brand name, which on a
  // Codex install made the help screen present itself as somebody else's product, and in
  // either case named something the owner never chose to talk to. The brain is still stated
  // where it is a technical fact: the event stream reports which CLI actually ran, and the
  // "cerebro" section is where it can be changed.
  function paintSosLabels(brain) {
    var sub = el("sosSub");
    if (sub) {
      sub.textContent = "Habla con Olivaw sobre tu instalación — sin pasar por tu agente";
    }
    Array.prototype.forEach.call(document.querySelectorAll("[data-brain-name]"), function (n) {
      n.textContent = "Habla con Olivaw";
    });
    var fab = el("sosFab");
    // The tooltip is the one place the brain stays visible: useful when diagnosing, and it
    // is not the label anybody reads first.
    if (fab) {
      fab.title = "¿Algo falla? Habla con Olivaw" + (brain ? " (motor: " + brain + ")" : "");
    }
    var foot = el("sosSideFoot");
    if (foot) {
      foot.textContent = "Se guardan en tu equipo y Olivaw mantiene el contexto: " +
        "puedes retomarlas cuando quieras.";
    }
  }

  function openSos(convId) {
    var sos = el("sos");
    if (!sos) return;
    SOS.open = true;
    sos.hidden = false;
    document.body.classList.add("sos-open");
    document.documentElement.style.overflow = "hidden";
    loadStatus();
    loadList().then(function (list) {
      var want = convId || (SOS.conv && SOS.conv.id);
      if (want) { openConv(want); return; }
      // Reopen where the owner left off — like coming back to a Claude Code session.
      var first = list.filter(function (c) { return !c.archived; })[0];
      if (first) openConv(first.id); else { paintMsgs(); }
    });
    var q = el("rescueQ"); if (q) setTimeout(function () { q.focus(); }, 60);
  }

  function closeSos() {
    var sos = el("sos");
    if (!sos) return;
    SOS.open = false;
    sos.hidden = true;
    document.body.classList.remove("sos-open");
    document.documentElement.style.overflow = "";
    if (location.hash) {
      try { history.replaceState(null, "", location.pathname + location.search); }
      catch (e) { location.hash = ""; }
    }
  }

  (function wireSos() {
    var hb = el("helpBtn");
    if (hb) hb.onclick = function () { openSos(); };
    // the floating twin, for when the sidebar is hidden
    var fab = el("sosFab");
    if (fab) fab.onclick = function () { openSos(); };
    if (el("sosClose")) el("sosClose").onclick = closeSos;
    if (el("sosNew")) el("sosNew").onclick = newConv;
    var send = el("rescueSend");
    if (send) send.onclick = askSos;
    var qbox = el("rescueQ");
    if (qbox) qbox.onkeydown = function (ev) {
      if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) { ev.preventDefault(); askSos(); }
    };
    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape" && SOS.open) closeSos();
    });
  })();
  // ── boot ──────────────────────────────────────────────────────────────
  function boot() {
    el("panel").innerHTML = '<div style="text-align:center;padding:60px 0;color:var(--muted)">' +
      '<div class="spinner" style="margin:0 auto 14px;width:26px;height:26px;color:var(--accent)"></div>' +
      'Revisando tu computadora…</div>';
    api("state", {}).then(function (st) {
      if (st && st.ok) {
        META.providers = st.providers || [];
        META.usecases = st.usecases || [];
        META.hermes = st.hermes || {};
        META.agents = st.agents || { default: null, extra: [] };
        META.smtp_providers = st.smtp_providers || [];
        META.image_options = st.image_options || [];
        META.google_presets = st.google_presets || {};
        META.default_provider = st.default_provider || "claude-code";
        var d = st.defaults || {};
        // fill only empty fields (don't clobber a resumed session)
        S.python = d.python || S.python;
        if (!S.claude) S.claude = d.claude || "";
        if (!S.codex) S.codex = d.codex || "";
        if (!S.install_dir) S.install_dir = d.install_dir || "";
        if (!S.workspace) S.workspace = d.workspace || "";
        if (!S.hermes_config) S.hermes_config = d.hermes_config || "";
        if (!S.repo || S.repo === "Walt9819/olivaw") S.repo = d.repo || S.repo;
        META.version = st.version || "";
      }
      // The version line, straight away and from META - no network. The badge needs a
      // real check, which runs in the background so a slow or offline GitHub delays
      // nothing on screen. On a first install there is nothing to badge: it just
      // downloaded the latest release minutes ago.
      paintVersion(null);
      if (hasAnyAgent()) {
        api("update/status", {}).then(paintVersion).catch(function () {});
      }
      // Open on the brain this machine was installed with, unless the owner has since picked
      // one here: answering "Codex" in the installer and then finding Claude selected is being
      // asked the same question twice.
      if (!S.providerPicked && META.default_provider) S.provider = META.default_provider;
      // "Already set up" was only remembered in THIS browser, so a different browser or cleared
      // storage hid the extras step (channels, routines) for an agent that is demonstrably
      // running. Trust the machine over localStorage: a live bridge means setup happened.
      if (!S.applied && META.agents && META.agents.default && META.agents.default.bridge_up) {
        S.applied = true;
      }
      S._max = Math.max(S._max || 0, S.step || 0);
      if (S.applied) S._max = STEPS.length - 1;
      // Which of the two UIs opens. The stepper is for the first install on this machine;
      // after that the console is home, unless we were left mid-install (adding a second
      // agent, say), in which case finishing that comes first.
      if (!hasAnyAgent()) S.view = "setup";
      else if (S.view !== "setup") S.view = "console";
      // Deep link: #channels / #agent … opens straight to that step, and #rescue (or #sos)
      // opens the help console over whatever step you were on. The desktop shortcut and
      // support links can point right at the help console.
      var want = (location.hash || "").replace(/^#/, "").trim().toLowerCase();
      var wantSos = (want === "rescue" || want === "sos" || want === "ayuda" || want === "help");
      if (want && !wantSos) {
        // #canales, #gasto, #entre-agentes… open a console section; the old step names
        // (#agent, #channels) still work and drop you into the wizard where they used to.
        if (want === "channels" || want === "agentes" || want === "agents") {
          want = (want === "channels") ? "canales" : "home";
        }
        var isSec = CONSOLE.some(function (x) { return x.id === want; });
        if (isSec && hasAnyAgent()) { S.view = "console"; S.sec = want; }
        else {
          var idx = STEPS.map(function (x) { return x.id; }).indexOf(want);
          if (idx >= 0) { S.view = "setup"; S.step = idx; S._max = Math.max(S._max || 0, idx); }
        }
      }
      render();
      if (wantSos) openSos();
    }).catch(function () {
      el("panel").innerHTML = '<h1>No pude conectar con el asistente.</h1>' +
        '<p class="muted">Cierra esta pestaña y vuelve a ejecutar el instalador.</p>';
    });
  }
  boot();
})();
