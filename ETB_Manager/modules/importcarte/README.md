# Module ImportCarte

Module de génération et de diagnostic des cartes 2D depuis les exports FModel.

## Utilisation
1. Choisir le dossier de sortie.
2. Importer l'export JSON du niveau avec **Importer une carte**.
3. Ajouter les exports JSON de StaticMesh muraux avec **Ajouter des JSON de mur**.
4. Relancer l'import de la carte pour recalculer les collisions avec les meshes ajoutés.

Les exports de murs sont conservés dans `mesh/imported/` en local. Ils sont passés au générateur via `--meshes` à chaque génération.

## Identité des meshes
Le nom court (ex. `SM_Wall_01`) ne suffit pas à identifier un asset. Le générateur privilégie désormais le chemin complet de l'asset FModel quand il est disponible et ne retombe sur le nom court qu'en compatibilité avec l'ancien catalogue. L'interface liste les chemins et signale les noms dupliqués.


## Réinitialisation des imports
Le bouton **Réinitialiser les imports** archive le dossier `mesh/imported/` dans un dossier `mesh/imported_backup_*`, puis recrée une bibliothèque vide. Les fichiers archivés restent récupérables. Le catalogue intégré `wall_database.json` n'est pas modifié.
