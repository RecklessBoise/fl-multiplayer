# Test manuel — étape 4 : transport et tempo

But : vérifier **dans FL Studio** ce que les tests automatiques ne peuvent pas prouver. Notez
le résultat de chaque case (✅ / ❌ + ce que vous avez vu) et renvoyez-moi la liste.

Toutes les commandes se tapent dans le Terminal, depuis le dossier du projet :

```bash
cd ~/fl-multiplayer
```

## A. Installation (une seule fois)

- [ ] **A1. Bus IAC.** Ouvrez *Configuration audio et MIDI* (Applications > Utilitaires).
  Menu *Fenêtre > Afficher le studio MIDI*, puis double-cliquez sur *Gestionnaire IAC*.
  Cochez *Le gestionnaire est en ligne*. Avec le bouton **+**, créez deux bus nommés
  exactement `FLMP In` et `FLMP Out`.
- [ ] **A2. Diagnostic.** `uv run flmp doctor` doit afficher `OK` sur les trois lignes.
- [ ] **A3. Script.** `uv run flmp install-script` doit répondre « Script installé dans … ».
- [ ] **A4. FL Studio.** Quittez puis relancez FL. Ouvrez *Options > MIDI settings* :
  - **Input :** sélectionnez `IAC Driver FLMP In`, activez-le (*Enable*), choisissez
    *Controller type* = **FL Multiplayer** et réglez *Port* sur un numéro libre, par exemple `10`.
  - **Input :** ⚠️ n'activez **pas** `IAC Driver FLMP Out`. FL relirait alors ses propres
    messages.
  - **Output :** sélectionnez `IAC Driver FLMP Out` et donnez-lui **le même numéro de port**
    (`10`). Ne donnez ce numéro à aucune autre sortie, et n'attribuez **pas** de port de sortie
    à `IAC Driver FLMP In`.
- [ ] **A5. Message de démarrage.** Ouvrez *View > Script output* (le nom exact peut varier selon
  la version). Vous devez voir `FL Multiplayer 0.1.0-dev chargé (API FL N)`.
  **Notez N** (la version de l'API) **et votre version de FL Studio.**
  Il ne doit **pas** y avoir le message « aucune sortie MIDI reliée ».
  S'il y a une erreur, par exemple `ImportError`, copiez-la telle quelle.

## B. Test seul (un seul FL, sur ce Mac)

Ouvrez **trois fenêtres de Terminal**, toutes dans `~/fl-multiplayer` :

1. Le serveur : `uv run flmp-server`
2. Le relais (votre FL) : `uv run flmp join --server ws://localhost:8765 --room TEST1 --name Moi`
3. Le faux participant : `uv run flmp monitor --server ws://localhost:8765 --room TEST1`

Le relais doit afficher « Connecté au salon 'TEST1' ».

**De FL vers le réseau** (regardez la fenêtre 3) :

- [ ] **B1.** Lancez la lecture dans FL. Le moniteur affiche `'Moi' → lecture depuis le tick …`.
  Notez le nombre. Arrêtez la lecture : le moniteur affiche `→ arrêt`.
- [ ] **B2.** Placez la tête de lecture plus loin (par exemple mesure 3), puis lancez la lecture.
  Le tick affiché doit être plus grand. Avec un PPQ de 96 en 4/4, la mesure 3 vaut 768.
- [ ] **B3.** Changez le tempo de FL, par exemple en tapant 128.5. Le moniteur affiche
  `→ tempo 128.500 BPM`. Si vous faites glisser le tempo à la souris, vous voyez une rafale
  limitée, et la **dernière** valeur doit correspondre à celle de FL.

**Du réseau vers FL** (tapez dans la fenêtre 3, puis Entrée) :

- [ ] **B4.** `tempo 95`. Le tempo de FL passe à 95. Aucune ligne `'Moi' → tempo` ne doit
  revenir dans le moniteur, sinon c'est un écho.
- [ ] **B5.** `play 0`. FL lance la lecture depuis le début. `stop` l'arrête.
- [ ] **B6.** `play 768`. FL démarre vers la mesure 3. Notez où exactement.
- [ ] **B7.** Pendant que FL joue, tapez `play 0`. FL doit **continuer à jouer**, ramené au
  début. Il ne doit pas se mettre en pause. Notez si le saut se fait proprement ou avec un
  accroc audible : je ne peux pas le vérifier sans FL.
- [ ] **B8. Pas de boucle.** Après chaque action, attendez 5 s : le moniteur ne doit plus rien
  afficher, et FL ne doit pas clignoter entre deux états.
- [ ] **B9. Reconnexion.** Faites Ctrl+C dans la fenêtre 1 (serveur), attendez que le relais
  affiche « Nouvelle tentative… », puis relancez `uv run flmp-server`. Le relais se reconnecte
  tout seul. Relancez le moniteur, puis refaites B4.

## C. À deux (optionnel à ce stade, même réseau local)

Le serveur public, chiffré (wss), sera mis en place à l'étape 6. En attendant, ce test passe
**en clair sur le réseau local**. Faites-le uniquement sur un réseau de confiance (chez vous,
pas un Wi-Fi public), et protégez le salon avec un mot de passe.

Sur le Mac A, notez l'adresse IP locale (*Réglages > Wi-Fi > Détails*), puis lancez le serveur
sur cette seule adresse : `uv run flmp-server --host IP_DE_A`.

- Sur A : `uv run flmp join --server ws://IP_DE_A:8765 --room JAM1 --name A --insecure --ask-secret`
- Sur B : `uv run flmp join --server ws://IP_DE_A:8765 --room JAM1 --name B --insecure --ask-secret`

Tapez le même mot de passe (8 caractères minimum) des deux côtés. Arrêtez le serveur (Ctrl+C)
après le test.

- [ ] **C1.** Lecture et arrêt sur A : B suit. Puis l'inverse.
- [ ] **C2.** Tempo sur B : A suit. Pas d'écho, pas de boucle.
- [ ] **C3.** Arrivée en retard : changez le tempo de A, puis lancez le relais de B. B doit
  prendre le tempo de A.

## À me renvoyer

- Votre version de FL Studio, et le N affiché en A5.
- Le résultat de chaque case, avec les nombres notés en B1, B2 et B6.
- Tout message d'erreur de *Script output* ou des Terminaux, copié tel quel.

## Limites connues de cette étape

- Seuls la lecture, l'arrêt, la position au démarrage et le tempo sont synchronisés. Le mixer et
  le channel rack arrivent à l'étape 5.
- La position n'est pas calée à l'échantillon près. Comptez la latence réseau, souvent 50 à
  150 ms.
- Si l'API de FL est antérieure à la version 3, la position de départ n'est pas synchronisée.
- La lecture en cours n'est pas transmise à quelqu'un qui arrive : seul le tempo l'est.
- Juste après une lecture ou un arrêt reçu d'un autre participant (moins d'une seconde environ),
  un appui sur lecture ou arrêt de votre côté peut ne pas être transmis. Refaites-le.
