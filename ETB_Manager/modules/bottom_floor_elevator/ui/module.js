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
        ? "La porte verrouillée de l'ascenseur se trouve à cet étage."
        : "Le module surveille les trois portes connues et leur état IsLocked.";
      $("bfeCount").textContent = String(data.door_count ?? 0) + " / 3";
      $("bfeLocked").textContent = String(data.locked_count ?? 0);
      $("bfeOpen").textContent = data.is_open == null ? "—" : (data.is_open ? "Oui" : "Non");
      $("bfeCollision").textContent = data.can_collide == null ? "—" : (data.can_collide ? "Oui" : "Non");
      $("bfeZ").textContent = data.z == null ? "—" : Number(data.z).toFixed(1) + " cm";
      $("bfeDistance").textContent = data.distance == null ? "—" : Number(data.distance).toFixed(1) + " unités";
      $("bfeUpdated").textContent = data.last_scan
        ? "Dernier scan : " + new Date(data.last_scan * 1000).toLocaleTimeString()
        : "Dernier scan : —";
    }
    return { update, destroy() {} };
  }
  window.ETBModulePlugins = window.ETBModulePlugins || {};
  window.ETBModulePlugins.bottom_floor_elevator = { mount };
})();
