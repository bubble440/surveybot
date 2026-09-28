# sync_autofix_clone.ps1
#
# Partie C du chantier "clone dedie a l'orchestrateur autofix" (cf.
# Utils/AUTOFIX_CLONE_ORCHESTRATEUR.md). Amene, dans la branche d'integration du
# clone, les commits recents de la branche de developpement de l'operateur, pour
# que le clone ne derive pas au fil du temps. CHANGE L'ETAT GIT DU CLONE --
# traite avec prudence (memes garde-fous que Survey/merge_executor.py) :
#
#   - REFUSE si le verrou de l'orchestrateur (Partie B) est present et NON
#     perime -- une invocation du pipeline est probablement en cours, un fetch/
#     merge concurrent romprait son hypothese de depot stable.
#   - REFUSE si le clone n'est pas propre (git status --porcelain
#     --untracked-files=all non vide) -- jamais de stash automatique.
#   - REFUSE si la branche courante n'est pas la branche d'integration.
#   - En cas de conflit reel : `git merge --abort` immediat, signalement
#     explicite, JAMAIS de resolution automatique.
#   - Jamais de push, quel que soit le resultat.
#
# Usage :
#   .\sync_autofix_clone.ps1
#   .\sync_autofix_clone.ps1 -DevBranch "feature/phase-1a-observability"   # si le fichier sidecar est absent/perime
#
# Prerequis : setup_autofix_clone.ps1 (Partie A) deja execute au moins une fois
# (fichier sidecar <clone>.autofix-clone-meta.json present, sinon -DevBranch est
# obligatoire).

param(
    [string]$ClonePackageRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$IntegrationBranch = "autofix-integration",
    [string]$DevBranch = "",
    [string]$VenvDirName = "venv",
    [string]$VenvPythonRelPath = "",
    [string]$PipelineRunsRelRoot = "autofix_pipeline_runs",
    [double]$GitTimeoutS = 120
)

$ErrorActionPreference = "Stop"

function Write-Log {
    param([string]$Message)
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Output "[$ts] [SYNC_AUTOFIX_CLONE] $Message"
}

function Invoke-GitTimed {
    # Meme mecanisme que setup_autofix_clone.ps1/preflight_autofix_clone.ps1
    # (duplique volontairement, cf. convention deja etablie dans ce depot).
    param(
        [Parameter(Mandatory = $true)][string[]]$GitArgs,
        [Parameter(Mandatory = $true)][string]$WorkDir,
        [double]$TimeoutS = $GitTimeoutS
    )
    $stdoutFile = [System.IO.Path]::GetTempFileName()
    $stderrFile = [System.IO.Path]::GetTempFileName()
    try {
        $p = Start-Process -FilePath "git" -ArgumentList $GitArgs -WorkingDirectory $WorkDir `
            -RedirectStandardOutput $stdoutFile -RedirectStandardError $stderrFile `
            -PassThru -NoNewWindow
        $finished = $p.WaitForExit([int]($TimeoutS * 1000))
        if (-not $finished) {
            try { Stop-Process -Id $p.Id -Force -ErrorAction Stop } catch {}
            return [PSCustomObject]@{ Ok = $false; TimedOut = $true; ExitCode = $null; StdOut = ""; StdErr = "budget ${TimeoutS}s depasse" }
        }
        $stdout = Get-Content -Path $stdoutFile -Raw -ErrorAction SilentlyContinue
        $stderr = Get-Content -Path $stderrFile -Raw -ErrorAction SilentlyContinue
        return [PSCustomObject]@{
            Ok = ($p.ExitCode -eq 0); TimedOut = $false; ExitCode = $p.ExitCode
            StdOut = $(if ($stdout) { $stdout.Trim() } else { "" })
            StdErr = $(if ($stderr) { $stderr.Trim() } else { "" })
        }
    } finally {
        Remove-Item -Path $stdoutFile, $stderrFile -Force -ErrorAction SilentlyContinue
    }
}

function Exit-Refused {
    param([string]$Reason)
    Write-Output "[REFUS] $Reason"
    Write-Output "Aucune modification effectuee."
    exit 1
}

# ---------------------------------------------------------------------------
# 0) Resolution des chemins
# ---------------------------------------------------------------------------

if (-not (Test-Path $ClonePackageRoot)) {
    Write-Error "ClonePackageRoot introuvable : $ClonePackageRoot"
    exit 1
}

