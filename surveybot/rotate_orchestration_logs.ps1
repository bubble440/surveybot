# rotate_orchestration_logs.ps1
#
# ROLE : rotation quotidienne + purge par anciennete des logs TRANSVERSES
# d'orchestration (wake_scheduler_task.log, orchestration_sync_task.log,
# launch_all.log, ...) - fichiers ouverts en mode append en continu par les
# taches planifiees existantes (Start-Transcript -Append pour wake_scheduler.ps1/
# sync_orchestration_scripts.ps1, Add-Content pour launch_all.ps1), sans aucune
# rotation ni purge jusqu'ici -> croissance illimitee.
#
# HORS PERIMETRE (volontairement non touche) : les logs PAR BOT
# ($LogDir\<account_id>\bot_<account_id>.log[.N]), qui ont deja leur propre
# mecanisme de rotation par cycles (LOG_HISTORY_CYCLES, cf. launch_all.ps1).
# Ce script ne descend jamais dans les sous-dossiers de $LogDir (scan non
# recursif) : ces logs par bot vivent dans des sous-dossiers, jamais a plat
# dans $LogDir, donc structurellement hors de portee de ce scan - aucune
# exclusion explicite necessaire.
#
# MECANISME - generique, sans liste de fichiers en dur :
#   1. Scan non recursif de $LogDir (fichiers *.log directement a la racine).
#      Tout fichier .log trouve a plat est par construction un log transverse
#      (cf. ci-dessus) - une nouvelle source future (nouveau script planifie
#      qui ecrit son propre .log a plat dans $LogDir) est couverte
#      automatiquement, sans modification de ce script.
#   2. Un fichier dont la date de creation (CreationTime) est anterieure a
#      aujourd'hui est renomme (Move-Item) vers $ArchiveDir sous le nom
#      "<basename>__<CreationTime:yyyy-MM-dd>.log" - dossier UNIQUE et PLAT
#      (pas de sous-dossier par source, cf. contrainte de structure). La
#      prochaine ecriture du script source (Start-Transcript -Append ou
#      Add-Content) recree naturellement un fichier frais au meme chemin.
#   3. Purge : tout fichier de $ArchiveDir plus vieux que $RetentionDays jours
#      (LastWriteTime) est supprime. Boucle generique sur le contenu du
#      dossier plat, independante du nombre/nom de sources - $RetentionDays
#      est l'unique endroit ou la retention est codee en dur.
#
# ROBUSTESSE :
#   - Un fichier verrouille (transcript actif d'une autre execution en cours,
#     ou meme transcript de CETTE execution si Start-Transcript est demarre
#     avant le scan) fait echouer Move-Item/Remove-Item silencieusement :
#     capture, avertissement logue, fichier NON touche, nouvelle tentative au
#     prochain cycle planifie - jamais d'exception bloquante, jamais de
#     suppression forcee.
#   - Budgets bornes (MAX_FILES / MAX_ARCHIVE_FILES) sur les deux boucles :
#     abandon controle + log si depasse (dossier dans un etat inattendu),
#     jamais de boucle non bornee.
#   - Aucune modification de la logique metier existante (cooldown, sync,
#     lancement, NSSM) : ce script ne lit ni ne modifie accounts.json, les
#     services NSSM, ou tout etat de supervision - il ne touche qu'a des
#     fichiers .log a plat dans $LogDir.
#
# Usage :
#   .\rotate_orchestration_logs.ps1
#   .\rotate_orchestration_logs.ps1 -LogDir "D:\surveybot\logs" -RetentionDays 14
#
# Installation en tache planifiee (une seule fois, en tant qu'administrateur),
# meme cadence que les autres taches transverses (10 min) - la rotation
# elle-meme ne s'applique qu'une fois par jour par fichier (verifie via
# CreationTime), un cycle plus frequent ne fait que reduire le delai de
# nouvelle tentative apres un fichier verrouille :
#   $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
#   $action    = New-ScheduledTaskAction -Execute "powershell.exe" `
#                  -Argument "-NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File C:\surveybot\rotate_orchestration_logs.ps1"
#   $trigger   = New-ScheduledTaskTrigger -RepetitionInterval (New-TimeSpan -Minutes 10) `
#                  -Once -At (Get-Date)
#   $settings  = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
#   Register-ScheduledTask -TaskName "SurveyBot_LogRotation" -Action $action `
#     -Trigger $trigger -Settings $settings -Principal $principal -Force

param(
    [string]$LogDir        = "C:\surveybot\logs",
    [string]$ArchiveDir    = "",                  # defaut : $LogDir\archive
    [int]   $RetentionDays = 7,                   # SEUL endroit ou la retention est codee en dur
    [string]$SelfLogFile   = ""                   # defaut : $LogDir\log_rotation_task.log
)

