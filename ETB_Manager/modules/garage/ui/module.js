(() => {
  function mount(root, api) {
    const $ = id => root.querySelector("#" + id);
    const post = (action, payload = {}) => api.post("action", { action, ...payload }).catch(api.showError);
    $("lampClear")?.addEventListener("click", () => post("clear"));
    $("lampPlayers")?.addEventListener("change", event => post("players", { value: event.target.value }));

    let countdownDeadline = null;
    const timer = setInterval(() => {
      const now = Date.now();
      $("lampCountdown").textContent = countdownDeadline == null ? "—" : formatTime(Math.max(0, (countdownDeadline - now) / 1000));
    }, 100);

    const formatTime = seconds => {
      if (!Number.isFinite(seconds)) return "—";
      return Math.max(0, seconds).toFixed(2) + " s";
    };
    const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));

    function update(state) {
      const d = state.module_state?.garage || {};
      const stats = d.stats || {};
      const stateLabel = d.lamp_state || (d.running ? "Recherche des lampes" : "Connexion…");
      $("lampState").textContent = stateLabel;
      $("lampStateDot").dataset.state = d.lamp_state_key || "idle";
      $("lampCount").textContent = stats.readable ?? stats.lamp_actors ?? 0;
      $("lampPeriod").textContent = d.period_label || "Calibration…";
      $("lampQuality").textContent = d.quality || "—";
      $("lampEvents").textContent = d.event_count ?? 0;
      $("lampStatus").textContent = d.status || "";
      if ($("lampPlayers") && d.player_count) $("lampPlayers").value = d.player_count;
      countdownDeadline = d.remaining == null ? null : Date.now() + d.remaining * 1000;
      $("lampCountdown").textContent = d.remaining == null ? "En attente de calibration" : formatTime(d.remaining);

      const lamps = d.lamps || [];
      $("lampRows").innerHTML = lamps.length ? lamps.map(lamp => {
        const on = Number(lamp.intensity) >= 50;
        const status = lamp.intensity == null ? "Inconnue" : (on ? "Allumée" : "Éteinte");
        return '<div class="lamp-row"><span class="lamp-mini-dot '+(on ? "on" : "off")+'"></span><span>'+esc(lamp.name)+'</span><strong>'+status+'</strong></div>';
      }).join("") : '<span class="muted">Aucun lampadaire détecté.</span>';
    }

    return { update, destroy() { clearInterval(timer); } };
  }
  window.ETBModulePlugins = window.ETBModulePlugins || {};
  window.ETBModulePlugins.garage = { mount };
})();
