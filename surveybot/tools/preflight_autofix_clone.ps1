# preflight_autofix_clone.ps1
#
# Partie B du chantier "clone dedie a l'orchestrateur autofix" (cf.
# Utils/AUTOFIX_CLONE_ORCHESTRATEUR.md). LECTURE SEULE : verifie et rapporte,
# point par point, l'etat du clone AVANT une invocation de
# tools\run_autofix_pipeline.py -- ne modifie JAMAIS rien (aucun git checkout,
# aucun fetch, aucune ecriture). A executer avant chaque planification et a la
# demande.
#
# Convention identique aux scripts d'orchestration existants de ce depot
# (wake_scheduler.ps1, check_zombie_bots.ps1) : chemins par defaut calcules a
# partir de $PSScriptRoot -- ce script est cense vivre DANS le clone lui-meme
# (il y arrive naturellement, copie par le clone Git, cf. setup_autofix_clone.ps1)
# et verifie donc, par defaut, l'environnement qui l'entoure. Un usage explicite
# depuis un autre emplacement reste possible via -ClonePackageRoot.
#
# Code de sortie : 0 si tous les points bloquants sont OK, 1 sinon -- la raison
# exacte de chaque echec est imprimee, jamais seulement un code silencieux.
#
# Usage :
#   .\preflight_autofix_clone.ps1
#   .\preflight_autofix_clone.ps1 -ExpectFleetImport   # verifie aussi FLEET_R2_*
#   .\preflight_autofix_clone.ps1 -ClonePackageRoot "D:\surveybot-autofix-clone\surveybot"

param(
    [string]$ClonePackageRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$IntegrationBranch = "autofix-integration",
    [string]$VenvPythonRelPath = "",
    [string]$VenvDirName = "venv",
    [string]$PipelineRunsRelRoot = "autofix_pipeline_runs",
    [double]$GitTimeoutS = 15,
    [switch]$ExpectFleetImport
)

# Pas de Set-StrictMode / $ErrorActionPreference = "Stop" ici : un preflight
# doit accumuler TOUS les points bloquants avant de conclure (jamais s'arreter
# a la premiere erreur rencontree), meme principe que les phases Python de ce
# chantier (cf. Survey/autofix/autofix_worktree.py::check_eligibility).

if (-not $VenvPythonRelPath) {
    $VenvPythonRelPath = Join-Path $VenvDirName (Join-Path "Scripts" "python.exe")
}

$script:results = @()

function Add-Result {
    param([string]$Name, [bool]$Ok, [string]$Detail, [bool]$Blocking = $true)
    $script:results += [PSCustomObject]@{ Name = $Name; Ok = $Ok; Detail = $Detail; Blocking = $Blocking }
}

function Invoke-GitTimed {
    # Meme mecanisme que setup_autofix_clone.ps1 (duplique volontairement --
    # convention deja etablie dans ce depot de dupliquer les petits helpers
    # plutot que de partager un module entre scripts independants).
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
        # Windows PowerShell 5.1 : sans lecture de .Handle juste apres Start-Process,
        # ExitCode peut valoir $null une fois le processus termine (handle non mis en
        # cache) -- un git reussi etait alors pris pour un echec, sans aucun message.
        $null = $p.Handle
        $finished = $p.WaitForExit([int]($TimeoutS * 1000))
        if (-not $finished) {
            try { Stop-Process -Id $p.Id -Force -ErrorAction Stop } catch {}
            return [PSCustomObject]@{ Ok = $false; TimedOut = $true; ExitCode = $null; StdOut = ""; StdErr = "budget ${TimeoutS}s depasse" }
        }
        # Le processus a termine dans le budget : cet appel sans delai finalise la
        # lecture des flux rediriges et l'etat du code de sortie.
        $p.WaitForExit()
        $exitCode = $p.ExitCode
        $stdout = Get-Content -Path $stdoutFile -Raw -ErrorAction SilentlyContinue
        $stderr = Get-Content -Path $stderrFile -Raw -ErrorAction SilentlyContinue
        $stdoutText = $(if ($stdout) { $stdout.Trim() } else { "" })
        $stderrText = $(if ($stderr) { $stderr.Trim() } else { "" })
        if ($null -eq $exitCode) {
            # Jamais un echec sans message : un code de sortie illisible doit se voir.
            return [PSCustomObject]@{ Ok = $false; TimedOut = $false; ExitCode = $null; StdOut = $stdoutText; StdErr = ("code de sortie de git illisible (ExitCode null) " + $stderrText).Trim() }
        }
        return [PSCustomObject]@{
            Ok = ($exitCode -eq 0); TimedOut = $false; ExitCode = $exitCode
            StdOut = $stdoutText
            StdErr = $stderrText
        }
    } finally {
        Remove-Item -Path $stdoutFile, $stderrFile -Force -ErrorAction SilentlyContinue
    }
}

