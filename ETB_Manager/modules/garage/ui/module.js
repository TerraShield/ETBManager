(() => {
  function mount(root, api) {
    const $ = (id) => root.querySelector(`#${id}`);
    const controls = [
      ["startStopButton", () => api.post("action", { action: api.getState()?.module_state?.garage?.running ? "stop" : "start" })],
      ["chooseRegionButton", () => api.post("action", { action: "choose_region" })],
      ["resetButton", () => api.post("action", { action: "reset" })],
      ["flickerButton", () => api.post("action", { action: "flicker" })],
      ["relightButton", () => api.post("action", { action: "relight" })],
    ];
    controls.forEach(([id, callback]) => $(id)?.addEventListener("click", () => callback().catch(api.showError)));
    $("playersSelect")?.addEventListener("change", (event) =>
      api.post("action", { action: "players", value: event.target.value }).catch(api.showError));

    function update(state) {
      const garage = state.module_state?.garage || {};
      $("garageStatus").textContent = garage.status || "—";
      $("detectChip").textContent = garage.running ? "DÉTECTION ACTIVE" : "À L'ARRÊT";
      $("detectChip").classList.toggle("is-running", !!garage.running);
      $("startStopButton").textContent = garage.running ? "Arrêter la détection" : "Démarrer la détection";
      $("periodValue").textContent = garage.period_label || "En attente";
      $("eventsValue").textContent = String(garage.event_count ?? 0);
      $("qualityValue").textContent = garage.quality || "—";
      $("lumaValue").textContent = garage.luma == null ? "—" : `${Number(garage.luma).toFixed(1)} / 255`;
      $("hotkeysValue").textContent = garage.hotkeys_available ? "F8 / F9 actifs" : "Boutons navigateur";
      $("regionInfo").textContent = garage.region
        ? `Zone : ${garage.region.width} × ${garage.region.height} px · position ${garage.region.left}, ${garage.region.top}`
        : "Aucune zone sélectionnée";
      if (garage.player_count && $("playersSelect").value !== garage.player_count) $("playersSelect").value = garage.player_count;
      const remaining = garage.remaining;
      const countdown = $("countdown");
      if (remaining == null) {
        countdown.innerHTML = "--.--<small> s</small>";
        countdown.classList.remove("expired");
      } else {
        countdown.innerHTML = `${Number(remaining).toFixed(2)}<small> s</small>`;
        countdown.classList.toggle("expired", Number(remaining) <= 0.02);
      }
    }
    return { update, destroy() {} };
  }
  window.ETBModulePlugins = window.ETBModulePlugins || {};
  window.ETBModulePlugins.garage = { mount };
})();
