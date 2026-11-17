# Gestionnaire de cartes

Les cartes de jeu restent dans leur module (Level 0, Boiler Room, Garage, etc.).
Le module `map_loader` centralise les paramètres communs.

## Priorité des paramètres

1. `modules/map_loader/public/map-defaults.json` : valeurs générales.
2. `modules/<module_carte>/public/map.config.json` : remplacements propres à la carte.

Les paramètres locaux sont fusionnés avec les paramètres généraux et sont prioritaires.

## Configuration actuelle

```json
{
  "version": 1,
  "visibility": {
    "defaultGroups": [3, 12],
    "walls": true,
    "entities": true,
    "floors": false,
    "prefabs": false,
    "points": false,
    "volumes": false
  },
  "interface": {
    "filters": "context-menu",
    "fitMapOnOpen": true
  },
  "tools": {
    "manualTrace": false,
    "shortestPath": false,
    "export": false
  }
}
```

Les groupes 3 et 12 correspondent actuellement aux murs et aux objets/entités dans les cartes Boiler Room et Garage. Le Level 0 utilise des couches de visibilité propres à son moteur de carte.

## Importation

Le chargeur JSON vers HTML est une étape distincte. Avant de l'activer, il faut figer le schéma du JSON source afin de ne pas confondre les exports de géométrie Unreal, les configurations de carte et les JSON de calibration.
