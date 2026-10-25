(function () {
  "use strict";

  // Rafraîchissement : rapide tant que des scores se calculent, lent sinon
  // (sur le PC, l'index peut aussi changer par la synchro du Drive).
  var REFRESH_CALCUL_MS = 4000;
  var REFRESH_REPOS_MS = 30000;

  var LIBELLES_STATUT = { a_trier: "à trier", conserve: "conservé", supprime: "supprimé" };

  var liste = document.getElementById("liste");
  var resume = document.getElementById("tri-resume");
  var erreur = document.getElementById("tri-erreur");
  var tri = document.getElementById("tri");
  var filtreStatut = document.getElementById("filtre-statut");
  var filtreScore = document.getElementById("filtre-score");
  var filtreNom = document.getElementById("filtre-nom");
  var toutSelectionner = document.getElementById("tout-selectionner");
  var nbSelection = document.getElementById("nb-selection");
  var lecteur = document.getElementById("lecteur");
  var lecteurNom = document.getElementById("lecteur-nom");

  var lignes = [];
  var selection = {};       // nom -> true
  var enLecture = null;     // nom du message chargé dans le lecteur
  var calculEnCours = false;
  var minuterie = null;

  function afficherErreur(texte) {
    erreur.textContent = texte ? "⚠️ " + texte : "";
    erreur.hidden = !texte;
  }

  function formatScore(score) {
    return score == null ? "score…" : score.toFixed(1).replace(".", ",") + "/10";
  }

  function formatDuree(sec) {
    if (sec == null) { return null; }
    var s = Math.round(sec);
    return Math.floor(s / 60) + ":" + ("0" + (s % 60)).slice(-2);
  }

  // --- Filtre et tri ---------------------------------------------------

  function lignesVisibles() {
    var statut = filtreStatut.value;
    var scoreMin = parseFloat(filtreScore.value) || 0;
    var texte = filtreNom.value.trim().toLowerCase();
    var visibles = lignes.filter(function (l) {
      if (statut && l.statut !== statut) { return false; }
      // Un score pas encore calculé n'est pas écarté : il n'est pas mauvais.
      if (scoreMin > 0 && l.score != null && l.score < scoreMin) { return false; }
      if (texte && l.nom.toLowerCase().indexOf(texte) === -1) { return false; }
      return true;
    });
    var ordre = tri.value;
    if (ordre === "date-desc") {
      visibles.reverse();
    } else if (ordre === "score" || ordre === "score-desc") {
      var sens = ordre === "score" ? 1 : -1;
      // Tri stable (date en second critère) ; scores inconnus en fin de liste.
      visibles = visibles.map(function (l, i) { return [l, i]; }).sort(function (a, b) {
        var sa = a[0].score, sb = b[0].score;
        if (sa == null || sb == null) {
          return (sa == null) - (sb == null) || a[1] - b[1];
        }
        return sens * (sa - sb) || a[1] - b[1];
      }).map(function (p) { return p[0]; });
    }
    return visibles;
  }

  // --- Affichage -----------------------------------------------------------

  function bouton(texte, titre, classe, action) {
    var b = document.createElement("button");
    b.type = "button";
    b.className = "tri-bouton " + classe;
    b.textContent = texte;
    b.title = titre;
    b.setAttribute("aria-label", titre);
    b.addEventListener("click", action);
    return b;
  }

  function ligneElement(l) {
    var li = document.createElement("li");
    li.className = "tri-item statut-" + l.statut;

    var coche = document.createElement("input");
    coche.type = "checkbox";
    coche.checked = !!selection[l.nom];
    coche.setAttribute("aria-label", "Sélectionner " + l.nom);
    coche.addEventListener("change", function () {
      if (coche.checked) { selection[l.nom] = true; } else { delete selection[l.nom]; }
      majSelection();
    });

    var bloc = document.createElement("div");
    bloc.className = "tri-nom";
    var nom = document.createElement("span");
    nom.className = "tri-fichier";
    nom.textContent = l.nom;
    var meta = document.createElement("span");
    meta.className = "tri-meta";
    var morceaux = [l.numero != null ? "n° " + l.numero : "non lu en restitution",
                    formatScore(l.score)];
    var duree = formatDuree(l.duree_sec);
    if (duree) { morceaux.push(duree); }
    morceaux.push(LIBELLES_STATUT[l.statut] || l.statut);
    meta.textContent = morceaux.join(" · ");
    if (l.score != null) {
      meta.dataset.niveau = l.score >= 7 ? "haut" : (l.score >= 4 ? "moyen" : "bas");
    }
    bloc.appendChild(nom);
    bloc.appendChild(meta);

    var actions = document.createElement("div");
    actions.className = "tri-actions";
    var joue = enLecture === l.nom && !lecteur.paused;
    actions.appendChild(bouton(joue ? "⏸" : "▶", joue ? "Pause" : "Écouter", "lecture",
                               function () { lire(l.nom); }));
    var conserve = l.statut === "conserve";
    var bConserver = bouton("✔", conserve ? "Ne plus conserver" : "Conserver",
                            conserve ? "actif" : "", function () {
      agir(conserve ? "ne_plus_conserver" : "conserver", [l.nom]);
    });
    bConserver.setAttribute("aria-pressed", conserve ? "true" : "false");
    actions.appendChild(bConserver);
    if (l.statut === "supprime") {
      actions.appendChild(bouton("↺", "Restaurer", "", function () { agir("restaurer", [l.nom]); }));
    } else {
      actions.appendChild(bouton("🗑", "Supprimer", "", function () { agir("supprimer", [l.nom]); }));
    }

    li.appendChild(coche);
    li.appendChild(bloc);
    li.appendChild(actions);
    return li;
  }

  function afficher() {
    var visibles = lignesVisibles();
    liste.textContent = "";
    visibles.forEach(function (l) { liste.appendChild(ligneElement(l)); });

    var compte = { a_trier: 0, conserve: 0, supprime: 0 };
    lignes.forEach(function (l) { compte[l.statut] = (compte[l.statut] || 0) + 1; });
    var texte = lignes.length + " message(s) : " + compte.a_trier + " à trier, " +
      compte.conserve + " conservé(s), " + compte.supprime + " supprimé(s)";
    if (visibles.length !== lignes.length) { texte += " — " + visibles.length + " affiché(s)"; }
    if (calculEnCours) { texte += " — calcul des scores en cours…"; }
    resume.textContent = texte;
    majSelection();
  }

  function majSelection() {
    // Une sélection ne survit pas à la disparition de son message.
    var existants = {};
    lignes.forEach(function (l) { existants[l.nom] = true; });
    Object.keys(selection).forEach(function (n) { if (!existants[n]) { delete selection[n]; } });

    var n = Object.keys(selection).length;
    nbSelection.textContent = n + (n > 1 ? " sélectionnés" : " sélectionné");
    document.querySelectorAll(".tri-groupe button").forEach(function (b) { b.disabled = n === 0; });
    var visibles = lignesVisibles();
    var cochees = visibles.filter(function (l) { return selection[l.nom]; }).length;
    toutSelectionner.checked = visibles.length > 0 && cochees === visibles.length;
    toutSelectionner.indeterminate = cochees > 0 && cochees < visibles.length;
  }

  // --- Échanges avec le serveur ------------------------------------------------

  function recevoir(data) {
    lignes = data.messages || [];
    calculEnCours = data.calcul_en_cours === true;
    afficher();
    programmer();
  }

  function programmer() {
    clearTimeout(minuterie);
    var attente = lignes.some(function (l) { return l.a_calculer; }) || calculEnCours;
    minuterie = setTimeout(charger, attente ? REFRESH_CALCUL_MS : REFRESH_REPOS_MS);
  }

  function charger() {
    fetch("/api/messages")
      .then(function (r) {
        if (!r.ok) { throw new Error("HTTP " + r.status); }
        return r.json();
      })
      .then(function (data) { afficherErreur(""); recevoir(data); })
      .catch(function (e) {
        afficherErreur("Liste des messages indisponible (" + e.message + ").");
        programmer();
      });
  }

  function agir(action, noms, viderSelection) {
    if (!noms.length) { return; }
    fetch("/api/messages/actions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: action, noms: noms }),
    })
      .then(function (r) {
        return r.json().then(function (data) {
          if (!r.ok) { throw new Error(data.erreur || ("HTTP " + r.status)); }
          return data;
        });
      })
      .then(function (data) {
        var inconnus = Object.keys(data.resultats || {}).filter(function (n) {
          return data.resultats[n] !== "ok";
        });
        afficherErreur(inconnus.length ? "Introuvable(s) : " + inconnus.join(", ") : "");
        if (viderSelection) { selection = {}; }
        recevoir(data);
      })
      .catch(function (e) { afficherErreur(e.message); });
  }

  // --- Lecture ------------------------------------------------------------------

  function lire(nom) {
    if (enLecture === nom) {
      if (lecteur.paused) { lecteur.play(); } else { lecteur.pause(); }
      return;
    }
    enLecture = nom;
    lecteurNom.textContent = nom;
    lecteur.src = "/api/messages/" + encodeURIComponent(nom) + "/audio";
    lecteur.play().catch(function () { /* lecture refusée : les contrôles restent là */ });
  }

  ["play", "pause", "ended"].forEach(function (evt) {
    lecteur.addEventListener(evt, afficher);
  });

  // --- Commandes -------------------------------------------------------------------

  [tri, filtreStatut].forEach(function (el) { el.addEventListener("change", afficher); });
  [filtreScore, filtreNom].forEach(function (el) { el.addEventListener("input", afficher); });

  // « Tout » ne coche que les lignes affichées : un filtre puis « Tout »
  // sélectionne exactement ce que l'on voit.
  toutSelectionner.addEventListener("change", function () {
    var coche = toutSelectionner.checked;
    lignesVisibles().forEach(function (l) {
      if (coche) { selection[l.nom] = true; } else { delete selection[l.nom]; }
    });
    afficher();
  });

  document.querySelectorAll(".tri-groupe button").forEach(function (b) {
    b.addEventListener("click", function () {
      agir(b.dataset.action, Object.keys(selection), true);
    });
  });

  charger();
})();
