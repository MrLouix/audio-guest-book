<#
.SYNOPSIS
    Met à jour le livre d'or à distance, dès que le téléphone est joignable.

.DESCRIPTION
    Attend que le Pi réponde en ssh, puis lui envoie scripts/mise_a_jour.sh et
    l'exécute : réseau WiFi enregistré (facultatif), code mis à jour,
    dépendances réinstallées si besoin, puis relance de dashboard et livre-dor
    une fois le téléphone raccroché. Le script voyage avec la commande : le Pi
    n'a pas besoin de l'avoir déjà.

    Le mot de passe ssh est demandé une fois, quand le Pi répond (sauf clé ssh).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\mise_a_jour_distance.ps1 -Wifi "SSID" -Mdp "mot de passe"

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\mise_a_jour_distance.ps1 -Hote 192.168.1.42 -Utilisateur moi -Depot "~/audio-guest-book"
#>
param(
    [string]$Hote = "livredor.local",
    [string]$Utilisateur = "pi",
    [string]$Wifi,
    [string]$Mdp,
    [string]$Branche,
    [string]$Depot,          # chemin du dépôt sur le Pi, si introuvable tout seul
    [int]$Intervalle = 30,
    [switch]$Forcer
)

$ErrorActionPreference = "Stop"
$script = Join-Path $PSScriptRoot "mise_a_jour.sh"
if (-not (Test-Path $script)) { throw "Introuvable : $script" }

if ($Wifi -and -not $Mdp) {
    $saisie = Read-Host "Mot de passe du WiFi $Wifi" -AsSecureString
    $Mdp = [System.Net.NetworkCredential]::new("", $saisie).Password
}

# Arguments transmis à bash, chacun entre apostrophes pour le shell du Pi.
function Citer([string]$v) { "'" + ($v -replace "'", "'\''") + "'" }
$arguments = @()
if ($Wifi)    { $arguments += "--wifi", (Citer $Wifi), "--mdp", (Citer $Mdp) }
if ($Branche) { $arguments += "--branche", (Citer $Branche) }
if ($Depot)   { $arguments += "--depot", (Citer $Depot) }
if ($Forcer)  { $arguments += "--forcer" }

# Joignable = le port ssh répond (un ping ne dit pas que sshd est prêt).
function Test-Ssh {
    $client = [System.Net.Sockets.TcpClient]::new()
    try { return $client.ConnectAsync($Hote, 22).Wait(3000) -and $client.Connected }
    catch { return $false }
    finally { $client.Dispose() }
}

Write-Host "Attente de $Hote (essai toutes les $Intervalle s, Ctrl+C pour abandonner)..."
while (-not (Test-Ssh)) { Start-Sleep -Seconds $Intervalle }
Write-Host "$Hote répond : mise à jour."

# Le script part sur l'entrée de ssh. UTF-8 sans BOM, et retours chariot
# retirés côté Pi : un clone Windows peut avoir converti les fins de ligne.
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$commande = "tr -d '\r' | bash -s -- " + ($arguments -join " ")
Get-Content -Raw -Encoding UTF8 $script | ssh "$Utilisateur@$Hote" $commande
if ($LASTEXITCODE -ne 0) {
    Write-Host "La mise à jour a échoué (code $LASTEXITCODE) : voir les messages ci-dessus." -ForegroundColor Red
    exit $LASTEXITCODE
}
Write-Host "Mise à jour terminée." -ForegroundColor Green
