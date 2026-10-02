
## 24/09/2026 — Etat des lieu 3 micros + campagne chaine A (jack MIC)

- [etat des lieu] Les 3 entrees fonctionnent. MEMS = le plus propre (-57.9 dBFS a gain +54),
  mais COUPE si une fiche jack est enfichee (constat Louis + mesures). B (ADA1063, piles AAA)
  et A (electret) fonctionnent. Raies 50/150 Hz partout sauf MEMS mux1.
- [MUX Mic 1] MIC_P (1) seul capte la voix (ton 1 kHz : +17 dB au-dessus du plancher).
  Differential (0) : signal -22 dB. MIC_N (2) : rien. Decision : MUX=1 definitif.
- [hum 50 Hz] Suit le gain au dB pres (54->30 dB : 43.5->19.6) => capte en amont, sur la
  ligne micro. Le gain ne change pas le SNR. Decision : gain total 42 dB (marge anti-clipping,
  claquements a -1.2 dBFS a 54 dB).
- [HPF codec] Coupure max = Fs/3000 = 5.3 Hz : inutile contre le 50 Hz. Decision : garder on
  (anti-DC), traiter le 50 Hz en offline.
- [traitement] notch (highpass 80 + equalizer 50/100/150 Q25 -45 dB) : 50 Hz -40 dB pour
  0.7 dB de perte. noisered 0.25 : +20 dB de SNR pour 5-6 dB de perte. RNNoise (sh.rnnn,
  48 kHz) : detruit les transitoires (-24 dB sur claquements) => rejete. afftdn : inefficace
  sur le 50 Hz => rejete.
- [pipeline final] despike (clics <5 ms, >-26 dBFS, interpolation locale) -> notch -> noisered
  0.25 (profil pris sur fenetre silencieuse 1,5 s SANS clic). Sur les prises voix de Louis :
  SNR 34.7 et 36.5 dB, perte de voix 6.1 et 5.0 dB, 0 clic residuel.
- [clics] Source ELECTRIQUE : presents en silence pur sans personne a cote, pics jusqu a
  -5 dBFS, non periodiques. Piste : contact fiche jack / cable / soudures capsule.
  Tests materiels a demander a Louis.
- [outils] ~/bench/voix_test.sh (enregistre + pipeline + mesure), ~/bench/pipeline_v2.py,
  ~/bench/sweep_A.sh, ~/bench/sig_mux.sh, ~/bench/etat_des_lieu.sh. Prises dans ~/bench/etat_des_lieu/.
- [reste] SNR >= 40 dB non atteint (34.7-36.5). Levier : voix plus proche (20-25 cm, +6 dB
  attendus) ou noisered 0.35 (perte ~8 dB). Ecoute Louis sur _traite.wav a confirmer.

## 25/09/2026 — Comparaison honnete voie A vs voie B + reglages valides

- [reglages valides Louis] Chaine de traitement retenue a l oreille : despike -> notch
  50/100/150 (Q25, -45 dB) -> noisered 0.25 -> expandeur doux (mcompand, seuil -60 dB).
  Fichiers "_traite" (defaut pipeline_v2.py). NR et EXP restent reglables par env.
  Artefacts corriges : resonance metallique (noisered 0.25 seul) et scintillement
  (bruit musical sur les pauses) -> expandeur. Variants rejetes : peigne de notchs
  harmoniques (4.5 dB de perte de voix seule), afftdn (SNR 9-5 dB), lowpass 4 kHz
  (2 dB de voix pour rien), RNNoise (transitoires detruits).
- [comparaison A/B, meme phrase, meme distance, meme traitement] Prises 20260925-001506 (A)
  et 20260925-001618 (B) :
  * Voie A (jack MIC, electret) : brut SNR 6.9 dB, 17 clics. Traite : SNR 38.3 dB,
    perte de voix 8.0 dB, 7 clics residuels.
  * Voie B (ADA1063/MAX4466 sur Aux, piles AAA) : brut SNR 32.0 dB (!), 1 clic.
    Traite : SNR 56.1 dB, perte de voix 7.2 dB, 0 clic.
- [verdict Louis] Voie B un peu mieux (plus propre, moins de bruit de fond) MAIS schema
  electronique plus complexe. Voie A presque sans soudure. Choix final A vs B : non tranche,
  arbitrage qualite vs simplicite de realisation.
- [constat] Les clics electriques quasi absents sur B (1 vs 17) alors que B est sur piles :
  la source des clics est probablement le reseau bias/jack de la voie A (et non le secteur).
## 25/09/2026 (suite) — Fichiers complets, artefacts de demarrage, alim USB vs piles

- [correction majeure] Les fichiers "_traite" tronquaient les 2 premieres secondes (trim
  anti-bias du pipeline). Le traitement agit desormais sur le signal COMPLET (11,92 s) ; la
  rampe de bias n est exclue que des mesures et du profil de bruit. Prises A/B du 25/09
  regenerees.
