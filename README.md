# FL Multiplayer

> **Version 0.1.0.** Testé dans FL Studio 2026 (API FL 45) sur macOS, par une seule machine
> reliée à un faux participant. Le test à deux Mac réels n'a pas encore été fait.

Jouez à deux sur le même projet FL Studio, chacun chez soi : quand l'un change le tempo ou
bouge un fader, l'autre voit la même chose dans son FL. Chacun lance et arrête la lecture de son
côté, quand il veut.

**Ce qui est synchronisé :**

- **Tempo.**
- **Mixer :** volume, pan, mute et solo de chaque piste.
- **Channel rack :** volume, pan et mute de chaque channel (FL avec API 33 ou plus).
- **Notes du piano roll, à la demande :** un clic pour envoyer un pattern, un clic chez l'autre
  pour le recevoir (voir « Envoyer un pattern » plus bas).

**Ce qui ne l'est pas :** la lecture et l'arrêt (chacun reste libre), les notes en direct pendant
qu'on les dessine, la playlist, les plugins, les samples et l'audio. Chacun doit donc ouvrir
**le même projet** (envoyez-vous le fichier `.flp` et les samples avant de commencer).

```
[FL Studio A] ⇄ script MIDI ⇄ ports MIDI ⇄ relais A ⇄ Internet (wss) ⇄ serveur ⇄ relais B ⇄ … ⇄ [FL Studio B]
```

---

## Guide d'installation (Mac)

Comptez 15 minutes la première fois. Chaque participant fait les étapes 1 à 5 sur son Mac.

### 1. Installer l'outil `uv`

Ouvrez l'application **Terminal** (Applications > Utilitaires) et collez :

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

C'est l'installeur officiel d'[uv](https://docs.astral.sh/uv/), qui s'occupe de Python pour vous.
Si vous utilisez Homebrew, `brew install uv` fait la même chose. Fermez puis rouvrez le Terminal.

### 2. Télécharger FL Multiplayer

Sur la page GitHub du projet : **Code → Download ZIP**, puis décompressez le dossier dans vos
Documents. Dans le Terminal, placez-vous dedans (adaptez le nom du dossier si besoin) :

```bash
cd ~/Documents/fl-multiplayer-main
```

Toutes les commandes qui suivent se tapent depuis ce dossier. **Chaque nouvelle fenêtre du
Terminal démarre dans votre dossier personnel** : refaites ce `cd` à chaque fois, sinon `uv`
répond « Failed to spawn: flmp » ou « No pyproject.toml found ».

### 3. Installer le script dans FL Studio

```bash
uv run flmp install-script
```

La première fois, `uv` télécharge ce qu'il faut (une minute environ). Le script est copié dans
`Documents/Image-Line/FL Studio/Settings/Hardware/FL Multiplayer/`, et les deux scripts de piano
roll (« FL Multiplayer - Envoyer » et « - Recevoir ») dans
`Documents/Image-Line/FL Studio/Settings/Piano roll scripts/`.

### 4. Lancer le relais

Le relais fait le lien entre votre FL et le serveur. Remplacez l'adresse, le code du salon et
votre pseudo :

```bash
uv run flmp join --server wss://ADRESSE-DU-SERVEUR --room JAM42 --name Alice --virtual --ask-secret
```

- `--room` : le code du salon, 4 à 12 lettres ou chiffres, **le même pour les deux**.
- `--ask-secret` : le Terminal vous demande le mot de passe du salon (8 caractères minimum),
  **le même pour les deux**. Il ne s'affiche pas pendant la frappe.
- `--virtual` : le relais crée lui-même les ports MIDI `FLMP In` et `FLMP Out`. Pas besoin de
  configurer quoi que ce soit dans macOS.
- L'adresse du serveur vous est donnée par la personne qui l'a mis en ligne
  (voir [docs/DEPLOIEMENT.md](docs/DEPLOIEMENT.md)).

Le relais doit afficher « Connecté au salon 'JAM42' ». **Laissez cette fenêtre ouverte** pendant
toute la session (Ctrl+C pour arrêter).

### 5. Régler FL Studio (une seule fois)

