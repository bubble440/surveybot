# sync_orchestration_scripts.ps1
#
# ROLE : synchronise automatiquement, sur chaque machine du parc, les scripts
# d'orchestration racine qui vivent HORS du dossier code\ et que
# update_checker.py ne touche donc JAMAIS (il ne remplace que code\, voir
# Utils\ORCHESTRATION_TRACKING.md section 1bis). Ces scripts (launch_all.ps1,
# nssm_setup_bot.ps1, wake_scheduler.ps1, check_zombie_bots.ps1, run_tabs.ps1,
# tools\attach_tab.ps1) devaient jusqu'ici etre recopies manuellement sur
# chaque machine a chaque modification - oubli frequent sur un parc
# multi-machines, en particulier en phase de debogage prod ou ces fichiers
# changent souvent.
#
# MECANISME SEPARE ET INDEPENDANT de update_checker.py/code\ :
#   - manifeste different (orchestration_manifest.json, pas manifest.json)
#   - script different (celui-ci, pas update_checker.py)
#   - tache planifiee differente (celle-ci, pas liee au lancement d'un bot)
# update_checker.py et manifest.json (dist_zip\) ne sont PAS modifies par ce
# patch et ne doivent jamais l'etre par ce mecanisme.
#
# LIMITES CONNUES (volontairement non traitees par ce script) :
#   - requirements.txt est SUIVI (hash compare) mais JAMAIS applique : un
#     changement declenche un signal (log + Telegram) uniquement. Une
#     reinstallation pip automatique et silencieuse sur le parc est un risque
#     inacceptable (peut casser un venv en prod sans supervision) - action
#     manuelle requise (rejouer setup_machine.ps1 sur la machine concernee).
#   - remplacer nssm_setup_bot.ps1 sur disque ne reconfigure PAS retroactivement
#     un service NSSM deja installe : NSSM lit les parametres au moment de
#     `nssm install`/`nssm set`, pas en relisant le fichier a chaque demarrage.
#     Ce script rend seulement la version a jour du fichier disponible sur la
#     machine ; si son contenu a change, il faut rejouer manuellement
#     `nssm_setup_bot.ps1` (le signal Telegram le rappelle explicitement).
#   - build_release_zip.ps1 et nuitka_build_release.ps1 ne font PAS partie du
#     perimetre suivi ici : scripts de build, machine de dev uniquement, jamais
#     presents/executes sur une machine du parc.
#
# Usage :
#   .\sync_orchestration_scripts.ps1 -ManifestUrl "https://pub-565d2bb59d364c1490255c5dddc296aa.r2.dev/orchestration_manifest.json"
#
# Installation en tache planifiee (une seule fois, en tant qu'administrateur),
# cadence de verification 10 minutes :
#   ATTENTION : -ManifestUrl est obligatoire (pas de valeur par defaut) - sans lui,
#   le script se termine immediatement (exit 1) a chaque declenchement.
#   $action  = New-ScheduledTaskAction -Execute "powershell.exe" `
#                -Argument "-NonInteractive -File C:\surveybot\sync_orchestration_scripts.ps1 -ManifestUrl `"https://pub-565d2bb59d364c1490255c5dddc296aa.r2.dev/orchestration_manifest.json`""
#   $trigger = New-ScheduledTaskTrigger -RepetitionInterval (New-TimeSpan -Minutes 10) `
#                -Once -At (Get-Date)
#   $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
#   Register-ScheduledTask -TaskName "SurveyBot_OrchestrationSync" -Action $action `
#     -Trigger $trigger -Settings $settings -RunLevel Highest -Force

param(
    [string]$ManifestUrl        = "",                                   # obligatoire, pas de valeur en dur
    [string]$InstallDir         = "C:\surveybot",
    [string]$StateFile          = "",                                   # defaut : $InstallDir\pids\orchestration_sync_state.json
    [string]$ReceiverConfigFile = "",                                   # defaut : $InstallDir\receiver_config.json
    [string]$LogFile            = "",                                   # defaut : $InstallDir\logs\orchestration_sync_task.log
    [string]$MachineLabel       = $env:COMPUTERNAME
)

