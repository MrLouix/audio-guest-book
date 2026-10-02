#!/usr/bin/env python3
"""Traitement des messages après enregistrement (src/traitement_audio.py).

Vérifie, sur un message synthétique (souffle, ronflement secteur légèrement
décalé de 50 Hz avec harmoniques, « voix » tonale hors des raies) :

1. le format produit : même cadence, stéréo, même durée ;
2. le brut conservé à l'identique dans messages/brut/, le message remplacé
   atomiquement (aucun fichier intermédiaire ne traîne) ;
3. l'effet de la chaîne : ronflement et harmoniques retirés, souffle des
   pauses abaissé, voix préservée ;
4. qu'un retraitement repart du brut (résultat identique) ;
5. le débruitage : sauté sur un message parlé d'un bout à l'autre ou trop
   court, sans échec ; suivi de la fréquence secteur ;
6. les échecs sans perte : sox absent, fichier illisible -> brut intact ;
7. la liste des messages en attente et le lancement en arrière-plan ;
8. la normalisation : voix faible ramenée à la cible, sans écrêtage.

Les vérifications qui demandent sox et numpy sont ignorées, et non mises en
échec, si la dépendance manque.

Usage :
    python3 tests/test_traitement.py
"""

import shutil
import tempfile
import wave
from pathlib import Path

import harness                       # règle sys.path : doit précéder les imports de src/
from harness import Rapport

import config                        # noqa: E402
import traitement_audio              # noqa: E402

np = traitement_audio.np
SR = 16000
SECTEUR = 49.97


def _message(chemin: Path, secondes: float = 12.0, parole_continue: bool = False,
             amplitude_voix: float = 0.1, claquement: bool = False) -> None:
    """Message synthétique au format d'enregistrement (16 kHz, stéréo L = R)."""
    rng = np.random.default_rng(1)
    t = np.arange(int(secondes * SR)) / SR
    d = rng.normal(0, 0.001, len(t))                     # plancher ~ -60 dBFS
    for k, a in ((1, 0.01), (3, 0.005), (7, 0.002)):     # secteur à 49,97 Hz ~ -42 dBFS
        d += a * np.sin(2 * np.pi * k * SECTEUR * t + k)
    # Raies de « voix » hors des harmoniques du secteur, comme une vraie voix
    # dont la hauteur ne coïncide jamais longtemps avec un multiple de 50 Hz.
    voix = amplitude_voix * (np.sin(2 * np.pi * 310 * t) + 0.5 * np.sin(2 * np.pi * 825 * t))
    if parole_continue:
        d += voix
    else:
        for a, b in ((2.5, 4.5), (9.0, 10.5)):
            d[int(a * SR):int(b * SR)] += voix[int(a * SR):int(b * SR)]
    if claquement:                                       # raccroché : choc bref et fort
        d[int((secondes - 0.5) * SR):int((secondes - 0.45) * SR)] += 0.5
    traitement_audio.ecrire(chemin, d, SR)


def _canaux(chemin: Path):
    with wave.open(str(chemin), "rb") as w:
        return (w.getframerate(), w.getnchannels(),
                np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).reshape(-1, 2))


def _raie(d, frequence: float, a: float, b: float) -> float:
    """Énergie (dB) de la raie `frequence` sur la plage [a, b] s."""
    x = d[int(a * SR):int(b * SR)]
    spectre = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    f = np.fft.rfftfreq(len(x), 1 / SR)
    return 10 * np.log10(spectre[(f > frequence - 3) & (f < frequence + 3)].sum() + 1e-18)


def _rms(d, a: float, b: float) -> float:
    x = d[int(a * SR):int(b * SR)]
    return 20 * np.log10(np.sqrt((x ** 2).mean()) + 1e-12)


