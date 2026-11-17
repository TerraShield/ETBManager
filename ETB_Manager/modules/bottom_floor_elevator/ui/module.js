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
      $("bfeDiagnostics").textContent = (data.diagnostics || []).map((group) => {
        const lines = [
          group.floor + " — repère (" + group.reference.x + ", " + group.reference.y + ", " + group.reference.z + ")",
          "Objets trouvés : " + group.objects_total + " | rayon XY " + group.radius_xy + " | rayon Z " + group.radius_z
        ];
        (group.objects || []).forEach((object) => {
          const props = object.properties || {};
          const fmt = (key) => {
            const item = props[key] || {};
            return key + "=" + (item.value == null ? "?" : item.value ? "true" : "false") +
              (item.offset ? " [" + item.offset + "]" : " [absente]");
          };
          lines.push(
            "  " + object.name + " <" + object.class + "> @" + object.id +
            " | pos=(" + object.x + ", " + object.y + ", " + object.z + ")" +
            " | écart=" + object.distance + " (XY=" + object.horizontal_distance + ")" +
            " | " + fmt("IsLocked") + " ; " + fmt("IsOpen") + " ; " + fmt("CanCollide")
          );
        });
        return lines.join("\n");
      }).join("\n\n") || "Aucun diagnostic disponible : vérifie que garageLevel2 est chargé.";
      $("bfeLogPath").textContent = data.log_path || "—";
    }
    return { update, destroy() {} };
  }
  window.ETBModulePlugins = window.ETBModulePlugins || {};
  window.ETBModulePlugins.bottom_floor_elevator = { mount };
})();
