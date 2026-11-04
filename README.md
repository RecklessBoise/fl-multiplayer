# FL Multiplayer

> **Statut : en développement (pré-v0.1.0).** Rien n'a encore été testé dans FL Studio.

Synchronisation en temps réel, par Internet, de deux copies de FL Studio (20.7 ou plus récent)
sur macOS : transport, tempo, mixer et channel rack.

```
[FL Studio A] ⇄ script MIDI ⇄ IAC Driver ⇄ relais A ⇄ WebSocket ⇄ serveur ⇄ WebSocket ⇄ relais B ⇄ IAC Driver ⇄ script MIDI ⇄ [FL Studio B]
```

## Périmètre de la v1

- Transport : lecture, arrêt, position au démarrage, tempo.
- Mixer : volume, pan, mute et solo de chaque piste.
- Channel rack : volume, pan et mute de chaque canal.
- Pattern actif (optionnel).

Hors périmètre : notes, playlist, ajout de plugins ou de samples, audio.

## Composants

| Dossier | Rôle |
|---|---|
| `fl_script/` | Script de contrôleur MIDI pour FL Studio, et logique pure partagée (`flmp_core`) |
| `relay/` | Relais local : bus IAC ⇄ serveur WebSocket (commande `flmp`) |
| `server/` | Serveur de salons (commande `flmp-server`) |

Le guide d'installation pas à pas pour Mac sera ajouté avec la v0.1.0.

## Développement

```bash
uv sync
uv run ruff check . && uv run ruff format --check .
uv run pytest
```

Voir [CONTRIBUTING.md](CONTRIBUTING.md).

## Avertissement

Ce projet est indépendant et **n'est ni affilié, ni approuvé, ni soutenu par Image-Line**.
« FL Studio » est une marque d'Image-Line Software. Aucun logo officiel n'est utilisé.

## Licence

[MIT](LICENSE)

---

## English summary

**FL Multiplayer** syncs two FL Studio instances (20.7+, macOS) in real time over the Internet:
transport, tempo, mixer (volume/pan/mute/solo) and channel rack (volume/pan/mute). It uses an
FL Studio MIDI controller script that talks SysEx over the macOS IAC Driver to a local Python
relay, which connects to a WebSocket room server. Work in progress, not yet tested inside
FL Studio. Not affiliated with Image-Line; "FL Studio" is a trademark of Image-Line Software.
MIT licensed.
