(() => {
  function mount(root) {
    const $ = id => root.querySelector(`#${id}`);
    let lastMapUrl = "";
    function update(state) {
      const pos = state.memory?.position;
      if (pos) {
        $("boilerRoomX").textContent = Number(pos.x).toFixed(1);
        $("boilerRoomY").textContent = Number(pos.y).toFixed(1);
        $("boilerRoomZ").textContent = Number(pos.z).toFixed(1);
        $("boilerRoomYaw").textContent = Number.isFinite(Number(pos.yaw)) ? `${Number(pos.yaw).toFixed(1)}°` : "—";
      }
      const data = state.module_state?.boiler_room || {};
      const available = !!data.map_available && !!data.map_url;
      $("boilerRoomMapStatus").textContent = available ? `Carte interactive prête · ${data.segments_count || 0} segments de déplacement analysés.` : "Carte Boiler Room introuvable dans le dossier public du module.";
      $("boilerRoomMapCard").hidden = !available;
      if (available && data.map_url !== lastMapUrl) { $("boilerRoomMapFrame").src = data.map_url; lastMapUrl = data.map_url; }
      const hint = data.movement_hint || {kind:"waiting",label:"En attente de la position du joueur"};
      $("boilerRoomHint").dataset.kind = hint.kind || "waiting";
      $("boilerRoomHintText").textContent = hint.label || "En attente de la position du joueur";
      $("boilerRoomHintIcon").textContent = hint.kind === "green" ? "➜" : hint.kind === "orange" ? "▾" : hint.kind === "none" ? "✓" : "⌁";
      $("boilerRoomHintDistance").textContent = hint.distance !== null && hint.distance !== undefined && Number.isFinite(Number(hint.distance)) ? `Tracé le plus proche : ${(Number(hint.distance)/100).toFixed(1)} m` : (hint.kind === "none" ? "Aucun tracé dans le rayon de détection." : "En attente de la lecture mémoire…");
    }
    return {update,destroy(){}};
  }
  window.ETBModulePlugins = window.ETBModulePlugins || {};
  window.ETBModulePlugins.boiler_room = {mount};
})();
