# Module Papillons

L'interface et le backend de ce module sont indépendants du cœur ETB Manager.

## Génération des cartes

Le script `etb_map_builder.py` génère une carte statique et une carte live depuis un export JSON de FModel.

Depuis l'interface du module :
1. Choisir le dossier de destination avec **Parcourir…** (ou saisir un chemin).
2. Importer l'export JSON.
3. Le générateur crée automatiquement les fichiers HTML et `map.config.json` dans le dossier choisi.

Le chemin de destination est mémorisé entre les lancements d'ETB Manager. Les fichiers de carte et la configuration restent associés dans le même dossier. L'application sert la carte et sa configuration depuis ce dossier, même si celui-ci est extérieur au répertoire du projet.

Le catalogue des collisions de murs reste chargé depuis `wall_database.json`. Les exports complémentaires peuvent être importés ponctuellement avec `--meshes`, sans scan automatique du dossier d'exports.

Les exports FModel et les cartes générées restent locaux. Ils ne sont pas publiés sur Internet.