$MAX_FILES = 50   # garde-fou boucle : le manifeste ne doit jamais lister un nombre anormal de fichiers

if (-not $StateFile)          { $StateFile          = Join-Path $InstallDir "pids\orchestration_sync_state.json" }
if (-not $ReceiverConfigFile) { $ReceiverConfigFile  = Join-Path $InstallDir "receiver_config.json" }
if (-not $LogFile)            { $LogFile             = Join-Path $InstallDir "logs\orchestration_sync_task.log" }

# -- Capture de sortie (transcript) -- meme convention que wake_scheduler.ps1 --
$_logDir = Split-Path -Path $LogFile -Parent
if ($_logDir -and -not (Test-Path $_logDir)) {
    New-Item -ItemType Directory -Path $_logDir -Force | Out-Null
}
try {
    Start-Transcript -Path $LogFile -Append -ErrorAction Stop | Out-Null
} catch {
    Write-Warning "[ORCH_SYNC] Impossible de demarrer le transcript ($LogFile) : $_"
}

if (-not $ManifestUrl) {
    Write-Warning "[ORCH_SYNC] -ManifestUrl non fourni - rien a faire, abandon."
    try { Stop-Transcript | Out-Null } catch {}
    exit 1
}

# -- Telechargement du manifeste ------------------------------------------------
try {
    $manifest = Invoke-RestMethod -Uri $ManifestUrl -TimeoutSec 15 -ErrorAction Stop
} catch {
    Write-Warning "[ORCH_SYNC] Manifeste inaccessible ($ManifestUrl) - ignore, reessai au prochain cycle : $_"
    try { Stop-Transcript | Out-Null } catch {}
    exit 0
}

if (-not $manifest.files) {
    Write-Warning "[ORCH_SYNC] Manifeste invalide (champ 'files' absent) - abandon."
    try { Stop-Transcript | Out-Null } catch {}
    exit 1
}

$fileEntries     = @($manifest.files)
$trackOnlyEntries = @($manifest.track_only)

if (($fileEntries.Count + $trackOnlyEntries.Count) -gt $MAX_FILES) {
    Write-Warning "[ORCH_SYNC] $($fileEntries.Count + $trackOnlyEntries.Count) entrees > MAX_FILES ($MAX_FILES) - abort (manifeste suspect)."
    try { Stop-Transcript | Out-Null } catch {}
    exit 1
}

# -- Etat local (dernier hash applique / dernier hash notifie) ------------------
$state = @{}
if (Test-Path $StateFile) {
    try {
        $raw = Get-Content -Path $StateFile -Raw -ErrorAction Stop
        $obj = $raw | ConvertFrom-Json
        foreach ($prop in $obj.PSObject.Properties) { $state[$prop.Name] = $prop.Value }
    } catch {
        Write-Warning "[ORCH_SYNC] Etat local illisible ($StateFile) - repart d'un etat vide : $_"
        $state = @{}
    }
}

function Get-FileSha256([string]$Path) {
    if (-not (Test-Path $Path)) { return $null }
    return (Get-FileHash -Path $Path -Algorithm SHA256).Hash.ToLower()
}

# -- Application des fichiers synchronises --------------------------------------
$changedFiles = New-Object System.Collections.Generic.List[string]

