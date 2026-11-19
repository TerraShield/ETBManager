"""Diagnostic mémoire en lecture seule des BP_Lamp_C du Garage BottomFloor."""
from __future__ import annotations
import logging, math, struct, threading, time
from memory_reader import (PROCESS_NAME, ProcessMemory, find_process_id, find_module, locate_gworld, locate_gnames, FNameResolver, _read_unreal_tarray_pointers, _find_unreal_property_offset, OFF_WORLD_PERSISTENT_LEVEL, OFF_WORLD_LEVELS, OFF_LEVEL_ACTORS, OFF_UOBJECT_CLASS)

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
    def _scan_world(self,mem,world,resolver):
        levels=[]; persistent=mem.ptr(world+OFF_WORLD_PERSISTENT_LEVEL)
        if persistent: levels.append(persistent)
        levels.extend(_read_unreal_tarray_pointers(mem,world+OFF_WORLD_LEVELS,MAX_LEVELS))
        seen=set(); lamps=[]
        for level in dict.fromkeys(levels):
            for actor in _read_unreal_tarray_pointers(mem,level+OFF_LEVEL_ACTORS,MAX_ACTORS):
                if actor in seen: continue
                seen.add(actor); cls=mem.ptr(actor+OFF_UOBJECT_CLASS)
                if not cls: continue
                class_name=resolver.object_name(cls)
                if class_name.casefold() not in ("bp_lamp_c","bp_lamp"): continue
                actor_name=resolver.object_name(actor)
                comp_off=_find_unreal_property_offset(mem,resolver,cls,"Spotlight")
                component=mem.ptr(actor+comp_off) if comp_off is not None else 0
                comp_class=None; intensity=None; int_off=None
                status="Spotlight property introuvable" if comp_off is None else "Pointeur Spotlight illisible"
                if component:
                    comp_cls=mem.ptr(component+0x10)
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
                lamps.append({"name":actor_name,"class":class_name,"actor":"%X"%actor,"spotlight":"%X"%component if component else None,"component_class":comp_class,"intensity":intensity,"spotlight_offset":"+0x%X"%comp_off if comp_off is not None else None,"intensity_offset":"+0x%X"%int_off if int_off is not None else None,"read_status":status})
        readable=sum(x["intensity"] is not None for x in lamps)
        status=("%d BP_Lamp_C trouvé(s), %d intensité(s) lisible(s)."%(len(lamps),readable)) if lamps else "Aucun BP_Lamp_C trouvé dans les niveaux chargés."
        return {"lamps":lamps,"stats":{"actors_seen":len(seen),"lamp_actors":len(lamps),"readable":readable,"unreadable":len(lamps)-readable},"status":status}
    def snapshot(self):
        with self.lock: return {"running":self.running,"status":self.status,"world_name":self.world_name,"last_scan":self.last_scan,"lamps":list(self.lamps),"stats":dict(self.stats)}