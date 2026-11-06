# Test manuel — v0.2 : lecture indépendante et envoi de patterns

À faire à deux, chacun sur son Mac, avec **le même projet** ouvert. Notez ✅ / ❌ et ce que vous
voyez.

## A. Mise à jour (chacun)

- [ ] **A1.** Dans le dossier du projet : `uv run flmp install-script`. Le Terminal affiche
  « Script installé dans … » **et** « Scripts de piano roll installés dans … ».
- [ ] **A2.** Relancez le relais (`uv run flmp join …`, comme d'habitude), puis dans FL :
  *View > Script output*, onglet **FLMP In**, bouton **Reload script**.

## B. Lecture indépendante

- [ ] **B1.** L'un lance la lecture : chez l'autre, **rien ne bouge**.
- [ ] **B2.** L'autre lance aussi la lecture, puis l'arrête : la lecture du premier continue.
- [ ] **B3.** Le tempo, un fader du mixer et un mute passent toujours dans les deux sens.

## C. Envoyer un pattern

- [ ] **C1.** A dessine 3 ou 4 notes dans le piano roll d'un channel, puis, menu du piano roll
  > **Tools > Scripting > FL Multiplayer - Envoyer** > OK. Son Terminal affiche « Pattern envoyé ».
- [ ] **C2.** Le Terminal de B affiche « Pattern reçu de A (N notes) ».
- [ ] **C3.** B ouvre le piano roll **du même channel**, puis **Tools > Scripting > FL
  Multiplayer - Recevoir**, mode **Remplacer les notes** > OK : les notes de A apparaissent, aux
  mêmes hauteurs et positions.
- [ ] **C4.** Refaites C1–C3 en sélectionnant seulement 2 notes chez A et en cochant « Seulement
  les notes sélectionnées », puis mode **Ajouter aux notes** chez B : les 2 notes s'ajoutent.
- [ ] **C5.** Dans l'autre sens (B envoie, A reçoit).

## À me renvoyer

Le résultat de chaque case, et tout message d'erreur (fenêtres de FL ou Terminaux), copié tel
quel.
