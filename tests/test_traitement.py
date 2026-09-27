#!/usr/bin/env python3
"""Traitement des messages après enregistrement (src/traitement_audio.py).

Vérifie, sur un message synthétique (bruit de fond, hum 50 Hz, « voix »
tonale, clics courts, zone saturée au démarrage) :

1. le format produit : même cadence, stéréo, même durée ;
2. le brut conservé à l'identique dans messages/brut/, le message remplacé
   atomiquement (aucun fichier intermédiaire ne traîne) ;
3. l'effet de la chaîne : hum 50 Hz atténué, clics et saturation effacés,
   voix préservée ;
4. qu'un retraitement repart du brut (résultat identique) ;
5. le choix de la fenêtre de profil : message court ou parlé d'un bout à
   l'autre -> noisered sauté, sans échec ;
6. les échecs sans perte : sox absent, fichier illisible -> brut intact ;
7. la liste des messages en attente et le lancement en arrière-plan.

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


def _message(chemin: Path, secondes: float = 12.0, parole_continue: bool = False) -> None:
    """Message synthétique au format d'enregistrement (16 kHz, stéréo L = R)."""
    rng = np.random.default_rng(1)
    t = np.arange(int(secondes * SR)) / SR
    d = rng.normal(0, 0.001, len(t))                     # plancher ~ -60 dBFS
    d += 0.01 * np.sin(2 * np.pi * 50 * t)               # hum 50 Hz ~ -43 dBFS
    voix = 0.1 * (np.sin(2 * np.pi * 300 * t) + 0.5 * np.sin(2 * np.pi * 800 * t))
    if parole_continue:
        d += voix
    else:
        for a, b in ((2.5, 4.5), (9.0, 10.5)):
            d[int(a * SR):int(b * SR)] += voix[int(a * SR):int(b * SR)]
        for instant in (6.0, 7.0):                       # clics dans le silence
            if instant < secondes:
                d[int(instant * SR)] += 0.4
        d[int(0.1 * SR):int(0.1 * SR) + 50] = 0.99       # saturation au démarrage
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
    gain_50 = _raie(avant, 50, 5.0, 8.5) - _raie(apres, 50, 5.0, 8.5)
    rapport.verifie("hum 50 Hz atténué d'au moins 30 dB", gain_50 >= 30, f"{gain_50:.1f} dB")
    pic = max(np.abs(apres[int(5.99 * SR):int(6.01 * SR)]).max(),
              np.abs(apres[int(6.99 * SR):int(7.01 * SR)]).max())
    rapport.verifie("clics effacés", pic < 0.02, f"pic résiduel : {pic:.3f}")
    rapport.verifie("saturation du démarrage effacée",
                    np.abs(apres[:int(0.3 * SR)]).max() < traitement_audio.SEUIL_SATURATION)
    perte = _rms(avant, 2.7, 4.3) - _rms(apres, 2.7, 4.3)
    rapport.verifie("voix préservée (perte < 3 dB)", perte < 3, f"perte : {perte:.1f} dB")
    bruit = _rms(avant, 5.0, 8.5) - _rms(apres, 5.0, 8.5)
    rapport.verifie("bruit de fond des pauses abaissé d'au moins 20 dB", bruit >= 20,
                    f"{bruit:.1f} dB")

    rapport.section("4. Retraitement")
    premier = message.read_bytes()
    rapport.verifie("un second traitement réussit", traitement_audio.traiter(message))
    rapport.verifie("il repart du brut : résultat identique", message.read_bytes() == premier)
    rapport.verifie("le brut n'est pas écrasé", brut.read_bytes() == original)


def test_profil(rapport: Rapport, dossier: Path) -> None:
    rapport.section("5. Fenêtre de profil de bruit")
    d = np.zeros(SR)
    rapport.egal("message de 1 s : pas de fenêtre",
                 traitement_audio.fenetre_profil(d, [], SR, 0.5), None)
    continu = dossier / "continu.wav"
    _message(continu, secondes=6.0, parole_continue=True)
    d, _ = traitement_audio.charger(continu)
    rapport.egal("parole continue : pas de fenêtre (on ne débruite pas la voix)",
                 traitement_audio.fenetre_profil(d, [], SR, 0.5), None)
    rapport.verifie("parole continue : le traitement réussit quand même",
                    traitement_audio.traiter(continu))
    court = dossier / "court.wav"
    _message(court, secondes=1.0)
    rapport.verifie("message de 1 s : le traitement réussit",
                    traitement_audio.traiter(court))
    d, _ = traitement_audio.charger(dossier / "brut" / "message_2026-06-20_14-00-00.wav")
    t0 = traitement_audio.fenetre_profil(d, [], SR, 0.5)
    hors_parole = t0 is not None and (t0 + 1.5 <= 2.5 or 4.5 <= t0 <= 9.0 - 1.5)
    rapport.verifie("message nominal : fenêtre prise hors parole", hors_parole,
                    f"début : {t0}")


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


def main() -> None:
    parser = harness.parseur(__doc__, reel=False)
    parser.parse_args()
    rapport = Rapport("TRAITEMENT DES MESSAGES",
                      "declip, clics, 50 Hz, noisered, expandeur — brut conservé")
    if np is None or shutil.which("sox") is None:
        rapport.ignore("chaîne de traitement", "sox ou numpy absent")
        rapport.conclure()
    dossier = Path(tempfile.mkdtemp(prefix="livredor_test_traitement_"))
    try:
        test_chaine(rapport, dossier)
        test_profil(rapport, dossier)
        test_echecs(rapport, dossier)
        test_attente(rapport, dossier)
    finally:
        shutil.rmtree(dossier, ignore_errors=True)
    rapport.conclure()


if __name__ == "__main__":
    main()
