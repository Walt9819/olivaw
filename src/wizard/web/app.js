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
    install_dir: "", workspace: "", repo: "Walt9819/olivaw",
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
  var META = { providers: [], usecases: [], default_provider: "claude-code", hermes: {},
    agents: { default: null, extra: [] }, smtp_providers: [], image_options: [] };

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
    renderStepper();
    el("panel").innerHTML = STEPS[S.step].render();
    el("navProgress").textContent = "Paso " + (S.step + 1) + " de " + STEPS.length;
    el("navBack").style.visibility = S.step === 0 ? "hidden" : "visible";
    var next = el("navNext");
    next.textContent = S.step === STEPS.length - 1 ? "Cerrar asistente" : "Continuar →";
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
    if (S.step === STEPS.length - 1) { closeWizard(); return; }
    go(S.step + 1);
  };

  function closeWizard() {
    api("shutdown", {});
    document.body.innerHTML =
      '<div style="display:grid;place-items:center;height:100vh;text-align:center;font-family:sans-serif">' +
      '<div><div style="font-size:52px">👋</div><h2>Puedes cerrar esta pestaña.</h2>' +
      '<p style="color:#888">Tu agente ya está configurándose en segundo plano.</p></div></div>';
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
    return !!(a.default) || (a.extra && a.extra.length);
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
      save(); go(1);
    };
    Array.prototype.forEach.call(document.querySelectorAll("#panel [data-act]"), function (b) {
      b.onclick = function () {
        var act = b.getAttribute("data-act"), slug = b.getAttribute("data-slug");
        if (act === "reconfigure") {
          var all = [META.agents.default].concat(META.agents.extra || []);
          var a = all.filter(function (x) { return x && x.slug === slug; })[0] || {};
          S.agent = { mode: a.is_default ? "default" : "reconfigure", slug: slug,
                      port: a.port, workspace: a.workspace, name: a.name };
          if (!a.is_default) { resetAgentFields(); }  // reconfiguring an extra: fresh inputs
          save(); go(1); return;
        }
        if (act === "reset" && !confirm("¿Eliminar este agente por completo? Se borra su perfil, memoria y datos. No se puede deshacer.")) return;
        var pill = document.querySelector('[data-pill="' + slug + '"]');
        if (pill) { pill.style.display = "inline-flex"; }
        runTest(b, pill, function () { return api("agent/action", { slug: slug, action: act }); })
          .then(function () { setTimeout(refreshAgents, 400); });
      };
    });
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
      var badge = p.status === "ready"
        ? '<span class="badge">Recomendado</span>'
        : '<span class="badge soon">Próximamente</span>';
      return '<div class="opt' + sel + dis + '" data-pid="' + p.id + '">' +
        '<div class="ic">' + esc(p.label[0]) + '</div>' +
        '<div class="grow"><div class="row" style="justify-content:space-between">' +
        '<span class="ttl">' + esc(p.label) + '</span>' + badge + '</div>' +
        '<div class="muted small">' + esc(p.tagline) + '</div></div></div>';
    }).join("");

    var p = provider();
    var steps = (p.steps || []).map(function (g) {
      var link = g.link ? ' <a href="' + esc(g.link) + '" target="_blank" rel="noopener">abrir</a>' : "";
      return '<div class="g"><div class="gn"></div><div class="gt"><b>' + esc(g.title) +
        "</b> — " + esc(g.body) + link + "</div></div>";
    }).join("");

    return '' +
      '<div class="eyebrow">Paso 1 · El cerebro</div>' +
      '<h1>Elige quién piensa por tu agente</h1>' +
      '<p class="lead">El cerebro es el modelo de IA que razona. Hoy el recomendado es ' +
      'Claude Code: usa tu suscripción de Claude, sin claves de API.</p>' +
      '<div class="opt-grid" id="provOpts">' + opts + '</div>' +
      '<div class="callout"><b>Necesitas una cuenta de pago.</b> ' + esc(p.paid_note) +
      ' &nbsp;<a href="' + esc(p.download_url) + '" target="_blank" rel="noopener">Descargar</a> · ' +
      '<a href="' + esc(p.help_url) + '" target="_blank" rel="noopener">Ayuda oficial</a></div>' +
      '<h2>Cómo dejarlo listo</h2><div class="guide">' + steps + '</div>' +
      '<div class="card">' +
      '<label class="field"><span class="lab">Ruta de Claude Code <span class="hint">(la detectamos sola; edítala solo si hace falta)</span></span>' +
      '<input type="text" id="claudePath" placeholder="claude" value="' + esc(S.claude) + '"></label>' +
      '<div class="row">' +
      '<button class="btn btn-soft btn-sm" id="btnNode">Verificar Node.js</button>' +
      '<button class="btn btn-soft btn-sm" id="btnInstall">Instalar Claude Code</button>' +
      '<button class="btn btn-soft btn-sm" id="btnCheckClaude">Verificar Claude Code</button>' +
      '</div><div id="pillProv" class="pill load" style="display:none"></div>' +
      '<div class="callout warn small" style="margin-top:14px">Después de instalar, abre una terminal, ' +
      'escribe <code>claude</code> y completa el inicio de sesión (una sola vez). ' + esc(p.login_hint) + '</div>' +
      '</div>' +
      '<div class="card pad">' +
      '<b>La prueba clave</b><p class="muted small">Enviamos un mensaje real a través del puente. ' +
      'Si el cerebro responde, todo lo demás funcionará.</p>' +
      '<div class="row"><button class="btn btn-primary" id="btnBrain">Probar el cerebro</button>' +
      '<span id="pillBrain" class="pill" style="display:none"></span></div></div>';
  }
  function provider() {
    return META.providers.filter(function (p) { return p.id === S.provider; })[0] ||
      META.providers[0] || { steps: [], paid_note: "", download_url: "#", help_url: "#", login_hint: "" };
  }
  function eProvider() {
    Array.prototype.forEach.call(document.querySelectorAll("#provOpts .opt"), function (o) {
      o.onclick = function () {
        var pid = o.getAttribute("data-pid");
        var p = META.providers.filter(function (x) { return x.id === pid; })[0];
        if (!p || p.status !== "ready") { toast("Ese proveedor aún no está disponible."); return; }
        S.provider = pid; save(); render();
      };
    });
    var cp = el("claudePath"); if (cp) cp.oninput = function () { S.claude = cp.value.trim(); save(); };
    var pill = el("pillProv");
    el("btnNode").onclick = function () {
      pill.style.display = "inline-flex";
      runTest(this, pill, function () { return api("check", { what: "node" }); });
    };
    el("btnInstall").onclick = function () {
      pill.style.display = "inline-flex";
      runTest(this, pill, function () { return api("provider/install", { provider: S.provider }); }, "Instalando…");
    };
    el("btnCheckClaude").onclick = function () {
      pill.style.display = "inline-flex";
      runTest(this, pill, function () {
        return api("provider/check", { provider: S.provider, claude: S.claude });
      }).then(function (r) { if (r.ok && r.path) { S.claude = r.path; save(); if (el("claudePath")) el("claudePath").value = r.path; } });
    };
    var pb = el("pillBrain");
    el("btnBrain").onclick = function () {
      pb.style.display = "inline-flex";
      runTest(this, pb, function () {
        return api("test-brain", { claude: S.claude, workspace: S.workspace });
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
      '<h1>Conecta el motor Hermes</h1>' +
      '<p class="lead">Hermes es el cuerpo del agente: ejecuta las acciones (terminal, archivos, ' +
      'web, recordatorios, mensajes) que el cerebro decide.</p>' +
      '<div class="guide">' +
      '<div class="g"><div class="gn"></div><div class="gt"><b>Instala Hermes</b> — sigue la guía oficial ' +
      'de instalación para tu sistema. <a href="https://hermes.nousresearch.com" target="_blank" rel="noopener">Abrir guía</a></div></div>' +
      '<div class="g"><div class="gn"></div><div class="gt"><b>Deja el gateway corriendo</b> — es lo que ' +
      'mantiene a tu agente escuchando.</div></div>' +
      '<div class="g"><div class="gn"></div><div class="gt"><b>Verifica aquí abajo</b> — comprobamos que ' +
      'el comando <code>hermes</code> esté disponible.</div></div>' +
      '</div>' +
      '<div class="card"><div class="row">' +
      '<button class="btn btn-primary" id="btnHermes">Verificar Hermes</button>' +
      '<span id="pillHermes" class="pill" style="display:none"></span></div></div>' +
      '<p class="muted small">¿Aún no lo instalas? Puedes continuar y hacerlo después; te dejaremos ' +
      'preparado el bloque de configuración que Hermes necesita.</p>';
  }
  function eHermes() {
    var pill = el("pillHermes");
    el("btnHermes").onclick = function () {
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
      '<a href="https://t.me/BotFather" target="_blank" rel="noopener">t.me/BotFather</a></div></div>' +
      '<div class="g"><div class="gn"></div><div class="gt">Envía <code>/newbot</code> y sigue las dos ' +
      'preguntas: un nombre y un usuario que termine en <code>bot</code>.</div></div>' +
      '<div class="g"><div class="gn"></div><div class="gt">Copia el <b>token</b> que te da (algo como ' +
      '<code>123456:ABC-...</code>) y pégalo aquí abajo.</div></div></div>' +
      '<div class="card">' +
      '<label class="field"><span class="lab">Token del bot</span>' +
      '<input type="password" id="tgToken" placeholder="123456789:AA..." value="' + esc(S.token) + '"></label>' +
      '<div class="row"><button class="btn btn-primary" id="btnValidate">Validar token</button>' +
      '<span id="pillTg" class="pill" style="display:none"></span></div></div>' +

      '<h2>2 · Vincula tu cuenta como dueño</h2>' +
      '<div class="card"><p class="small muted" style="margin-top:0">' +
      'Abre tu bot y pulsa <b>Start</b> (o escríbele “hola”). Luego pulsa capturar: ' +
      'la cuenta que escribió quedará como dueña.</p>' +
      (botLink ? '<a class="btn btn-soft btn-sm" href="' + botLink + '" target="_blank" rel="noopener">Abrir mi bot</a> ' : "") +
      '<div class="row" style="margin-top:8px"><button class="btn btn-primary" id="btnCapture">Capturar mi cuenta</button>' +
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
      field2("workspace", "Carpeta de trabajo del agente", S.workspace) +
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
        if (r && r.ok) { S.bot_username = r.username || ""; save(); render(); }
      });
    };
    var pillOwner = el("pillOwner");
    el("btnCapture").onclick = function () {
      pillOwner.style.display = "inline-flex";
      runTest(this, pillOwner, function () { return api("telegram/capture", { token: S.token }); }).then(function (r) {
        if (r && r.ok) {
          S.owner_id = String(r.owner_id); S.chat_id = String(r.chat_id);
          S.owner_username = r.name || r.username || "";
          if (!S.identity.owner_name) S.identity.owner_name = r.name || r.username || "";
          save(); render(); toast("Dueño vinculado 🔒");
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
      sumline("🧠", "Cerebro", provider().label + (S.brainOk ? " · probado ✓" : "")) +
      sumline("✨", "Agente", (S.identity.agent_name || "sin nombre") + (S.identity.purpose ? " — " + S.identity.purpose : "")) +
      sumline("🧩", "Habilidades", S.usecases.length ? String(S.usecases.length) + " seleccionadas" : "ninguna") +
      sumline("🔒", "Dueño", S.owner_id ? (S.owner_username || S.owner_id) + " (id " + S.owner_id + ")" : "sin vincular") +
      sumline("📁", "Carpeta", S.workspace || "(predeterminada)") +
      '</ul></div>' +
      '<div class="row"><button class="btn btn-primary" id="btnApply">Aplicar y activar</button>' +
      '<span id="pillApply" class="pill" style="display:none"></span></div>';
  }
  function sumline(ic, k, v) {
    return "<li>" + ic + " <b>" + esc(k) + ":</b> <span class='muted'>" + esc(v) + "</span></li>";
  }
  function rFinished(res) {
    var files = (res.written || []).map(function (f) { return "<li>📄 " + esc(f) + "</li>"; }).join("");
    var warns = (res.warnings || []).map(function (w) { return '<div class="callout warn small">' + esc(w) + "</div>"; }).join("");
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
      '<h1>¡Tu agente está vivo!</h1>' +
      '<p class="lead">Ya escribí todo y encendí el supervisor. A partir de ahora se actualiza solo, ' +
      'en silencio, cuando no lo estés usando.</p></div>' +
      newNote + isoNote +
      (botLink ? '<div class="card pad" style="text-align:center"><b>Habla con tu agente ahora</b>' +
        '<p class="muted small">Abre tu bot en Telegram y salúdalo.</p>' +
        '<a class="btn btn-primary" href="' + botLink + '" target="_blank" rel="noopener">Abrir ' +
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
    var btn = el("btnApply"); if (!btn) return;
    var pill = el("pillApply");
    btn.onclick = function () {
      pill.style.display = "inline-flex";
      var payload = {
        provider: S.provider, claude: S.claude, install_dir: S.install_dir,
        workspace: S.workspace, repo: S.repo, token: S.token, owner_id: S.owner_id,
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
        if (r && r.ok) { S.applied = true; S.applyResult = r; S._max = STEPS.length - 1; save(); render(); }
      });
    };
  }

  // ── step 6: extra channels (optional) ─────────────────────────────────────
  function targetProfile() {
    if (S.agent && S.agent.mode === "new" && S.applyResult && S.applyResult.agent)
      return S.applyResult.agent.profile;
    if (S.agent && S.agent.mode === "reconfigure") return S.agent.slug;
    return null; // default agent -> bare hermes
  }
  function targetWorkspace() {
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
    var smtpOpts = (META.smtp_providers || []).map(function (p) {
      return '<option value="' + p.id + '"' + (S.smtp.provider === p.id ? " selected" : "") +
        '>' + esc(p.label) + '</option>';
    }).join("");
    return '' +
      '<div class="eyebrow">Paso 6 · Más canales <span class="muted">(opcional)</span></div>' +
      '<h1>¿Por dónde más pueden hablarle?</h1>' +
      '<p class="lead">Tu agente ya vive en Telegram. Aquí puedes darle más capacidades ' +
      '(imágenes, video), conectar herramientas externas, y sumar más canales. Todo es opcional.</p>' +

      // Capabilities: image / video generation
      '<details open><summary>🎨 Generación de imágenes y video</summary>' +
      '<p class="small muted">Activa la generación en Hermes (se abre una ventana para elegir ' +
      'proveedor y su clave). Opciones gratis o con tu cuenta de Google:</p>' +
      (META.image_options || []).map(function (o) {
        return '<div class="row" style="gap:8px;align-items:flex-start;margin-bottom:6px">' +
          '<span>' + (o.free ? '🆓' : '💳') + '</span><div class="grow"><b class="small">' +
          esc(o.label) + '</b><div class="muted small">' + esc(o.note) +
          (o.link ? ' <a href="' + esc(o.link) + '" target="_blank" rel="noopener">abrir</a>' : '') +
          '</div></div></div>';
      }).join("") +
      '<div class="row" style="margin-top:8px"><button class="btn btn-soft btn-sm" id="capImg">Configurar imágenes en Hermes</button></div>' +
      chLine("capPill") + '</details>' +

      // Connectors (MCP)
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
      chLine("mcpPill") + '</details>' +

      // WhatsApp
      '<details open><summary>💬 WhatsApp</summary>' +
      '<p class="small muted">Personal (Baileys, con QR) o Business (Cloud API de Meta). ' +
      'El emparejamiento se hace en una ventana de terminal.</p>' +
      '<div class="row"><button class="btn btn-soft btn-sm" id="waPair">Emparejar WhatsApp (QR)</button>' +
      '<button class="btn btn-soft btn-sm" id="waCloud">WhatsApp Business (Cloud)</button></div>' +
      chLine("waPill") + '</details>' +

      // Slack
      '<details><summary>🟣 Slack</summary>' +
      '<p class="small muted">Genera el manifest, crea la app en ' +
      '<a href="https://api.slack.com/apps" target="_blank" rel="noopener">api.slack.com/apps</a> ' +
      '(«From an app manifest»), y termina la configuración en la terminal.</p>' +
      '<div class="row"><button class="btn btn-soft btn-sm" id="slackMan">Ver manifest</button>' +
      '<button class="btn btn-soft btn-sm" id="slackSetup">Configurar en terminal</button></div>' +
      '<div id="slackManWrap" style="display:none;margin-top:10px"><pre id="slackManTxt"></pre></div>' +
      chLine("slackPill") + '</details>' +

      // Webhook / Google Chat
      '<details><summary>🔗 Webhook / Google Chat</summary>' +
      '<p class="small muted">Crea una ruta <code>/webhooks/&lt;nombre&gt;</code> que activa al ' +
      'agente cuando llega un evento (Google Chat, Zapier, GitHub, lo que sea).</p>' +
      '<label class="field"><span class="lab">Nombre de la ruta</span>' +
      '<input type="text" id="whName" placeholder="ej: gchat" value="' + esc(S.wh_name || "") + '"></label>' +
      '<label class="field"><span class="lab">¿Qué hace? (opcional)</span>' +
      '<input type="text" id="whDesc" placeholder="Avisos desde Google Chat" value="' + esc(S.wh_desc || "") + '"></label>' +
      '<div class="row"><button class="btn btn-soft btn-sm" id="whAdd">Crear webhook</button>' +
      '<button class="btn btn-soft btn-sm" id="whTest">Probar</button></div>' +
      chLine("whPill") + '</details>' +

      // Email (SMTP)
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
      chLine("smtpPill") + '</details>' +

      '<p class="muted small" style="margin-top:16px">Cuando termines (o si no necesitas más ' +
      'canales), pulsa «Cerrar asistente».</p>';
  }

  function eChannels() {
    if (!S.applied) return;
    var prof = targetProfile(), ws = targetWorkspace();

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
    if (wa) wa.onclick = function () {
      waPill.style.display = "inline-flex";
      runTest(this, waPill, function () { return api("channel/whatsapp", { profile: prof, cloud: false }); }, "Abriendo…");
    };
    if (el("waCloud")) el("waCloud").onclick = function () {
      waPill.style.display = "inline-flex";
      runTest(this, waPill, function () { return api("channel/whatsapp", { profile: prof, cloud: true }); }, "Abriendo…");
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
      if (note) note.innerHTML = esc(p.note || "") + (p.link ? ' <a href="' + esc(p.link) + '" target="_blank" rel="noopener">obtener contraseña</a>' : "");
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
        META.default_provider = st.default_provider || "claude-code";
        var d = st.defaults || {};
        // fill only empty fields (don't clobber a resumed session)
        S.python = d.python || S.python;
        if (!S.claude) S.claude = d.claude || "";
        if (!S.install_dir) S.install_dir = d.install_dir || "";
        if (!S.workspace) S.workspace = d.workspace || "";
        if (!S.hermes_config) S.hermes_config = d.hermes_config || "";
        if (!S.repo || S.repo === "Walt9819/olivaw") S.repo = d.repo || S.repo;
      }
      S._max = Math.max(S._max || 0, S.step || 0);
      render();
    }).catch(function () {
      el("panel").innerHTML = '<h1>No pude conectar con el asistente.</h1>' +
        '<p class="muted">Cierra esta pestaña y vuelve a ejecutar el instalador.</p>';
    });
  }
  boot();
})();
