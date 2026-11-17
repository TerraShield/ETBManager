# Module Papillons

L'interface et le backend de ce module sont indépendants du cœur ETB Manager.

## Générateur de cartes

Le script `etb_map_builder.py` génère les cartes à partir des exports FModel.

Les collisions de murs connues sont stockées dans `wall_database.json` :
- `geometries` contient les données de collision uniques ;
- `aliases` associe chaque nom de StaticMesh à une géométrie ;
- plusieurs noms peuvent donc pointer vers une même géométrie, sans dupliquer ses données.

Le catalogue est chargé directement depuis ce fichier à chaque génération. Les dossiers d'exports JSON ne sont pas parcourus automatiquement.

L'option `--meshes ZIP|DOSSIER|JSON` permet encore de charger des exports supplémentaires pour un calcul ponctuel. Elle ne modifie pas le catalogue.

Le backend appelle le script avec l'export FModel et les options `--out <préfixe> --no-open`, puis sert le HTML live généré dans le cadre du module.

Les exports FModel et les HTML produits restent dans ce dossier. Ils ne sont pas publiés sur Internet.
