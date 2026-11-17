(() => {
  function mount(root) {
    return { update() {}, destroy() {} };
  }
  window.ETBModulePlugins = window.ETBModulePlugins || {};
  window.ETBModulePlugins.map_loader = { mount };
})();