foreach ($entry in $fileEntries) {
    $relPath    = $entry.rel_path
    $remoteHash = ($entry.sha256 | Out-String).Trim().ToLower()
    $url        = $entry.url

    if (-not $relPath -or -not $remoteHash -or -not $url) {
        Write-Warning "[ORCH_SYNC] Entree 'files' incomplete (rel_path/sha256/url manquant) - ignoree : $($entry | ConvertTo-Json -Compress)"
        continue
    }

    $target    = Join-Path $InstallDir $relPath
    $localHash = Get-FileSha256 -Path $target

    if ($localHash -eq $remoteHash) {
        Write-Output "[ORCH_SYNC] $relPath - a jour."
        continue
    }

    Write-Output "[ORCH_SYNC] $relPath - divergence detectee (local=$localHash, distant=$remoteHash) - telechargement..."

    $tmpPath = Join-Path ([System.IO.Path]::GetTempPath()) ("orch_sync_" + [guid]::NewGuid().ToString("N") + ".tmp")
    try {
        Invoke-WebRequest -Uri $url -OutFile $tmpPath -TimeoutSec 30 -UseBasicParsing -ErrorAction Stop
        $downloadedHash = Get-FileSha256 -Path $tmpPath

        if ($downloadedHash -ne $remoteHash) {
            Write-Warning "[ORCH_SYNC] $relPath - SHA256 invalide apres telechargement (attendu=$remoteHash, recu=$downloadedHash) - fichier local NON touche."
            continue
        }

        $targetDir = Split-Path -Path $target -Parent
        if ($targetDir -and -not (Test-Path $targetDir)) {
            New-Item -ItemType Directory -Path $targetDir -Force | Out-Null
        }

        # Backup best-effort de l'existant (un seul niveau, ecrase a chaque sync -
        # meme convention que update_checker.py::_swap_code_dir, pas de rotation).
        if (Test-Path $target) {
            try { Copy-Item -Path $target -Destination "$target.old" -Force } catch {
                Write-Warning "[ORCH_SYNC] $relPath - impossible de sauvegarder l'existant en .old : $_"
            }
        }

        Move-Item -Path $tmpPath -Destination $target -Force

        # Un fichier ecrit via Invoke-WebRequest herite du tag "provenance Internet"
        # (Zone.Identifier) - RemoteSigned bloquerait alors son execution ulterieure,
        # silencieusement (voir set-up.txt, prerequis n.3). Best-effort : ne doit
        # jamais faire echouer la synchro si le flux NTFS n'est pas manipulable.
        try { Unblock-File -Path $target -ErrorAction Stop } catch {
            Write-Warning "[ORCH_SYNC] $relPath - Unblock-File a echoue (fichier peut-etre bloque a l'execution) : $_"
        }

        # Remplacement confirme (SHA256 verifie + fichier deplace en place) : le
        # backup .old n'a plus de raison de rester sur le disque. Unblock-File est
        # deja best-effort ci-dessus et ne conditionne pas ce nettoyage.
        if (Test-Path "$target.old") {
            try { Remove-Item -Path "$target.old" -Force -ErrorAction Stop } catch {
                Write-Warning "[ORCH_SYNC] $relPath - impossible de supprimer le backup residuel .old : $_"
            }
        }

        Write-Output "[ORCH_SYNC] $relPath - mis a jour (sha256=$remoteHash)."
        $changedFiles.Add($relPath)
        $state["applied__$relPath"] = $remoteHash
    } catch {
        Write-Warning "[ORCH_SYNC] $relPath - echec telechargement/application : $_"
    } finally {
        if (Test-Path $tmpPath) { Remove-Item -Path $tmpPath -Force -ErrorAction SilentlyContinue }
    }
}

# -- requirements.txt et autres entrees "track_only" : signal, jamais d'action --
$driftSignals = New-Object System.Collections.Generic.List[string]

foreach ($entry in $trackOnlyEntries) {
    $relPath    = $entry.rel_path
    $remoteHash = ($entry.sha256 | Out-String).Trim().ToLower()

    if (-not $relPath -or -not $remoteHash) {
        Write-Warning "[ORCH_SYNC] Entree 'track_only' incomplete - ignoree : $($entry | ConvertTo-Json -Compress)"
        continue
    }

    $target    = Join-Path $InstallDir $relPath
    $localHash = Get-FileSha256 -Path $target
    $lastNotifiedKey = "notified__$relPath"
    $lastNotified    = $state[$lastNotifiedKey]

    if ($localHash -eq $remoteHash) {
        Write-Output "[ORCH_SYNC] $relPath (suivi seul) - a jour."
        continue
    }

    if ($lastNotified -eq $remoteHash) {
        # Deja signale pour ce hash distant precis - evite de spammer Telegram
        # toutes les 10 min tant que personne n'a mis a jour la machine.
        Write-Output "[ORCH_SYNC] $relPath (suivi seul) - divergence deja signalee (distant=$remoteHash), pas de nouveau signal."
        continue
    }

    Write-Output "[ORCH_SYNC] $relPath (suivi seul) - divergence detectee (local=$localHash, distant=$remoteHash) - signal uniquement, AUCUNE action automatique."
    $driftSignals.Add("$relPath (local=$localHash, distant=$remoteHash) - action manuelle requise (ex. rejouer setup_machine.ps1)")
    $state[$lastNotifiedKey] = $remoteHash
}

