(function () {
  "use strict";

  var button = document.getElementById("sync-now-button");
  var feedback = document.getElementById("sync-feedback");
  if (!button) return;

  button.addEventListener("click", function () {
    button.disabled = true;
    feedback.textContent = "Synchronisation en cours…";
    fetch("/api/rclone/sync-now", { method: "POST" })
      .then(function (r) {
        return r.json().then(function (data) { return { ok: r.ok, data: data }; });
      })
      .then(function (result) {
        feedback.textContent = result.data.message || (result.ok ? "Terminé." : "Échec.");
      })
      .catch(function () {
        feedback.textContent = "Échec de la requête.";
      })
      .finally(function () {
        button.disabled = false;
      });
  });
})();
