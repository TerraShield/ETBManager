(() => {
  function mount(root, api) {
    const $ = id => root.querySelector("#" + id);
    const action = name => api.post("action", { action: name }).catch(api.showError);
    $("lampStart")?.addEventListener("click", () => action("start"));
    $("lampStop")?.addEventListener("click", () => action("stop"));
    $("lampClear")?.addEventListener("click", () => action("clear"));
    $("lampPlayers")?.addEventListener("change", event => api.post("action", { action: "players", value: event.target.value }).catch(api.showError));
    let countdownDeadline = null;
    const countdownTimer = setInterval(() => {
      const el = $("lampCountdown");
      if (!el) return;
      if (countdownDeadline == null) { el.textContent = "—"; return; }
      const seconds = Math.max(0, (countdownDeadline - Date.now()) / 1000);
      el.textContent = seconds.toFixed(2) + " s";
    }, 100);
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
      $("lampSampleRate").textContent = (d.sample_hz || 20) + " Hz";
      $("lampSignal").textContent = d.signal == null ? "—" : Number(d.signal).toFixed(2) + " lm";
      $("lampEvents").textContent = d.event_count ?? 0;
      $("lampPeriod").textContent = d.period_label || "En attente";
      $("lampQuality").textContent = d.quality || "—";
      if ($("lampPlayers") && d.player_count) $("lampPlayers").value = d.player_count;
      countdownDeadline = d.remaining == null ? null : Date.now() + d.remaining * 1000;
      const lamps = d.lamps || [];
      $("lampRows").innerHTML = lamps.length ? lamps.map(x => "<tr><td><strong>"+esc(x.name)+"</strong><small>Classe : "+esc(x.class)+" · 0x"+esc(x.actor)+"</small><small>Hiérarchie : "+esc(x.hierarchy || "—")+"</small></td><td>"+esc(x.component_class || "—")+"</td><td>"+(x.intensity == null ? "—" : Number(x.intensity).toFixed(3)+" lm")+"</td><td><small>Spotlight "+esc(x.spotlight_offset || "—")+"<br>Intensity "+esc(x.intensity_offset || "—")+"</small></td><td>"+esc(x.read_status)+"<small>"+esc(x.properties || "")+"</small></td></tr>").join("") : '<tr><td colspan="5">Aucun lampadaire détecté.</td></tr>';
    }
    return { update, destroy() { clearInterval(countdownTimer); } };
  }
  window.ETBModulePlugins = window.ETBModulePlugins || {};
  window.ETBModulePlugins.garage = { mount };
})();