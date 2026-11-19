(() => {
  function mount(root, api) {
    const $ = id => root.querySelector("#" + id);
    const action = name => api.post("action", { action: name }).catch(api.showError);
    $("lampStart")?.addEventListener("click", () => action("start"));
    $("lampStop")?.addEventListener("click", () => action("stop"));
    $("lampClear")?.addEventListener("click", () => action("clear"));
    const esc = value => String(value ?? "").replace(/[&<>"\']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","\'":"&#39;"}[c]));
    function update(state) {
      const d = state.module_state?.garage || {}; const stats = d.stats || {};
      $("lampStatus").textContent = d.status || "—";
      $("lampStart").disabled = !!d.running; $("lampStop").disabled = !d.running;
      $("lampWorld").textContent = d.world_name || "—";
      $("lampActorsSeen").textContent = stats.actors_seen ?? 0;
      $("lampCount").textContent = stats.lamp_actors ?? 0;
      $("lampReadable").textContent = stats.readable ?? 0;
      $("lampLastScan").textContent = d.last_scan ? new Date(d.last_scan * 1000).toLocaleTimeString() : "—";
      const lamps = d.lamps || [];
      $("lampRows").innerHTML = lamps.length ? lamps.map(x => "<tr><td><strong>"+esc(x.name)+"</strong><small>0x"+esc(x.actor)+"</small></td><td>"+esc(x.component_class || "—")+"</td><td>"+(x.intensity == null ? "—" : Number(x.intensity).toFixed(3)+" lm")+"</td><td><small>Spotlight "+esc(x.spotlight_offset || "—")+"<br>Intensity "+esc(x.intensity_offset || "—")+"</small></td><td>"+esc(x.read_status)+"</td></tr>").join("") : '<tr><td colspan="5">Aucun lampadaire détecté.</td></tr>';
    }
    return { update, destroy() {} };
  }
  window.ETBModulePlugins = window.ETBModulePlugins || {};
  window.ETBModulePlugins.garage = { mount };
})();