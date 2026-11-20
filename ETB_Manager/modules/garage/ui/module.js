(() => {
  function mount(root, api) {
    const $ = id => root.querySelector("#" + id);
    const post = (action, payload = {}) => api.post("action", { action, ...payload }).catch(api.showError);
    $("lampClear")?.addEventListener("click", () => post("clear"));
    $("lampPlayers")?.addEventListener("change", event => post("players", { value: event.target.value }));

    const formatTime = seconds => Number.isFinite(seconds) ? Math.max(0, seconds).toFixed(2) + " s" : "—";
    const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));
    const setText = (id, text) => { const el = $(id); if (el && el.textContent !== text) el.textContent = text; };

    // Décompte entre deux sondages : le backend envoie des durées restantes, on les convertit en échéances locales.
    let countdown = null;
    function renderCountdown() {
      if (!countdown) return;
      const now = Date.now();
      const left = ms => Math.max(0, (ms - now) / 1000);
      if (countdown.overdue) {
        setText("lampCountdown", "Dépassé");
        setText("lampWindow", "Clignotement manqué ou délai modifié : la mesure repart au prochain rallumage.");
        return;
      }
      if (countdown.mode === "window") {
        setText("lampCountdown", "≈ " + formatTime(left(countdown.central)));
        setText("lampWindow", `Entre ${formatTime(left(countdown.min))} et ${formatTime(left(countdown.max))} (délai aléatoire à chaque cycle)`);
      } else {
        setText("lampCountdown", formatTime(left(countdown.central)));
        setText("lampWindow", "± " + ((countdown.max - countdown.min) / 2000).toFixed(2) + " s");
      }
    }
    const timer = setInterval(renderCountdown, 100);

    function update(state) {
      const d = state.module_state?.garage || {};
      const stats = d.stats || {};
      setText("lampState", d.lamp_state || (d.running ? "Recherche des lampes" : "Connexion…"));
      $("lampStateDot").dataset.state = d.lamp_state_key || "idle";
      setText("lampCount", String(stats.readable ?? stats.lamp_actors ?? 0));
      setText("lampPeriod", d.period_label || "Calibration…");
      setText("lampQuality", d.quality || "—");
      setText("lampEvents", String(d.event_count ?? 0));
      setText("lampStatus", d.status || "");
      const player = $("lampPlayers");
      if (player && d.player_count && document.activeElement !== player) player.value = d.player_count;

      const rejectedRow = $("lampRejectedRow");
      if (rejectedRow) {
        rejectedRow.hidden = !(d.rejected_count > 0);
        if (d.rejected_count > 0) setText("lampRejected", `${d.rejected_count} (dernière : ${Number(d.last_rejected).toFixed(2)} s) · vérifie le nombre de joueurs`);
      }

      if (d.timer_state === "running" && d.remaining != null) {
        const now = Date.now();
        countdown = {
          mode: d.prediction_mode, overdue: !!d.overdue,
          central: now + d.remaining * 1000, min: now + (d.remaining_min ?? d.remaining) * 1000, max: now + (d.remaining_max ?? d.remaining) * 1000,
        };
        setText("lampLead", d.blackout_lead != null ? `Coupure complète ≈ ${d.blackout_lead.toFixed(2)} s après le début du clignotement` : "");
        renderCountdown();
      } else {
        countdown = null;
        setText("lampWindow", "");
        setText("lampLead", "");
        setText("lampCountdown",
          d.timer_state === "off" ? "En attente du rallumage"
          : d.timer_state === "pending" ? "Fin du clignotement…"
          : (d.progress_label || "Détection des cycles…"));
      }

      const lamps = d.lamps || [];
      $("lampRows").innerHTML = lamps.length ? lamps.map(lamp => {
        const label = lamp.on == null ? "Inconnue" : (lamp.on ? "Allumée" : "Éteinte");
        const value = lamp.intensity == null ? "" : ` · ${Math.round(lamp.intensity)}`;
        return '<div class="lamp-row"><span class="lamp-mini-dot ' + (lamp.on ? "on" : "off") + '"></span><span>' + esc(lamp.name) + '</span><strong>' + label + value + '</strong></div>';
      }).join("") : '<span class="muted">Aucun lampadaire détecté.</span>';
    }

    return { update, destroy() { clearInterval(timer); } };
  }
  window.ETBModulePlugins = window.ETBModulePlugins || {};
  window.ETBModulePlugins.garage = { mount };
})();
