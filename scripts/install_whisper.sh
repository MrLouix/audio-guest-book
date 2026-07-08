#!/usr/bin/env bash
# Installation de whisper.cpp + modèle tiny quantisé (§5.5), pour la
# transcription batch post-événement (src/transcribe_batch.py).
#
# À exécuter manuellement, une fois : soit sur le Pi (de préférence de
# nuit, service livre-dor.service arrêté), soit sur une machine plus
# puissante vers laquelle les WAV auront été transférés. Ne fait partie
# d'aucun service auto-démarré.
set -euo pipefail

WHISPER_DIR="${WHISPER_DIR:-$HOME/whisper.cpp}"
MODEL_NAME="${WHISPER_MODEL_NAME:-tiny-q5_0}"

echo "== Dépendances de compilation =="
sudo apt-get update
sudo apt-get install -y build-essential cmake git ffmpeg

echo "== Clonage / mise à jour de whisper.cpp =="
if [ -d "$WHISPER_DIR/.git" ]; then
    git -C "$WHISPER_DIR" pull
else
    git clone https://github.com/ggerganov/whisper.cpp "$WHISPER_DIR"
fi

echo "== Compilation =="
cmake -B "$WHISPER_DIR/build" -S "$WHISPER_DIR"
cmake --build "$WHISPER_DIR/build" --config Release -j"$(nproc)"

echo "== Téléchargement du modèle '$MODEL_NAME' (script officiel whisper.cpp) =="
if ! bash "$WHISPER_DIR/models/download-ggml-model.sh" "$MODEL_NAME"; then
    echo
    echo "ATTENTION : le téléchargement de '$MODEL_NAME' a échoué." >&2
    echo "Lancez 'bash $WHISPER_DIR/models/download-ggml-model.sh' sans argument pour lister" >&2
    echo "les modèles disponibles, ou quantisez vous-même un modèle 'tiny' en q5_0 avec" >&2
    echo "l'outil './build/bin/quantize' fourni par whisper.cpp." >&2
    exit 1
fi

BINARY_PATH=""
for candidate in "$WHISPER_DIR/build/bin/whisper-cli" "$WHISPER_DIR/build/bin/main"; do
    if [ -x "$candidate" ]; then
        BINARY_PATH="$candidate"
        break
    fi
done

echo
echo "Installation terminée."
if [ -n "$BINARY_PATH" ]; then
    echo "Binaire   : $BINARY_PATH"
else
    echo "ATTENTION : binaire whisper introuvable sous $WHISPER_DIR/build/bin/ — vérifiez la compilation." >&2
fi
echo "Modèle    : $WHISPER_DIR/models/ggml-${MODEL_NAME}.bin"
echo
echo "Configurez ensuite (variables d'environnement, ou surcharge dans config.py) :"
echo "  WHISPER_BINARY=${BINARY_PATH:-$WHISPER_DIR/build/bin/whisper-cli}"
echo "  WHISPER_MODEL_PATH=$WHISPER_DIR/models/ggml-${MODEL_NAME}.bin"
