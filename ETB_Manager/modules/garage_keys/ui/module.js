(() => {
  function mount(root) {
    const $ = (id) => root.querySelector("#" + id);
    const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
    function update(state) {
      const data = state.module_state?.garage_keys || {};
      const keys = Array.isArray(data.keys) ? data.keys : [];
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
