(() => {
  function mount(root, api) {
    const $ = id => root.querySelector(`#${id}`);
    let mapUrl = "";
    const browse = $("butterfliesBrowseOutput");
    browse?.addEventListener("click", async () => {
      browse.disabled = true;
      try {
        const result = await api.post("action", {action:"choose_output_dir"});
        if (result.output_dir) $("butterfliesOutputDir").value = result.output_dir;
        if (result.cancelled) return;
      } catch (error) {
        $("butterfliesError").hidden = false;
        $("butterfliesError").textContent = error.message;
      } finally {
        browse.disabled = false;
      }
    });
    function update(state) {
      const data = state.module_state?.butterflies || {};
      const output = root.querySelector("#butterfliesOutputDir");
      if (output && !output.value && data.output_dir) output.value = data.output_dir;
      $("butterfliesStatus").textContent = data.status || "Importe un export JSON de FModel depuis la barre d’outils.";
      if (!data.builder_available && !data.map_ready) $("butterfliesStatus").textContent = "Générateur introuvable : place etb_map_builder.py dans modules/butterflies/.";
      $("butterfliesError").hidden = !data.error;
      $("butterfliesError").textContent = data.error || "";
      $("butterfliesMapCard").hidden = !data.map_ready;
      if (data.map_ready && data.map_url) {
        const desired = `${data.map_url}?v=${encodeURIComponent(data.map_version || "")}`;
        if (desired !== mapUrl) { $("butterfliesMapFrame").src = desired; mapUrl = desired; }
      }
    }
    return {update,destroy(){}};
  }
  function mountToolbar(container, api) {
    const button = document.createElement("button");
    button.type = "button"; button.className = "toolbar-action"; button.textContent = "Importer une carte";
    const input = document.createElement("input"); input.type = "file"; input.accept = ".json,application/json"; input.hidden = true;
    button.addEventListener("click", () => input.click());
    input.addEventListener("change", async () => {
      const file = input.files?.[0]; if (!file) return;
      const root = api.getRoot(); const status = root?.querySelector("#butterfliesStatus"); const errorBox = root?.querySelector("#butterfliesError");
      button.disabled = true; button.textContent = "Import en cours…"; if (errorBox) errorBox.hidden = true;
      if (status) status.textContent = `Lecture de ${file.name}…`;
      try {
        const outputDir = root?.querySelector("#butterfliesOutputDir")?.value?.trim();
        if (!outputDir) throw new Error("Indique un dossier de destination.");
        await api.post("action", {action:"set_output_dir", output_dir:outputDir});
        const result = await api.upload(file);
        if (status) status.textContent = result.message || "Carte générée.";
        await api.refresh();
      }
      catch (error) { if (errorBox) { errorBox.hidden = false; errorBox.textContent = error.message; } if (status) status.textContent = "La carte n'a pas pu être générée."; }
      finally { button.disabled = false; button.textContent = "Importer une carte"; input.value = ""; }
    });
    container.append(button,input);
    return () => { button.remove(); input.remove(); };
  }
  window.ETBModulePlugins = window.ETBModulePlugins || {};
  window.ETBModulePlugins.butterflies = {mount,mountToolbar};
})();
