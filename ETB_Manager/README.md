# ETB Manager v0.3 — architecture modulaire

ETB Manager est maintenant un hôte qui découvre les aides installées dans `modules/`. Chaque aide possède son dossier, ses alias de niveaux, son interface et, si nécessaire, son backend Python. Le cœur ne contient plus une liste de niveaux/modules à modifier à chaque ajout.

## Modules fournis

| Dossier | Module | Détection UWorld |
|---|---|---|
| `modules/level0/` | Carte Level 0 | `Level0` |
| `modules/level1/` | Habitable Zone · Interphone couleur | `TopFloor` |
| `modules/garage/` | Prédiction du garage | `Floor4`, `GarageLevel2`, `garage_floor_4` |
| `modules/butterflies/` | Carte du labyrinthe des papillons | `Moths` |

Les alias sont déclarés dans le `module.json` de chaque dossier. Ils peuvent être modifiés sans toucher à `level_detector.py`.

## Installation

1. Ferme l'ancienne version d'ETB Manager et fais une copie de sauvegarde de son dossier.
2. Décompresse cette version dans un nouveau dossier `ETB_Manager` (ou remplace les fichiers de l'ancienne installation après sauvegarde).
3. Installe les dépendances avec `python -m pip install -r requirements.txt`.
4. Pour conserver ta carte Level 0 interactive existante, copie son fichier `level0_live_map.html` dans `modules/level0/` ou `modules/level0/public/`. L'aperçu statique `Level0_map_v0.png` et ses données JSON sont inclus.
5. Place ton générateur `etb_map_builder.py` dans `modules/butterflies/` sans modifier son contenu.
6. Lance `python main.py`.

Le lecteur mémoire reste partagé par tous les modules. L'API locale `/pos` est conservée pour les cartes live historiques.

## Ajouter un module

Aucun fichier du cœur (`main.py`, `module_system.py`, `level_detector.py`, `web/app.js`) n'a besoin d'être modifié pour ajouter un module normal.

1. Copie `modules/_template/` vers `modules/mon_module/`.
2. Renomme `module.json.example` en `module.json`.
3. Mets dans `id` exactement `mon_module` (minuscules, chiffres et `_`), choisis un nom, une icône, un ordre et une description.
4. Renseigne les noms de niveaux Unreal dans `levels`, par exemple `["Moths", "Moths_P"]`. Laisse `[]` si le module doit seulement être sélectionné à la main.
5. Construis ton écran dans `ui/index.html`. Ajoute la logique d'affichage dans `ui/module.js`. Ajoute une feuille `ui/style.css` uniquement si nécessaire.
6. Si le module a besoin de traitement côté Python, renomme `backend.py.example` en `backend.py`.
7. Redémarre ETB Manager. Le nouveau module apparaît dans la barre latérale et dans le catalogue de l'accueil.

### Fichiers d'un module

```text
modules/mon_module/
├── module.json          # nom, icône, alias de niveaux, ordre, UI
├── backend.py           # optionnel : logique Python et actions serveur
├── ressources/          # données privées du module (non servies au navigateur)
└── ui/
    ├── index.html       # fragment HTML du module (pas une page complète)
    ├── module.js        # contrôleur frontend
    └── style.css        # optionnel
```

Le navigateur ne peut télécharger que les fichiers de `ui/` et `public/` (ainsi que les cartes Level 0 prévues). Les exports, scripts Python et données privées restent côté local.

### Contrat frontend

Le script du module enregistre un contrôleur sous `window.ETBModulePlugins["mon_module"]` :

```js
window.ETBModulePlugins = window.ETBModulePlugins || {};
window.ETBModulePlugins.mon_module = {
  mount(root, api) {
    // root : conteneur HTML réservé au module.
    // api.getState() : dernier état commun ETB Manager.
    // api.post("action", { action: "nom_action" }) : appel backend.
    // api.upload(file) : envoi brut d'un fichier au backend.
    // api.refresh() : demande une actualisation immédiate.
    return {
      update(state) { /* actualiser l'interface depuis l'état */ },
      destroy() { /* retirer les écouteurs/ressources si nécessaire */ }
    };
  }
};
```

`mount` est appelé lorsque le module est ouvert. `update(state)` est appelé à chaque actualisation. L'objet `state` contient notamment `memory`, `car_puzzle`, `modules` et `module_state`. Les données du backend du module se trouvent dans `state.module_state.mon_module`.

### Contrat backend optionnel

`backend.py` doit exposer `create_module(context)`. L'objet retourné peut définir :

- `snapshot(shared_state) -> dict` : état envoyé au navigateur dans `module_state` ;
- `action(action, payload, shared_state) -> dict` : actions reçues par `POST /api/modules/mon_module/action` ;
- `upload(temp_path, filename, shared_state) -> dict` : réception d'un fichier par `POST /api/modules/mon_module/upload` ;
- `shutdown()` : arrêt propre de ses ressources.

`context` fournit `module_id`, `module_dir`, `base_dir` et `port`. `shared_state` contient le snapshot du lecteur mémoire et le puzzle de couleurs. Si aucun `backend.py` n'est déclaré dans `module.json`, le module fonctionne uniquement côté navigateur.

### Tester la détection

```powershell
python main.py --fake --fake-level TopFloor
python main.py --fake --fake-level Level0
python main.py --fake --fake-level Floor4
python main.py --fake --fake-level Moths
python main.py --fake --fake-level MonNouveauNiveau
```

`--fake-level` accepte n'importe quel nom, y compris ceux ajoutés dans les `module.json`. Le nom du niveau doit être confirmé sur deux lectures successives avant le changement automatique.

## Limites et conservation

- Les alias `Floor4`, `GarageLevel2` et `Moths` sont conservés comme précédemment ; leur exactitude dépend du nom UWorld réellement observé en jeu.
- Le fichier HTML live de Level 0 n'était pas présent dans les archives de projet disponibles lors de la création de cette version. Cette archive inclut l'image et les données de l'export Level 0, et sait charger l'ancien HTML si tu le remets à l'emplacement indiqué.
- Le générateur `etb_map_builder.py` reste indépendant et n'est pas réécrit par cette architecture. Il est à déposer dans le dossier Papillons.
- Le lecteur mémoire Windows est en lecture seule. Les modules ne transmettent pas de commandes au jeu par défaut.

## Contrôle rapide de la version

Depuis la racine du projet :

```powershell
python tests/smoke_test.py
python tests/test_butterflies_backend.py
```

Le premier test vérifie le chargement des quatre modules, la découverte d'un nouveau module factice, les alias de niveau, les routes principales et les ressources UI. Le second teste le backend Papillons avec un faux générateur temporaire, sans modifier ni remplacer le générateur réel.


## Version 0.3.1

- Barre d'outils horizontale extensible par module via `mountToolbar(container, api)`.
- Import JSON du module Papillons déplacé dans la barre supérieure.
- Nouveau module `boiler_room` avec carte live et 57 segments manuels.
- Vert = marcher/courir ; orange = s'accroupir. Rayon de proximité initial : 250 unités Unreal (2,5 m).
