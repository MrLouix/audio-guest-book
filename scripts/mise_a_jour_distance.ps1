<#
.SYNOPSIS
    Met à jour le livre d'or à distance, dès que le téléphone est joignable.

.DESCRIPTION
    Attend que le Pi réponde en ssh, puis lui envoie scripts/mise_a_jour.sh et
    l'exécute : réseau WiFi enregistré (facultatif), règle polkit pour la
    page /wifi du dashboard (-Polkit), code mis à jour,
    dépendances réinstallées si besoin, puis relance de dashboard et livre-dor
    une fois le téléphone raccroché. Le script voyage avec la commande : le Pi
    n'a pas besoin de l'avoir déjà.

    Le Pi est cherché sur le réseau local (-Hote, défaut livredor.local) puis
    sur Tailscale (-Tailscale, défaut livredor) : son adresse 100.x.y.z est
    demandée au client Tailscale du PC s'il est installé, et son nom est
    essayé tel quel pour MagicDNS. Le premier qui répond en ssh est retenu.
    -SansTailscale limite la recherche au réseau local.

    Le mot de passe ssh est demandé une fois, quand le Pi répond (sauf clé ssh).
    À la première connexion par une nouvelle adresse, ssh demande aussi de
    confirmer l'empreinte du Pi : répondre « yes ».

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\mise_a_jour_distance.ps1 -Wifi "SSID" -Mdp "mot de passe"

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\mise_a_jour_distance.ps1 -Hote 192.168.1.42 -Utilisateur moi -Depot "~/audio-guest-book"

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\mise_a_jour_distance.ps1 -Tailscale raspberrypi
    (le Pi porte un autre nom sur le réseau Tailscale)
#>
param(
    [string[]]$Hote = @("livredor.local"),
    [string]$Tailscale = "livredor",   # nom du Pi sur Tailscale
    [string]$Utilisateur = "pi",
    [string]$Wifi,
    [string]$Mdp,
    [string]$Branche,
    [string]$Depot,          # chemin du dépôt sur le Pi, si introuvable tout seul
    [int]$Intervalle = 30,
    [switch]$Forcer,
    [switch]$Polkit,         # règle polkit : la page /wifi du dashboard peut créer des réseaux
    [switch]$SansTailscale   # ne chercher le Pi que sur le réseau local
)

$ErrorActionPreference = "Stop"
if ($SansTailscale) { $Tailscale = "" }
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
if ($Polkit)  { $arguments += "--polkit" }

# Joignable = le port ssh répond (un ping ne dit pas que sshd est prêt).
# 5 s : via Tailscale, la première connexion passe par un relais et peut
# mettre quelques secondes à s'établir.
function Test-Ssh([string]$adresse) {
    $client = [System.Net.Sockets.TcpClient]::new()
    try { return $client.ConnectAsync($adresse, 22).Wait(5000) -and $client.Connected }
    catch { return $false }
    finally { $client.Dispose() }
}

# Client Tailscale du PC : dans le PATH, sinon à son emplacement d'installation.
$cliTailscale = $null
if ($Tailscale) {
    $commandeTs = Get-Command tailscale -ErrorAction SilentlyContinue
    if ($commandeTs) { $cliTailscale = $commandeTs.Source }
    elseif (Test-Path "$env:ProgramFiles\Tailscale\tailscale.exe") {
        $cliTailscale = "$env:ProgramFiles\Tailscale\tailscale.exe"
    }
}

# Adresse Tailscale (100.x.y.z) du Pi, d'après le client du PC ; $null si
# Tailscale est arrêté ou ne connaît pas ce nom. Redemandée à chaque essai :
# Tailscale a pu être lancé entre-temps.
function Get-AdresseTailscale {
    if (-not $cliTailscale) { return $null }
    $ErrorActionPreference = "Continue"   # PowerShell 5.1 : stderr d'un exe ne doit pas lever d'erreur
    try { $sortie = & $cliTailscale ip -4 $Tailscale 2>$null } catch { return $null }
    return ($sortie | Where-Object { $_ -match '^\d+\.\d+\.\d+\.\d+$' } | Select-Object -First 1)
}

# Réseau local d'abord (plus direct), puis Tailscale : adresse, puis nom (MagicDNS).
function Get-Candidats {
    $liste = @($Hote)
    if ($Tailscale) {
        $ip = Get-AdresseTailscale
        if ($ip) { $liste += $ip }
        $liste += $Tailscale
    }
    $liste | Where-Object { $_ } | Select-Object -Unique
}

$chercher = ($Hote -join ", ")
if ($Tailscale) {
    $chercher += " ; Tailscale : $Tailscale"
    if (-not $cliTailscale) { $chercher += " (MagicDNS seulement : client Tailscale introuvable)" }
}
Write-Host "Attente du Pi ($chercher), essai toutes les $Intervalle s, Ctrl+C pour abandonner..."
$cible = $null
while (-not $cible) {
    $candidats = @(Get-Candidats)
    foreach ($adresse in $candidats) {
        if (Test-Ssh $adresse) { $cible = $adresse; break }
    }
    if (-not $cible) { Start-Sleep -Seconds $Intervalle }
}
Write-Host "$cible répond : mise à jour."

# Le script part sur l'entrée de ssh. UTF-8 sans BOM, et retours chariot
# retirés côté Pi : un clone Windows peut avoir converti les fins de ligne.
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$commande = "tr -d '\r' | bash -s -- " + ($arguments -join " ")
Get-Content -Raw -Encoding UTF8 $script | ssh "$Utilisateur@$cible" $commande
if ($LASTEXITCODE -ne 0) {
    Write-Host "La mise à jour a échoué (code $LASTEXITCODE) : voir les messages ci-dessus." -ForegroundColor Red
    exit $LASTEXITCODE
}
Write-Host "Mise à jour terminée." -ForegroundColor Green
