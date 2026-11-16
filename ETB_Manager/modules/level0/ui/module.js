(() => {
  function mount(root) {
    const $ = (id) => root.querySelector(`#${id}`);
    let lastMapUrl = "";
    function update(state) {
      const position = state.memory?.position;
      if (position) {
        $("level0X").textContent = Number(position.x).toFixed(1);
        $("level0Y").textContent = Number(position.y).toFixed(1);
        $("level0Z").textContent = Number(position.z).toFixed(1);
        $("level0Yaw").textContent = Number.isFinite(Number(position.yaw)) ? `${Number(position.yaw).toFixed(1)}°` : "—";
      }
      const moduleState = state.module_state?.level0 || {};
      const live = !!moduleState.live_map_available && !!moduleState.map_url;
      const preview = !!moduleState.preview_available;
      $("level0MapStatus").textContent = live
        ? "Carte interactive détectée — suivi live actif."
        : (preview ? "Carte interactive absente : affichage de l'aperçu statique fourni." : "Aucune carte trouvée. Ajoute ton HTML de carte dans modules/level0/.");
      $("level0LiveCard").hidden = !live;
      $("level0PreviewCard").hidden = live || !preview;
      if (live && moduleState.map_url !== lastMapUrl) {
        $("level0LiveFrame").src = moduleState.map_url;
        lastMapUrl = moduleState.map_url;
      }
    }
    return { update, destroy() {} };
  }
  window.ETBModulePlugins = window.ETBModulePlugins || {};
  window.ETBModulePlugins.level0 = { mount };
})();
