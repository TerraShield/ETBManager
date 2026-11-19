"""Diagnostic mémoire en lecture seule des BP_Lamp_C du Garage BottomFloor."""
from __future__ import annotations
import logging, math, struct, threading, time
from memory_reader import (PROCESS_NAME, ProcessMemory, find_process_id, find_module, locate_gworld, locate_gnames, FNameResolver, _read_unreal_tarray_pointers, _find_unreal_property_offset, _read_i32, _read_fname_at, OFF_WORLD_PERSISTENT_LEVEL, OFF_WORLD_LEVELS, OFF_LEVEL_ACTORS, OFF_UOBJECT_CLASS)

log = logging.getLogger("ETB.BottomFloor.Lamps")
MAX_LEVELS = 256
MAX_ACTORS = 250000

class LampMemoryScanner:
    def __init__(self):
        self.lock=threading.RLock(); self.stop_event=threading.Event(); self.thread=None
        self.running=False; self.status="Diagnostic arrêté. Charge BottomFloor puis démarre le scan."
        self.world_name=None; self.last_scan=None; self.lamps=[]
        self.stats={"actors_seen":0,"lamp_actors":0,"readable":0,"unreadable":0}
    def start(self):
        with self.lock:
            if self.running: return {"ok":True,"message":"Diagnostic déjà actif."}
            self.stop_event.clear(); self.running=True
            self.status="Connexion au jeu et recherche des BP_Lamp_C…"
            self.thread=threading.Thread(target=self._run,name="BottomFloorLampScan",daemon=True); self.thread.start()
        log.info("Démarrage du diagnostic BP_Lamp_C / Spotlight.")
        return {"ok":True,"message":self.status}
    def stop(self):
        self.stop_event.set()
        with self.lock: self.running=False; self.status="Diagnostic arrêté."
        log.info("Arrêt du diagnostic des lampadaires.")
        return {"ok":True,"message":self.status}
    def clear(self):
        with self.lock: self.lamps=[]; self.stats={"actors_seen":0,"lamp_actors":0,"readable":0,"unreadable":0}
        log.info("Mesures affichées effacées."); return {"ok":True}
    def _run(self):
        mem=None
        try:
            pid=find_process_id(PROCESS_NAME); mem=ProcessMemory(pid); base,_=find_module(pid,PROCESS_NAME)
            slot=locate_gworld(mem,base); names=locate_gnames(mem,base); resolver=FNameResolver(mem,names)
            log.info("Connexion PID=%s base=0x%X GWorldSlot=0x%X GNames=0x%X",pid,base,slot,names)
            while not self.stop_event.is_set():
                try:
                    world=mem.ptr(slot)
                    if not world: raise RuntimeError("GWorld nul")
                    world_name=resolver.object_name(world)
                    result=self._scan_world(mem,world,resolver)
                    now=time.time()
                    with self.lock:
                        previous={x["actor"]:x for x in self.lamps}; self.lamps=result["lamps"]
                        self.stats=result["stats"]; self.world_name=world_name; self.last_scan=now; self.status=result["status"]
                    for lamp in result["lamps"]:
                        old_lamp=previous.get(lamp["actor"])
                        before=old_lamp.get("intensity") if old_lamp else None
                        changed=old_lamp is None or before != lamp["intensity"] or old_lamp.get("read_status") != lamp["read_status"]
                        if not changed:
                            continue
                        if old_lamp is not None and before is not None and lamp["intensity"] is not None:
                            log.warning("VARIATION %s (0x%s) : %.5f -> %.5f lm",lamp["name"],lamp["actor"],before,lamp["intensity"])
                        log.info("LAMP name=%s class=%s actor=0x%s component=0x%s class_component=%s intensity=%s unit=lumens offsets=%s/%s status=%s",lamp["name"],lamp["class"],lamp["actor"],lamp["spotlight"] or "?",lamp["component_class"] or "?",lamp["intensity"],lamp["spotlight_offset"] or "?",lamp["intensity_offset"] or "?",lamp["read_status"])
                    if world_name.casefold() != "bottomfloor": log.warning("Niveau courant=%s (BottomFloor attendu).",world_name)
                except Exception as exc:
                    with self.lock: self.status="Erreur de lecture : "+str(exc)
                    log.exception("Échec du scan BottomFloor")
                self.stop_event.wait(1.5)
        except Exception as exc:
            with self.lock: self.status="Connexion impossible : "+str(exc)
            log.exception("Initialisation du scanner impossible")
        finally:
            if mem: mem.close()
            with self.lock: self.running=False
    def _describe_class_properties(self, mem, resolver, class_address):
        """Énumère les propriétés de la classe et de ses parents."""
        classes = []
        current = class_address
        visited_classes = set()
        while current and current not in visited_classes and len(classes) < 32:
            visited_classes.add(current)
            class_name = resolver.object_name(current)
            fields = []
            field = mem.ptr(current + OFF_USTRUCT_CHILD_PROPERTIES)
            visited_fields = set()
            steps = 0
            while field and field not in visited_fields and steps < 4096:
                visited_fields.add(field)
                steps += 1
                name = _read_fname_at(mem, resolver, field + OFF_FFIELD_NAME)
                offset = _read_i32(mem, field + OFF_FPROPERTY_OFFSET)
                fields.append({"name": name, "offset": offset})
                field = mem.ptr(field + OFF_FFIELD_NEXT)
            classes.append({"name": class_name, "fields": fields, "field_count": steps})
            current = mem.ptr(current + OFF_USTRUCT_SUPER)
        return classes

    def _scan_world(self,mem,world,resolver):
        levels=[]; persistent=mem.ptr(world+OFF_WORLD_PERSISTENT_LEVEL)
        if persistent: levels.append(persistent)
        levels.extend(_read_unreal_tarray_pointers(mem,world+OFF_WORLD_LEVELS,MAX_LEVELS))
        seen=set(); lamps=[]; logged_classes=set()
        for level in dict.fromkeys(levels):
            for actor in _read_unreal_tarray_pointers(mem,level+OFF_LEVEL_ACTORS,MAX_ACTORS):
                if actor in seen: continue
                seen.add(actor); cls=mem.ptr(actor+OFF_UOBJECT_CLASS)
                if not cls: continue
                class_name=resolver.object_name(cls)
                actor_name=resolver.object_name(actor)
                # Certains objets de la scène sont nommés SM_mp_Lamp_Axxx
                # alors que leur classe réelle n'est pas BP_Lamp_C.
                if class_name.casefold() not in ("bp_lamp_c","bp_lamp") and not actor_name.casefold().startswith(("sm_mp_lamp_","bp_lamp")): continue
                hierarchy=self._describe_class_properties(mem,resolver,cls)
                if class_name not in logged_classes:
                    logged_classes.add(class_name)
                    details=" | ".join("%s[%d]: %s"%(c["name"],c["field_count"],", ".join("%s=%s"%(p["name"],("+0x%X"%p["offset"]) if p["offset"] is not None else "?") for p in c["fields"]) or "aucune propriété décodée") for c in hierarchy)
                    log.info("LAMP_CLASS class=%s actor_example=%s hierarchy=%s",class_name,actor_name,details)
                properties=[p for c in hierarchy for p in c["fields"]]
                matches=[p for p in properties if "spot" in p["name"].casefold() or "light" in p["name"].casefold()]
                comp_off=next((p["offset"] for p in properties if p["name"].casefold()=="spotlight" and p["offset"] is not None),None)
                component=mem.ptr(actor+comp_off) if comp_off is not None else 0
                comp_class=None; intensity=None; int_off=None
                status="Spotlight absent des propriétés inspectées" if comp_off is None else "Pointeur Spotlight illisible"
                if component:
                    comp_cls=mem.ptr(component+OFF_UOBJECT_CLASS)
                    comp_class=resolver.object_name(comp_cls) if comp_cls else "?"
                    int_off=_find_unreal_property_offset(mem,resolver,comp_cls,"Intensity") if comp_cls else None
                    if int_off is None: status="FProperty Intensity introuvable"
                    else:
                        raw=mem.read(component+int_off,4)
                        if raw:
                            value=struct.unpack("<f",raw)[0]
                            if math.isfinite(value): intensity=round(value,5); status="OK"
                            else: status="Intensité non finie"
                        else: status="Intensité illisible"
                props_summary=", ".join("%s%s"%(p["name"],("=+0x%X"%p["offset"]) if p["offset"] is not None else "") for p in matches)
                lamps.append({"name":actor_name,"class":class_name,"actor":"%X"%actor,"spotlight":"%X"%component if component else None,"component_class":comp_class,"intensity":intensity,"spotlight_offset":"+0x%X"%comp_off if comp_off is not None else None,"intensity_offset":"+0x%X"%int_off if int_off is not None else None,"read_status":status,"properties":props_summary or "Aucune propriété Spotlight/Light reconnue","hierarchy":" > ".join(c["name"] for c in hierarchy)})
        readable=sum(x["intensity"] is not None for x in lamps)
        status=("%d acteur(s) lampe trouvé(s), %d intensité(s) lisible(s)."%(len(lamps),readable)) if lamps else "Aucun acteur lampe trouvé dans les niveaux chargés."
        return {"lamps":lamps,"stats":{"actors_seen":len(seen),"lamp_actors":len(lamps),"readable":readable,"unreadable":len(lamps)-readable},"status":status}
    def snapshot(self):
        with self.lock: return {"running":self.running,"status":self.status,"world_name":self.world_name,"last_scan":self.last_scan,"lamps":list(self.lamps),"stats":dict(self.stats)}