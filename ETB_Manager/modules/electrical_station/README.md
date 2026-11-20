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
