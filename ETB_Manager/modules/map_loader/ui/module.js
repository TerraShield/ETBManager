export function mount(root) {
  // Module de configuration cartographique ; les fonctions d'importation seront ajoutées ici.
  root.querySelectorAll(".map-loader-card").forEach((card) => {
    card.addEventListener("click", () => card.classList.toggle("is-active"));
  });
}
