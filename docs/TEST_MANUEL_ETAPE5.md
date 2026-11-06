# Test manuel — étape 5 : mixer et channel rack

But : vérifier **dans FL Studio** la synchro du mixer (volume, pan, mute, solo de chaque piste)
et du channel rack (volume, pan, mute de chaque channel). Notez le résultat de chaque case
(✅ / ❌ + ce que vous avez vu) et renvoyez-moi la liste.

⚠️ Faites ce test sur un **projet vide ou une copie** : les commandes du moniteur modifient
vraiment le mixer et le channel rack.

## A. Préparation

- [ ] **A1. Script à jour.** `uv run flmp install-script`, puis dans FL :
  *View > Script output*, onglet **FLMP In**, bouton **Reload script** (ou relancez FL).
  Le message `FL Multiplayer <version> chargé (API FL N)` doit réapparaître, sans erreur.
- [ ] **A2. Trois Terminaux**, comme à l'étape 4, dans `~/fl-multiplayer` :
  1. `uv run flmp-server`
  2. `uv run flmp join --server ws://localhost:8765 --room TEST1 --name Moi --virtual`
     (retirez `--virtual` si vous utilisez les bus IAC). Avec `--virtual`, lancez-le **avant** FL.
  3. `uv run flmp monitor --server ws://localhost:8765 --room TEST1`

À l'arrivée du moniteur, le relais doit afficher « État complet envoyé (N valeurs) ». Notez N :
il compte le tempo, 4 valeurs par piste du mixer et 3 par channel.

## B. Mixer

**De FL vers le réseau** (regardez la fenêtre 3) :

- [ ] **B1.** Bougez le fader de volume de la piste Insert 1. Le moniteur affiche
  `'Moi' → mixer piste 1 : volume … %`. Pendant le geste : une rafale limitée ; la **dernière**
  valeur correspond au fader.
- [ ] **B2.** Tournez le pan de la piste 2 : `mixer piste 2 : pan … %`.
- [ ] **B3.** Mutez la piste 3 : `mixer piste 3 : mute activé`. Démutez : `désactivé`.
- [ ] **B4. Solo.** Mutez la piste 3, puis mettez la piste 1 en solo. On attend **une seule** ligne :
  `mixer piste 1 : solo activé`, et **aucune** série de `mute` sur les autres pistes, même si FL
  les coupe. Retirez le solo : `solo désactivé` **et** `mixer piste 3 : mute désactivé`, car FL
  démute toutes les pistes au retrait d'un solo (comportement de FL, vérifié dans FL 2026).
- [ ] **B5. Master.** Bougez le volume du Master : `mixer piste 0 : volume …`.

**Du réseau vers FL** (tapez dans la fenêtre 3) :

- [ ] **B6.** `mix vol 1 0.5` : le fader de la piste 1 descend vers la moitié. `mix vol 1 0.8`
  le remet à la valeur par défaut.
- [ ] **B7.** `mix pan 2 -1` : pan tout à gauche. `mix pan 2 0` : au centre.
- [ ] **B8.** `mix mute 3 on`, puis `mix mute 3 off`.
- [ ] **B9.** `mix solo 1 on` : la piste 1 passe en solo, et le moniteur n'affiche **rien** venant de
  `'Moi'` (ni solo, ni mutes). `mix solo 1 off` : les mutes d'avant reviennent, toujours sans écho.
- [ ] **B9 bis.** Mutez la piste 2, puis tapez `mix solo 1 off` alors qu'aucune piste n'est en solo :
  la piste 2 doit **rester mutée** (rien ne bouge, rien ne revient).
- [ ] **B10. Pas d'écho.** Après B6–B8, aucune ligne `'Moi' → …` ne doit revenir pour la valeur que
  vous venez d'envoyer. Attendez 5 s : plus rien ne doit défiler.

## C. Channel rack

Les channels sont numérotés à partir de **0**, dans l'ordre du groupe « All ».

- [ ] **C1.** Bougez le petit bouton de volume du premier channel :
  `'Moi' → channel 0 : volume … %`.
- [ ] **C2.** Son pan : `channel 0 : pan … %`. Son bouton vert (mute) : `channel 0 : mute activé`.
- [ ] **C3.** `ch vol 0 0.25`, `ch pan 0 0.5`, `ch mute 0 on` puis `ch mute 0 off` : le channel 0
  suit à chaque fois, sans écho.
- [ ] **C4. Groupes.** Si votre projet a des groupes de channels, affichez un autre groupe que
  « All », puis refaites `ch mute 0 on` : c'est bien le **premier channel du groupe All** qui doit
  être muté, pas le premier affiché.
- [ ] **C5. Solo d'un channel.** Clic droit sur le bouton vert d'un channel → solo. Notez ce
  qu'affiche le moniteur. Le solo de channel n'est pas synchronisé en v1 ; on attend des `mute`
  sur les autres channels.

## D. Fluidité

- [ ] **D1.** Pendant la lecture d'un projet chargé (beaucoup de pistes ou de channels), FL ne doit
  pas ralentir ni craquer à cause du script. Si c'est le cas, notez-le : le script relit tout le
  mixer à chaque cycle, et je réduirai la fréquence.

## E. À deux (optionnel, même réseau local)

Mêmes commandes qu'à l'étape 4 (partie C), sur un **même projet** ouvert des deux côtés.

- [ ] **E1.** Volume, pan et mute d'une piste sur A : B suit. Puis l'inverse.
- [ ] **E2.** Solo sur B : A se retrouve dans le même état (même piste en solo, mêmes pistes coupées).
- [ ] **E3.** Arrivée en retard : réglez quelques faders sur A, puis lancez le relais de B. B doit
  prendre les valeurs de A.

## À me renvoyer

- Le N de A2 et votre version de FL.
- Le résultat de chaque case, avec **tout** ce qu'affiche le moniteur en B4, B9 et C5.
- Tout message d'erreur de *Script output* ou des Terminaux, copié tel quel.

## Limites connues de cette étape

- Les pistes et les channels sont repérés par leur **numéro**, pas par leur nom : les deux
  participants doivent partir du **même projet**. Ajouter, supprimer ou déplacer un channel décale
  les numéros.
- Le solo de channel, les sends, les effets et les noms ne sont pas synchronisés.
- Le channel rack n'est synchronisé qu'à partir de l'API FL 33 (avant, la numérotation des channels
  dépend du groupe affiché, ce qui enverrait les valeurs au mauvais channel).
- Comportement de FL (vérifié dans FL 2026) : retirer un solo démute **toutes** les pistes, et
  changer un mute pendant un solo annule le solo. La synchro reproduit fidèlement ces effets.
- Un mute changé **pendant** un solo de piste n'est transmis qu'une fois le solo retiré.
