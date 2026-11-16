(() => {
  function mount(root, api) {
    const message = root.querySelector("#moduleMessage");
    root.querySelector("#moduleRefresh")?.addEventListener("click", () => api.refresh());
    function update(state) {
      const moduleData = state.module_state?.mon_module || {};
      message.textContent = moduleData.message || "Module chargé. Remplace ce texte par ton interface.";
    }
    return { update, destroy() {} };
  }
  window.ETBModulePlugins = window.ETBModulePlugins || {};
  window.ETBModulePlugins.mon_module = { mount };
})();
