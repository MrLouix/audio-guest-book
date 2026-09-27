# IQaudio Codec Zero — micro électret sur jack MIC — Pi Zero 2 W

Note de référence, session du 22/09/2026.

> **Révision du 24/09/2026.** `Mic 1 Amp Source MUX` (numid=79) doit être sur **MIC_P (1)** :
> Differential (0) ne capte pas (constat de Louis). Cette note indiquait l'inverse. On ne sait
> donc pas avec certitude dans quel état était ce MUX pendant chacune des mesures chiffrées
> ci-dessous : elles sont **à reconfirmer**, et plusieurs conclusions ont été formulées trop
> catégoriquement. Le document `plan-action-bruit-livredor.md` reprend l'ensemble avec des
> niveaux de confiance, et un banc de test pour refaire les mesures proprement.
Hôte : `livredor` (Raspberry Pi Zero 2 W Rev 1.0), kernel 6.18.50+rpt-rpi-v8.

**Complément à `iqaudio-codec-zero-notes.md`**, qui documente l'autre chaîne :
module amplifié ADA1063 sur l'entrée **Aux**. Ici il s'agit de la capsule électret
nue branchée sur le **jack MIC**, alimentée par le bias interne du DA7213.

---

## 1. Conclusion — la chaîne qui fonctionne

**Les trois éléments qui ont débloqué la situation :**

| # | Élément | Effet |
|---|---|---|
| 1 | `MIC Jack Switch` (numid=76) **on** | Chemin DAPM complet, même rôle que numid=78 pour l'Aux |
| 2 | Mesurer le bias **pendant un `arecord` actif** | Le bias n'existe pas au repos — voir §2 |
| 3 | Capturer en **16 kHz** au lieu de 44,1 kHz | −5,4 dB de plancher, gratuits |

**Performances mesurées**

| | Valeur |
|---|---|
| Bruit rapporté à l'entrée | **−94,6 dBFS** (constant sur 3 paliers de gain) |
| SNR brut, parole | 22 à 27 dB selon la distance |
| SNR après traitement (pauses) | 46 à 51 dB |
| Perte de voix au traitement | 7 à 9,5 dB |

Le bruit est **blanc et thermique**, généré en amont de toute la chaîne de gain.
Aucun réglage ALSA ne l'améliore.

---

## 2. Découverte clé — le mic bias est une supply DAPM

Il n'existe **aucun contrôle ALSA** pour le bias électret. Un `amixer contents | grep -i bias`
ne renvoie rien, et c'est normal. Dans `sound/soc/codecs/da7213.c` :

```c
SND_SOC_DAPM_SUPPLY("Mic Bias 1", DA7213_MICBIAS_CTRL,
                    DA7213_MICBIAS1_EN_SHIFT, DA7213_NO_INVERT, NULL, 0),
...
{"Mic Bias 1", NULL, "VDDMIC"},
{"MIC1",       NULL, "Mic Bias 1"},
```

La supply n'est alimentée **que lorsqu'un flux de capture tourne réellement**.
Au repos, `VDDMIC` est coupée et le jack est à 0 V sur toutes ses broches,
quelle que soit la configuration mixer. Les switches arment le chemin, ils ne
l'alimentent pas.

Confirmation dans le driver machine `sound/soc/bcm/iqaudio-codec.c` :

```c
case SND_SOC_DAPM_POST_PMU:
      /* Delay for mic bias ramp */
      msleep(1000);
```

**Deux conséquences pratiques :**