def test_chaine(rapport: Rapport, dossier: Path) -> None:
    rapport.section("1. Format du message traité")
    message = dossier / "message_2026-06-20_14-00-00.wav"
    _message(message)
    original = message.read_bytes()
    rapport.verifie("le traitement réussit", traitement_audio.traiter(message))
    sr, canaux, pcm = _canaux(message)
    rapport.egal("même cadence (16 kHz)", sr, SR)
    rapport.egal("stéréo", canaux, 2)
    rapport.egal("même durée que l'enregistrement", len(pcm), 12 * SR)
    rapport.verifie("les deux pistes sont identiques", np.array_equal(pcm[:, 0], pcm[:, 1]))

    rapport.section("2. Brut conservé, remplacement atomique")
    brut = traitement_audio.brut_path(message)
    rapport.egal("le brut est dans messages/brut/", brut, dossier / "brut" / message.name)
    rapport.verifie("le brut est identique à l'enregistrement",
                    brut.exists() and brut.read_bytes() == original)
    rapport.egal("aucun fichier intermédiaire ne reste dans messages/",
                 sorted(p.name for p in dossier.iterdir() if p.is_file()), [message.name])

    rapport.section("3. Effet de la chaîne")
    avant = _canaux(brut)[2][:, 0] / 32768
    apres = pcm[:, 0] / 32768
    for k in (1, 3, 7):
        f = k * SECTEUR
        gain = _raie(avant, f, 5.0, 8.5) - _raie(apres, f, 5.0, 8.5)
        rapport.verifie(f"raie secteur {f:.0f} Hz atténuée d'au moins 30 dB", gain >= 30,
                        f"{gain:.1f} dB")
    # Normalisation retirée de la comparaison : seul le timbre compte ici.
    gain_norm = _rms(apres, 9.2, 10.3) - _rms(avant, 9.2, 10.3)
    for f in (310, 825):
        perte = _raie(avant, f, 2.7, 4.3) - (_raie(apres, f, 2.7, 4.3) - gain_norm)
        rapport.verifie(f"voix préservée à {f} Hz (écart < 1 dB)", abs(perte) < 1,
                        f"écart : {perte:+.2f} dB")
    bruit = _rms(avant, 5.0, 8.5) - (_rms(apres, 5.0, 8.5) - gain_norm)
    rapport.verifie("bruit de fond des pauses abaissé d'au moins 20 dB", bruit >= 20,
                    f"{bruit:.1f} dB")

    rapport.section("4. Retraitement")
    premier = message.read_bytes()
    rapport.verifie("un second traitement réussit", traitement_audio.traiter(message))
    rapport.verifie("il repart du brut : résultat identique", message.read_bytes() == premier)
    rapport.verifie("le brut n'est pas écrasé", brut.read_bytes() == original)