$toplevelResult = Invoke-GitTimed -GitArgs @("rev-parse", "--show-toplevel") -WorkDir $ClonePackageRoot
if ($toplevelResult.TimedOut -or -not $toplevelResult.Ok) {
    Write-Error "Depot Git introuvable depuis $ClonePackageRoot : $($toplevelResult.StdErr)"
    exit 1
}
$CloneRepoRoot = $toplevelResult.StdOut

if (-not $VenvPythonRelPath) {
    $VenvPythonRelPath = Join-Path $VenvDirName (Join-Path "Scripts" "python.exe")
}
$venvPython = Join-Path $ClonePackageRoot $VenvPythonRelPath
$probeScript = Join-Path $ClonePackageRoot (Join-Path "tools" "introspect_autofix_pipeline.py")

# Branche de developpement : parametre explicite prioritaire, sinon fichier
# sidecar ecrit par setup_autofix_clone.ps1 (Partie A) -- jamais une supposition
# sur la branche courante du depot operateur au moment du sync (qui peut avoir
# change depuis la mise en place du clone).
if (-not $DevBranch) {
    $metaPath = Join-Path (Split-Path -Parent $CloneRepoRoot) "$(Split-Path -Leaf $CloneRepoRoot).autofix-clone-meta.json"
    if (-not (Test-Path $metaPath)) {
        Write-Error "Impossible de determiner la branche de developpement a synchroniser : fichier sidecar absent ($metaPath) -- fournir -DevBranch explicitement, ou relancer setup_autofix_clone.ps1."
        exit 1
    }
    try {
        $meta = Get-Content -Path $metaPath -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
        $DevBranch = $meta.dev_branch
    } catch {
        Write-Error "Fichier sidecar illisible ($metaPath) : $_ -- fournir -DevBranch explicitement."
        exit 1
    }
    if (-not $DevBranch) {
        Write-Error "Fichier sidecar sans dev_branch exploitable ($metaPath) -- fournir -DevBranch explicitement."
        exit 1
    }
}
Write-Log "Depot : $CloneRepoRoot"
Write-Log "Branche d'integration : $IntegrationBranch  <-  Branche de developpement : $DevBranch"

# ---------------------------------------------------------------------------
# 1) REFUS si le verrou de l'orchestrateur est present et non perime
# ---------------------------------------------------------------------------

$lockStaleAfterS = 7200.0
$lockFilename = "orchestrator.lock"
if ((Test-Path $venvPython) -and (Test-Path $probeScript)) {
    try {
        $probeJson = & $venvPython $probeScript $ClonePackageRoot 2>$null
        $probe = $probeJson | ConvertFrom-Json -ErrorAction Stop
        if (-not $probe.error) {
            $lockStaleAfterS = [double]$probe.default_lock_stale_after_s
            $lockFilename = $probe.lock_filename
        }
    } catch {
        # Sonde indisponible : on continue avec les valeurs par defaut ci-dessus
        # (memes valeurs que Survey/autofix_orchestrator.py au moment de
        # l'ecriture de ce script) -- jamais bloquant pour CETTE resolution,
        # mais la verification du verrou elle-meme reste stricte plus bas.
        Write-Log "Sonde pipeline indisponible pour lire le seuil de peremption du verrou -- valeur par defaut ${lockStaleAfterS}s utilisee."
    }
}

$lockPath = Join-Path $ClonePackageRoot (Join-Path $PipelineRunsRelRoot $lockFilename)
if (Test-Path $lockPath) {
    try {
        $lockData = Get-Content -Path $lockPath -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
        $createdAt = [DateTimeOffset]::Parse($lockData.created_at)
        $ageS = ([DateTimeOffset]::UtcNow - $createdAt).TotalSeconds
        if ($ageS -lt $lockStaleAfterS) {
            Exit-Refused "verrou de l'orchestrateur present et RECENT ($lockPath, age=$([math]::Round($ageS))s < seuil ${lockStaleAfterS}s, pid=$($lockData.pid)) -- une invocation est probablement en cours."
        }
        Write-Log "Verrou present mais perime (age=$([math]::Round($ageS))s) -- sync autorise."
    } catch {
        # Age illisible/malforme : jamais considere perime (meme convention que
        # Survey/autofix_orchestrator.py::_lock_age_s) -- refus par prudence,
        # jamais une hypothese optimiste sur un etat qu'on ne peut pas lire.
        Exit-Refused "verrou de l'orchestrateur present mais illisible ($lockPath) : $_ -- verifier manuellement avant de relancer le sync."
    }
} else {
    Write-Log "Aucun verrou d'orchestrateur present."
}

# ---------------------------------------------------------------------------
# 2) REFUS si le clone n'est pas propre
# ---------------------------------------------------------------------------