- [artefacts de demarrage] Louis : "artefacts fort en volume dans la premiere demi seconde".
  Cause = transient de demarrage (charge du bias electret), que masquait l ancien trim.
  Sur alim USB (prise 20260925-005855) : rafales saturees (|signal|>=95 %) au depart ET
  recurentes (~toutes les 0,5 s) = parasites de l alim USB.
  Correctifs :
  * invite des scripts : "attends 0,5 s avant de parler" (minimum mesure). LE FICHIER NE
    COUPE PLUS RIEN : si un invite parle des la 1re ms, sa voix reste dans le fichier.
  * declip automatique dans pipeline_v2.py : les zones saturees (inexploitables
    electriquement) sont interpolees. 10 zones neutralisees sur la prise USB ; les 5
    "clics" residuels n etaient que les bords de ces saturations.
- [alim USB vs piles AAA — micro B, prise 20260925-005855] USB : bruit brut -39.6 dB
  (-64.1 sur piles) = +24.5 dB de bruit de fond. SNR brut 7.3 dB (vs 32.0 sur piles).
  Perte de voix traitee 14.8 dB (chaîne complete) / 8.7 dB (chaîne legere NR seul),
  SNR final 38.6 / 41.5 dB. => sur USB la chaîne legere gagne (les notchs coutent 2-5.6 dB
  de voix et n apportent rien : le bruit USB est large bande, pas secteur).
  Verdict : l alim USB est un mode degrade de secours ; piles AAA = reference propre.
- [ajout] env NOTCH=0 (skip des coupe-bandes 50/100/150) ; env EXP=0 (skip expandeur).
  Rappel syntaxe : variables AVANT la commande ("EXP=0 NR=0.25 bash ./voix_test_B.sh"),
  sinon elles deviennent des arguments ignorees.
- [pris en compte] 20260925-003347-VOIX (voie A, alim USB) : rendu correct selon Louis,
  artefacts confines a la premiere demi seconde.
## 02/10/2026 — Chaine allegee : anti-ronflement + debruitage leger

- [constat Louis] Sur le telephone monte, la chaine du 25/09 rend une voix faussee et
  metallique. Prises de reference (Drive) : 6 messages + silence.wav de 25 s, 16 kHz.
- [mesures silence.wav] Bruit -28.8 dBFS dont 94 % dans les raies secteur (50 Hz -33 dB,
  150 Hz -39 dB, harmoniques visibles jusqu a ~4 kHz). Frequence secteur 49.98-49.99 Hz,
  49.92-50.03 Hz selon les prises : un peigne de notchs fixes rate les harmoniques hautes.
  Souffle large bande restant ~ -41 dBFS.
- [mesures messages] Plusieurs prises saturent dans la voix elle-meme (jusqu a 19 % des
  echantillons a pleine echelle sur 19-55-25) : declip et despike interpolaient donc des
  morceaux de voix. Ecretage irreversible : a corriger par le gain de capture, pas par
  le traitement.
- [nouvelle chaine] (1) soustraction du ronflement : harmoniques du secteur jusqu a 4 kHz,
  frequence suivie par trame de 1 s, amplitude/phase projetees puis retirees en
  recouvrement-addition (~1 Hz retire par raie) ; (2) Wiener decision dirigee plafonne a
  -10 dB, bruit = 20 % des trames les plus calmes, saute sans pause ; (3) normalisation
  -20 dBFS inchangee. Plus de declip, despike, notch Q25, noisered ni expandeur.
- [resultats, sans normalisation] silence : raies -29 dB, souffle -5 a -9 dB. Messages :
  perte de voix (95e centile) 0.3-0.5 dB (contre 5-8 dB avant), pauses -10 a -28 dB,
  spectre hors raies inchange a 0.4 dB pres de 300 Hz a 8 kHz. Cout : ~1 s de calcul pour
  40 s de message sur PC.
- [gain de capture] Abaisse de 42 dB (Mic 1 +36, PGA +6) a 24 dB (Mic 1 +24, PGA 0) dans
  scripts/audio-setup.sh. Sur les prises du 02/10 a 42 dB, 8 a 15 % des tranches de 100 ms
  ecretent sur des messages parles normalement (20-05-32, 20-00-36), 39 a 60 % en parlant
  fort (19-50-25, 19-55-25). SNR inchange (hum et souffle suivent le gain), plancher ~ -70
  dBFS ; la normalisation remonte la voix. A verifier : un message parle fort ne doit plus
  atteindre la pleine echelle ; s il ecrete encore, PGA a 1 (-3 dB) ; si la voix sort
  vers -40 dBFS ou moins, PGA a 5 (+3 dB).
