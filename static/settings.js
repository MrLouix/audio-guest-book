(function () {
  "use strict";

  // Éléments DOM
  var gpioHookEl = document.getElementById("gpio-hook");
  var gpioDialOffnormalEl = document.getElementById("gpio-dial-offnormal");
  var gpioDialPulseEl = document.getElementById("gpio-dial-pulse");
  var settingsForm = document.getElementById("settings-form");
  var settingsFeedback = document.getElementById("settings-feedback");
  var saveMessage = document.getElementById("save-message");
  var audioForm = document.getElementById("audio-form");
  var audioFeedback = document.getElementById("audio-feedback");
  var audioSaveButton = document.getElementById("audio-save-button");
  var audioReconvertButton = document.getElementById("audio-reconvert-button");

  // Rafraîchir le statut GPIO toutes les 500ms
  var GPIO_REFRESH_MS = 500;

  // Décoder une dizaine de fichiers sur un Pi Zero 2 W prend du temps ;
  // fetch() n'a aucun timeout par défaut, l'interface resterait sinon
  // bloquée indéfiniment sur « Conversion en cours… ».
  var AUDIO_TIMEOUT_MS = 180000;

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

  // --- Fichiers audio : choix des sources et conversion -----------------

  // Réaffiche les badges d'état sans recharger la page : après une
  // conversion, recharger ferait perdre le message de résultat.
  function renderAudioRoles(roles) {
    if (!roles) return;
    roles.forEach(function (role) {
      var cellule = document.querySelector('.audio-etat[data-role="' + role.nom + '"]');
      if (!cellule) return;
      var badge, texte;
      if (role.perimee) {
        badge = "badge badge-erreur";
        texte = "source modifiée, à reconvertir";
      } else if (role.pret) {
        badge = "badge badge-decroche";
        texte = "converti";
      } else if (role.obligatoire) {
        badge = "badge badge-erreur";
        texte = "manquant";
      } else {
        badge = "badge";
        texte = "non converti";
      }
      var html = '<span class="' + badge + '">' + texte + "</span>";
      if (role.origine === "convention") {
        html += ' <span class="muted">repli : ' + role.source_effective + "</span>";
      }
      cellule.innerHTML = html;
    });
  }

  function audioRequest(url, body, message) {
    if (audioFeedback) audioFeedback.textContent = message;
    if (audioSaveButton) audioSaveButton.disabled = true;
    if (audioReconvertButton) audioReconvertButton.disabled = true;

    var controleur = new AbortController();
    var minuterie = setTimeout(function () {
      controleur.abort();
    }, AUDIO_TIMEOUT_MS);

    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: controleur.signal,
    })
      .then(function (response) {
        return response.json().then(function (json) {
          return { ok: response.ok, data: json };
        });
      })
      .then(function (result) {
        if (audioFeedback) {
          audioFeedback.textContent = result.ok
            ? result.data.message || "Terminé."
            : "Erreur : " + (result.data.erreur || "erreur inconnue");
        }
        renderAudioRoles(result.data.roles);
      })
      .catch(function (error) {
        if (audioFeedback) {
          audioFeedback.textContent =
            error.name === "AbortError"
              ? "Conversion trop longue : vérifiez les logs du dashboard."
              : "Erreur : " + error.message;
        }
      })
      .finally(function () {
        clearTimeout(minuterie);
        if (audioSaveButton) audioSaveButton.disabled = false;
        if (audioReconvertButton) audioReconvertButton.disabled = false;
      });
  }

  function handleAudioSubmit(event) {
    event.preventDefault();
    var data = {};
    new FormData(event.target).forEach(function (valeur, cle) {
      data[cle] = valeur;
    });
    audioRequest("/api/audio/roles", data, "Conversion en cours…");
  }

  function handleReconvertAll() {
    audioRequest("/api/audio/reconvert", {}, "Reconversion de tous les fichiers…");
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

    // Formulaire des fichiers audio : charge utile disjointe de celle des
    // paramètres, pour ne pas déclencher une conversion en changeant un timeout.
    if (audioForm) {
      audioForm.addEventListener("submit", handleAudioSubmit);
    }
    if (audioReconvertButton) {
      audioReconvertButton.addEventListener("click", handleReconvertAll);
    }
  }

  // Démarrer quand le DOM est chargé
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
