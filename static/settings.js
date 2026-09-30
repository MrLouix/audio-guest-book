(function () {
  "use strict";

  // Éléments DOM
  var settingsForms = document.querySelectorAll(".settings-form");
  var saveMessage = document.getElementById("save-message");
  var audioForm = document.getElementById("audio-form");
  var audioFeedback = document.getElementById("audio-feedback");
  var audioSaveButton = document.getElementById("audio-save-button");
  var audioReconvertButton = document.getElementById("audio-reconvert-button");

  // Décoder une dizaine de fichiers sur un Pi Zero 2 W prend du temps ;
  // fetch() n'a aucun timeout par défaut, l'interface resterait sinon
  // bloquée indéfiniment sur « Conversion en cours… ».
  var AUDIO_TIMEOUT_MS = 180000;

  // Gestion du formulaire de configuration
  function handleFormSubmit(event) {
    event.preventDefault();

    // Chaque section a son propre formulaire : n'envoyer que ses champs,
    // pour qu'un utilisateur non admin ne soumette jamais un paramètre admin.
    var form = event.target;
    var settingsFeedback = form.querySelector(".settings-feedback");
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
      saveMessage.hidden = true;
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
              saveMessage.hidden = false;
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

  // Les noms de fichiers viennent du Drive : jamais insérés tels quels en HTML.
  function echapper(texte) {
    var div = document.createElement("div");
    div.textContent = texte == null ? "" : String(texte);
    return div.innerHTML;
  }

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
      if (role.source_introuvable) {
        html += ' <span class="badge badge-erreur">fichier choisi introuvable</span>';
      }
      if (role.origine === "convention") {
        html += ' <span class="muted">repli : ' + echapper(role.source_effective) + "</span>";
      }
      cellule.innerHTML = html;

      // La liste suit le choix réellement enregistré : après un échec, elle
      // ne doit pas continuer d'afficher une sélection qui n'a pas été prise.
      var liste = document.querySelector('#audio-form select[name="' + role.nom + '"]');
      if (liste) {
        var valeur = role.source || "";
        var existe = Array.prototype.some.call(liste.options, function (o) {
          return o.value === valeur;
        });
        if (existe) liste.value = valeur;
      }
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
    // Gérer les formulaires (un par section de paramètres)
    Array.prototype.forEach.call(settingsForms, function (form) {
      form.addEventListener("submit", handleFormSubmit);
    });

    // Curseurs de volume : afficher la valeur pendant le déplacement.
    Array.prototype.forEach.call(
      document.querySelectorAll('.range-field input[type="range"]'),
      function (curseur) {
        var affichage = curseur.parentNode.querySelector("output");
        curseur.addEventListener("input", function () {
          if (affichage) affichage.textContent = curseur.value;
        });
      }
    );

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
