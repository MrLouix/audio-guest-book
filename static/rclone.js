(function () {
  "use strict";

  var button = document.getElementById("sync-now-button");
  var feedback = document.getElementById("sync-feedback");
  var resyncButton = document.getElementById("resync-button");
  var resyncFeedback = document.getElementById("resync-feedback");

  function lancer(url, bouton, zone, enCours) {
    bouton.disabled = true;
    zone.textContent = enCours;
    fetch(url, { method: "POST" })
      .then(function (r) {
        return r.json().then(function (data) { return { ok: r.ok, data: data }; });
      })
      .then(function (result) {
        zone.textContent =
          result.data.message || result.data.erreur || (result.ok ? "Terminé." : "Échec.");
      })
      .catch(function () {
        zone.textContent = "Échec de la requête.";
      })
      .finally(function () {
        bouton.disabled = false;
      });
  }

  if (button) {
    button.addEventListener("click", function () {
      lancer("/api/rclone/sync-now", button, feedback, "Synchronisation en cours…");
    });
  }

  // La réinitialisation peut transférer beaucoup et écrase l'état de
  // référence de rclone : jamais sans confirmation explicite.
  if (resyncButton) {
    resyncButton.addEventListener("click", function () {
      var ok = window.confirm(
        "Réinitialiser la synchronisation bidirectionnelle ?\n\n" +
          "Ce premier passage compare l'intégralité de audio_src/ et du dossier " +
          "Drive, et peut être long. À faire à l'installation, pas pendant l'événement."
      );
      if (!ok) return;
      lancer("/api/rclone/resync", resyncButton, resyncFeedback,
             "Réinitialisation en cours, cela peut prendre plusieurs minutes…");
    });
  }
})();
