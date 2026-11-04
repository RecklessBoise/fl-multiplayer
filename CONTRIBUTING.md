# Contribuer à FL Multiplayer

Merci de votre intérêt ! Les contributions sont bienvenues, en français ou en anglais.

## Mise en place

1. Installez [uv](https://docs.astral.sh/uv/).
2. `uv sync` installe Python 3.11+ si besoin, les dépendances et les outils de dev.

## Règles

- Une branche par sujet (`feat/…`, `fix/…`, `chore/…`, `docs/…`), une PR par branche.
- Messages de commit au format [Conventional Commits](https://www.conventionalcommits.org/).
- Tests d'abord pour toute logique testable (`pytest`).
- `uv run ruff check .`, `uv run ruff format --check .` et `uv run pytest` doivent passer :
  la CI les lance à chaque push et à chaque PR.
- Code typé quand c'est possible.

## Contraintes du script FL Studio

Le code de `fl_script/` s'exécute dans l'interpréteur Python embarqué de FL Studio :

- aucun import en dehors des modules FL (`transport`, `mixer`, `channels`, `patterns`,
  `device`, `general`, `midi`, `ui`) et des built-ins ;
- pas de réseau, pas de dépendance externe ;
- syntaxe compatible Python 3.7 (contrôlée par `fl_script/ruff.toml`) ;
- chaque fonction de l'API FL utilisée doit exister dans la
  [documentation officielle](https://il-group.github.io/FL-Studio-API-Stubs/).

## Sécurité et vie privée

- Aucun secret dans le code : utilisez des variables d'environnement.
- Ne commitez ni chemin personnel ni donnée privée.
- Pour signaler une faille, ouvrez une issue sans détails exploitables et demandez un contact privé.