if (-not (Test-Path $ClonePackageRoot)) {
    Write-Output "[BLOQUANT] ClonePackageRoot introuvable : $ClonePackageRoot"
    exit 1
}

$toplevelResult = Invoke-GitTimed -GitArgs @("rev-parse", "--show-toplevel") -WorkDir $ClonePackageRoot
if ($toplevelResult.TimedOut -or -not $toplevelResult.Ok) {
    Add-Result -Name "depot Git" -Ok $false -Detail "impossible de resoudre le depot depuis $ClonePackageRoot : $($toplevelResult.StdErr)"
    # Bloquant et structurant : sans depot resolu, aucun autre point Git n'est
    # verifiable de facon fiable -- seul cas ou ce script s'arrete tot, avec
    # un rapport explicite plutot qu'une cascade d'erreurs derivees.
    $CloneRepoRoot = $null
} else {
    $CloneRepoRoot = $toplevelResult.StdOut
    Add-Result -Name "depot Git" -Ok $true -Detail $CloneRepoRoot
}

$venvPython = Join-Path $ClonePackageRoot $VenvPythonRelPath
$probeScript = Join-Path $ClonePackageRoot (Join-Path "tools" "introspect_autofix_pipeline.py")

# -- Sonde unique (Survey/autofix/autofix_worktree.py::PROTECTED_BRANCHES, --
#    Survey/autofix/autofix_orchestrator.py::LOCK_FILENAME/DEFAULT_LOCK_STALE_AFTER_S,
#    racines d'artefacts derivees de tools/*.py) -- jamais une valeur recopiee a
#    la main dans ce script PowerShell (cf. tools/introspect_autofix_pipeline.py).
$probe = $null
if (-not (Test-Path $venvPython)) {
    Add-Result -Name "interpreteur Python (venv)" -Ok $false -Detail "introuvable : $venvPython"
} elseif (-not (Test-Path $probeScript)) {
    Add-Result -Name "sonde introspect_autofix_pipeline.py" -Ok $false -Detail "introuvable : $probeScript"
} else {
    try {
        $probeJson = & $venvPython $probeScript $ClonePackageRoot 2>$null
        $probe = $probeJson | ConvertFrom-Json -ErrorAction Stop
        if ($probe.error) {
            Add-Result -Name "sonde pipeline (import Survey)" -Ok $false -Detail $probe.error
            $probe = $null
        } else {
            Add-Result -Name "sonde pipeline (import Survey)" -Ok $true -Detail "PROTECTED_BRANCHES/verrou/racines derives avec succes"
        }
    } catch {
        Add-Result -Name "sonde pipeline (import Survey)" -Ok $false -Detail "$_"
    }
}

# -- 1) Clone propre, EXACTEMENT le critere de Survey/autofix/merge_executor.py --
if ($CloneRepoRoot) {
    $statusResult = Invoke-GitTimed -GitArgs @("status", "--porcelain", "--untracked-files=all") -WorkDir $CloneRepoRoot
    if ($statusResult.TimedOut) {
        Add-Result -Name "depot propre" -Ok $false -Detail "git status a depasse son budget (${GitTimeoutS}s)"
    } elseif (-not $statusResult.Ok) {
        Add-Result -Name "depot propre" -Ok $false -Detail "git status a echoue : $($statusResult.StdErr)"
    } elseif ($statusResult.StdOut) {
        $preview = ($statusResult.StdOut -split "`n" | Select-Object -First 10) -join "; "
        Add-Result -Name "depot propre" -Ok $false -Detail "changements en attente (fichiers non suivis inclus) -- Survey/autofix/merge_executor.py refusera tout merge : $preview"
    } else {
        Add-Result -Name "depot propre" -Ok $true -Detail "git status --porcelain --untracked-files=all vide"
    }
}

