(() => {
  const COLORS = [
    { value: 0, label: "Rouge", className: "red" },
    { value: 1, label: "Vert", className: "green" },
    { value: 2, label: "Bleu", className: "blue" },
    { value: 3, label: "Jaune", className: "yellow" },
  ];
  function mount(root) {
    const $ = (selector) => root.querySelector(selector);
    function update(state) {
      const puzzle = state.car_puzzle || {};
      const rows = [...root.querySelectorAll("[data-code-row]")];
      const active = !!puzzle.active;
      const found = !!puzzle.found;
      const colors = Array.isArray(puzzle.colors) ? puzzle.colors : [];
      const values = colors.map((item) => Number(item.value));
      $("#carPuzzleDot")?.classList.toggle("is-active", active && found);
      $("#intercomLed")?.classList.toggle("is-active", active && found);
      $("#carPuzzleStatus").textContent = puzzle.status || "En attente du niveau TopFloor…";
      $("#carPuzzleCount").textContent = puzzle.cars_needed ?? 4;
      $("#carPuzzleManager").textContent = puzzle.manager_name || "BP_Car_Manager_C";
      $("#carPuzzleReadState").textContent = found ? "LECTURE MÉMOIRE : OK" : (active ? "LECTURE MÉMOIRE : RECHERCHE" : "LECTURE MÉMOIRE : EN ATTENTE");
      $("#carPuzzleSource").textContent = puzzle.source === "simulation"
        ? "Source : données de démonstration (--fake)"
        : "Source : BP_Car_Manager_C · CorrectColors";
      rows.forEach((row, index) => {
        const value = values[index];
        const valid = Number.isInteger(value);
        row.querySelectorAll(".intercom-key").forEach((key) => {
          const lit = found && valid && Number(key.dataset.color) === value;
          key.classList.toggle("is-lit", lit);
          key.classList.toggle("is-muted", !lit);
          key.setAttribute("aria-current", lit ? "true" : "false");
        });
        row.querySelector(".intercom-extra")?.remove();
        if (found && valid && (value < 0 || value > 3)) {
          const extra = document.createElement("span");
          extra.className = "intercom-extra";
          extra.textContent = colors[index]?.label || `Couleur ${value}`;
          row.appendChild(extra);
        }
      });
      const sequence = $("#carPuzzleSequence");
      sequence.replaceChildren();
      if (found && colors.length) {
        colors.forEach((entry, index) => {
          const color = COLORS.find((item) => item.value === Number(entry.value));
          const chip = document.createElement("div");
          chip.className = "sequence-chip";
          const number = document.createElement("span"); number.className = "sequence-number"; number.textContent = String(index + 1).padStart(2, "0");
          const swatch = document.createElement("i"); swatch.className = `sequence-swatch color-${color?.className || "unknown"}`;
          const label = document.createElement("strong"); label.textContent = entry.label || color?.label || `Couleur ${entry.value}`;
          chip.append(number, swatch, label); sequence.appendChild(chip);
        });
      } else {
        const placeholder = document.createElement("div");
        placeholder.className = "sequence-placeholder";
        placeholder.textContent = active ? "Gestionnaire de voitures en attente ou combinaison non lisible." : "Les couleurs apparaîtront ici lorsque le niveau TopFloor sera chargé.";
        sequence.appendChild(placeholder);
      }
    }
    return { update, destroy() {} };
  }
  window.ETBModulePlugins = window.ETBModulePlugins || {};
  window.ETBModulePlugins.level1 = { mount };
})();
