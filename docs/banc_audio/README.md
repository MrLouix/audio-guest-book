# Banc audio — micro électret sur le jack MIC

Notes de mesure des sessions du 22 au 25/09/2026 sur le Pi `livredor`, copiées
telles quelles depuis le dossier de banc (les chemins `~/bench/...` qu'elles
citent sont ceux du Pi, hors dépôt).

- `JOURNAL.md` — journal des décisions : MUX MIC_P, gain total 42 dB, chaîne de
  traitement retenue à l'oreille, comparaison voie A (électret, jack MIC) et
  voie B (ADA1063 sur Aux), alimentation USB contre piles.
- `iqaudio-codec-zero-micro-electret.md` — note de référence de la chaîne
  électret. Sa révision du 24/09 prévaut : le MUX est sur MIC_P, et plusieurs
  mesures chiffrées sont à reconfirmer.

Ce qui en est tiré dans le code :

- `scripts/audio-setup.sh` — réglages du codec (voie A, gain 42 dB) ;
- `src/config.py` — capture en 16 kHz, paramètres `TRAITEMENT_*` ;
- `src/traitement_audio.py` — port de `pipeline_v2.py` (sans les mesures),
  appliqué à chaque message. Deux écarts volontaires avec le banc : les clics
  sont supprimés dès 0,5 s (et non 2 s), et la fin du message n'est plus
  amputée des 64 ms que `sox noisered` retire.