def test_debruitage(rapport: Rapport, dossier: Path) -> None:
    rapport.section("5. Débruitage et suivi du secteur")
    continu = dossier / "continu.wav"
    _message(continu, secondes=6.0, parole_continue=True)
    d, _ = traitement_audio.charger(continu)
    rapport.egal("parole continue : débruitage sauté (on ne débruite pas la voix)",
                 traitement_audio.debruitage(d, SR, 10.0, 0.5), None)
    rapport.verifie("parole continue : le traitement réussit quand même",
                    traitement_audio.traiter(continu))
    rapport.egal("message de 0,5 s : débruitage sauté",
                 traitement_audio.debruitage(np.zeros(SR // 2), SR, 10.0, 0.5), None)
    court = dossier / "court.wav"
    _message(court, secondes=1.0)
    rapport.verifie("message de 1 s : le traitement réussit",
                    traitement_audio.traiter(court))
    d, _ = traitement_audio.charger(dossier / "brut" / "message_2026-06-20_14-00-00.wav")
    f0 = traitement_audio.frequence_secteur(d[5 * SR:6 * SR], SR)
    rapport.verifie("fréquence secteur retrouvée à 0,01 Hz près", abs(f0 - SECTEUR) <= 0.01,
                    f"{f0:.3f} Hz pour {SECTEUR} Hz")


def test_echecs(rapport: Rapport, dossier: Path) -> None:
    rapport.section("6. Échecs sans perte")
    message = dossier / "sans_sox.wav"
    _message(message, secondes=3.0)
    original = message.read_bytes()
    with harness.remplacer(traitement_audio.shutil, "which", lambda nom: None):
        rapport.egal("sox absent : échec signalé", traitement_audio.traiter(message), False)
    rapport.verifie("sox absent : message intact", message.read_bytes() == original)

    illisible = dossier / "illisible.wav"
    illisible.write_bytes(b"pas un wav")
    rapport.egal("fichier illisible : échec signalé", traitement_audio.traiter(illisible), False)
    rapport.egal("fichier illisible : contenu intact", illisible.read_bytes(), b"pas un wav")
    rapport.verifie("fichier illisible : pas de fichier intermédiaire",
                    not (dossier / "illisible.wav.tmp").exists())
    rapport.verifie("fichier illisible : pas de copie brute (il reste en attente)",
                    not traitement_audio.brut_path(illisible).exists())

    rapport.egal("fichier absent : échec signalé",
                 traitement_audio.traiter(dossier / "absent.wav"), False)


def test_attente(rapport: Rapport, dossier: Path) -> None:
    rapport.section("7. Messages en attente et lancement en arrière-plan")
    with harness.remplacer(config, "MESSAGES_DIR", dossier):
        attente = [p.name for p in traitement_audio.messages_en_attente()]
    rapport.egal("seuls les messages sans brut sont en attente",
                 attente, ["illisible.wav", "sans_sox.wav"])

    proc = traitement_audio.lancer_en_arriere_plan(dossier / "sans_sox.wav")
    rapport.verifie("le processus est lancé", proc is not None)
    if proc is not None:
        rapport.egal("il se termine sans erreur", proc.wait(timeout=60), 0)
        rapport.verifie("le brut a été conservé",
                        traitement_audio.brut_path(dossier / "sans_sox.wav").exists())


def test_normalisation(rapport: Rapport, dossier: Path) -> None:
    rapport.section("8. Normalisation du niveau de la voix")
    faible = dossier / "faible.wav"
    _message(faible, amplitude_voix=0.01, claquement=True)   # voix vers -40 dBFS
    rapport.verifie("le traitement réussit", traitement_audio.traiter(faible))
    x = _canaux(faible)[2][:, 0] / 32768
    niveau = np.percentile(traitement_audio.niveaux_blocs(x, SR // 50), 95)
    cible = config.TRAITEMENT_NIVEAU_VOIX_DBFS
    rapport.verifie(f"voix ramenée vers {cible:.0f} dBFS (± 3 dB)", abs(niveau - cible) <= 3,
                    f"niveau : {niveau:.1f} dBFS")
    rapport.verifie("aucun échantillon écrêté malgré le claquement",
                    np.abs(x).max() < 0.999, f"pic : {np.abs(x).max():.3f}")

    muet = np.zeros(SR)
    rapport.egal("gain plafonné sur un message sans voix",
                 traitement_audio.gain_normalisation(muet + 1e-6, SR),
                 config.TRAITEMENT_GAIN_MAX_DB)

    brut = dossier / "sans_norm.wav"
    _message(brut, amplitude_voix=0.01)
    with harness.remplacer(config, "TRAITEMENT_NORMALISATION", False):
        traitement_audio.traiter(brut)
    x = _canaux(brut)[2][:, 0] / 32768
    niveau = np.percentile(traitement_audio.niveaux_blocs(x, SR // 50), 95)
    rapport.verifie("TRAITEMENT_NORMALISATION=False : niveau laissé tel quel",
                    niveau < cible - 10, f"niveau : {niveau:.1f} dBFS")


def main() -> None:
    parser = harness.parseur(__doc__, reel=False)
    parser.parse_args()
    rapport = Rapport("TRAITEMENT DES MESSAGES",
                      "ronflement secteur, débruitage léger, niveau — brut conservé")
    if np is None or shutil.which("sox") is None:
        rapport.ignore("chaîne de traitement", "sox ou numpy absent")
        rapport.conclure()
    dossier = Path(tempfile.mkdtemp(prefix="livredor_test_traitement_"))
    try:
        test_chaine(rapport, dossier)
        test_debruitage(rapport, dossier)
        test_echecs(rapport, dossier)
        test_attente(rapport, dossier)
        test_normalisation(rapport, dossier)
    finally:
        shutil.rmtree(dossier, ignore_errors=True)
    rapport.conclure()


if __name__ == "__main__":
    main()
