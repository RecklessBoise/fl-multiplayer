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
| `play` | 0x01 | — | 4 octets (28 bits) | position, ticks absolus |
| `stop` | 0x02 | — | — | |
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