Le relais doit tourner pendant ce réglage. Dans FL : **Options > MIDI settings**.

1. Dans **Input**, cliquez sur `FLMP In`. Choisissez **Controller type : FL Multiplayer**, cochez
   **Enable**, et donnez-lui un numéro de **Port** (molette sur la case rouge), par exemple `1`.
2. Dans **Output**, cliquez sur `FLMP Out` et donnez-lui **le même numéro de port**.
3. N'activez pas `FLMP Out` dans *Input*, et ne donnez ce numéro de port à aucun autre appareil.

Vérification : **View > Script output**, onglet **FLMP In** : vous devez lire
`FL Multiplayer 0.1.0 chargé (API FL …)`, sans message d'erreur.

FL garde ces réglages. Les fois suivantes : lancez le relais (étape 4), puis ouvrez FL.

### Vérifier son installation

```bash
uv run flmp doctor --virtual --server wss://ADRESSE-DU-SERVEUR
```

Lancez-la **avant** le relais (sinon les ports sont déjà pris par lui). Chaque ligne doit être
`OK` ; sinon, la commande explique quoi corriger.

### Variante sans `--virtual` (bus IAC)

Si vous préférez des ports permanents : *Configuration audio et MIDI* > *Fenêtre > Afficher le
studio MIDI* > double-clic sur *Gestionnaire IAC* > cochez *Le gestionnaire est en ligne*, et
créez deux bus nommés exactement `FLMP In` et `FLMP Out`. Lancez ensuite `flmp join` et
`flmp doctor` **sans** `--virtual` ; dans FL, les ports s'appellent `IAC Driver FLMP In` et
`IAC Driver FLMP Out`.

---

## Envoyer un pattern (notes du piano roll)

FL ne permet pas aux scripts de suivre les notes **pendant** qu'on les dessine. FL Multiplayer les
envoie donc à la demande, en deux clics. Les deux relais doivent tourner.

1. **Chez celui qui envoie :** ouvrez le piano roll du pattern, puis, dans le menu du piano roll
   (la petite flèche en haut à gauche), **Tools > Scripting > FL Multiplayer - Envoyer**. Cochez
   « Seulement les notes sélectionnées » pour n'envoyer qu'une partie. Le Terminal du relais
   affiche « Pattern envoyé (N notes) ».
2. **Chez l'autre :** son Terminal affiche « Pattern reçu de … ». Il ouvre le piano roll **du même
   channel** (le script ne peut pas le choisir lui-même), puis **Tools > Scripting > FL
   Multiplayer - Recevoir**, et choisit :
   - **Remplacer les notes** : les notes du piano roll sont remplacées par celles reçues ;
   - **Ajouter aux notes** : les notes reçues s'ajoutent à celles déjà là.

Hauteur, position, durée, vélocité, pan et release sont transmis. Si vos projets n'ont pas la
même résolution (PPQ), les positions sont converties. 4000 notes au maximum par envoi.

## Questions fréquentes

**Faut-il le même projet des deux côtés ?** Oui. Les pistes et les channels sont repérés par leur
numéro, pas par leur nom : ajouter ou déplacer un channel chez l'un décale tout chez l'autre.

**Le son passe-t-il par FL Multiplayer ?** Non. Chacun entend son propre FL. Pour vous parler,
utilisez un appel à côté (Discord, FaceTime…).

**Pourquoi un léger décalage ?** Les messages traversent Internet : comptez souvent 50 à 150 ms
entre le moment où l'un bouge un réglage et celui où l'autre le voit.

**Le tempo passe dans un sens mais pas dans l'autre.** Chez celui dont les changements ne partent
pas, la sortie `FLMP Out` n'a pas le même numéro de port que `FLMP In` dans *Options > MIDI
settings*. FL l'indique aussi dans sa barre d'aide (« aucune sortie MIDI reliée »).

**Le relais affiche « Nouvelle tentative dans … ».** La connexion au serveur est coupée (Internet,
ou serveur gratuit en veille). Le relais se reconnecte tout seul ; un serveur Render endormi met
environ une minute à se réveiller.

