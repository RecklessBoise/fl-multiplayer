# Protocole SysEx FL Multiplayer (v1)

Source de vérité : [`fl_script/flmp_core/messages.py`](../fl_script/flmp_core/messages.py).

## Trame

```
F0 7D <version=01> <code> [index : 2 octets] [valeur : n octets] F7
```

- `7D` : identifiant fabricant réservé à l'usage non commercial.
- Tous les octets de données sont sur 7 bits. Les entiers sont découpés en groupes de 7 bits,
  octet de poids fort en premier.
- Une trame qui ne respecte pas exactement ce format est rejetée (`ProtocolError`).

## Types de messages

| Nom | Code | Index | Valeur | Unité |
|---|---|---|---|---|
| `play` | 0x01 | — | 4 octets (28 bits) | position, ticks absolus (ignoré par le script FL depuis la v0.2) |
| `stop` | 0x02 | — | — | ignoré par le script FL depuis la v0.2 |
| `tempo` | 0x03 | — | 3 octets (21 bits) | BPM × 1000 |
| `mix_vol` | 0x10 | piste | 14 bits | 0.0–1.0 → 0–16383 |
| `mix_pan` | 0x11 | piste | 14 bits | -1.0–1.0 → 0–16383 |
| `mix_mute` | 0x12 | piste | 0/1 | |
| `mix_solo` | 0x13 | piste | 0/1 | |
| `ch_vol` | 0x20 | canal | 14 bits | 0.0–1.0 → 0–16383 |
| `ch_pan` | 0x21 | canal | 14 bits | -1.0–1.0 → 0–16383 |
| `ch_mute` | 0x22 | canal | 0/1 | |
| `pattern` | 0x30 | — | 14 bits | numéro de pattern |
| `snapshot_req` | 0x40 | — | — | demande l'état complet |
| `snapshot_begin` | 0x41 | — | — | début d'un état complet |
| `snapshot_end` | 0x42 | — | — | fin d'un état complet |

Les index tiennent sur 14 bits (0–16383).

## Anti-boucle

`StateTracker` (dans `state.py`) :

1. `diff()` reçoit toujours l'état **complet**. Le premier appel sert de référence et n'émet
   rien.
2. Chaque `diff()` suivant n'émet que les valeurs qui ont réellement changé. Une clé apparue
   entre-temps est signalée, une clé disparue est oubliée.
3. Une valeur reçue du réseau est d'abord écrite dans FL, puis passe par `apply_remote()`.
   `apply_remote()` refuse une clé qui n'existe pas dans le projet local et lève
   `ProtocolError` si la valeur est invalide.
4. La marque « valeur distante » ne sert qu'une fois. La première relecture qui correspond,
   à la tolérance près, l'efface. Les petits mouvements suivants sont donc bien envoyés.
5. Si FL relit une autre valeur, elle n'est pas renvoyée pendant `REMOTE_CONFIRM_TICKS` (3)
   appels : FL n'a peut-être pas encore appliqué la valeur. Ensuite, elle compte comme un vrai
   mouvement local.
6. `suppressing()` met l'état à jour sans rien émettre, par exemple pendant l'application d'un
   snapshot.

## Throttling

`Throttle` (dans `throttle.py`) limite chaque clé continue (volume, pan, tempo) à
30 messages/s par défaut. Le premier message part tout de suite. Pendant une rafale, seule la
dernière valeur est gardée et envoyée à l'échéance, donc la valeur finale n'est jamais perdue.
Les événements discrets (play, stop, mute, solo, pattern) ne sont pas limités.

Quand une valeur distante est appliquée à une clé, l'appelant doit faire `discard(key)`. Sinon,
une ancienne valeur locale encore en attente l'écraserait chez les autres.

Note : 16383 est impair, donc un pan centré se relit à environ 6e-5 près de 0.

## Protocole réseau (relais ⇄ serveur, JSON sur WebSocket)

| Sens | Message |
|---|---|
| client → serveur | `{"type":"hello"}` (diagnostic), `{"type":"join","room","name","secret"?,"proto":1}` |
| client → serveur | `{"type":"change","kind","index","value"}` |
| client → serveur | `{"type":"snapshot","for":<pair>,"changes":[[kind,index,value],…]}` (uniquement en réponse à une demande) |
| client → serveur | `{"type":"pattern","ppq","notes":[[hauteur,temps,durée,vélocité,pan,release],…]}` (v0.2) |
| serveur → client | `welcome`, `peer_joined`, `peer_left`, `error` (`code`, `message`) |
| serveur → client | `{"type":"change",…,"seq","from"}`, diffusé à **tous**, émetteur compris |
| serveur → client | `{"type":"snapshot_request","for","base_seq"}` puis, au nouvel arrivant, `{"type":"snapshot","from","seq","base_seq","changes"}` |
| serveur → client | `{"type":"pattern","from","ppq","notes"}`, diffusé aux **autres** membres, jamais stocké (v0.2) |

### Patterns du piano roll (v0.2)

- Validation commune au relais et au serveur (`flmp_core.pattern`) : `ppq` entier de 24 à 1920 ;
  au plus 4000 notes ; hauteur 0–131 ; temps et durée entiers de 0 à 2³¹−1 ; vélocité, pan et
  release entre 0 et 1. Un pattern invalide est refusé (`bad_message`) et n'est pas relayé.
- Côté Mac, le script de piano roll « Envoyer » écrit `outbox.json` dans
  `Piano roll scripts/FL Multiplayer/` ; le relais l'envoie dès qu'il change (pas au démarrage).
  À la réception, le relais écrit `inbox.json` d'un seul coup (fichier temporaire puis
  renommage), que le script « Recevoir » lit et valide à nouveau.

### Le dernier qui écrit gagne

- Le serveur numérote chaque changement (`seq`, propre au salon) et le diffuse à tout le salon,
  émetteur compris, dans l'ordre des numéros.
- Tant que l'écho d'une de nos écritures sur une clé n'est pas revenu, le relais ignore les
  changements des autres sur cette clé. Ils sont forcément plus anciens que le nôtre, et les
  autres recevront notre valeur juste après.
- Une écriture sans écho expire au bout de 2 s : le serveur peut l'avoir rejetée ou limitée.

### Arrivée dans un salon

1. Le serveur demande l'état au plus ancien membre qui n'attend pas lui-même un snapshot. Il
   note alors le numéro de séquence courant (`base_seq`).
2. Le relais de ce membre demande à FL un export complet (`snapshot_req`, puis
   `snapshot_begin` … `snapshot_end`). Un seul export de FL répond à tous les nouveaux arrivants
   en attente.
3. Le nouvel arrivant met de côté les changements reçus entre-temps. Il applique le snapshot,
   sans toucher ses propres écritures encore en vol, puis rejoue uniquement les changements dont
   `seq > base_seq`.
4. Si aucun état n'arrive (`snapshot_timeout`, `snapshot_unavailable`, ou 15 s sans réponse), le
   relais continue avec ce qu'il a.

### Limites du serveur (par défaut)

- 256 Kio par message, 200 messages/s par connexion (rafale de 400).
- 8 membres par salon, 1000 salons au total.
- Par adresse IP : 16 connexions, 8 salons créés, 10 échecs de mot de passe par tranche de 5 min.
- Secret de 8 à 128 caractères, conservé en mémoire sous forme de hachage SHA-256.
