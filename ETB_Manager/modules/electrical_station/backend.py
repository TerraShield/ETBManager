"""Détection mémoire des boîtiers à fusibles d'ElectricStation."""
from __future__ import annotations
import html, json, logging, math, struct, threading, time
from pathlib import Path
log = logging.getLogger("ETB.ElectricalStation")
OFF_WORLD_PERSISTENT_LEVEL=0x30
OFF_WORLD_LEVELS=0x138
OFF_LEVEL_ACTORS=0x98
OFF_UOBJECT_CLASS=0x10
OFF_USTRUCT_SUPER=0x40
OFF_ACTOR_ROOT_COMPONENT=0x130
OFF_SCENE_COMPONENT_TO_WORLD_TRANSLATION=0x1D0
MAX_LEVELS=256
MAX_ACTORS_PER_LEVEL=250_000
SCAN_INTERVAL=1.0
from memory_reader import (PROCESS_NAME, ProcessMemory, FNameResolver, find_process_id,
    find_module, locate_gworld, locate_gnames, _read_i32,
    _read_unreal_tarray_pointers, _find_unreal_property_offset)

def _read_bool(mem,address):
    raw=mem.read(address,1)
    return bool(raw[0]) if raw else None

class ElectricalStationScanner:
    def __init__(self):
        self.lock=threading.RLock()
        self.stop_event=threading.Event()
        self.thread=None
        self.map_scan_requested=threading.Event()
        self.auto_map_attempted_world=None
        self.mem=self.pid=self.world_slot=self.resolver=None
        self.state={"running":False,"connected":False,"level_active":False,
            "status":"En attente d'Escape the Backrooms…","fuseboxes":[],"count":0,
            "manager_found":False,"manager":{},"last_scan":None,"scan_error":None,
            "map_scan_status":"En attente","map_wall_count":0}
    def start(self):
        if self.thread and self.thread.is_alive():
            return {"ok":True,"message":"Le scanner est déjà actif."}
        self.stop_event.clear()
        self.thread=threading.Thread(target=self._run,name="ETB-ElectricalStation",daemon=True)
        self.thread.start()
        with self.lock:
            self.state["running"]=True
            self.state["status"]="Recherche des fuseboxes…"
        log.info("SCANNER_STARTED")
        return {"ok":True,"message":"Recherche des fuseboxes démarrée."}
    def stop(self):
        self.stop_event.set()
        if self.thread and self.thread.is_alive(): self.thread.join(timeout=2)
        self._disconnect()
        with self.lock:
            self.state["running"]=False
            self.state["status"]="Scanner arrêté."
        log.info("SCANNER_STOPPED")
        return {"ok":True,"message":"Scanner arrêté."}
    def _disconnect(self):
        if self.mem:
            try: self.mem.close()
            except Exception: pass
        self.mem=self.pid=self.world_slot=self.resolver=None
    def _connect(self):
        pid=find_process_id(PROCESS_NAME)
        mem=ProcessMemory(pid)
        try:
            base,_=find_module(pid,PROCESS_NAME)
            slot=locate_gworld(mem,base)
            try: resolver=FNameResolver(mem,locate_gnames(mem,base))
            except Exception:
                log.exception("GNAMES_UNAVAILABLE")
                resolver=None
        except Exception:
            mem.close()
            raise
        self.mem,self.pid,self.world_slot,self.resolver=mem,pid,slot,resolver
        log.info("PROCESS_CONNECTED pid=%s gworld=0x%X names=%s",pid,slot,bool(resolver))
    def _class_chain(self,class_address):
        chain=[]
        seen=set()
        current=class_address
        while current and current not in seen and len(chain)<32:
            seen.add(current)
            name=self.resolver.object_name(current) if self.resolver else "?"
            chain.append((current,name))
            current=self.mem.ptr(current+OFF_USTRUCT_SUPER)
        return chain
    def _property_offsets(self,class_address,names):
        return {name:_find_unreal_property_offset(self.mem,self.resolver,class_address,name) for name in names}
    def _read_fusebox(self,actor,class_name,offsets):
        root=self.mem.ptr(actor+OFF_ACTOR_ROOT_COMPONENT)
        position=self.mem.vec3(component+OFF_SCENE_COMPONENT_TO_WORLD_TRANSLATION) if root else None
        if position is None: return None
        io=offsets.get("FuseboxIndex")
        eo=offsets.get("IsEnabled")
        do=offsets.get("IsDoorOpen")
        index=_read_i32(self.mem,actor+io) if io is not None else None
        enabled=_read_bool(self.mem,actor+eo) if eo is not None else None
        door_open=_read_bool(self.mem,actor+do) if do is not None else None
        return {"address":f"0x{actor:X}","name":self.resolver.object_name(actor),
            "class":class_name,"fusebox_index":index,"x":round(position[0],2),
            "y":round(position[1],2),"z":round(position[2],2),
            "is_enabled":enabled,"is_door_open":door_open,"group":"unknown"}
    def _scan_static_meshes(self, world):
        """Lit les StaticMeshComponent racines et leurs bounds depuis la mémoire UE4."""
        if not self.resolver:
            raise RuntimeError("GNames indisponible : impossible d'identifier les StaticMeshComponent.")
        levels=[]
        persistent=self.mem.ptr(world+OFF_WORLD_PERSISTENT_LEVEL)
        if persistent: levels.append(persistent)
        levels.extend(_read_unreal_tarray_pointers(self.mem,world+OFF_WORLD_LEVELS,MAX_LEVELS))
        levels=list(dict.fromkeys(x for x in levels if x))
        records=[]
        seen=set()
        component_offsets={}
        bounds_offsets={}
        actor_component_offsets={}
        seen_components=set()
        actor_total=0
        component_total=0
        for level in levels:
            actors=_read_unreal_tarray_pointers(self.mem,level+OFF_LEVEL_ACTORS,MAX_ACTORS_PER_LEVEL)
            for actor in actors:
                if not actor or actor in seen: continue
                seen.add(actor); actor_total+=1
                root=self.mem.ptr(actor+OFF_ACTOR_ROOT_COMPONENT)
                components=[root] if root else []
                actor_class=self.mem.ptr(actor+OFF_UOBJECT_CLASS)
                if actor_class:
                    if actor_class not in actor_component_offsets:
                        actor_component_offsets[actor_class]=[
                            _find_unreal_property_offset(self.mem,self.resolver,actor_class,name)
                            for name in ("InstanceComponents","BlueprintCreatedComponents")
                        ]
                    for offset in actor_component_offsets[actor_class]:
                        if offset is not None:
                            components.extend(_read_unreal_tarray_pointers(self.mem,actor+offset,2048))
                for component in components:
                    if not component or component in seen_components: continue
                    seen_components.add(component)
                    component_class=self.mem.ptr(component+OFF_UOBJECT_CLASS)
                    if not component_class: continue
                    chain=self._class_chain(component_class)
                    if not any("staticmeshcomponent" in name.casefold() for _,name in chain): continue
                    component_total+=1
                    if component_class not in component_offsets:
                        component_offsets[component_class]=_find_unreal_property_offset(self.mem,self.resolver,component_class,"StaticMesh")
                    mesh_offset=component_offsets[component_class]
                    if mesh_offset is None: continue
                    mesh=self.mem.ptr(component+mesh_offset)
                    if not mesh: continue
                    mesh_class=self.mem.ptr(mesh+OFF_UOBJECT_CLASS)
                    if not mesh_class: continue
                    if mesh_class not in bounds_offsets:
                        bounds_offsets[mesh_class]=_find_unreal_property_offset(self.mem,self.resolver,mesh_class,"ExtendedBounds")
                    bounds_offset=bounds_offsets[mesh_class]
                    if bounds_offset is None: continue
                    raw=self.mem.read(mesh+bounds_offset,28)
                    if not raw: continue
                    values=struct.unpack("<7f",raw)
                    if not all(math.isfinite(v) for v in values): continue
                    ox,oy,oz,ex,ey,ez,_radius=values
                    if min(ex,ey,ez)<0 or max(ex,ey,ez)>100000: continue
                    transform=self.mem.read(component+0x1C0,40)
                    yaw=0.0; sx=sy=sz=1.0
                    if transform:
                        tv=struct.unpack("<10f",transform)
                        qx,qy,qz,qw=tv[:4]
                        if all(math.isfinite(v) for v in tv) and 0.5 < sum(v*v for v in (qx,qy,qz,qw)) < 1.5:
                            yaw=math.degrees(math.atan2(2*(qw*qz+qx*qy),1-2*(qy*qy+qz*qz)))
                            sx,sy,sz=tv[7:10]
                            if max(abs(sx),abs(sy),abs(sz))>100 or min(abs(sx),abs(sy),abs(sz))<0.001:
                                sx=sy=sz=1.0
                    position=self.mem.vec3(component+OFF_SCENE_COMPONENT_TO_WORLD_TRANSLATION)
                    if position is None: continue
                    angle=math.radians(yaw)
                    local_x,local_y=ox*sx,oy*sy
                    cx=position[0]+local_x*math.cos(angle)-local_y*math.sin(angle)
                    cy=position[1]+local_x*math.sin(angle)+local_y*math.cos(angle)
                    cz=position[2]+oz*sz
                    hx=abs(ex*sx*math.cos(angle))+abs(ey*sy*math.sin(angle))
                    hy=abs(ex*sx*math.sin(angle))+abs(ey*sy*math.cos(angle))
                    hz=abs(ez*sz)
                    # On retient les volumes verticaux plausibles (murs, portes, piliers), pas les sols plats.
                    if hz<60 or max(hx,hy)<35: continue
                    mesh_name=self.resolver.object_name(mesh)
                    actor_name=self.resolver.object_name(actor)
                    outer_parts=[]; outer=mesh
                    for _ in range(8):
                        if not outer: break
                        part=self.resolver.object_name(outer)
                        if part and part!="?": outer_parts.append(part)
                        outer=self.mem.ptr(outer+0x20)
                    asset_path="/".join(reversed(outer_parts))
                    records.append({"actor":actor_name,"mesh":mesh_name,"asset_path":asset_path,
                        "x":cx,"y":cy,"z":cz,"half_x":hx,"half_y":hy,"half_z":hz,"yaw":yaw})
        log.info("AUTO_MAP_MESH_SCAN actors=%s static_mesh_components=%s wall_candidates=%s",actor_total,component_total,len(records))
        return records

    def _write_auto_map(self, records, level_name):
        if not records:
            raise RuntimeError("Aucun mesh vertical exploitable n'a été trouvé dans ce niveau.")
        min_x=min(r["x"]-r["half_x"] for r in records); max_x=max(r["x"]+r["half_x"] for r in records)
        min_y=min(r["y"]-r["half_y"] for r in records); max_y=max(r["y"]+r["half_y"] for r in records)
        margin=max(150.0,(max(max_x-min_x,max_y-min_y))*0.025)
        min_x-=margin; max_x+=margin; min_y-=margin; max_y+=margin
        width=max(1.0,max_x-min_x); height=max(1.0,max_y-min_y)
        shapes=[]
        for item in records:
            x=item["x"]-item["half_x"]; y=item["y"]-item["half_y"]
            title=html.escape(f"{item['actor']} · {item['mesh']} · {item['asset_path']}",quote=True)
            shapes.append(f'<rect x="{x:.2f}" y="{y:.2f}" width="{2*item["half_x"]:.2f}" height="{2*item["half_y"]:.2f}" rx="8" fill="#8da2b8" fill-opacity="0.62" stroke="#d6e0ea" stroke-width="12"><title>{title}</title></rect>')
        svg=('<?xml version="1.0" encoding="UTF-8"?>'
             f'<svg xmlns="http://www.w3.org/2000/svg" width="1400" height="900" viewBox="{min_x:.2f} {-max_y:.2f} {width:.2f} {height:.2f}">'
             '<rect x="-1000000" y="-1000000" width="2000000" height="2000000" fill="#17212d"/>'
             '<g transform="scale(1,-1)">'+'<g>'+''.join(shapes)+'</g></g>'
             '</svg>')
        public=self.root/"public"; public.mkdir(parents=True,exist_ok=True)
        (public/"electrical_station_auto_map.svg").write_text(svg,encoding="utf-8")
        config_path=public/"map.config.json"
        try: config=json.loads(config_path.read_text(encoding="utf-8")) if config_path.is_file() else {}
        except Exception: config={}
        config.update({"version":1,"source":"runtime_static_mesh_scan","level":level_name,
            "worldBounds":{"minX":min_x,"maxX":max_x,"minY":min_y,"maxY":max_y},
            "autoMap":{"wallCandidates":len(records),"generatedAt":time.time()}})
        config_path.write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding="utf-8")
        (public/"auto_map_data.json").write_text(json.dumps({"level":level_name,"wallCandidates":records},ensure_ascii=False,indent=2),encoding="utf-8")
        return len(records)

    def _auto_map_needed(self):
        public=self.root/"public"
        manual=[p for suffix in (".png",".jpg",".jpeg",".webp") for p in public.glob("*"+suffix)]
        generated=public/"electrical_station_auto_map.svg"
        return not manual and not generated.is_file()

    def _scan_world(self,world):
        if not self.resolver: return [],{},"GNames indisponible : les classes Unreal ne peuvent pas être identifiées."
        levels=[]
        persistent=self.mem.ptr(world+OFF_WORLD_PERSISTENT_LEVEL)
        if persistent: levels.append(persistent)
        levels.extend(_read_unreal_tarray_pointers(self.mem,world+OFF_WORLD_LEVELS,MAX_LEVELS))
        levels=list(dict.fromkeys(x for x in levels if x))
        found=[]
        manager_data={}
        manager_found=False
        seen=set()
        offsets_by_class={}
        for level in levels:
            actors=_read_unreal_tarray_pointers(self.mem,level+OFF_LEVEL_ACTORS,MAX_ACTORS_PER_LEVEL)
            for actor in actors:
                if not actor or actor in seen: continue
                seen.add(actor)
                class_address=self.mem.ptr(actor+OFF_UOBJECT_CLASS)
                if not class_address: continue
                chain=self._class_chain(class_address)
                names=[name.casefold() for _,name in chain]
                class_name=chain[0][1] if chain else "?"
                if "bp_stationmanager_c" in names:
                    manager_found=True
                    if class_address not in offsets_by_class:
                        offsets_by_class[class_address]=self._property_offsets(class_address,[
                            "HasOpenedEastSubstation","HasOpenedWestSubstation",
                            "HasOpenedGenerator","HasPoweredAllGenerators"])
                    mo=offsets_by_class[class_address]
                    manager_data={key:_read_bool(self.mem,actor+mo[prop]) if mo.get(prop) is not None else None
                        for key,prop in {"east_open":"HasOpenedEastSubstation",
                        "west_open":"HasOpenedWestSubstation","generator_open":"HasOpenedGenerator",
                        "all_generators_powered":"HasPoweredAllGenerators"}.items()}
                    manager_data["name"]=self.resolver.object_name(actor)
                if "bp_fusebox_base_c" not in names: continue
                base_class=next(addr for addr,name in chain if name.casefold()=="bp_fusebox_base_c")
                if base_class not in offsets_by_class:
                    offsets_by_class[base_class]=self._property_offsets(base_class,
                        ["FuseboxIndex","IsEnabled","IsDoorOpen"])
                item=self._read_fusebox(actor,class_name,offsets_by_class[base_class])
                if item: found.append(item)
        found.sort(key=lambda x:(x["fusebox_index"] is None,x["fusebox_index"] or 0,x["name"].casefold()))
        return found,manager_data,None
    def _run(self):
        while not self.stop_event.is_set():
            try:
                if self.mem is None: self._connect()
                if find_process_id(PROCESS_NAME)!=self.pid: raise RuntimeError("Le processus du jeu a changé.")
                world=self.mem.ptr(self.world_slot)
                if not world: raise RuntimeError("GWorld est nul : entre dans une partie.")
                world_name=self.resolver.object_name(world) if self.resolver else "?"
                if world_name.casefold()!="electricalstation":
                    with self.lock:
                        self.state.update({"connected":True,"level_active":False,
                            "status":f"Niveau détecté : {world_name}. En attente d'ElectricalStation.",
                            "fuseboxes":[],"count":0,"manager_found":False,"manager":{},
                            "last_scan":time.time(),"scan_error":None})
                else:
                    fuseboxes,manager,error=self._scan_world(world)
                    force_map=self.map_scan_requested.is_set()
                    if force_map: self.map_scan_requested.clear()
                    if force_map: self.auto_map_attempted_world=None
                    map_status="Carte déjà disponible" if not self._auto_map_needed() else "En attente de génération"
                    map_count=0
                    should_scan_map=force_map or (self._auto_map_needed() and self.auto_map_attempted_world!=world)
                    if should_scan_map:
                        self.auto_map_attempted_world=world
                        with self.lock: self.state["map_scan_status"]="Analyse des meshes du niveau…"
                        try:
                            mesh_records=self._scan_static_meshes(world)
                            map_count=self._write_auto_map(mesh_records,world_name)
                            map_status=f"Carte générée : {map_count} éléments verticaux"
                            log.info("AUTO_MAP_GENERATED level=%s candidates=%s",world_name,map_count)
                        except Exception as map_exc:
                            map_status=f"Échec de la cartographie automatique : {map_exc}"
                            log.exception("AUTO_MAP_FAILED level=%s",world_name)
                    with self.lock:
                        self.state.update({"connected":True,"level_active":True,
                            "status":error or f"{len(fuseboxes)} fusebox(es) détectée(s).",
                            "fuseboxes":fuseboxes,"count":len(fuseboxes),
                            "manager_found":bool(manager),"manager":manager,
                            "map_scan_status":map_status,"map_wall_count":map_count or self.state.get("map_wall_count",0),
                            "last_scan":time.time(),"scan_error":error})
                    log.info("SCAN_OK level=%s fuseboxes=%s manager=%s",world_name,len(fuseboxes),bool(manager))
            except Exception as exc:
                log.warning("SCAN_FAILED error=%s: %s",type(exc).__name__,exc)
                with self.lock:
                    self.state.update({"connected":False,"level_active":False,
                        "status":str(exc),"scan_error":str(exc)})
                self._disconnect()
                self.stop_event.wait(1.5)
            self.stop_event.wait(SCAN_INTERVAL)
        self._disconnect()
    def snapshot(self):
        with self.lock:
            result=dict(self.state)
            result["fuseboxes"]=[dict(x) for x in self.state["fuseboxes"]]
            result["manager"]=dict(self.state["manager"])
        return result