# -- 2) Branche courante = branche d'integration, non protegee --
if ($CloneRepoRoot) {
    $branchResult = Invoke-GitTimed -GitArgs @("symbolic-ref", "-q", "--short", "HEAD") -WorkDir $CloneRepoRoot
    $currentBranch = if ($branchResult.Ok -and -not $branchResult.TimedOut) { $branchResult.StdOut } else { $null }
    if (-not $currentBranch) {
        Add-Result -Name "branche courante" -Ok $false -Detail "depot en detached HEAD -- attendu : '$IntegrationBranch'"
    } elseif ($currentBranch -ne $IntegrationBranch) {
        Add-Result -Name "branche courante" -Ok $false -Detail "branche courante '$currentBranch' != branche d'integration attendue '$IntegrationBranch'"
    } elseif ($probe -and ($probe.protected_branches -contains $currentBranch)) {
        Add-Result -Name "branche courante" -Ok $false -Detail "'$currentBranch' est une branche protegee (PROTECTED_BRANCHES) -- ne devrait jamais servir de branche d'integration"
    } elseif (-not $probe) {
        Add-Result -Name "branche courante" -Ok $false -Detail "branche courante = '$currentBranch' (attendu), mais non verifiee contre PROTECTED_BRANCHES (sonde pipeline indisponible, cf. point ci-dessus)"
    } else {
        Add-Result -Name "branche courante" -Ok $true -Detail "'$currentBranch', non protegee"
    }
}

# -- 3) Racines d'artefacts par defaut : chacune ignoree par Git --
# IMPORTANT (verifie empiriquement) : `git check-ignore` sur un chemin qui
# n'existe pas encore sur disque ne matche un motif .gitignore reserve aux
# repertoires (suffixe "/") QUE si le chemin interroge porte lui-meme le
# suffixe "/" -- sans lui, un faux "non ignore" est rapporte meme quand le
# motif existe reellement. D'ou le "$root/" ci-dessous, jamais juste "$root".
if ($CloneRepoRoot -and $probe -and $probe.artifact_roots) {
    $missing = @()
    foreach ($root in $probe.artifact_roots) {
        $checkResult = Invoke-GitTimed -GitArgs @("check-ignore", "-q", "$root/") -WorkDir $ClonePackageRoot
        if ($checkResult.TimedOut -or $checkResult.ExitCode -ne 0) {
            $missing += $root
        }
    }
    if ($missing.Count -gt 0) {
        Add-Result -Name "racines d'artefacts ignorees par Git" -Ok $false -Detail "non couvertes par .gitignore : $($missing -join ', ') -- Survey/autofix/merge_executor.py refusera tout merge des qu'un case y sera ecrit"
    } else {
        Add-Result -Name "racines d'artefacts ignorees par Git" -Ok $true -Detail "$($probe.artifact_roots.Count) racine(s) verifiee(s) (derivees de tools/*.py)"
    }
} elseif ($CloneRepoRoot) {
    Add-Result -Name "racines d'artefacts ignorees par Git" -Ok $false -Detail "liste des racines indisponible (sonde pipeline en echec, cf. point ci-dessus)"
}

