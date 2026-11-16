(() => {
  function mount(root) {
    const $ = (id) => root.querySelector("#" + id);
    const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
    function update(state) {
      const data = state.module_state?.hotel_terror_code || {};
      const locks = Array.isArray(data.locks) ? data.locks : [];
      $("htcStatus").textContent = data.status || "En attente du scanner…";
      $("htcWorld").textContent = data.world_name || "Niveau inconnu";
      $("htcDot").classList.toggle("live", !!data.active);
      $("htcCode").textContent = data.code || "·····";
      $("htcCode").classList.toggle("ready", !!data.code);
      $("htcHint").textContent = data.code ? "Code confirmé depuis la mémoire du jeu" : "Le code apparaîtra dès que le cadenas sera détecté";
      if (locks.length) {
        const lock = locks[0];
        const position = lock.x == null ? "Position indisponible" : "X " + Number(lock.x).toFixed(0) + " · Y " + Number(lock.y).toFixed(0) + " · Z " + Number(lock.z).toFixed(0);
        const distance = lock.distance == null ? "" : " · " + Number(lock.distance).toFixed(0) + " unités du joueur";
        $("htcDetails").textContent = "Cadenas détecté · " + position + distance + " · Propriété " + (lock.property_offset || "Password");
      } else {
        $("htcDetails").textContent = esc(data.status || "Aucune donnée de cadenas pour le moment.");
      }
      $("htcPlayer").textContent = data.player_position ? "Joueur : X " + Number(data.player_position[0]).toFixed(0) + " · Y " + Number(data.player_position[1]).toFixed(0) + " · Z " + Number(data.player_position[2]).toFixed(0) : "Position joueur : —";
      $("htcUpdated").textContent = data.last_scan ? "Dernier scan : " + new Date(data.last_scan * 1000).toLocaleTimeString() : "Dernier scan : —";
    }
    return { update, destroy() {} };
  }
  window.ETBModulePlugins = window.ETBModulePlugins || {};
  window.ETBModulePlugins.hotel_terror_code = { mount };
})();
