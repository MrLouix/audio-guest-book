(function () {
  "use strict";

  var STATUS_REFRESH_MS = 4000;
  var LOGS_REFRESH_MS = 8000;
  var SCROLL_BOTTOM_TOLERANCE_PX = 20;

  var etatBadge = document.getElementById("etat-badge");
  var etatDetail = document.getElementById("etat-detail");
  var etatMaj = document.getElementById("etat-maj");
  var messagesCount = document.getElementById("messages-count");
  var reseauMode = document.getElementById("reseau-mode");
  var reseauIp = document.getElementById("reseau-ip");
  var logsContent = document.getElementById("logs-content");
  var ringButton = document.getElementById("ring-button");
  var ringFeedback = document.getElementById("ring-feedback");

  function refreshStatus() {
    fetch("/api/status")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var etat = data.etat || "inconnu";
        etatBadge.textContent = etat;
        etatBadge.className = "badge badge-" + etat;
        etatDetail.textContent = data.detail || "—";
        etatMaj.textContent = "dernière mise à jour : " + (data.derniere_maj || "inconnue");
        messagesCount.textContent = data.messages_count != null ? data.messages_count : "—";
        if (data.reseau) {
          reseauMode.textContent = "mode : " + (data.reseau.mode || "inconnu");
          reseauIp.textContent = "IP : " + (data.reseau.ip || "inconnue");
        }
      })
      .catch(function () {
        etatBadge.textContent = "inconnu";
        etatBadge.className = "badge badge-inconnu";
      });
  }

  function refreshLogs() {
    var wasAtBottom = (logsContent.scrollHeight - logsContent.scrollTop - logsContent.clientHeight)
      <= SCROLL_BOTTOM_TOLERANCE_PX;
    var previousScrollTop = logsContent.scrollTop;

    fetch("/api/logs")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var lignes = data.lignes || [];
        logsContent.textContent = lignes.length ? lignes.join("\n") : "(aucun log pour le moment)";
        if (wasAtBottom) {
          logsContent.scrollTop = logsContent.scrollHeight;
        } else {
          logsContent.scrollTop = previousScrollTop;
        }
      })
      .catch(function () {
        // Tolérant : on laisse le contenu précédent affiché en cas d'échec réseau.
      });
  }

  ringButton.addEventListener("click", function () {
    ringButton.disabled = true;
    ringFeedback.textContent = "Déclenchement…";
    fetch("/api/ring", { method: "POST" })
      .then(function (r) { return r.json().then(function (data) { return { ok: r.ok, data: data }; }); })
      .then(function (result) {
        ringFeedback.textContent = result.ok ? "Sonnerie déclenchée." : (result.data.erreur || "Échec.");
      })
      .catch(function () {
        ringFeedback.textContent = "Échec de la requête.";
      })
      .finally(function () {
        ringButton.disabled = false;
      });
  });

  refreshStatus();
  refreshLogs();
  setInterval(refreshStatus, STATUS_REFRESH_MS);
  setInterval(refreshLogs, LOGS_REFRESH_MS);
})();