# -- 4) Variables d'environnement requises PRESENTES -- jamais leur valeur --
foreach ($varName in @("telegram_bot_token", "telegram_chat_id")) {
    $val = [Environment]::GetEnvironmentVariable($varName)
    if ([string]::IsNullOrWhiteSpace($val)) {
        Add-Result -Name "variable d'environnement $varName" -Ok $false -Detail "absente ou vide -- requise par Survey/autofix/human_review.py (Phase 13/notifications)"
    } else {
        Add-Result -Name "variable d'environnement $varName" -Ok $true -Detail "presente (valeur non affichee)"
    }
}
if ($ExpectFleetImport) {
    foreach ($varName in @("FLEET_R2_ACCOUNT_ID", "FLEET_R2_ACCESS_KEY_ID", "FLEET_R2_SECRET_ACCESS_KEY", "FLEET_R2_BUCKET")) {
        $val = [Environment]::GetEnvironmentVariable($varName)
        if ([string]::IsNullOrWhiteSpace($val)) {
            Add-Result -Name "variable d'environnement $varName" -Ok $false -Detail "absente ou vide -- requise par Survey/autofix/fleet_case_import.py (--import-fleet demande via -ExpectFleetImport)"
        } else {
            Add-Result -Name "variable d'environnement $varName" -Ok $true -Detail "presente (valeur non affichee)"
        }
    }
} else {
    Add-Result -Name "variables FLEET_R2_*" -Ok $true -Detail "non verifiees (-ExpectFleetImport non fourni -- import fleet non prevu pour cette invocation)" -Blocking $false
}

# -- 5) Binaire claude present et authentifie --
$claudeCmd = Get-Command claude -ErrorAction SilentlyContinue
if (-not $claudeCmd) {
    Add-Result -Name "binaire claude" -Ok $false -Detail "introuvable sur le PATH"
} else {
    try {
        $authJson = & claude auth status --json 2>$null
        $authObj = $authJson | ConvertFrom-Json -ErrorAction Stop
        if ($authObj.loggedIn -eq $true) {
            Add-Result -Name "binaire claude" -Ok $true -Detail "present et authentifie ($($authObj.authMethod))"
        } else {
            Add-Result -Name "binaire claude" -Ok $false -Detail "present mais NON authentifie -- executer 'claude auth login'"
        }
    } catch {
        Add-Result -Name "binaire claude" -Ok $false -Detail "'claude auth status --json' a echoue : $_"
    }
}

# -- 6) Interpreteur Python et dependances OK --
if (-not (Test-Path $venvPython)) {
    Add-Result -Name "dependances Python (venv)" -Ok $false -Detail "venv introuvable : $venvPython -- executer setup_autofix_clone.ps1"
} else {
    & $venvPython -m pip check *> $null
    $pipCheckOk = ($LASTEXITCODE -eq 0)
    $orchestratorModule = Join-Path $ClonePackageRoot "Survey/autofix/autofix_orchestrator.py"
    $importOk = $false
    $importDetail = ""
    if (Test-Path $orchestratorModule) {
        # Push-Location : "import Survey...." ne resout que par rapport au
        # repertoire courant du PROCESSUS python (ajoute a sys.path[0] par
        # "-c", jamais suppose) -- doit etre ClonePackageRoot, exactement comme
        # une vraie invocation de tools\run_autofix_pipeline.py (verifie
        # empiriquement : sans ce Push-Location, l'import echoue si ce script
        # est lance depuis un autre repertoire).
        Push-Location $ClonePackageRoot
        try {
            $importOut = & $venvPython -c "import Survey.autofix.autofix_orchestrator" 2>&1
            $importOk = ($LASTEXITCODE -eq 0)
        } finally {
            Pop-Location
        }
        if (-not $importOk) { $importDetail = ($importOut -join " ") }
    } else {
        $importDetail = "Survey/autofix/autofix_orchestrator.py introuvable dans le clone"
    }
    if ($pipCheckOk -and $importOk) {
        Add-Result -Name "dependances Python (venv)" -Ok $true -Detail "pip check OK, import Survey.autofix.autofix_orchestrator OK"
    } else {
        $reasons = @()
        if (-not $pipCheckOk) { $reasons += "pip check a signale un probleme de dependances" }
        if (-not $importOk) { $reasons += "import Survey.autofix.autofix_orchestrator a echoue : $importDetail" }
        Add-Result -Name "dependances Python (venv)" -Ok $false -Detail ($reasons -join " ; ")
    }
}

