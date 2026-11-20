# ElectricalStation

Le module recherche les acteurs dont la classe hérite de BP_Fusebox_Base_C dans les niveaux Unreal chargés.

## Carte

Déposer une seule image de carte dans public/ (.png, .jpg, .jpeg ou .webp). Le module prend la première image par ordre alphabétique.

Pour positionner les marqueurs, compléter public/map.config.json :

    {
      "version": 1,
      "worldBounds": {
        "minX": -10000,
        "maxX": 10000,
        "minY": -8000,
        "maxY": 8000
      }
    }

Ces valeurs sont des exemples à remplacer par les coordonnées Unreal des bords de ta carte. Sans calibration, les positions et états restent consultables dans la liste, mais les marqueurs ne sont pas dessinés sur l'image.

## Données lues

- Position : RootComponent -> ComponentToWorld.Translation.
- Index : propriété FuseboxIndex de BP_Fusebox_Base_C.
- États locaux : IsEnabled et IsDoorOpen.
- Gestionnaire : HasOpenedEastSubstation, HasOpenedWestSubstation, HasOpenedGenerator et HasPoweredAllGenerators.

Les groupes Est/Ouest/Générateur ne sont pas devinés : les maps TMap du StationManager ne sont pas encore décodées. IsEnabled et IsDoorOpen restent distincts de HasActivated.


## Cartographie automatique depuis le jeu (expérimental)

Si aucune image de carte n'est présente dans `public/`, le module lance une analyse mémoire du niveau `ElectricalStation`. Il parcourt les acteurs et les composants racines, `InstanceComponents` et `BlueprintCreatedComponents`, repère les `StaticMeshComponent`, lit leur référence `StaticMesh`, leurs `ExtendedBounds` et leur transform monde, puis dessine une carte SVG des volumes verticaux plausibles (murs, portes, piliers). La carte et les données brutes sont conservées dans `public/electrical_station_auto_map.svg` et `public/auto_map_data.json`; les limites monde sont écrites dans `public/map.config.json`.

Une image manuelle (`png`, `jpg`, `jpeg`, `webp` ou `svg`, autre que la carte auto-générée) reste prioritaire. Le bouton **Analyser / reconstruire la carte** force une nouvelle lecture. Les volumes générés sont des approximations à partir des bounds Unreal : ils ne remplacent pas l'extraction exacte des polygones de collision. Cette fonctionnalité est expérimentale et doit être vérifiée en jeu.

La géométrie locale des assets est également ajoutée à la bibliothèque partagée `ETB_Manager/mesh_library/static_meshes.json`, indexée par chemin complet d'asset. Si la lecture mémoire des bounds d'un mesh échoue lors d'une analyse ultérieure, le scanner réutilise les bounds locaux déjà enregistrés. Les prochains modules pourront également exploiter ce même fichier.