$MAX_FILES         = 50    # garde-fou boucle rotation : nombre de .log a plat anormalement eleve
$MAX_ARCHIVE_FILES = 1000  # garde-fou boucle purge : nombre d'archives anormalement eleve

if (-not $ArchiveDir)  { $ArchiveDir  = Join-Path $LogDir "archive" }
if (-not $SelfLogFile) { $SelfLogFile = Join-Path $LogDir "log_rotation_task.log" }

# -- Capture de sortie (transcript) -- meme convention que wake_scheduler.ps1 --
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}
try {
    Start-Transcript -Path $SelfLogFile -Append -ErrorAction Stop | Out-Null
} catch {
    Write-Warning "[LOG_ROTATE] Impossible de demarrer le transcript ($SelfLogFile) : $_"
}

if (-not (Test-Path $ArchiveDir)) {
    try {
        New-Item -ItemType Directory -Path $ArchiveDir -Force | Out-Null
    } catch {
        Write-Warning "[LOG_ROTATE] Impossible de creer le dossier d'archives ($ArchiveDir) : $_ - abandon."
        try { Stop-Transcript | Out-Null } catch {}
        exit 1
    }
}

$today = (Get-Date).Date

# -- Etape 1 : rotation des logs transverses actifs (scan non recursif) --------
$sourceFiles = @(Get-ChildItem -Path $LogDir -File -Filter "*.log" -ErrorAction SilentlyContinue)

if ($sourceFiles.Count -gt $MAX_FILES) {
    Write-Warning "[LOG_ROTATE] $($sourceFiles.Count) fichiers .log a plat dans $LogDir > MAX_FILES ($MAX_FILES) - dossier dans un etat inattendu, rotation abandonnee ce cycle."
} else {
    $rotated = 0
    foreach ($file in $sourceFiles) {
        if ($file.CreationTime.Date -ge $today) {
            Write-Output "[LOG_ROTATE] $($file.Name) - segment du jour, pas de rotation."
            continue
        }

        $dateTag  = $file.CreationTime.ToString("yyyy-MM-dd")
        $baseName = [System.IO.Path]::GetFileNameWithoutExtension($file.Name)
        $archiveName = "${baseName}__${dateTag}.log"
        $archivePath = Join-Path $ArchiveDir $archiveName

        # Collision (rotation deja effectuee un cycle precedent pour ce jour, ou
        # plusieurs segments crees le meme jour apres redemarrages) : suffixe
        # horaire pour ne jamais ecraser une archive existante.
        if (Test-Path $archivePath) {
            $archiveName = "${baseName}__${dateTag}__$($file.CreationTime.ToString('HHmmss')).log"
            $archivePath = Join-Path $ArchiveDir $archiveName
        }

        try {
            Move-Item -Path $file.FullName -Destination $archivePath -ErrorAction Stop
            Write-Output "[LOG_ROTATE] $($file.Name) - archive vers $archiveName (segment $dateTag)."
            $rotated++
        } catch {
            Write-Warning "[LOG_ROTATE] $($file.Name) - rotation echouee (fichier probablement verrouille par une execution en cours) : $_ - nouvelle tentative au prochain cycle."
        }
    }
    Write-Output "[LOG_ROTATE] Rotation terminee - $rotated fichier(s) archive(s) sur $($sourceFiles.Count) examine(s)."
}

# -- Etape 2 : purge des archives plus vieilles que $RetentionDays jours -------
$cutoff = (Get-Date).AddDays(-$RetentionDays)
$archiveFiles = @(Get-ChildItem -Path $ArchiveDir -File -Filter "*.log" -ErrorAction SilentlyContinue)

if ($archiveFiles.Count -gt $MAX_ARCHIVE_FILES) {
    Write-Warning "[LOG_ROTATE] $($archiveFiles.Count) archives dans $ArchiveDir > MAX_ARCHIVE_FILES ($MAX_ARCHIVE_FILES) - dossier dans un etat inattendu, purge abandonnee ce cycle."
} else {
    $purged = 0
    foreach ($archive in $archiveFiles) {
        if ($archive.LastWriteTime -ge $cutoff) {
            continue
        }
        try {
            Remove-Item -Path $archive.FullName -Force -ErrorAction Stop
            Write-Output "[LOG_ROTATE] Archive purgee (> $RetentionDays j) : $($archive.Name)."
            $purged++
        } catch {
            Write-Warning "[LOG_ROTATE] Purge echouee pour $($archive.Name) : $_ - nouvelle tentative au prochain cycle."
        }
    }
    Write-Output "[LOG_ROTATE] Purge terminee - $purged archive(s) supprimee(s) sur $($archiveFiles.Count) examinee(s) (retention=$RetentionDays j)."
}

try { Stop-Transcript | Out-Null } catch {}
