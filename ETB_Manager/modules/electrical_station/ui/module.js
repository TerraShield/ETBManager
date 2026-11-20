(() => {
  function mount(root, api) {
    const $ = id => root.querySelector("#" + id);
    const post = action => api.post("action", {action}).catch(api.showError);
    $("esStart")?.addEventListener("click", () => post("start"));
    $("esStop")?.addEventListener("click", () => post("stop"));
    let lastMapUrl = "";
    let currentFuseboxes = [];
    const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
    const yesNo = value => value === true ? "Oui" : value === false ? "Non" : "Inconnu";
    const stateClass = value => value === true ? "on" : value === false ? "off" : "unknown";
    function renderList(items) {
      if (!items.length) {
        $("esFuseboxList").innerHTML = '<p class="muted">Aucune fusebox détectée pour le moment.</p>';
        return;
      }
      $("esFuseboxList").innerHTML = items.map((item,index) => {
        const id = item.fusebox_index == null ? "Fusebox " + (index+1) : "Fusebox " + item.fusebox_index;
        return '<article class="es-fusebox-row"><div class="es-fusebox-number">'+(index+1)+'</div>'+
          '<div class="es-fusebox-main"><strong>'+esc(id)+'</strong><small>'+esc(item.name)+' · '+esc(item.class)+'</small>'+
          '<small>X '+Number(item.x).toFixed(1)+' · Y '+Number(item.y).toFixed(1)+' · Z '+Number(item.z).toFixed(1)+'</small></div>'+
          '<div class="es-fusebox-states"><span class="es-pill '+stateClass(item.is_enabled)+'">Enabled : '+yesNo(item.is_enabled)+'</span>'+
          '<span class="es-pill '+stateClass(item.is_door_open)+'">Porte : '+yesNo(item.is_door_open)+'</span></div></article>';
      }).join("");
    }
    function renderMarkers(data) {
      const b=data.map_config?.worldBounds;
      const valid=data.map_calibrated && b;
      $("esMapMarkers").innerHTML="";
      if (!valid) return;
      currentFuseboxes.forEach((item,index) => {
        const x=(Number(item.x)-Number(b.minX))/(Number(b.maxX)-Number(b.minX))*100;
        const y=(Number(b.maxY)-Number(item.y))/(Number(b.maxY)-Number(b.minY))*100;
        if (!Number.isFinite(x)||!Number.isFinite(y)||x<0||x>100||y<0||y>100) return;
        const marker=document.createElement("button");
        marker.type="button";
        marker.className="es-map-marker "+(item.is_enabled===true?"enabled":item.is_enabled===false?"disabled":"unknown");
        marker.style.left=x+"%"; marker.style.top=y+"%";
        marker.title=item.name+" · X "+item.x+" · Y "+item.y;
        marker.textContent=item.fusebox_index==null?"F":String(item.fusebox_index);
        marker.addEventListener("click",()=>{
          const row=$("esFuseboxList").children[index];
          row?.scrollIntoView({behavior:"smooth",block:"center"});
          row?.classList.add("highlight");
          setTimeout(()=>row?.classList.remove("highlight"),1300);
        });
        $("esMapMarkers").appendChild(marker);
      });
    }
    function update(state) {
      const d=state.module_state?.electrical_station||{};
      $("esStatus").textContent=d.status||d.backend_error||"En attente du scanner…";
      $("esDot").className=d.connected&&d.level_active?"active":d.connected?"connected":"idle";
      $("esCount").textContent=String(d.count??0);
      $("esLevel").textContent=d.level_active?"ElectricalStation":d.connected?"Autre niveau":"Déconnecté";
      $("esManagerFound").textContent=d.manager_found?"Détecté":"Introuvable";
      $("esManager").hidden=!d.manager_found;
      if(d.manager_found){
        $("esEast").textContent=yesNo(d.manager.east_open);
        $("esWest").textContent=yesNo(d.manager.west_open);
        $("esGenerator").textContent=yesNo(d.manager.generator_open);
        $("esAllGenerators").textContent=yesNo(d.manager.all_generators_powered);
      }
      currentFuseboxes=d.fuseboxes||[];
      renderList(currentFuseboxes);
      $("esLastScan").textContent=d.last_scan?"Mis à jour à "+new Date(d.last_scan*1000).toLocaleTimeString():"Aucun relevé";
      if(d.map_available&&d.map_url){
        $("esMapViewport").hidden=false;
        $("esMapStatus").textContent=d.map_calibrated?"Carte calibrée":"Carte détectée · calibration requise";
        if(lastMapUrl!==d.map_url){$("esMapImage").src=d.map_url;lastMapUrl=d.map_url;}
        $("esMapHelp").textContent=d.map_calibrated?"Clique sur un marqueur pour retrouver sa fiche dans la liste.":"Image chargée. Renseigne minX, maxX, minY et maxY dans public/map.config.json.";
        renderMarkers(d);
      } else {
        $("esMapViewport").hidden=true;
        $("esMapStatus").textContent="Carte non installée";
        $("esMapHelp").textContent="Dépose une image dans modules/electrical_station/public/, puis configure ses limites Unreal dans map.config.json.";
      }
    }
    return {update,destroy(){}};
  }
  window.ETBModulePlugins=window.ETBModulePlugins||{};
  window.ETBModulePlugins.electrical_station={mount};
})();
