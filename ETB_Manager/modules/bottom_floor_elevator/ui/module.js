(() => {
  function mount(root) {
    const $ = (id) => root.querySelector("#" + id);
    function update(state) {
      const data = state.module_state?.bottom_floor_elevator || {};
      const floor = data.floor || null;
      $("bfeStatus").textContent = data.status || "En attente du scanner…";
      $("bfeWorld").textContent = data.world_name || "Niveau inconnu";
      $("bfeDot").classList.toggle("live", !!data.active && !!floor);
      $("bfeFloor").textContent = floor || "Non détecté";
      $("bfeFloor").classList.toggle("ready", !!floor);
      $("bfeHint").textContent = floor
        ? "L'ascenseur est associé à cet étage."
        : "L'étage apparaîtra dès qu'une instance exploitable sera détectée.";
      $("bfeFrom").textContent = data.from_level || "—";
      $("bfeTo").textContent = data.to_level || "—";
      $("bfeZ").textContent = data.z == null ? "—" : Number(data.z).toFixed(1) + " cm";
      $("bfeCount").textContent = String(data.elevator_count ?? 0);
      $("bfeUpdated").textContent = data.last_scan
        ? "Dernier scan : " + new Date(data.last_scan * 1000).toLocaleTimeString()
        : "Dernier scan : —";
    }
    return { update, destroy() {} };
  }
  window.ETBModulePlugins = window.ETBModulePlugins || {};
  window.ETBModulePlugins.bottom_floor_elevator = { mount };
})();
