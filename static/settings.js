(function () {
  "use strict";

  // Éléments DOM
  var gpioHookEl = document.getElementById("gpio-hook");
  var gpioDialOffnormalEl = document.getElementById("gpio-dial-offnormal");
  var gpioDialPulseEl = document.getElementById("gpio-dial-pulse");
  var settingsForm = document.getElementById("settings-form");
  var settingsFeedback = document.getElementById("settings-feedback");
  var saveMessage = document.getElementById("save-message");

  // Rafraîchir le statut GPIO toutes les 500ms
  var GPIO_REFRESH_MS = 500;

  // Fonction pour rafraîchir le statut GPIO
  function refreshGPIOStatus() {
    fetch("/api/gpio-status")
      .then(function (response) {
        if (!response.ok) {
          throw new Error("Erreur lors de la récupération du statut GPIO");
        }
        return response.json();
      })
      .then(function (data) {
        if (data.gpio && !data.gpio_available) {
          // GPIO non disponible, ne rien faire
          return;
        }

        if (data.gpio) {
          if (gpioHookEl) {
            gpioHookEl.textContent = data.gpio.hook || "inconnu";
          }
          if (gpioDialOffnormalEl) {
            gpioDialOffnormalEl.textContent = data.gpio.dial_offnormal || "inconnu";
          }
          if (gpioDialPulseEl) {
            gpioDialPulseEl.textContent = data.gpio.dial_pulse || "inconnu";
          }
        }
      })
      .catch(function (error) {
        console.error("Erreur GPIO:", error);
      });
  }

  // Gestion du formulaire de configuration
  function handleFormSubmit(event) {
    event.preventDefault();

    var form = event.target;
    var formData = new FormData(form);
    var data = {};

    // Convertir FormData en objet
    for (var [key, value] of formData.entries()) {
      data[key] = value;
    }

    // Afficher l'indicateur de chargement
    if (settingsFeedback) {
      settingsFeedback.textContent = "Enregistrement en cours…";
    }
    if (saveMessage) {
      saveMessage.textContent = "";
    }

    // Envoyer la requête
    fetch("/api/settings", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(data),
    })
      .then(function (response) {
        return response.json().then(function (json) {
          return { ok: response.ok, data: json, status: response.status };
        });
      })
      .then(function (result) {
        if (result.ok) {
          if (settingsFeedback) {
            settingsFeedback.textContent = result.data.message || "Paramètres sauvegardés.";
          }
          if (result.data.redemarrage_necessaire) {
            if (saveMessage) {
              saveMessage.textContent = "⚠️ Certains paramètres nécessitent un redémarrage du service.";
            }
          }
          // Rafraîchir la page après 2 secondes pour refléter les changements
          setTimeout(function () {
            window.location.reload();
          }, 2000);
        } else {
          var errorMsg = result.data.erreur || "Erreur inconnue";
          if (result.status === 403) {
            errorMsg = "Vous devez être administrateur pour modifier ces paramètres.";
          }
          if (settingsFeedback) {
            settingsFeedback.textContent = "Erreur: " + errorMsg;
          }
        }
      })
      .catch(function (error) {
        if (settingsFeedback) {
          settingsFeedback.textContent = "Erreur: " + error.message;
        }
      });
  }

  // Initialisation
  function init() {
    // Démarrer le rafraîchissement périodique du GPIO
    if (gpioHookEl || gpioDialOffnormalEl || gpioDialPulseEl) {
      refreshGPIOStatus();
      setInterval(refreshGPIOStatus, GPIO_REFRESH_MS);
    }

    // Gérer le formulaire
    if (settingsForm) {
      settingsForm.addEventListener("submit", handleFormSubmit);
    }
  }

  // Démarrer quand le DOM est chargé
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