- Toute mesure de tension sur le jack doit se faire **pendant** `arecord`, après 3 s d'attente.
- La **première seconde de chaque flux est inexploitable** (montée du bias, échelon DC
  qui sature l'ADC). Si l'application ouvre et ferme la capture à la demande, il faut
  jeter ce début — d'où le `trim 2` systématique dans les scripts. Pour du déclenchement
  à la voix, mieux vaut garder le flux ouvert en permanence.

**Mesure du bias :**

```bash
arecord -D hw:1,0 -f S16_LE -r 16000 -c 2 -d 60 /dev/null &
sleep 3
# sonder le jack maintenant
```

---

## 3. Le jack MIC est câblé sur Mic 1

Confirmé par le driver machine :

```c
/* Assume Mic1 is linked to Headset and Mic2 to on-board mic */
{"MIC1", NULL, "MIC Jack"},
{"MIC2", NULL, "Onboard MIC"},
```

Le mot « Assume » est du driver lui-même, et le guide produit IQaudio dit l'inverse
(« Mono Electret microphone (Mic2 left) », « Automatic MEMS disabling on Mic2 insert
detect »). **En pratique, Mic 1 fonctionne** — c'est ce qui est retenu ici.

### Brochage mesuré

Bias ≈ **+2,0 V sur une seule broche**, les deux autres au potentiel de masse.
→ câblage **single-ended**, pas différentiel.

| Borne de la capsule | Identification | Va sur |
|---|---|---|
| **+** (drain) | Borne isolée, résine noire, **aucune liaison au boîtier** | La broche à +2 V |
| **−** (masse) | Borne reliée au **boîtier métallique** par des pistes visibles | Sleeve / masse |

Vérification au multimètre, capsule débranchée, en mode résistance : lecture de quelques
centaines d'Ω à quelques kΩ dans un sens, valeur bien plus haute dans l'autre (JFET interne).

**Aucune résistance de polarisation à ajouter** : elle est déjà sur la carte, entre le
bias du DA7213 et le connecteur.

> ⚠️ **Non confirmé** : cette mesure a été faite **fiche non insérée**. Le connecteur
> comporte des contacts de commutation (détection d'insertion, désactivation du MEMS),
> donc les potentiels peuvent différer fiche enfoncée. À revérifier sur les fils d'une
> fiche jack insérée.

> ✅ **Corrigé le 24/09** : `Mic 1 Amp Source MUX` (numid=79) = **MIC_P (1)**. Differential (0),
> la valeur du fichier officiel Raspberry Pi, ne capte pas avec ce câblage single-ended.

---

## 4. Paramétrage ALSA

Chemin actif : `Mic 1 → Mixin PGA → ADC → I2S → DAC → Mixout → Lineout`

### Entrée

| Contrôle | numid | Valeur | dB |
|---|---|---|---|
| MIC Jack Switch | 76 | on | — |
| Mic 1 Switch | 23 | on | — |
| Mic 1 Amp Source MUX | 79 | **1 (MIC_P)** | Differential ne capte pas |
| Mic 1 Volume | 1 | 7 | +36 (max) |
| Mixin Left Mic 1 | 82 | on | — |
| Mixin Right Mic 1 | 87 | on | — |
| Mixin PGA Switch | 26 | on,on | — |
| Mixin PGA Volume | 4 | 15,15 | +18 (max) |
| ADC Switch | 27 | on,on | — |
| ADC Volume | 5 | 112,112 | 0 |
| ADC HPF Switch | 15 | on | — |
| ADC HPF Cutoff | 16 | 0 (Fs/24000) | — |
| ADC Voice Mode Switch | 17 | off | sans effet, voir §5 |
| ALC Switch | 60 | off,off | — |

### Entrées désactivées

`AUX Jack Switch` (78) · `Aux Switch` (25) · `Mixin L/R Aux` (81/85)
`Onboard MIC` (77) · `Mic 2 Switch` (24) · `Mixin L/R Mic 2` (83/86)

### Duplication mono → stéréo et sortie

Identiques à la chaîne Aux : numid 89/90/91/92 pour les MUX, 6/96/103/29/8/28 pour la sortie.
Voir `iqaudio-codec-zero-notes.md` §5.

### Échelles dB

| Contrôle | numid | Formule |
|---|---|---|
| Mic 1 Volume | 1 | `dB = −6 + v × 6` — 8 crans seulement, pas de finesse |
| Mixin PGA Volume | 4 | `dB = −4,5 + v × 1,5` |
| ADC / DAC Volume | 5 / 6 | `dB = −78 + (v − 8) × 0,75` — 0 dB = 112 |

---

## 5. Caractérisation du bruit

### La répartition du gain est indifférente

Balayage à gain total constant (`gainsweep.sh`), plancher mesuré par `RMS Tr` :

| Gain total | Mic / PGA | RMS Tr |
|---|---|---|
| +48 dB | 7 / 11 | −46,56 |
| +48 dB | 6 / 15 | −46,66 |
| +42 dB | 7 / 7 | −52,72 |
| +42 dB | 6 / 11 | −52,58 |
| +42 dB | 5 / 15 | −52,76 |
| +36 dB | 7 / 3 | −58,55 |
| +36 dB | 6 / 7 | −58,68 |
| +36 dB | 5 / 11 | −58,62 |

**Écart maximal dans un bloc : 0,2 dB.** Déplacer 12 dB d'un étage à l'autre ne change rien.

### Le plancher suit le gain total au dB près

| Gain total | RMS Tr | Rapporté à l'entrée |
|---|---|---|
| +48 dB | −46,6 | **−94,6 dBFS** |
| +42 dB | −52,7 | **−94,7 dBFS** |
| +36 dB | −58,6 | **−94,6 dBFS** |

**Si ce résultat se confirme**, il élimine le Mixin PGA, l'ADC, l'I2S et l'alimentation
numérique de la carte comme sources du bruit. Mesure à refaire avec le bon MUX.

**N'élimine pas** : la capsule, le réseau de bias, et le bruit propre de l'entrée micro
du DA7213. Les trois se rapportent au même point et sont indiscernables par logiciel.

### Le bruit est blanc

Analyse spectrale (`freqan.py`) : **7 dB d'écart seulement entre 48 Hz et 10 kHz**, aucune
raie dominante. Un captage parasite — secteur, découpage, horloge — produirait des raies
ou une concentration basse fréquence. Du bruit blanc, c'est du bruit thermique et de
grenaille, intrinsèque.

**Corollaire** : un passe-haut ne retire du bruit qu'en proportion de la bande supprimée.
Couper sous 100 Hz à 44,1 kHz retire `10·log10(21950/22050) = 0,02 dB`. Mesuré : 0,03 dB.
C'est pourquoi le mode voix du DA7213 (numid=17/18) est inutile ici — c'est un outil
d'intelligibilité, pas de réduction de bruit.

### Le vrai levier : réduire la bande passante

| Fréquence | Bande | Gain mesuré | Prédit (bruit blanc) |
|---|---|---|---|
| 44 100 Hz | 22 050 Hz | référence | — |
| 16 000 Hz | 8 000 Hz | **−5,40 dB** | −4,40 dB |
| 8 000 Hz | 4 000 Hz | **−7,32 dB** | −7,41 dB |

Le 8 kHz tombe à 0,09 dB de la prédiction : l'hypothèse du bruit blanc tient, sur cette série
de mesures.

Le 16 kHz rend 1 dB de **plus** qu'attendu → le bruit remonte au-dessus de 8 kHz.
C'est la signature du **noise shaping du convertisseur sigma-delta** : le DA7213
repousse son bruit de quantification vers le haut du spectre, et à 44,1 kHz le filtre
de décimation en laisse passer une partie près de 20 kHz.

**Retenu : 16 kHz.** Descendre à 8 kHz ne rend que 1,9 dB de plus en sacrifiant la bande
4–8 kHz, celle des fricatives (`/s/`, `/f/`, `/ʃ/`) — mauvais échange pour un livre d'or.

---

## 6. Chaîne de traitement

Ordre imposé : **trim → normalisation → déclic → débruitage → limitation de bande → normalisation finale**

```bash
sox in.wav  a.wav trim 2          # transitoire de bias
sox a.wav   b.wav gain -n -3      # normalisation AVANT debruitage

ffmpeg -i b.wav \
  -af "adeclick,aresample=48000,pan=mono|c0=c0,\
arnndn=m=$HOME/sh.rnnn,highpass=f=150,lowpass=f=5000,pan=stereo|c0=c0|c1=c0" \
  -ar 48000 -y c.wav

sox c.wav out.wav gain -n -3      # normalisation finale
```

Prérequis :

```bash
sudo apt install ffmpeg -y
wget -O ~/sh.rnnn https://raw.githubusercontent.com/GregorR/rnnoise-models/master/somnolent-hogwash-2018-09-01/sh.rnnn
```

**Option `afftdn`** (soustraction spectrale, agit aussi *pendant* la parole), à insérer
avant `arnndn` :

```
afftdn=nr=4:nf=-40:nt=w:tn=1,
```

`nt=w` (profil bruit blanc) et `tn=1` (suivi adaptatif) limitent les artefacts.
**Au-delà de `nr=6`, le bruit musical métallique devient audible.** Compromis à faire
à l'oreille, sur une prise unique retraitée (`proc.sh`).

### Résultats types

| | Plancher | Voix | SNR |
|---|---|---|---|
| Brut | −43,9 | −16,7 | 27 dB |
| Traité (NR=0) | −75,8 | −26,3 | 49 dB |

---

## 7. Pièges rencontrés — à ne pas refaire

**Mesurer le bias au repos ne prouve rien.** 0 V sur toutes les broches est l'état normal
tant qu'aucun flux de capture ne tourne. C'est ce qui a fait perdre le plus de temps.

**Le test « jack débranché » n'est pas une référence de silence.** Le connecteur a des
contacts de commutation : débranché, le plancher mesuré est *plus haut* que branché
(−36,07 contre −38,47). Un test de substitution demande une fiche jack câblée, pas
un socle vide.

**`sox stat` et `sox stats` sont deux commandes différentes.** `stat` sort
`Maximum amplitude` / `RMS amplitude`, `stats` sort `RMS lev` / `RMS Pk` / `RMS Tr` /
`Pk lev` / `Crest factor` / `Pk count`. Un `grep` écrit pour l'une ne matche rien sur l'autre.

**`RMS Tr` est la seule bonne métrique de plancher.** C'est la fenêtre de 50 ms la plus
silencieuse : un bruit ponctuel dans la pièce ne la pollue pas, contrairement à `RMS lev`.
Et `Pk lev` ne mesure pas le niveau de voix quand il reste des clics — utiliser `RMS Pk`.

**Ne jamais normaliser par crête après un débruiteur.** RNNoise vide le fichier ;
`sox gain -n` s'accroche alors à ce qu'il reste et applique +33 dB, ce qui remonte le
plancher de 30 dB. Normaliser **avant**.

**`loudnorm` en passe unique n'est pas un gain, c'est un AGC.** Il travaille dynamiquement
(gating, cible `LRA`) et remonte le gain pendant les silences — donc amplifie exactement
le bruit que le débruiteur venait de supprimer. Symptôme objectif : le `Crest factor`
s'effondre (20,4 → 9,2). Utiliser `sox gain -n` (linéaire) à la place.

**Comparer deux réglages sur deux prises différentes ne vaut rien.** Les planchers bruts
ont varié de 9 dB entre prises selon l'ambiance. Toute comparaison de paramètres doit se
faire en retraitant **le même fichier** (`proc.sh`).

**`gainsweep.sh` laisse les gains sur sa dernière combinaison.** Relancer `mic-setup.sh`
après usage, sinon toute mesure suivante est 18 dB en dessous de la référence.

**RNNoise supprime ce qui n'est pas de la parole.** Sur une prise sans voix, il retire
38 dB de signal — ce n'est pas un dysfonctionnement. Ne juger la chaîne que sur une prise
où l'on parle distinctement.

**Le paramètre `mix` d'`arnndn` n'était pas en cause** dans les régressions observées
(`mix=1` et valeur par défaut donnent des résultats rigoureusement identiques).

---

## 8. Ce qui reste à faire

1. **Test de substitution sur fiche jack câblée**, gain figé, trois configurations :
   capsule / résistance 2,2 kΩ / court-circuit bias↔masse.
   - Court-circuit fait chuter le plancher → le bruit vient du réseau de bias → découplage utile
   - Court-circuit ne change rien → bruit propre de l'entrée micro du DA7213 → rien à faire
   - Capsule nettement pire que 2,2 kΩ → changer de capsule
2. **Mesurer la vraie résistance de charge du HAT** (non documentée) avec la config 2,2 kΩ :
   `R_série = 2200 × (Voc / V_mesurée − 1)`, avec Voc ≈ 2,0 V.
3. **Comparer le SNR avec la chaîne Aux / ADA1063** sur un pied d'égalité : même voix,
   même distance, `RMS Pk − RMS Tr` des deux côtés. C'est ce qui décide quelle chaîne garder.
4. **Rapprocher le micro** — levier le plus puissant et gratuit : diviser la distance par
   deux donne +6 dB de SNR, plus que tout traitement logiciel sans dégradation.

> ⚠️ Un découplage 10 µF + 100 nF **ne peut pas** se monter sur la broche du jack.
> En single-ended, ce nœud porte le signal autant que le DC : 10 µF y présente 16 Ω à
> 1 kHz face à une charge de ~2,2 kΩ, soit −43 dB de signal. Le découplage n'aurait de
> sens que sur la broche MICBIAS du DA7213, **en amont de la résistance série**, donc en
> CMS sur le HAT — ou en abandonnant le bias du codec pour une alimentation externe,
> ce qui revient à la topologie ADA1063 déjà documentée.

---

## 9. Scripts

`~/mic-setup.sh` — configuration ALSA complète (fichier joint, corrigé le 24/09 : `numid=79` = 1).

Lancement au boot, `/etc/systemd/system/mic-setup.service` :

```ini
[Unit]
Description=IQaudio Codec Zero — micro electret setup
After=sound.target

[Service]
Type=oneshot
ExecStart=/home/louispm/mic-setup.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now mic-setup.service
```

> Ne pas activer `mic-setup.service` et `audio-setup.service` en même temps : ils
> configurent deux chemins d'entrée concurrents. Un seul `enable` à la fois.

### `~/rectest.sh` — capture 16 kHz + mesure + traitement

```bash
#!/bin/bash
D=${1:-10}
R=16000
TS=$(date +%H%M%S)
RAW="./mic_${TS}.wav"
CLEAN="./mic_${TS}_clean.wav"
A="/tmp/rt_${TS}_a.wav"
B="/tmp/rt_${TS}_b.wav"
MODEL="$HOME/sh.rnnn"
PEAK=-3      # crete cible avant debruitage, dBFS
NR=0         # afftdn : 0 = desactive. Sinon 4 a 6 max (metallique au-dela)
HP=150       # passe-haut, Hz
LP=5000      # passe-bas, Hz

FFT=""
[ "$NR" -gt 0 ] && FFT="afftdn=nr=${NR}:nf=-40:nt=w:tn=1,"

stats() {
  sox "$1" -n stats 2>&1 | awk -v t="$2" '
    /^RMS Tr dB/ {tr=$4}
    /^RMS Pk dB/ {pk=$4}
    /^Pk lev dB/ {pl=$4}
    /^Crest/     {cr=$4}
    END{printf "  %-7s plancher %8s   voix %8s   crete %7s   crest %6s   SNR %5.1f dB\n",
               t, tr, pk, pl, cr, pk-tr}'
}

arecord -D hw:1,0 -f S16_LE -r $R -c 2 -d "$D" -V stereo "$RAW"

sox "$RAW" "$A" trim 2
sox "$A"   "$B" gain -n $PEAK

echo; echo "=== $RAW  (${R} Hz)   NR=$NR  bande ${HP}-${LP} Hz ==="
stats "$B" "brut"

PLAY="$B"
if command -v ffmpeg >/dev/null 2>&1 && [ -f "$MODEL" ]; then
  ffmpeg -loglevel error -i "$B" \
    -af "adeclick,aresample=48000,pan=mono|c0=c0,${FFT}arnndn=m=${MODEL},highpass=f=${HP},lowpass=f=${LP},pan=stereo|c0=c0|c1=c0" \
    -ar 48000 -y "$CLEAN"
  [ -s "$CLEAN" ] && { sox "$CLEAN" "$A" gain -n $PEAK; mv "$A" "$CLEAN"; stats "$CLEAN" "traite"; PLAY="$CLEAN"; }
else
  echo "  (traitement ignore : ffmpeg ou $MODEL absent)"
fi

aplay -D hw:1,0 "$PLAY" >/dev/null 2>&1
rm -f "$A" "$B"
```

### `~/proc.sh` — retraite une prise existante (comparaison de réglages)

```bash
#!/bin/bash
# proc.sh fichier.wav [NR]
SRC="$1"; NR=${2:-0}
MODEL="$HOME/sh.rnnn"; HP=150; LP=5000; PEAK=-3
OUT="${SRC%.wav}_nr${NR}.wav"
T="/tmp/proc_$$"
FFT=""; [ "$NR" -gt 0 ] && FFT="afftdn=nr=${NR}:nf=-40:nt=w:tn=1,"

sox "$SRC"      "${T}a.wav" trim 2
sox "${T}a.wav" "${T}b.wav" gain -n $PEAK
ffmpeg -loglevel error -i "${T}b.wav" \
  -af "adeclick,aresample=48000,pan=mono|c0=c0,${FFT}arnndn=m=${MODEL},highpass=f=${HP},lowpass=f=${LP},pan=stereo|c0=c0|c1=c0" \
  -ar 48000 -y "${T}c.wav"
sox "${T}c.wav" "$OUT" gain -n $PEAK

sox "$OUT" -n stats 2>&1 | awk -v n="$NR" -v o="$OUT" '
  /^RMS Tr dB/{tr=$4} /^RMS Pk dB/{pk=$4} /^Crest/{cr=$4}
  END{printf "  NR=%-3s plancher %8s   voix %8s   crest %6s   SNR %5.1f dB   -> %s\n",
             n, tr, pk, cr, pk-tr, o}'
rm -f ${T}*.wav
```

Usage : `for n in 0 4 6 8; do ./proc.sh mic_021534.wav $n; done`

### `~/gainsweep.sh` — balayage de répartition à gain total constant

```bash
#!/bin/bash
C=1
run() {
  amixer -c $C cset numid=1 $1 >/dev/null
  amixer -c $C cset numid=4 $2,$2 >/dev/null
  sleep 0.5
  f=$(mktemp /tmp/gs_XXXXXX.wav)
  arecord -D hw:1,0 -f S16_LE -r 44100 -c 2 -d 6 "$f" 2>/dev/null
  s=$(sox "$f" -n trim 2 stats 2>&1)
  lev=$(echo "$s" | awk '/^RMS lev dB/{print $4}')
  tr=$(echo  "$s" | awk '/^RMS Tr dB/{print $4}')
  awk -v m=$1 -v p=$2 -v lev="$lev" -v tr="$tr" 'BEGIN{
    md=-6+m*6; pd=-4.5+p*1.5
    printf "  Mic=%d (%+5.1f)  PGA=%2d (%+5.1f)  total %+5.1f dB  ->  RMS lev %8s   RMS Tr %8s\n",
           m,md,p,pd,md+pd,lev,tr}'
  rm -f "$f"
}
echo "=> SILENCE COMPLET pendant toute la sequence (~60 s)"; sleep 3
echo "--- total +48 dB ---"; run 7 11; run 6 15
echo "--- total +42 dB ---"; run 7 7;  run 6 11; run 5 15
echo "--- total +36 dB ---"; run 7 3;  run 6 7;  run 5 11
```

### `~/freqan.py` — spectre moyen + périodicité des transitoires

```python
#!/usr/bin/env python3
import sys, wave, numpy as np

w = wave.open(sys.argv[1], 'rb')
fs, n, ch = w.getframerate(), w.getnframes(), w.getnchannels()
x = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float64) / 32768.0
if ch > 1:
    x = x.reshape(-1, ch)[:, 0]
x = x[int(2 * fs):]
x = x - x.mean()
print(f"fs={fs} Hz   analyse sur {len(x)/fs:.1f} s")

N, hop = 8192, 4096
win = np.hanning(N)
segs = [x[i:i+N] * win for i in range(0, len(x) - N, hop)]
P = np.mean([np.abs(np.fft.rfft(s))**2 for s in segs], axis=0)
f = np.fft.rfftfreq(N, 1/fs)
PdB = 10 * np.log10(P / P.max() + 1e-20)

print("\n--- raies dominantes ---")
shown = []
for i in np.argsort(P)[::-1]:
    if f[i] < 20 or any(abs(f[i] - g) < 40 for g in shown):
        continue
    shown.append(f[i])
    print(f"  {f[i]:8.1f} Hz   {PdB[i]:6.1f} dB")
    if len(shown) >= 12:
        break

d = np.abs(np.diff(x))
thr = d.mean() + 8 * d.std()
pk = np.where(d > thr)[0]
print("\n--- transitoires ---")
if len(pk):
    groups = np.split(pk, np.where(np.diff(pk) > fs * 0.005)[0] + 1)
    t = np.array([g[0] / fs for g in groups])
    print(f"  {len(t)} clics detectes (seuil {thr:.5f})")
    if len(t) > 2:
        iv = np.diff(t)
        print(f"  intervalle median : {np.median(iv)*1000:8.1f} ms  ->  {1/np.median(iv):.2f} Hz")
        print(f"  ecart-type        : {iv.std()*1000:8.1f} ms")
else:
    print("  aucun")
```

**Lecture** : si l'écart-type des intervalles est < 5 ms, la source est périodique donc
numérique ; > 50 ms, elle est aléatoire donc physique (masse, soudure, contact).
Et si l'écart entre raies dominantes tient dans quelques dB, ce n'est pas un spectre de
raies mais du bruit large bande — la liste ne veut alors rien dire.

---

## 10. Commandes usuelles

```bash
# Capture — le DA7213 est STRICTEMENT stereo, pas de -c 1
arecord -D hw:1,0 -f S16_LE -r 16000 -c 2 -d 10 -V stereo test.wav

# Mesure
sox test.wav -n trim 2 stats 2>&1 | grep -E "RMS lev|RMS Pk|RMS Tr|Pk lev|Crest"
sox test.wav -n spectrogram -x 1800 -y 900 -z 90 -o spectro.png

# Energie dans une bande donnee
sox test.wav -n trim 2 lowpass 400 stats 2>&1 | grep "RMS Tr"

# Etat des controles
amixer -c 1 contents | grep -A2 -E "numid=(76|79|23|1|82|87|26|4|27|5|15|17)\b"
```