**« Le serveur a refusé la connexion : bad_secret: 'wrong room secret' ».** Le mot de passe ne
correspond pas à celui du premier arrivé dans le salon. Vérifiez-le, ou changez de code de salon.

**« Un port MIDI « FLMP In » existe déjà ».** Un relais tourne déjà dans une autre fenêtre du
Terminal, ou un autre programme utilise ce nom. Fermez-le, puis relancez.

**Avec l'option `-v` (`uv run flmp -v join …`), le relais affiche « SysEx ignoré (pas FL
Multiplayer) ».** Un autre logiciel (par exemple Logic Pro) envoie des messages à tous les ports
MIDI. C'est sans effet : le relais les ignore.

**Quand je retire un solo, tous les mutes disparaissent.** C'est le comportement de FL Studio : le
retrait d'un solo démute toutes les pistes. FL Multiplayer reproduit la même chose chez l'autre.

**Ça marche sous Windows ?** Pas en v0.1.0 : le guide et les tests visent macOS.

## Limites connues

- Test réel fait sur une seule machine avec un faux participant (`flmp monitor`) ; pas encore à
  deux Mac.
- Un participant qui arrive reçoit le tempo, le mixer et le channel rack.
- Un mute changé pendant un solo n'est transmis qu'au retrait du solo.
- Le solo des channels, les sends, les effets et les noms ne sont pas synchronisés.
- Avant l'API FL 33, le channel rack n'est pas synchronisé.
- Les notes du piano roll passent à la demande (Envoyer / Recevoir), pas en direct. Le pattern
  reçu remplace ou complète le piano roll **ouvert** : ouvrez le bon channel avant de recevoir.
  Seul le dernier pattern reçu est gardé.

## Pour les développeurs

| Dossier | Rôle |
|---|---|
| `fl_script/` | Script de contrôleur MIDI pour FL Studio, et logique pure partagée (`flmp_core`) |
| `relay/` | Relais local : ports MIDI ⇄ serveur WebSocket (commande `flmp`) |
| `server/` | Serveur de salons (commande `flmp-server`) ; image Docker à la racine |

```bash
uv sync
uv run ruff check . && uv run ruff format --check .
uv run pytest                      # unitaires + simulation de bout en bout (tests/e2e)
uv run flmp-server                 # serveur local (ws://localhost:8765)
uv run flmp monitor --server ws://localhost:8765 --room TEST1   # faux participant
```

- Protocole : [docs/PROTOCOL.md](docs/PROTOCOL.md)
- Mise en ligne du serveur : [docs/DEPLOIEMENT.md](docs/DEPLOIEMENT.md)
- Tests manuels dans FL : [étape 4](docs/TEST_MANUEL_ETAPE4.md), [étape 5](docs/TEST_MANUEL_ETAPE5.md)
- Contribuer : [CONTRIBUTING.md](CONTRIBUTING.md)

## Avertissement

Ce projet est indépendant et **n'est ni affilié, ni approuvé, ni soutenu par Image-Line**.
« FL Studio » est une marque d'Image-Line Software. Aucun logo officiel n'est utilisé.

## Licence

[MIT](LICENSE)

---

## English summary

**FL Multiplayer** lets two people work on the same FL Studio project from two Macs: tempo,
mixer (volume, pan, mute, solo per track) and channel rack (volume, pan, mute per channel,
API 33+) are mirrored in real time over the Internet. Each user plays and stops on their own:
the transport is not synced. Notes, playlist, plugins and audio are not synced either, so both
users open the same project.

An FL Studio MIDI controller script talks SysEx to a local Python relay (`flmp join --virtual`
creates the MIDI ports itself; IAC buses also work), which connects to a small WebSocket room
server (free deployment on Render, or Fly.io; see `docs/DEPLOIEMENT.md`). Rooms are protected by
a shared code and an optional password; the server keeps no data.

Version 0.1.0 was tested inside FL Studio 2026 (API 45) on one Mac with a simulated peer; a real
two-Mac session has not been tested yet. Not affiliated with Image-Line; "FL Studio" is a
trademark of Image-Line Software. MIT licensed.
