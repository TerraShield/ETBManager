# Module Papillons

L'interface et le backend de ce module sont indépendants du cœur ETB Manager.

Le script générateur déjà développé pour la carte doit être placé ici sous le nom exact :

`etb_map_builder.py`

Ne modifie pas son contenu pour l'intégrer. Le backend appelle ce script avec l'export FModel et les options `--out <préfixe> --no-open`, puis sert le HTML live généré dans le cadre du module.

Les exports FModel et les HTML produits restent dans ce dossier. Ils ne sont pas publiés sur Internet.
