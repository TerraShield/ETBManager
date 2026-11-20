"""Prédicteur BottomFloor : échantillonnage rapide des intensités des lampes en mémoire."""
from __future__ import annotations
import logging, math, struct, threading, time
from collections import deque
from memory_reader import (PROCESS_NAME, ProcessMemory, find_process_id, find_module, locate_gworld, locate_gnames, FNameResolver, _read_unreal_tarray_pointers, _find_unreal_property_offset, _read_i32, _read_fname_at, OFF_WORLD_PERSISTENT_LEVEL, OFF_WORLD_LEVELS, OFF_LEVEL_ACTORS, OFF_UOBJECT_CLASS, OFF_USTRUCT_SUPER, OFF_USTRUCT_CHILD_PROPERTIES, OFF_FFIELD_NEXT, OFF_FFIELD_NAME, OFF_FPROPERTY_OFFSET)

log = logging.getLogger("ETB.BottomFloor.Lamps")
MAX_LEVELS, MAX_ACTORS = 256, 250000
POLL_INTERVAL = 0.02       # 50 Hz : lecture des intensités toutes les 20 ms
DISCOVERY_INTERVAL = 1.0   # nouvelle recherche des acteurs toutes les secondes
BLACKOUT_THRESHOLD = 1.25
RANGES = {"1": (4.25, 7.26), "2": (4.50, 7.50), "3": (5.00, 7.50), "4": (5.00, 7.50), "Inconnu": (4.00, 8.00)}

def estimate_period(times, player_count):
    if len(times) < 3: return None, None
    low, high = RANGES[player_count]
    intervals = [b-a for a,b in zip(times,times[1:]) if b>a]
    if len(intervals)<2: return None,None
    best_t,best_score,best_max=None,float("inf"),float("inf")
    for i in range(int(round((high-low)*100))+1):
        candidate=low+i/100.0; residuals=[]
        for interval in intervals:
            multiple=max(1,round(interval/candidate)); residuals.append(abs(interval-multiple*candidate))
        rms=math.sqrt(sum(x*x for x in residuals)/len(residuals)); max_res=max(residuals)
        if (rms,max_res)<(best_score,best_max): best_t,best_score,best_max=candidate,rms,max_res
    if best_score>0.30 or best_max>0.60: return None,best_score
    return best_t,best_score

