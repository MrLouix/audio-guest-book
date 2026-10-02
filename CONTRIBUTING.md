# Contribuer

Merci de l'intérêt porté au livre d'or téléphonique ! Le projet est maintenu
à titre personnel : les contributions sont bienvenues, mais les réponses
peuvent prendre un peu de temps.

## Signaler un problème ou proposer une idée

Ouvrez une [issue](../../issues/new/choose) en choisissant le modèle adapté
(bug ou suggestion). Pour un problème audio, joignez si possible un extrait
de `logs/livre_dor.log` et précisez le matériel (modèle de Raspberry Pi, carte
son, alimentation) : beaucoup de défauts viennent du câblage ou de
l'alimentation plutôt que du code.

Une faille de sécurité ne se signale **pas** dans une issue publique : voir
[`SECURITY.md`](SECURITY.md).

## Proposer une modification

1. Créez une branche depuis la branche par défaut du dépôt.
2. Gardez chaque pull request centrée sur un seul sujet.
3. Lancez les tests avant de pousser (aucune dépendance à installer, ils
   tournent sur un poste de développement sans Raspberry Pi) :

   ```bash
   python3 tests/run_tous.py
   ```

   `tests/test_traitement.py` demande `numpy` et `sox` ; sans eux, ses
   vérifications sont ignorées, pas mises en échec.
4. Si la modification touche le matériel (GPIO, codec, cadran), dites dans la
   pull request si elle a été essayée sur le téléphone réel
   (`python3 tests/run_tous.py --reel`).
5. Remplissez le modèle de pull request.

## Conventions

- **Langue** : code, commentaires, messages de journal, documentation et
  messages de commit sont en français.
- **Style** : suivez celui du code environnant (noms en français, densité des
  commentaires, docstrings). Les commentaires expliquent le *pourquoi* : une
  valeur mesurée au banc se documente avec sa mesure.
- **Réglages** : un nouveau paramètre se déclare une seule fois dans
  `src/config.py` (variable d'environnement + défaut), et dans
  `MODIFIABLE_PARAMS` s'il doit apparaître dans le dashboard.
- **Codec** : les numids `amixer` du DA7213 ne vivent que dans
  `scripts/audio-setup.sh`, jamais côté Python.
- **Documentation** : mettez à jour le `README.md` et, si besoin,
  `docs/` dans la même pull request.

En contribuant, vous acceptez que votre contribution soit publiée sous la
[licence MIT](LICENSE) du projet, et vous vous engagez à respecter le
[code de conduite](CODE_OF_CONDUCT.md).