# -- 7) Repertoire des worktrees HORS de l'arbre suivi par Git --
# La localisation REELLE du worktree Git (par opposition a l'artefact de
# tracabilite worktree.json, ecrit sous --worktrees-root="autofix_worktrees",
# lui gitignore -- deux notions distinctes malgre le nom partage, verifie dans
# Survey/autofix/autofix_orchestrator.py/Survey/autofix/autofix_worktree.py) est calculee par
# Survey/autofix/autofix_worktree.py::_default_worktrees_root, jamais parametree par
# l'orchestrateur : <parent du depot>/<nom du depot>-worktrees, un frere du
# depot, donc structurellement hors de son arbre suivi.
if ($CloneRepoRoot) {
    $repoParent = Split-Path -Parent $CloneRepoRoot
    $repoName = Split-Path -Leaf $CloneRepoRoot
    $expectedWorktreesDir = Join-Path $repoParent "$repoName-worktrees"
    $normalizedRepo = (Resolve-Path $CloneRepoRoot).Path.TrimEnd('\', '/')
    $normalizedWorktrees = [System.IO.Path]::GetFullPath((Join-Path $repoParent "$repoName-worktrees")).TrimEnd('\', '/')
    if ($normalizedWorktrees.StartsWith($normalizedRepo + [System.IO.Path]::DirectorySeparatorChar) -or $normalizedWorktrees -eq $normalizedRepo) {
        Add-Result -Name "repertoire des worktrees hors de l'arbre suivi" -Ok $false -Detail "$expectedWorktreesDir semble imbrique dans le depot ($CloneRepoRoot)"
    } else {
        Add-Result -Name "repertoire des worktrees hors de l'arbre suivi" -Ok $true -Detail "$expectedWorktreesDir (frere du depot, par construction)"
    }
}

# -- 8) Verrou de l'orchestrateur --
$lockStaleAfterS = if ($probe) { [double]$probe.default_lock_stale_after_s } else { 7200.0 }
$lockFilename = if ($probe) { $probe.lock_filename } else { "orchestrator.lock" }
$pipelineRunsDir = Join-Path $ClonePackageRoot $PipelineRunsRelRoot
$lockPath = Join-Path $pipelineRunsDir $lockFilename

if (-not (Test-Path $lockPath)) {
    Add-Result -Name "verrou orchestrateur" -Ok $true -Detail "absent ($lockPath) -- aucune invocation en cours"
} else {
    try {
        $lockData = Get-Content -Path $lockPath -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
        $createdAt = [DateTimeOffset]::Parse($lockData.created_at)
        $ageS = ([DateTimeOffset]::UtcNow - $createdAt).TotalSeconds
        if ($ageS -lt $lockStaleAfterS) {
            Add-Result -Name "verrou orchestrateur" -Ok $false -Detail "present et RECENT (age=$([math]::Round($ageS))s < seuil de peremption ${lockStaleAfterS}s, pid=$($lockData.pid)) -- une invocation est probablement en cours"
        } else {
            Add-Result -Name "verrou orchestrateur" -Ok $true -Detail "present mais PERIME (age=$([math]::Round($ageS))s >= seuil ${lockStaleAfterS}s, pid=$($lockData.pid)) -- sera repris automatiquement par la prochaine invocation, non bloquant" -Blocking $false
        }
    } catch {
        Add-Result -Name "verrou orchestrateur" -Ok $false -Detail "present mais illisible ($lockPath) : $_ -- verifier manuellement avant de lancer une invocation"
    }
}

# ---------------------------------------------------------------------------
# Rapport final
# ---------------------------------------------------------------------------

Write-Output ""
Write-Output "=== preflight_autofix_clone.ps1 -- $ClonePackageRoot ==="
$anyBlockingFailure = $false
foreach ($r in $script:results) {
    $tag = if ($r.Ok) { "[OK]" } elseif ($r.Blocking) { "[BLOQUANT]" } else { "[AVERTISSEMENT]" }
    Write-Output "$tag $($r.Name) -- $($r.Detail)"
    if (-not $r.Ok -and $r.Blocking) { $anyBlockingFailure = $true }
}
Write-Output ""

if ($anyBlockingFailure) {
    Write-Output "RESULTAT : au moins un point bloquant a echoue -- ne pas lancer tools\run_autofix_pipeline.py depuis ce clone avant correction."
    exit 1
} else {
    Write-Output "RESULTAT : tous les points bloquants sont OK."
    exit 0
}