class ElectricalStationBackend:
    def __init__(self,context):
        self.root=context.module_dir
        self.scanner=ElectricalStationScanner()
        self.scanner.start()
    def snapshot(self,shared_state):
        import json
        data=self.scanner.snapshot()
        public=self.root/"public"
        images=sorted([p for suffix in (".png",".jpg",".jpeg",".webp",".svg") for p in public.glob("*"+suffix) if p.name!="electrical_station_auto_map.svg"],key=lambda p:p.name.casefold())
        auto_image=public/"electrical_station_auto_map.svg"
        if not images and auto_image.is_file(): images=[auto_image]
        data["map_available"]=bool(images)
        data["map_auto_generated"]=bool(images and images[0].name=="electrical_station_auto_map.svg")
        data["map_url"]=f"/modules/electrical_station/public/{images[0].name}" if images else None
        data["map_filename"]=images[0].name if images else None
        if data["map_auto_generated"]: data["map_scan_status"]=data.get("map_scan_status") or "Carte générée automatiquement"
        path=public/"map.config.json"
        try: config=json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        except Exception as exc:
            config={}
            data["map_config_error"]=str(exc)
        data["map_config"]=config
        bounds=config.get("worldBounds")
        data["map_calibrated"]=isinstance(bounds,dict) and all(isinstance(bounds.get(k),(int,float)) for k in ("minX","maxX","minY","maxY")) and bounds["maxX"]!=bounds["minX"] and bounds["maxY"]!=bounds["minY"]
        return data
    def action(self,action,payload,shared_state):
        if action=="start": return self.scanner.start()
        if action=="stop": return self.scanner.stop()
        if action=="rescan": return {"ok":True,"message":"Le prochain relevé sera effectué automatiquement."}
        if action=="generate_map":
            self.scanner.map_scan_requested.set()
            return {"ok":True,"message":"Analyse des meshes demandée. Le résultat apparaîtra automatiquement."}
        raise ValueError(f"Action ElectricalStation inconnue : {action}")
    def shutdown(self): self.scanner.stop()

def create_module(context): return ElectricalStationBackend(context)