class LampMemoryScanner:
    def __init__(self):
        self.lock=threading.RLock(); self.stop_event=threading.Event(); self.thread=None; self.running=False
        self.status="Diagnostic arrêté. Charge BottomFloor puis démarre le prédicteur."
        self.world_name=None; self.last_scan=None; self.lamps=[]; self.cache=[]
        self.stats={"actors_seen":0,"lamp_actors":0,"readable":0,"unreadable":0}
        self.player_count="1"; self.events=deque(maxlen=80); self.period=None; self.quality_score=None
        self.last_flicker=None; self.blackout_started=None; self.relight_time=None; self.dark_since=None
        self.signal=None; self.signal_state=None; self.next_tick=None; self.last_transition=None
        self.last_sample_at=None; self.sample_hz_actual=0.0
    def start(self):
        with self.lock:
            if self.running: return {"ok":True,"message":"Prédicteur déjà actif."}
            self.stop_event.clear(); self.running=True; self.status="Connexion au jeu et recherche des lampadaires…"
            self.thread=threading.Thread(target=self._run,name="BottomFloorLampPredictor",daemon=True); self.thread.start()
        log.info("Démarrage du prédicteur mémoire (lecture %.0f Hz).",1/POLL_INTERVAL)
        return {"ok":True,"message":self.status}
    def stop(self):
        self.stop_event.set()
        with self.lock: self.running=False; self.status="Prédicteur arrêté."
        log.info("Arrêt du prédicteur BottomFloor."); return {"ok":True,"message":self.status}
    def clear(self):
        with self.lock:
            self.events.clear(); self.period=None; self.quality_score=None; self.last_flicker=None
            self.blackout_started=None; self.relight_time=None; self.dark_since=None; self.last_transition=None
            self.next_tick=None; self.signal_state=None; self.flicker_until=0.0
            self.status="Calibration effacée. Les mesures mémoire continuent."
        return {"ok":True}
    def set_players(self,value):
        value=str(value)
        if value not in RANGES: raise ValueError("Nombre de joueurs invalide.")
        with self.lock: self.player_count=value; self._recalculate_locked()
        return {"ok":True}
    def _recalculate_locked(self): self.period,self.quality_score=estimate_period(list(self.events),self.player_count)
    def _run(self):
        mem=None
        try:
            pid=find_process_id(PROCESS_NAME); mem=ProcessMemory(pid); base,_=find_module(pid,PROCESS_NAME)
            slot=locate_gworld(mem,base); names=locate_gnames(mem,base); resolver=FNameResolver(mem,names)
            log.info("Connexion PID=%s base=0x%X GWorldSlot=0x%X GNames=0x%X",pid,base,slot,names)
            next_discovery=0.0
            while not self.stop_event.is_set():
                now=time.perf_counter()
                if now>=next_discovery:
                    try:
                        world=mem.ptr(slot)
                        if not world: raise RuntimeError("GWorld nul")
                        world_name=resolver.object_name(world); result=self._scan_world(mem,world,resolver)
                        with self.lock:
                            self.cache=result["cache"]; self.lamps=result["lamps"]; self.stats=result["stats"]
                            self.world_name=world_name; self.last_scan=time.time(); self.status=result["status"]
                        next_discovery=now+DISCOVERY_INTERVAL
                        if world_name.casefold()!="bottomfloor": log.warning("Niveau courant=%s (BottomFloor attendu).",world_name)
                    except Exception as exc:
                        with self.lock: self.status="Erreur de découverte : "+str(exc)
                        log.exception("Échec de découverte des lampadaires")
                        next_discovery=now+DISCOVERY_INTERVAL
                try:
                    self._sample_cached(mem,now)
                except Exception:
                    log.exception("Erreur pendant l'échantillonnage rapide")
                self.stop_event.wait(POLL_INTERVAL)
        except Exception as exc:
            with self.lock: self.status="Connexion impossible : "+str(exc)
            log.exception("Initialisation du prédicteur impossible")
        finally:
            if mem: mem.close()
            with self.lock: self.running=False
    def _describe_class_properties(self,mem,resolver,class_address):
        classes=[]; current=class_address; visited=set()
        while current and current not in visited and len(classes)<32:
            visited.add(current); class_name=resolver.object_name(current); fields=[]
            field=mem.ptr(current+OFF_USTRUCT_CHILD_PROPERTIES); seen=set(); steps=0
            while field and field not in seen and steps<4096:
                seen.add(field); steps+=1
                fields.append({"name":_read_fname_at(mem,resolver,field+OFF_FFIELD_NAME),"offset":_read_i32(mem,field+OFF_FPROPERTY_OFFSET)})
                field=mem.ptr(field+OFF_FFIELD_NEXT)
            classes.append({"name":class_name,"fields":fields,"field_count":steps})
            current=mem.ptr(current+OFF_USTRUCT_SUPER)
        return classes
    def _scan_world(self,mem,world,resolver):
        levels=[]; persistent=mem.ptr(world+OFF_WORLD_PERSISTENT_LEVEL)
        if persistent: levels.append(persistent)
        levels.extend(_read_unreal_tarray_pointers(mem,world+OFF_WORLD_LEVELS,MAX_LEVELS))
        seen=set(); lamps=[]; cache=[]; logged=set()
        for level in dict.fromkeys(levels):
            for actor in _read_unreal_tarray_pointers(mem,level+OFF_LEVEL_ACTORS,MAX_ACTORS):
                if actor in seen: continue
                seen.add(actor); cls=mem.ptr(actor+OFF_UOBJECT_CLASS)
                if not cls: continue
                class_name=resolver.object_name(cls); actor_name=resolver.object_name(actor)
                if class_name.casefold() not in ("bp_lamp_c","bp_lamp") and not actor_name.casefold().startswith(("sm_mp_lamp_","bp_lamp")): continue
                hierarchy=self._describe_class_properties(mem,resolver,cls)
                props=[p for c in hierarchy for p in c["fields"]]
                comp_off=next((p["offset"] for p in props if p["name"].casefold()=="spotlight" and p["offset"] is not None),None)
                component=mem.ptr(actor+comp_off) if comp_off is not None else 0
                comp_class=None; int_off=None; intensity=None; status="Spotlight absent" if comp_off is None else "Pointeur Spotlight illisible"
                if component:
                    comp_cls=mem.ptr(component+OFF_UOBJECT_CLASS); comp_class=resolver.object_name(comp_cls) if comp_cls else "?"
                    int_off=_find_unreal_property_offset(mem,resolver,comp_cls,"Intensity") if comp_cls else None
                    if int_off is not None:
                        raw=mem.read(component+int_off,4)
                        if raw:
                            value=struct.unpack("<f",raw)[0]
                            if math.isfinite(value): intensity=round(value,5); status="OK"
                            else: status="Intensité non finie"
                        else: status="Intensité illisible"
                    else: status="FProperty Intensity introuvable"
                item={"name":actor_name,"class":class_name,"actor":"%X"%actor,"spotlight":"%X"%component if component else None,
                      "component_class":comp_class,"intensity":intensity,"spotlight_offset":"+0x%X"%comp_off if comp_off is not None else None,
                      "intensity_offset":"+0x%X"%int_off if int_off is not None else None,"read_status":status,
                      "properties":", ".join(p["name"] for p in props if "spot" in p["name"].casefold() or "light" in p["name"].casefold()) or "—",
                      "hierarchy":" > ".join(c["name"] for c in hierarchy)}
                lamps.append(item)
                if component and int_off is not None: cache.append({"name":actor_name,"actor":actor,"component":component,"offset":int_off,"last":intensity})
                if class_name not in logged:
                    logged.add(class_name); log.info("LAMP_CLASS class=%s hierarchy=%s",class_name,item["hierarchy"])
        readable=sum(x["intensity"] is not None for x in lamps)
        status=("%d lampadaire(s), %d intensité(s) lisible(s). Échantillonnage à %.0f Hz."%(len(lamps),readable,1/POLL_INTERVAL)) if lamps else "Aucun lampadaire trouvé dans les niveaux chargés.")
        return {"lamps":lamps,"cache":cache,"stats":{"actors_seen":len(seen),"lamp_actors":len(lamps),"readable":readable,"unreadable":len(lamps)-readable},"status":status}
    def _sample_cached(self,mem,now):
        with self.lock:
            cache=list(self.cache)
            if self.last_sample_at is not None:
                dt=now-self.last_sample_at
                if dt>0:
                    measured=1.0/dt
                    self.sample_hz_actual=measured if self.sample_hz_actual<=0 else 0.8*self.sample_hz_actual+0.2*measured
            self.last_sample_at=now
        values=[]; updates={}
        for item in cache:
            raw=mem.read(item["component"]+item["offset"],4)
            if not raw: continue
            value=struct.unpack("<f",raw)[0]
            if math.isfinite(value) and -1.0<=value<=100000.0:
                value=max(0.0,value); values.append(value); updates[item["actor"]]=value
        if not values: return
        # Signal collectif : moyenne des intensités normalisées, robuste aux lampes isolées.
        signal=sum(min(v,100.0) for v in values)/len(values)
        with self.lock:
            for lamp in self.lamps:
                addr=int(lamp["actor"],16)
                if addr in updates: lamp["intensity"]=round(updates[addr],5); lamp["read_status"]="OK"
            self.signal=signal
            self._process_signal_locked(now,signal)
    def _process_signal_locked(self,now,value):
        # Un état initial ne constitue pas un clignotement : on attend une vraie transition.
        is_on=value>=50.0
        if self.signal_state is None:
            self.signal_state=is_on
            if not is_on: self.dark_since=now
            return
        if self.signal_state and not is_on:
            self.dark_since=now
            self.flicker_until=now+0.65
        elif not self.signal_state and is_on:
            dark_duration=now-self.dark_since if self.dark_since is not None else 0.0
            if self.blackout_started is not None and dark_duration>=BLACKOUT_THRESHOLD:
                self.relight_time=now
                self.status="Rallumage détecté : chronomètre prédictif actif."
                log.info("RALLUMAGE après %.3f s d'extinction.",dark_duration)
            elif self.dark_since is not None and dark_duration>0.04:
                # On horodate le début de la baisse, comme dans l'ancien prédicteur.
                self.events.append(self.dark_since)
                self.last_flicker=self.dark_since
                self.last_transition=now
                self._recalculate_locked()
                log.info("CLIGNOTEMENT détecté à %.3f s (durée sombre %.3f s, signal %.2f lm)",self.dark_since,dark_duration,value)
            self.dark_since=None
            self.blackout_started=None
            self.flicker_until=now+0.65
        self.signal_state=is_on
        if not is_on and self.dark_since is not None and now-self.dark_since>=BLACKOUT_THRESHOLD and self.blackout_started is None:
            self.blackout_started=self.dark_since
            self.relight_time=None
            self.status="Extinction prolongée détectée : attente du rallumage."
        if self.period is not None and self.relight_time is not None and self.last_flicker is not None:
            elapsed=self.relight_time-self.last_flicker
            steps=math.floor(elapsed/self.period)+1
            self.next_tick=self.last_flicker+steps*self.period
    def snapshot(self):
        with self.lock:
            remaining=None
            if self.next_tick is not None:
                now=time.perf_counter()
                if self.period is not None and self.next_tick<=now:
                    self.next_tick += (math.floor((now-self.next_tick)/self.period)+1)*self.period
                remaining=max(0.0,self.next_tick-now)
            if self.period is None:
                period_label="Calibration en cours" if self.events else "En attente de détection"
                quality="Collecte de plusieurs clignotements"
            else:
                period_label=f"{self.period:.2f} s"; score=self.quality_score or 0.0
                quality=("élevée" if score<0.07 else "moyenne" if score<0.16 else "faible")+f" (écart {score:.3f} s)"
            now=time.perf_counter()
            if not self.running:
                lamp_state, lamp_state_key = "Arrêté", "idle"
            elif self.blackout_started is not None:
                lamp_state, lamp_state_key = "Éteintes", "off"
            elif now < self.flicker_until:
                lamp_state, lamp_state_key = "Clignotement", "flicker"
            elif self.signal_state is True:
                lamp_state, lamp_state_key = "Allumées", "on"
            elif self.signal_state is False:
                lamp_state, lamp_state_key = "Éteintes", "off"
            else:
                lamp_state, lamp_state_key = "Recherche…", "idle"
            elapsed = None if self.relight_time is None else max(0.0, now-self.relight_time)
            return {"running":self.running,"status":self.status,"world_name":self.world_name,"last_scan":self.last_scan,
                    "lamps":list(self.lamps),"stats":dict(self.stats),"sample_hz":round(self.sample_hz_actual,1) if self.sample_hz_actual>0 else 1/POLL_INTERVAL,"sample_hz_target":1/POLL_INTERVAL,"signal":self.signal,
                    "player_count":self.player_count,"event_count":len(self.events),"period":self.period,"period_label":period_label,
                    "quality":quality,"remaining":remaining,"blackout":self.blackout_started is not None,"relight_time":self.relight_time,
                    "lamp_state":lamp_state,"lamp_state_key":lamp_state_key,"elapsed_since_relight":elapsed}
