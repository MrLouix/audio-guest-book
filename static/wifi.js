(function () {
  "use strict";

  var video = document.getElementById("camera");
  var canvas = document.getElementById("canvas");
  var ctx = canvas.getContext("2d", { willReadFrequently: true });
  var statusEl = document.getElementById("scan-status");
  var confirmSection = document.getElementById("confirm-section");
  var detectedSsidEl = document.getElementById("detected-ssid");
  var confirmButton = document.getElementById("confirm-button");
  var cancelButton = document.getElementById("cancel-button");
  var wifiFeedbackEl = document.getElementById("wifi-feedback");
  var manualForm = document.getElementById("manual-form");
  var manualFeedbackEl = document.getElementById("manual-feedback");

  var scanning = true;
  var pending = null;

  function parseWifiQr(text) {
    if (typeof text !== "string" || text.indexOf("WIFI:") !== 0) return null;
    var body = text.slice(5);
    var result = { ssid: "", password: "" };
    var field = "";
    var key = null;
    for (var i = 0; i < body.length; i++) {
      var ch = body[i];
      if (ch === "\\" && i + 1 < body.length) {
        field += body[i + 1];
        i++;
        continue;
      }
      if (ch === ":" && key === null) {
        key = field;
        field = "";
        continue;
      }
      if (ch === ";") {
        if (key === "S") result.ssid = field;
        if (key === "P") result.password = field;
        key = null;
        field = "";
        continue;
      }
      field += ch;
    }
    return result.ssid ? result : null;
  }

  function tick() {
    if (!scanning) return;
    if (video.readyState === video.HAVE_ENOUGH_DATA) {
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
      ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
      var imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
      var code = window.jsQR(imageData.data, imageData.width, imageData.height);
      if (code) {
        var wifi = parseWifiQr(code.data);
        if (wifi) {
          pending = wifi;
          detectedSsidEl.textContent = wifi.ssid;
          confirmSection.hidden = false;
          statusEl.textContent = "QR code détecté.";
          scanning = false;
          return;
        }
        statusEl.textContent = "QR code détecté, mais ce n'est pas un QR WiFi (format WIFI:...).";
      }
    }
    requestAnimationFrame(tick);
  }

  function resumeScanning() {
    pending = null;
    confirmSection.hidden = true;
    wifiFeedbackEl.textContent = "";
    scanning = true;
    requestAnimationFrame(tick);
  }

  function submitWifi(ssid, password, feedbackEl) {
    feedbackEl.textContent = "Connexion en cours…";
    fetch("/api/wifi/add", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ssid: ssid, password: password }),
    })
      .then(function (r) {
        return r.json().then(function (data) { return { ok: r.ok, data: data }; });
      })
      .then(function (result) {
        feedbackEl.textContent = result.ok
          ? "Connecté à " + result.data.ssid + "."
          : (result.data.erreur || "Échec de la connexion.");
      })
      .catch(function () {
        feedbackEl.textContent = "Échec de la requête (le Pi a peut-être changé de réseau entre-temps).";
      });
  }

  confirmButton.addEventListener("click", function () {
    if (pending) submitWifi(pending.ssid, pending.password, wifiFeedbackEl);
  });

  cancelButton.addEventListener("click", resumeScanning);

  manualForm.addEventListener("submit", function (event) {
    event.preventDefault();
    var ssid = document.getElementById("manual-ssid").value.trim();
    var password = document.getElementById("manual-password").value;
    if (ssid) submitWifi(ssid, password, manualFeedbackEl);
  });

  if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
    navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } })
      .then(function (stream) {
        video.srcObject = stream;
        requestAnimationFrame(tick);
      })
      .catch(function () {
        statusEl.textContent = "Caméra indisponible : utilisez la saisie manuelle ci-dessous.";
      });
  } else {
    statusEl.textContent = "Caméra non supportée par ce navigateur : utilisez la saisie manuelle ci-dessous.";
  }
})();