$statusResult = Invoke-GitTimed -GitArgs @("status", "--porcelain", "--untracked-files=all") -WorkDir $CloneRepoRoot
if ($statusResult.TimedOut) {
    Write-Error "git status a depasse son budget (${GitTimeoutS}s)."
    exit 1
}
if (-not $statusResult.Ok) {
    Write-Error "git status a echoue : $($statusResult.StdErr)"
    exit 1
}
if ($statusResult.StdOut) {
    Exit-Refused "clone non propre (fichiers non suivis inclus) -- jamais de stash automatique. Details :`n$($statusResult.StdOut)"
}
Write-Log "Clone propre."

# ---------------------------------------------------------------------------
# 3) REFUS si la branche courante n'est pas la branche d'integration
# ---------------------------------------------------------------------------

$branchResult = Invoke-GitTimed -GitArgs @("symbolic-ref", "-q", "--short", "HEAD") -WorkDir $CloneRepoRoot
$currentBranch = if ($branchResult.Ok -and -not $branchResult.TimedOut) { $branchResult.StdOut } else { $null }
if (-not $currentBranch) {
    Exit-Refused "depot en detached HEAD -- attendu : '$IntegrationBranch'."
}
if ($currentBranch -ne $IntegrationBranch) {
    Exit-Refused "branche courante '$currentBranch' != branche d'integration attendue '$IntegrationBranch'."
}
Write-Log "Branche courante : '$currentBranch' (attendue)."

# ---------------------------------------------------------------------------
# 4) Fetch cible (une seule branche, jamais tout le remote) puis merge
# ---------------------------------------------------------------------------

$beforeHeadResult = Invoke-GitTimed -GitArgs @("rev-parse", "HEAD") -WorkDir $CloneRepoRoot
$beforeHead = if ($beforeHeadResult.Ok) { $beforeHeadResult.StdOut } else { "?" }

Write-Log "git fetch origin $DevBranch ..."
$fetchResult = Invoke-GitTimed -GitArgs @("fetch", "origin", $DevBranch) -WorkDir $CloneRepoRoot
if ($fetchResult.TimedOut) {
    Write-Error "git fetch origin $DevBranch a depasse son budget (${GitTimeoutS}s)."
    exit 1
}
if (-not $fetchResult.Ok) {
    Write-Error "git fetch origin $DevBranch a echoue : $($fetchResult.StdErr) -- la branche existe-t-elle encore dans le depot operateur ?"
    exit 1
}

Write-Log "git merge origin/$DevBranch ..."
$mergeResult = Invoke-GitTimed -GitArgs @("merge", "origin/$DevBranch") -WorkDir $CloneRepoRoot
if ($mergeResult.TimedOut) {
    Write-Error "git merge origin/$DevBranch a depasse son budget (${GitTimeoutS}s) -- verifier manuellement l'etat du clone ($CloneRepoRoot) avant de relancer."
    exit 1
}

if (-not $mergeResult.Ok) {
    # Conflit reel (ou tout autre echec) -- abandon PROPRE, jamais a moitie
    # resolu, jamais de resolution automatique.
    $abortResult = Invoke-GitTimed -GitArgs @("merge", "--abort") -WorkDir $CloneRepoRoot
    if ($abortResult.TimedOut -or -not $abortResult.Ok) {
        Write-Output "[AVERTISSEMENT] git merge --abort a lui-meme echoue -- verifier manuellement l'etat de $CloneRepoRoot avant toute nouvelle tentative : $($abortResult.StdErr)"
    }
    Write-Output "[CONFLIT] git merge origin/$DevBranch a echoue -- merge abandonne, resolution MANUELLE requise (jamais automatique) :"
    Write-Output ($mergeResult.StdErr + "`n" + $mergeResult.StdOut)
    exit 1
}

$afterHeadResult = Invoke-GitTimed -GitArgs @("rev-parse", "HEAD") -WorkDir $CloneRepoRoot
$afterHead = if ($afterHeadResult.Ok) { $afterHeadResult.StdOut } else { "?" }

Write-Output ""
Write-Output "=== sync_autofix_clone.ps1 termine ==="
if ($beforeHead -eq $afterHead) {
    Write-Output "  Deja a jour (aucun nouveau commit sur origin/$DevBranch) -- HEAD inchange ($beforeHead)."
} else {
    Write-Output "  '$IntegrationBranch' mis a jour : $beforeHead -> $afterHead"
    Write-Output "  $($mergeResult.StdOut)"
}
Write-Output "  Aucun push effectue (jamais fait par ce script)."