# -- Persistance de l'etat local -------------------------------------------------
try {
    ($state | ConvertTo-Json -Depth 5) | Set-Content -Path $StateFile -Encoding UTF8
} catch {
    Write-Warning "[ORCH_SYNC] Impossible d'ecrire l'etat local ($StateFile) : $_"
}

# -- Notification Telegram (visibilite operateur sur l'etat de sync du parc) ---
# Reutilise receiver_config.json (cles telegram_bot_token/telegram_chat_id,
# meme convention que preselection/secret_loader.py::_from_receiver_config_file).
# Best-effort : l'absence de config Telegram ne doit jamais faire echouer la sync.
if ($changedFiles.Count -gt 0 -or $driftSignals.Count -gt 0) {
    $lines = New-Object System.Collections.Generic.List[string]
    $lines.Add("[SurveyBot][$MachineLabel] Sync scripts d'orchestration")

    if ($changedFiles.Count -gt 0) {
        $lines.Add("Fichiers mis a jour :")
        foreach ($f in $changedFiles) { $lines.Add("  - $f") }
        if ($changedFiles -contains "nssm_setup_bot.ps1") {
            $lines.Add("ATTENTION : nssm_setup_bot.ps1 a change - relancer ce script manuellement pour reconfigurer les services NSSM deja installes (le fichier seul ne suffit pas, NSSM ne relit pas le script tout seul).")
        }
    }
    if ($driftSignals.Count -gt 0) {
        $lines.Add("Divergence signalee (non appliquee automatiquement) :")
        foreach ($d in $driftSignals) { $lines.Add("  - $d") }
    }

    $message = ($lines -join "`n")
    Write-Output "[ORCH_SYNC] $message"

    if (Test-Path $ReceiverConfigFile) {
        try {
            $receiverConfig = Get-Content -Path $ReceiverConfigFile -Raw -Encoding UTF8 | ConvertFrom-Json
            $tgToken = $receiverConfig.telegram_bot_token
            $tgChat  = $receiverConfig.telegram_chat_id

            if ($tgToken -and $tgChat) {
                $tgUrl  = "https://api.telegram.org/bot$tgToken/sendMessage"
                $tgBody = @{ chat_id = $tgChat; text = $message }
                try {
                    Invoke-RestMethod -Uri $tgUrl -Method Post -Body $tgBody -TimeoutSec 10 -ErrorAction Stop | Out-Null
                    Write-Output "[ORCH_SYNC] Notification Telegram envoyee."
                } catch {
                    Write-Warning "[ORCH_SYNC] Echec envoi Telegram : $_"
                }
            } else {
                Write-Output "[ORCH_SYNC] Cles telegram_bot_token/telegram_chat_id absentes de $ReceiverConfigFile - notification non envoyee."
            }
        } catch {
            Write-Warning "[ORCH_SYNC] Impossible de lire $ReceiverConfigFile pour la notification Telegram : $_"
        }
    } else {
        Write-Output "[ORCH_SYNC] $ReceiverConfigFile introuvable - notification Telegram non envoyee."
    }
} else {
    Write-Output "[ORCH_SYNC] Termine - tous les fichiers suivis sont a jour, aucune divergence."
}

try { Stop-Transcript | Out-Null } catch {}