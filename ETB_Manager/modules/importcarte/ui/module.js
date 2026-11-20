(() => {
  function mount(root, api) {
    const $ = id => root.querySelector("#" + id);
    let mapUrl = "";
    const browse = $("butterfliesBrowseOutput");
    browse?.addEventListener("click", async () => {
      browse.disabled = true;
      try {
        const result = await api.post("action", {action:"choose_output_dir"});
        if (result.output_dir) $("butterfliesOutputDir").value = result.output_dir;
      } catch (error) {
        $("butterfliesError").hidden = false;
        $("butterfliesError").textContent = error.message;
      } finally { browse.disabled = false; }
    });
    const wallButton = $("importcarteAddWalls");
    const wallInput = $("importcarteWallFiles");
    const resetWallsButton = $("importcarteResetWalls");
    wallButton?.addEventListener("click", () => wallInput?.click());
    resetWallsButton?.addEventListener("click", async () => {
      const confirmed = window.confirm("Réinitialiser les imports de murs ? Les JSON actuels seront archivés dans un dossier de sauvegarde et pourront être récupérés.");
      if (!confirmed) return;
      resetWallsButton.disabled = true;
      try {
        const result = await api.post("action", {action:"reset_imported_walls"});
        $("importcarteWallStatus").textContent = result.message || "Imports réinitialisés.";
        renderWallCatalog(result.wall_json_files);
        await api.refresh();
      } catch (error) {
        $("importcarteWallStatus").textContent = "Erreur : " + error.message;
      } finally { resetWallsButton.disabled = false; }
    });
    wallInput?.addEventListener("change", async () => {
      const files = Array.from(wallInput.files || []);
      if (!files.length) return;
      wallButton.disabled = true;
      try {
        for (const file of files) {
          $("importcarteWallStatus").textContent = "Import de " + file.name + "…";
          const renamed = new File([file], "wall__" + file.name, {type:file.type || "application/json"});
          const result = await api.upload(renamed);
          $("importcarteWallStatus").textContent = result.message || "Export ajouté.";
          renderWallCatalog(result.wall_json_files);
        }
        await api.refresh();
      } catch (error) {
        $("importcarteWallStatus").textContent = "Erreur : " + error.message;
      } finally { wallButton.disabled = false; wallInput.value = ""; }
    });
    function renderWallCatalog(catalog) {
      if (!catalog) return;
      const files = catalog.files || [];
      $("importcarteWallList").replaceChildren();
      if (!files.length) $("importcarteWallList").textContent = "Aucun export de mur importé.";
      for (const asset of files) {
        const row = document.createElement("div");
        row.className = "importcarte-asset";
        const title = document.createElement("strong");
        title.textContent = asset.name + " ";
        const path = document.createElement("code");
        path.textContent = asset.asset_path || "(chemin non fourni)";
        row.append(title, path);
        $("importcarteWallList").append(row);
      }
      const duplicates = $("importcarteWallDuplicates");
      duplicates.replaceChildren();
      for (const group of (catalog.duplicates || [])) {
        const card = document.createElement("div");
        card.className = "importcarte-duplicate";
        const title = document.createElement("strong");
        title.textContent = group.name + " — " + group.assets.length + " chemins différents";
        card.append(title);
        for (const asset of group.assets) {
          const line = document.createElement("div");
          line.textContent = asset.asset_path + " · " + asset.file;
          card.append(line);
        }
        duplicates.append(card);
      }
    }
    function update(state) {
      const data = state.module_state?.importcarte || state.module_state?.butterflies || {};
      const output = $("butterfliesOutputDir");
      if (output && !output.value && data.output_dir) output.value = data.output_dir;
      $("butterfliesStatus").textContent = data.status || "Importe un export JSON de FModel depuis la barre d’outils.";
      if (!data.builder_available && !data.map_ready) $("butterfliesStatus").textContent = "Générateur introuvable : vérifie etb_map_builder.py.";
      $("butterfliesError").hidden = !data.error;
      $("butterfliesError").textContent = data.error || "";
      $("butterfliesMapCard").hidden = !data.map_ready;
      if (data.map_ready && data.map_url) {
        const desired = data.map_url + "?v=" + encodeURIComponent(data.map_version || "");
        if (desired !== mapUrl) { $("butterfliesMapFrame").src = desired; mapUrl = desired; }
      }
      renderWallCatalog(data.wall_json_files);
      const debugLog = $("importcarteDebugLog");
      if (debugLog && data.debug_log !== undefined) debugLog.textContent = data.debug_log || "Aucune génération enregistrée.";
    }
    return {update,destroy(){}};
  }
  function mountToolbar(container, api) {
    const makeInput = () => {
      const input = document.createElement("input");
      input.type = "file"; input.accept = ".json,application/json"; input.hidden = true;
      return input;
    };
    const mapButton = document.createElement("button");
    mapButton.type = "button"; mapButton.className = "toolbar-action"; mapButton.textContent = "Importer une carte";
    const mapInput = makeInput();
    mapButton.addEventListener("click", () => mapInput.click());
    mapInput.addEventListener("change", async () => {
      const file = mapInput.files?.[0]; if (!file) return;
      const root = api.getRoot(); const status = root?.querySelector("#butterfliesStatus"); const error = root?.querySelector("#butterfliesError");
      mapButton.disabled = true;
      try {
        const outputDir = root?.querySelector("#butterfliesOutputDir")?.value?.trim();
        if (!outputDir) throw new Error("Indique un dossier de destination.");
        await api.post("action", {action:"set_output_dir", output_dir:outputDir});
        const result = await api.upload(file);
        if (status) status.textContent = result.message || "Carte générée.";
        await api.refresh();
      } catch (e) { if (error) { error.hidden=false; error.textContent=e.message; } }
      finally { mapButton.disabled=false; mapInput.value=""; }
    });
    container.append(mapButton,mapInput);
    return () => { mapButton.remove(); mapInput.remove(); };
  }
  window.ETBModulePlugins = window.ETBModulePlugins || {};
  window.ETBModulePlugins.importcarte = {mount,mountToolbar};
})();
