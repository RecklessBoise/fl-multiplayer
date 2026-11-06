# Mettre le serveur en ligne

Le serveur de salons est le seul élément qui tourne sur Internet. Il ne reçoit que des réglages
(tempo, mixer, channel rack…), jamais d'audio ni de fichiers. **Une seule personne** l'installe ; les
autres se contentent de son adresse `wss://…`.

Deux hébergeurs sont prêts à l'emploi :

| | Render | Fly.io |
|---|---|---|
| Prix | **gratuit** (offre *Free*) | payant à l'usage ; pas d'offre gratuite pour les nouveaux comptes, seulement un essai |
| Mise en veille | après 15 min sans aucun message ; environ 1 min pour se réveiller | arrêté quand personne n'est connecté ; réveil en quelques secondes |
| Limites par adresse IP | partagées entre tous les utilisateurs (voir plus bas) | par vraie adresse IP |
| Fichier de configuration | `render.yaml` | `fly.toml` |

Les prix et les conditions changent : vérifiez-les sur le site de l'hébergeur avant de vous
inscrire. Créer le compte, accepter les conditions et, pour Fly.io, donner une carte bancaire,
c'est à vous de le faire.

Dans les deux cas, le chiffrement (TLS) est fourni par l'hébergeur : l'adresse commence par
`wss://`, et le relais refuse les adresses `ws://` non chiffrées hors de votre propre machine.

## Option 1 — Render (gratuit)

1. Créez un compte sur [render.com](https://render.com) et reliez-le à votre compte GitHub.
2. Faites une copie (*Fork*) du dépôt `fl-multiplayer` sur votre compte GitHub.
3. Dans Render : **New → Blueprint**, choisissez votre copie du dépôt. Render lit `render.yaml` et
   propose un service web nommé `fl-multiplayer`, offre *Free*. Validez (**Apply**).
4. Attendez la fin du déploiement (quelques minutes). Render affiche une adresse du type
   `https://fl-multiplayer-xxxx.onrender.com`.
5. Votre adresse de serveur est la même, avec `wss://` au lieu de `https://` :
   `wss://fl-multiplayer-xxxx.onrender.com`
6. Vérifiez depuis votre Mac, dans le dossier du projet :
   ```bash
   uv run flmp doctor --server wss://fl-multiplayer-xxxx.onrender.com
   ```
   La ligne « Serveur … joignable » doit être `OK`. La première fois, comptez jusqu'à une minute
   si le serveur dormait.

**À savoir avec Render :**

- Après 15 minutes sans aucun message, le serveur s'endort. Le relais affiche alors une perte de
  connexion, puis se reconnecte tout seul dès que le serveur est réveillé (environ une minute).
- Render ne transmet pas de façon sûre l'adresse IP réelle des utilisateurs. Les limites anti-abus
  « par adresse IP » du serveur s'appliquent donc à tout le monde à la fois : si quelqu'un se
  trompe dix fois de mot de passe en cinq minutes, les nouvelles connexions sont refusées pour
  tout le monde pendant quelques minutes. Utilisez toujours un mot de passe de salon
  (`--ask-secret`) et ne diffusez pas l'adresse publiquement. Si c'est gênant, préférez Fly.io.

## Option 2 — Fly.io (payant, mieux protégé)

1. Créez un compte sur [fly.io](https://fly.io) (une carte bancaire est demandée).
2. Installez l'outil `flyctl` (`brew install flyctl`, ou voir la documentation de Fly.io), puis
   connectez-vous : `fly auth login`.
3. Dans le dossier du projet, ouvrez `fly.toml` et remplacez `fl-multiplayer-CHANGEME` par un nom
   unique, par exemple `fl-multiplayer-votrepseudo`. Changez `primary_region` si vous n'êtes pas
   en Europe de l'Ouest (`cdg` = Paris).
4. Créez l'application, puis déployez :
   ```bash
   fly apps create fl-multiplayer-votrepseudo
   fly deploy
   ```
5. Votre adresse de serveur : `wss://fl-multiplayer-votrepseudo.fly.dev`. Vérifiez-la avec
   `uv run flmp doctor --server wss://fl-multiplayer-votrepseudo.fly.dev`.

Sur Fly.io, le serveur s'arrête quand personne n'est connecté et redémarre en quelques secondes à
la première connexion. `FLMP_TRUST_PROXY=1` (déjà dans `fly.toml`) lui permet de lire la vraie
adresse IP de chaque utilisateur, que Fly.io transmet dans l'en-tête `Fly-Client-IP`.
**N'activez jamais ce réglage ailleurs** : il permettrait à n'importe qui de se faire passer pour
une autre adresse.

## Ce que fait le serveur (et ne fait pas)

- Il ne stocke rien : pas de base de données, pas de fichiers. Un salon disparaît quand son
  dernier membre part.
- Il ne demande aucun secret de configuration. Le mot de passe d'un salon est choisi par ses
  membres ; le serveur n'en garde qu'une empreinte (SHA-256) tant que le salon existe.
- Il limite la taille des messages (256 Kio), le débit (200 messages par seconde et par
  connexion) et le nombre de connexions, de salons et d'échecs d'accès par adresse IP.
- Santé : `GET /healthz` répond `ok`.

## Lancer le serveur ailleurs

L'image Docker fournie fonctionne chez tout hébergeur de conteneurs qui assure le TLS :

```bash
docker build -t flmp-server .
docker run -p 8080:8080 flmp-server
```

Variables reconnues : `PORT` (8080 dans l'image), `FLMP_HOST` (`0.0.0.0`), `FLMP_LOG_LEVEL`
(`INFO`), `FLMP_TRUST_PROXY` (uniquement derrière le proxy de Fly.io).

L'image ne contient que le serveur et `websockets` (version figée, empreintes vérifiées), et
tourne sous un utilisateur sans privilèges. La CI la construit et la teste à chaque modification.
