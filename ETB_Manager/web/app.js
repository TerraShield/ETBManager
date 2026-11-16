const $ = (id) => document.getElementById(id);
let latestState = null;
let moduleList = [];
let currentModuleId = "none";
let currentController = null;
let currentToolbarCleanup = null;
let currentWorldName = null;
let manualSelection = false;
let loadedScripts = new Set();
let loadedModuleCss = null;
let moduleSignature = "";
let mountGeneration = 0;

async function requestJson(path, options = {}) {
  const response = await fetch(path, { cache: "no-store", ...options });
  let result;
  try { result = await response.json(); }
  catch { throw new Error(`Réponse invalide du serveur (${response.status}).`); }
  if (!response.ok || result?.ok === false) {
    throw new Error(result?.error || result?.message || `Erreur serveur (${response.status}).`);
  }
  return result;
}

function postModule(moduleId, endpoint, data = {}) {
  return requestJson(`/api/modules/${encodeURIComponent(moduleId)}/${endpoint}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

function showHostError(error) {
  console.error(error);
  let target = document.querySelector("#moduleStage .module-error") || $("hostError");
  if (!target && $("moduleStage")) {
    target = document.createElement("div");
    target.className = "module-error";
    $("moduleStage").prepend(target);
  }
  if (target) {
    target.hidden = false;
    target.textContent = error?.message || String(error);
  }
}


function clearModuleToolbar() {
  if (typeof currentToolbarCleanup === "function") {
    try { currentToolbarCleanup(); } catch (error) { console.warn(error); }
  }
  currentToolbarCleanup = null;
  const toolbar = $("moduleToolbar");
  if (toolbar) { toolbar.replaceChildren(); toolbar.hidden = true; }
}

function createModuleApi(moduleId) {
  return {
    moduleId, getState: () => latestState, getRoot: () => $("moduleStage"), refresh,
    showError: showHostError,
    post: (endpoint, data = {}) => postModule(moduleId, endpoint, data),
    upload: async (file) => {
      const response = await fetch(`/api/modules/${encodeURIComponent(moduleId)}/upload`, {
        method: "POST", headers: {"Content-Type":"application/octet-stream","X-File-Name":encodeURIComponent(file.name || "upload.bin")}, body:file,
      });
      const result = await response.json();
      if (!response.ok || result?.ok === false) throw new Error(result?.error || "Échec de l'import.");
      return result;
    },
  };
}

function renderModuleNavigation(modules) {
  const signature = JSON.stringify(modules.map((m) => [m.id, m.name, m.icon, m.short_name, m.order]));
  if (signature === moduleSignature) return;
  moduleSignature = signature;
  moduleList = modules;
  const nav = $("moduleNav");
  const catalog = $("moduleCatalog");
  nav.replaceChildren();
  catalog.replaceChildren();

  modules.forEach((module) => {
    const button = document.createElement("button");
    button.className = "nav-item";
    button.type = "button";
    button.dataset.module = module.id;
    button.innerHTML = `<span class="nav-icon"></span><span class="nav-name"></span>`;
    button.querySelector(".nav-icon").textContent = module.icon || "◇";
    button.querySelector(".nav-name").textContent = module.short_name || module.name;
    const level = document.createElement("span");
    level.className = "nav-level";
    level.textContent = (module.levels || []).length ? `${module.levels.length} NIV.` : "MANUEL";
    button.appendChild(level);
    button.addEventListener("click", () => selectModule(module.id, true));
    nav.appendChild(button);

    const card = document.createElement("button");
    card.type = "button";
    card.className = "module-catalog-card";
    card.dataset.module = module.id;
    const icon = document.createElement("span");
    icon.className = "module-catalog-icon";
    icon.textContent = module.icon || "◇";
    const copy = document.createElement("span");
    copy.className = "module-catalog-copy";
    const title = document.createElement("strong");
    title.textContent = module.name;
    const description = document.createElement("span");
    description.textContent = module.description || "Module ETB.";
    copy.append(title, description);
    card.append(icon, copy);
    card.addEventListener("click", () => selectModule(module.id, true));
    catalog.appendChild(card);
  });
  if (!modules.length) {
    nav.innerHTML = '<div class="nav-loading">Aucun module installé.</div>';
    catalog.innerHTML = '<div class="catalog-empty">Ajoute un module dans le dossier <code>modules/</code>.</div>';
  }
}

function updateNavigation() {
  document.querySelectorAll(".nav-item").forEach((item) => {
    const active = item.dataset.module === currentModuleId;
    item.classList.toggle("active", active);
    item.setAttribute("aria-current", active ? "page" : "false");
  });
  $("homeView").hidden = currentModuleId !== "none";
  $("moduleView").hidden = currentModuleId === "none";
}

function loadScript(url) {
  if (loadedScripts.has(url)) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = url;
    script.async = true;
    script.onload = () => { loadedScripts.add(url); resolve(); };
    script.onerror = () => reject(new Error(`Impossible de charger le script du module : ${url}`));
    document.body.appendChild(script);
  });
}

async function mountModule(moduleId) {
  const generation = ++mountGeneration;
  const descriptor = moduleList.find((item) => item.id === moduleId);
  if (!descriptor) throw new Error(`La fiche du module '${moduleId}' est introuvable.`);
  const stage = $("moduleStage");
  stage.innerHTML = '<div class="module-loading">Chargement du module…</div>';

  const entryUrl = `/modules/${encodeURIComponent(moduleId)}/${descriptor.ui.entry}`;
  const response = await fetch(entryUrl, { cache: "no-store" });
  if (!response.ok) throw new Error(`Interface du module indisponible (${response.status}) : ${descriptor.name}`);
  const markup = await response.text();
  if (generation !== mountGeneration || moduleId !== currentModuleId) return;
  stage.innerHTML = markup;

  if (loadedModuleCss) {
    loadedModuleCss.remove();
    loadedModuleCss = null;
  }
  if (descriptor.ui.style) {
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = `/modules/${encodeURIComponent(moduleId)}/${descriptor.ui.style}`;
    document.head.appendChild(link);
    loadedModuleCss = link;
  }

  if (descriptor.ui.script) {
    const scriptUrl = `/modules/${encodeURIComponent(moduleId)}/${descriptor.ui.script}`;
    await loadScript(scriptUrl);
  }
  if (generation !== mountGeneration || moduleId !== currentModuleId) return;
  const plugin = window.ETBModulePlugins?.[moduleId];
  const api = createModuleApi(moduleId);
  if (!descriptor.ui.script) { currentController = null; return; }
  if (!plugin || typeof plugin.mount !== "function") throw new Error(`Le module '${moduleId}' ne fournit pas de fonction mount(root, api).`);
  currentController = plugin.mount(stage, api) || null;
  if (currentController && typeof currentController.update === "function" && latestState) currentController.update(latestState);
  const toolbar = $("moduleToolbar");
  if (toolbar && typeof plugin.mountToolbar === "function") {
    toolbar.replaceChildren();
    currentToolbarCleanup = plugin.mountToolbar(toolbar, api) || null;
    toolbar.hidden = toolbar.childElementCount === 0;
  }
}

function selectModule(moduleId, manual = false) {
  if (moduleId !== "none" && !moduleList.some((item) => item.id === moduleId)) return;
  if (manual) manualSelection = true;
  if (moduleId === currentModuleId) {
    updateNavigation();
    return;
  }
  mountGeneration += 1; // Invalide un éventuel chargement UI asynchrone précédent.
  if (currentController?.destroy) {
    try { currentController.destroy(); } catch (error) { console.warn(error); }
  }
  currentController = null;
  clearModuleToolbar();
  currentModuleId = moduleId;
  updateNavigation();

  if (moduleId === "none") {
    $("pageTitle").textContent = "Accueil";
    $("pageSubtitle").textContent = "Une plateforme commune pour des modules développés indépendamment.";
    return;
  }
  const descriptor = moduleList.find((item) => item.id === moduleId);
  $("pageTitle").textContent = descriptor?.name || moduleId;
  $("pageSubtitle").textContent = descriptor?.description || "Module ETB.";
  mountModule(moduleId).catch((error) => {
    if (moduleId !== currentModuleId) return;
    $("moduleStage").innerHTML = '<div class="panel-card module-load-error"><strong>Le module n’a pas pu être chargé.</strong><p></p></div>';
    $("moduleStage p").textContent = error.message;
    console.error(error);
  });
}

function render(state) {
  latestState = state;
  renderModuleNavigation(Array.isArray(state.modules) ? state.modules : []);
  const memory = state.memory || {};
  const pos = memory.position;
  const connected = !!memory.ok;
  const worldName = memory.level_name || state.game_level_name || null;
  $("gameLevelName").textContent = worldName || (connected ? "Nom indisponible" : "En attente du jeu…");
  $("gameLevelPill").classList.toggle("has-level", !!worldName);
  $("connectionPill").classList.toggle("connected", connected);
  $("connectionText").textContent = connected ? "Jeu connecté" : "En attente du jeu";
  $("memoryDot").classList.toggle("ok", connected);
  $("memoryMiniStatus").textContent = memory.status || "En attente du jeu…";
  $("miniCoords").textContent = pos
    ? `X ${Number(pos.x).toFixed(1)} · Y ${Number(pos.y).toFixed(1)} · Z ${Number(pos.z).toFixed(1)}`
    : "X — · Y — · Z —";
  $("homeMemoryStatus").textContent = connected ? "Lecture active" : "En attente du jeu";
  $("homeModuleStatus").textContent = state.module_label || "Accueil";
  $("detectionStatus").textContent = state.level_detection || "En attente…";
  const hint = $("noModuleMessage");
  if (hint) {
    hint.textContent = !connected
      ? "En attente de la détection du niveau par le lecteur mémoire."
      : (state.module === "none"
        ? (worldName ? `Le niveau ${worldName} est détecté, mais aucun module ne lui est associé. Ajoute son alias dans le module concerné.` : "Le lecteur mémoire est connecté, mais le nom du niveau n'est pas disponible.")
        : "Le niveau est reconnu. Le module associé s'affiche automatiquement.");
  }

  // Une sélection manuelle reste active tant que le niveau du jeu ne change pas.
  if (worldName !== currentWorldName) {
    currentWorldName = worldName;
    manualSelection = false;
  }
  if (!manualSelection && (state.module || "none") !== currentModuleId) {
    selectModule(state.module || "none", false);
  }
  if (currentModuleId === "none") {
    updateNavigation();
  } else if (currentController?.update) {
    try { currentController.update(state); } catch (error) { showHostError(error); }
  }
}

async function refresh() {
  try {
    const response = await fetch("/api/state", { cache: "no-store" });
    if (!response.ok) throw new Error("Serveur inaccessible");
    render(await response.json());
  } catch (error) {
    $("connectionPill").classList.remove("connected");
    $("connectionText").textContent = "Serveur déconnecté";
    console.error(error);
  }
}

// L'accueil reste accessible depuis la barre latérale.
document.querySelector('[data-module="none"]')?.addEventListener("click", () => selectModule("none", true));
refresh();
setInterval(refresh, 350);
