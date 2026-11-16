# Modèle de module ETB Manager

Copie ce dossier dans `modules/mon_module/` (sans le préfixe `_template`), puis :

1. Renomme `module.json.example` en `module.json` et modifie les champs.
2. Renomme `backend.py.example` en `backend.py` uniquement si le module a besoin de Python côté serveur.
3. Modifie `ui/index.html`, `ui/module.js` et éventuellement `ui/style.css`.
4. Relance ETB Manager : il détectera le nouveau dossier.

Le champ `levels` reçoit les noms UWorld exacts à associer au module. Il peut rester vide pour un module ouvert manuellement.
