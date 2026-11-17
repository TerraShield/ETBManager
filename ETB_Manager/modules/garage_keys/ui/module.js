(() => {
  function mount(root) {
    const $ = (id) => root.querySelector("#" + id);
    const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
    const mapFrame = root.querySelector("#gkeysMapFrame");
    let latestKeys = [];
    let activeMapUrl = "";
    function sendKeysToMap() {
      if (!mapFrame || !mapFrame.contentWindow) return;
      mapFrame.contentWindow.postMessage({ type: "etb-garage-keys", keys: latestKeys }, "*");
    }
    if (mapFrame) mapFrame.addEventListener("load", sendKeysToMap);

    function updateMapSource(state) {
      if (!mapFrame) return;
      const generated = state.module_state?.butterflies || {};
      const isMiddleFloor = /middle.?floor/i.test(generated.map_name || "");
      const baseUrl = generated.map_ready && generated.map_url && isMiddleFloor
        ? generated.map_url
        : "/modules/garage_keys/public/MiddleFloor_live_map.html";
      const version = generated.map_ready && generated.map_url && isMiddleFloor
        ? (generated.map_version || "")
        : "legacy";
      const desired = baseUrl + "?v=" + encodeURIComponent(version);
      if (desired !== activeMapUrl) {
        activeMapUrl = desired;
        mapFrame.src = desired;
      }
    }

    function update(state) {
      updateMapSource(state);
      const data = state.module_state?.garage_keys || {};
      const keys = Array.isArray(data.keys) ? data.keys : [];
      latestKeys = keys.map(key => ({ x: Number(key.x), y: Number(key.y) }));
      sendKeysToMap();
      $("gkeysCount").textContent = String(data.count ?? keys.length);
      $("gkeysStatus").textContent = data.status || "En attente du scanner…";
      $("gkeysWorld").textContent = data.world_name || "Niveau inconnu";
      $("gkeysDot").classList.toggle("live", !!data.active);
      $("gkeysPlayer").textContent = data.player_position ? "Joueur : X " + Number(data.player_position[0]).toFixed(0) + " · Y " + Number(data.player_position[1]).toFixed(0) + " · Z " + Number(data.player_position[2]).toFixed(0) : "Position joueur : —";
      $("gkeysUpdated").textContent = data.last_scan ? "Dernier scan : " + new Date(data.last_scan * 1000).toLocaleTimeString() : "Dernier scan : —";
      if (!keys.length) {
        $("gkeysList").innerHTML = '<div class="gkeys-empty">' + esc(data.status || "Aucune clé détectée pour le moment.") + '</div>';
        return;
      }
      $("gkeysList").innerHTML = keys.map((key, index) => '<article class="gkeys-card"><div class="gkeys-icon">🔑</div><div><div class="gkeys-name">Clé ' + (index + 1) + '</div><div class="gkeys-coords">X ' + Number(key.x).toFixed(0) + ' · Y ' + Number(key.y).toFixed(0) + '<br>Z ' + Number(key.z).toFixed(0) + '</div></div><div class="gkeys-distance">' + Number(key.distance).toFixed(0) + '<small>unités Unreal</small></div></article>').join("");
    }
    return { update, destroy() {} };
  }
  window.ETBModulePlugins = window.ETBModulePlugins || {};
  window.ETBModulePlugins.garage_keys = { mount };
})();
