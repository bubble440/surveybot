# setup_autofix_clone.ps1
#
# Partie A du chantier "clone dedie a l'orchestrateur autofix" (cf.
# Utils/AUTOFIX_CLONE_ORCHESTRATEUR.md et Utils/SURVEYBOT_AUTOFIX_PLAN.md, suites
# 35-37). Cree, si necessaire, un clone Git separe du depot de l'operateur, dedie
# a l'execution de tools\run_autofix_pipeline.py, et prepare son environnement
# d'execution (venv Python, dependances, navigateurs Playwright, ruff). Ne lance
# JAMAIS l'orchestrateur lui-meme, ne pousse rien, n'ecrit jamais de secret.
#
# IDEMPOTENT : relancable sans dommage. Ne recree jamais un clone deja present,
# ne reinitialise jamais une branche d'integration deja existante (elle peut deja
# porter des correctifs merges) -- se contente de (re)verifier/(re)installer ce
# qui manque.
#
# Pourquoi un clone SEPARE, HORS de l'arbre du depot de l'operateur (decision deja
# actee, documentee ici, pas rediscutee) :
#   Survey/autofix/merge_executor.py (etape aval de l'orchestrateur) exige que le depot
#   PRINCIPAL soit ENTIEREMENT propre (git status --porcelain --untracked-files=all
#   vide) avant de basculer de branche pour y merger un correctif -- il refuse tant
#   qu'un fichier de travail de l'operateur traine. Faire tourner l'orchestrateur
#   dans le depot de l'operateur le bloquerait des qu'un developpement est en
#   cours, ET l'empecherait de merger sur une branche de developpement (jamais une
#   branche protegee, cf. Survey/autofix/autofix_worktree.py::PROTECTED_BRANCHES). D'ou un
#   clone dedie, en permanence sur sa propre branche d'integration NON protegee.
#
# Source du clone -- CHOIX DOCUMENTE (verifie, pas suppose) : ce script est lui-meme
# livre A L'INTERIEUR du depot de l'operateur (surveybot\tools\), donc le chemin
# local du depot operateur est TOUJOURS disponible au moment de l'execution --
# aucune verification de disponibilite reseau a faire pour l'obtenir. Clone en
# LOCAL depuis ce chemin (git clone <chemin-local> <destination>), jamais depuis
# l'URL "origin" distante (GitHub) : plus rapide (transport local, pas de reseau),
# et surtout permet a la Partie C (sync_autofix_clone.ps1) de recuperer par la
# suite les commits de la branche de developpement de l'operateur AVANT meme
# qu'ils soient pousses vers GitHub (le remote "origin" du clone pointe alors
# directement vers le depot de l'operateur sur le meme disque/reseau local,
# consequence normale de "git clone <chemin>"). Une seule strategie, pas de
# fallback vers l'URL distante.
#
# Usage :
#   .\setup_autofix_clone.ps1
#   .\setup_autofix_clone.ps1 -ClonePath "D:\surveybot-autofix-clone"
#   .\setup_autofix_clone.ps1 -IntegrationBranch "autofix-integration" -DevBranch "feature/phase-1a-observability"
#   .\setup_autofix_clone.ps1 -SkipPlaywrightInstall -SkipRuffInstall   # relance rapide, deps deja pretes
#
# Prerequis avant de lancer ce script :
#   - Git installe et sur le PATH.
#   - Python (3.11 recommande, cf. Dockerfile) installe et sur le PATH, ou fourni
#     via -SystemPythonExe.
#   - .gitignore du depot operateur DEJA a jour avec les racines d'artefacts du
#     pipeline autofix AVANT de creer le clone (ordre documente dans
#     Utils/AUTOFIX_CLONE_ORCHESTRATEUR.md) -- sinon le premier merge automatique
#     (Survey/autofix/merge_executor.py) echouera pour cause de depot non propre. Ce
#     script ne verifie PAS cette condition lui-meme (lecture seule sur ce point,
#     cf. preflight_autofix_clone.ps1 -- Partie B -- qui la verifie explicitement).

param(
    # Racine du PAQUET (le dossier contenant Survey\, tools\, requirements.txt --
    # dans ce depot, "surveybot\") du COTE OPERATEUR. Calcule par defaut a partir
    # de l'emplacement de ce script (un niveau au-dessus de tools\) -- jamais un
    # chemin code en dur.
    [string]$OperatorPackageRoot = (Split-Path -Parent $PSScriptRoot),

    # Chemin du clone dedie. Vide = calcule plus bas : frere du depot operateur,
    # nomme "<nom-du-depot>-autofix-clone".
    [string]$ClonePath = "",

    [string]$IntegrationBranch = "autofix-integration",

    # Branche de developpement source. Vide = branche courante du depot operateur
    # au moment de l'appel (detached HEAD sans -DevBranch fourni = erreur explicite,
    # jamais une supposition).
    [string]$DevBranch = "",

    # Chemin de l'interpreteur Python DANS LE CLONE, relatif a la racine du paquet
    # du clone. Calcule par defaut a partir de -VenvDirName (convention Windows
    # venv\Scripts\python.exe, meme convention que nssm_setup_bot.ps1
    # -PythonRelPath) -- parametrable pour un test hors Windows.
    [string]$VenvDirName = "venv",
    [string]$VenvPythonRelPath = "",

    [string]$RequirementsRelPath = "requirements.txt",

    # Interpreteur Python SYSTEME utilise pour CREER le venv (jamais suppose a un
    # chemin en dur -- resolu via le PATH, "python" par defaut, "python3" ou un
    # chemin absolu si l'environnement l'exige).
    [string]$SystemPythonExe = "python",

    [double]$GitTimeoutS = 300,

    [switch]$SkipPlaywrightInstall,
    [switch]$SkipRuffInstall,
    [switch]$SkipClaudeCheck
)

$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

function Write-Log {
    param([string]$Message)
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Output "[$ts] [SETUP_AUTOFIX_CLONE] $Message"
}

function Invoke-GitTimed {
    # Une seule strategie d'execution git dans ce script, avec budget de temps
    # explicite sur CHAQUE invocation (regle stricte du chantier) : Start-Process
    # + WaitForExit(ms) ; depassement = Stop-Process -Force, jamais une attente
    # indefinie. Sortie capturee via des fichiers temporaires (Start-Process ne
    # permet pas de capturer stdout/stderr directement en variable).
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

function Assert-GitOk {
    param([Parameter(Mandatory = $true)]$Result, [Parameter(Mandatory = $true)][string]$Context)
    if ($Result.TimedOut) {
        Write-Error "$Context : budget de temps depasse"
        exit 1
    }
    if (-not $Result.Ok) {
        Write-Error "$Context : $($Result.StdErr)"
        exit 1
    }
}

# ---------------------------------------------------------------------------
# 0) Resolution des chemins -- jamais de valeur codee en dur
# ---------------------------------------------------------------------------

if (-not (Test-Path $OperatorPackageRoot)) {
    Write-Error "OperatorPackageRoot introuvable : $OperatorPackageRoot"
    exit 1
}

$toplevelResult = Invoke-GitTimed -GitArgs @("rev-parse", "--show-toplevel") -WorkDir $OperatorPackageRoot
Assert-GitOk -Result $toplevelResult -Context "resolution du depot operateur depuis $OperatorPackageRoot"
$OperatorRepoRoot = $toplevelResult.StdOut

# Sous-chemin du paquet a l'interieur du depot (ex: "surveybot"), calcule par
# difference de chaines -- jamais un nom suppose ("surveybot" est une
# coincidence de ce depot precis, pas une regle) ; [System.IO.Path]::GetRelativePath
# volontairement evite (absent de .NET Framework 4.x / PowerShell 5.1).
$packageRelPath = $OperatorPackageRoot.Substring($OperatorRepoRoot.Length).TrimStart('\', '/')

Write-Log "Depot operateur : $OperatorRepoRoot"
Write-Log "Paquet (Survey\, tools\, requirements.txt) : $packageRelPath"

if (-not $ClonePath) {
    $repoParent = Split-Path -Parent $OperatorRepoRoot
    $repoName = Split-Path -Leaf $OperatorRepoRoot
    $ClonePath = Join-Path $repoParent "$repoName-autofix-clone"
}
Write-Log "Clone dedie : $ClonePath"

if (-not $DevBranch) {
    $branchResult = Invoke-GitTimed -GitArgs @("symbolic-ref", "-q", "--short", "HEAD") -WorkDir $OperatorRepoRoot
    if ($branchResult.TimedOut -or -not $branchResult.Ok -or -not $branchResult.StdOut) {
        Write-Error "Impossible de determiner la branche de developpement courante (depot operateur en detached HEAD ?) -- fournir -DevBranch explicitement."
        exit 1
    }
    $DevBranch = $branchResult.StdOut
}
Write-Log "Branche de developpement source : $DevBranch"
Write-Log "Branche d'integration cible : $IntegrationBranch"

if (-not $VenvPythonRelPath) {
    $VenvPythonRelPath = Join-Path $VenvDirName (Join-Path "Scripts" "python.exe")
}

# ---------------------------------------------------------------------------
# 1) Clone -- idempotent, jamais d'ecrasement d'un clone existant
# ---------------------------------------------------------------------------

$cloneGitDir = Join-Path $ClonePath ".git"

if (Test-Path $cloneGitDir) {
    Write-Log "Clone deja present : $ClonePath -- pas de nouveau clone (idempotent)."
    $originResult = Invoke-GitTimed -GitArgs @("remote", "get-url", "origin") -WorkDir $ClonePath
    if ($originResult.Ok) {
        $resolvedOperator = (Resolve-Path $OperatorRepoRoot).Path
        $resolvedOrigin = $null
        try { $resolvedOrigin = (Resolve-Path $originResult.StdOut -ErrorAction Stop).Path } catch { $resolvedOrigin = $originResult.StdOut }
        if ($resolvedOrigin -ne $resolvedOperator) {
            Write-Warning "Le remote 'origin' du clone ($resolvedOrigin) ne correspond pas au depot operateur attendu ($resolvedOperator) -- verifier manuellement ; ce script ne modifie jamais un remote existant."
        }
    } else {
        Write-Warning "Impossible de lire le remote 'origin' du clone existant : $($originResult.StdErr)"
    }
} elseif (Test-Path $ClonePath) {
    Write-Error "ClonePath existe deja mais ne ressemble pas a un depot Git (pas de .git) : $ClonePath -- abandon, jamais d'ecrasement d'un dossier existant non identifie."
    exit 1
} else {
    Write-Log "Creation du clone (source locale, cf. en-tete de ce script) : git clone `"$OperatorRepoRoot`" `"$ClonePath`""
    # WorkDir = OperatorRepoRoot (garanti existant) -- source et destination sont
    # deja des chemins resolus passes en argument, le repertoire de travail de la
    # commande elle-meme n'a pas besoin d'etre celui du futur clone.
    $cloneResult = Invoke-GitTimed -GitArgs @("clone", $OperatorRepoRoot, $ClonePath) -WorkDir $OperatorRepoRoot
    Assert-GitOk -Result $cloneResult -Context "git clone"
    Write-Log "Clone cree."
}

$ClonePackageRoot = Join-Path $ClonePath $packageRelPath
if (-not (Test-Path $ClonePackageRoot)) {
    Write-Error "Paquet introuvable dans le clone a l'emplacement attendu : $ClonePackageRoot"
    exit 1
}

# ---------------------------------------------------------------------------
# 2) Branche d'integration -- idempotent, JAMAIS reinitialisee si deja presente
#    (elle peut deja porter des correctifs merges par une invocation precedente
#    de l'orchestrateur -- la resynchronisation avec la branche de developpement
#    est le role de sync_autofix_clone.ps1, Partie C, pas de ce script).
# ---------------------------------------------------------------------------

$fetchResult = Invoke-GitTimed -GitArgs @("fetch", "origin", "--quiet") -WorkDir $ClonePath
Assert-GitOk -Result $fetchResult -Context "git fetch origin (clone)"

$branchExistsResult = Invoke-GitTimed -GitArgs @("show-ref", "--verify", "--quiet", "refs/heads/$IntegrationBranch") -WorkDir $ClonePath

if (-not $branchExistsResult.TimedOut -and $branchExistsResult.Ok) {
    Write-Log "Branche d'integration '$IntegrationBranch' deja presente dans le clone -- jamais recreee ni reinitialisee."
    $currentBranchResult = Invoke-GitTimed -GitArgs @("symbolic-ref", "-q", "--short", "HEAD") -WorkDir $ClonePath
    if ($currentBranchResult.Ok -and $currentBranchResult.StdOut -ne $IntegrationBranch) {
        $checkoutResult = Invoke-GitTimed -GitArgs @("checkout", $IntegrationBranch) -WorkDir $ClonePath
        Assert-GitOk -Result $checkoutResult -Context "checkout de la branche d'integration existante"
        Write-Log "Bascule sur '$IntegrationBranch'."
    } else {
        Write-Log "Deja sur '$IntegrationBranch'."
    }
} else {
    $devRefResult = Invoke-GitTimed -GitArgs @("show-ref", "--verify", "--quiet", "refs/remotes/origin/$DevBranch") -WorkDir $ClonePath
    if ($devRefResult.TimedOut -or -not $devRefResult.Ok) {
        Write-Error "origin/$DevBranch introuvable dans le clone -- verifier -DevBranch ('$DevBranch' existe-t-elle dans le depot operateur ?)."
        exit 1
    }
    Write-Log "Creation de la branche d'integration '$IntegrationBranch' depuis origin/$DevBranch."
    $createResult = Invoke-GitTimed -GitArgs @("checkout", "-b", $IntegrationBranch, "origin/$DevBranch") -WorkDir $ClonePath
    Assert-GitOk -Result $createResult -Context "creation de la branche d'integration"
}

# ---------------------------------------------------------------------------
# 3) Environnement Python -- venv + dependances (idempotent : re-executable sans
#    dommage, met a jour les dependances si requirements.txt a change).
# ---------------------------------------------------------------------------

$venvPython = Join-Path $ClonePackageRoot $VenvPythonRelPath

if (Test-Path $venvPython) {
    Write-Log "venv deja present : $venvPython"
} else {
    $venvDir = Join-Path $ClonePackageRoot $VenvDirName
    Write-Log "Creation du venv : $venvDir"
    if (-not (Get-Command $SystemPythonExe -ErrorAction SilentlyContinue)) {
        Write-Error "Interpreteur Python systeme introuvable sur le PATH : $SystemPythonExe -- fournir -SystemPythonExe."
        exit 1
    }
    & $SystemPythonExe -m venv $venvDir
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $venvPython)) {
        Write-Error "Echec de creation du venv (python -m venv) -- $venvPython introuvable apres coup."
        exit 1
    }
    Write-Log "venv cree."
}

$requirementsPath = Join-Path $ClonePackageRoot $RequirementsRelPath
if (-not (Test-Path $requirementsPath)) {
    Write-Error "requirements.txt introuvable dans le clone : $requirementsPath"
    exit 1
}

Write-Log "Installation/mise a jour des dependances depuis $requirementsPath ..."
& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { Write-Error "echec 'pip install --upgrade pip'"; exit 1 }
& $venvPython -m pip install -r $requirementsPath
if ($LASTEXITCODE -ne 0) { Write-Error "echec 'pip install -r requirements.txt'"; exit 1 }
Write-Log "Dependances du projet OK."

Write-Log "Installation/mise a jour de pytest (tests associes de la Phase 8) ..."
& $venvPython -m pip install --upgrade pytest
if ($LASTEXITCODE -ne 0) { Write-Error "echec 'pip install pytest'"; exit 1 }
Write-Log "pytest installe dans le venv."

# ruff : DELIBEREMENT absent de requirements.txt (investigation deja menee et
# documentee dans Survey/autofix/static_validator.py -- Phase 8 le resout via
# shutil.which("ruff"), jamais via une dependance pip declaree). Installe ici a
# part, dans le meme venv.
if ($SkipRuffInstall) {
    Write-Log "Installation de ruff sautee (-SkipRuffInstall)."
} else {
    Write-Log "Installation/mise a jour de ruff (utilise par la Phase 8, Survey/autofix/static_validator.py) ..."
    & $venvPython -m pip install --upgrade ruff
    if ($LASTEXITCODE -ne 0) { Write-Error "echec 'pip install ruff'"; exit 1 }
    Write-Log "ruff installe dans le venv."
}

# Playwright : navigateurs (Chromium seul necessaire -- cf. Survey/autofix/replay_browser.py
# ::IsolatedReplayBrowser, seul module de ce pipeline a lancer un navigateur reel).
if ($SkipPlaywrightInstall) {
    Write-Log "Installation des navigateurs Playwright sautee (-SkipPlaywrightInstall)."
} else {
    Write-Log "Installation du navigateur Chromium (Playwright) ..."
    & $venvPython -m playwright install chromium
    if ($LASTEXITCODE -ne 0) { Write-Error "echec 'playwright install chromium'"; exit 1 }
    Write-Log "Chromium (Playwright) installe."
}

# ---------------------------------------------------------------------------
# 4) Verification (non bloquante) du binaire claude et de son authentification
# ---------------------------------------------------------------------------

if ($SkipClaudeCheck) {
    Write-Log "Verification du binaire claude sautee (-SkipClaudeCheck)."
} else {
    $claudeCmd = Get-Command claude -ErrorAction SilentlyContinue
    if (-not $claudeCmd) {
        Write-Warning "Binaire 'claude' introuvable sur le PATH -- installer Claude Code avant la premiere invocation de tools\run_autofix_pipeline.py depuis ce clone."
    } else {
        Write-Log "Binaire claude trouve : $($claudeCmd.Source)"
        try {
            $authJson = & claude auth status --json 2>$null
            $authObj = $authJson | ConvertFrom-Json -ErrorAction Stop
            if ($authObj.loggedIn -eq $true) {
                Write-Log "claude authentifie (methode : $($authObj.authMethod))."
            } else {
                Write-Warning "claude est installe mais NON authentifie -- executer 'claude auth login' manuellement avant la premiere invocation du pipeline (jamais automatise par ce script)."
            }
        } catch {
            Write-Warning "Impossible de determiner l'etat d'authentification de claude ('claude auth status --json' a echoue) : $_"
        }
    }
}

# ---------------------------------------------------------------------------
# 5) Resume -- rappel du piege PATH/ruff verifie empiriquement (Partie A du
#    chantier) : shutil.which("ruff"), utilise par Survey/autofix/static_validator.py
#    (Phase 8), resout par rapport au PATH du PROCESSUS qui execute
#    tools\run_autofix_pipeline.py -- appeler directement venv\Scripts\python.exe
#    (sans "activer" le venv) ne prefixe PAS automatiquement venv\Scripts au
#    PATH de ce processus. Verifie reellement (voir Utils/AUTOFIX_CLONE_ORCHESTRATEUR.md) :
#    ruff installe UNIQUEMENT dans le venv reste introuvable pour ce processus si
#    son PATH n'est pas prefixe de venv\Scripts au moment du lancement. La tache
#    planifiee (Partie D, schedule_autofix_pipeline_task.ps1) applique ce prefixe
#    systematiquement pour son unique mecanisme de lancement ; un lancement
#    manuel doit faire de meme.
# ---------------------------------------------------------------------------

Write-Output ""
Write-Output "=== setup_autofix_clone.ps1 termine ==="
Write-Output "  Clone                : $ClonePath"
Write-Output "  Paquet (Survey/tools): $ClonePackageRoot"
Write-Output "  Branche d'integration: $IntegrationBranch (source : origin/$DevBranch)"
Write-Output "  venv Python          : $venvPython"
Write-Output ""
Write-Output "IMPORTANT (verifie empiriquement) avant tout lancement, manuel ou planifie, de"
Write-Output "tools\run_autofix_pipeline.py depuis ce clone : prefixer le dossier Scripts du"
Write-Output "venv au PATH de la session, sinon Survey/autofix/static_validator.py (Phase 8) ne"
Write-Output "trouvera pas ruff (shutil.which echoue silencieusement, cote Python, a une"
Write-Output "verification -- jamais a une hypothese optimiste) :"
$venvScriptsDir = Split-Path -Parent $venvPython
Write-Output "  `$env:PATH = `"$venvScriptsDir;`$env:PATH`""
Write-Output ""
Write-Output "Executer preflight_autofix_clone.ps1 (Partie B) avant la premiere invocation"
Write-Output "reelle du pipeline, et avant chaque planification."

# ---------------------------------------------------------------------------
# 6) Fichier sidecar (jamais dans l'arbre suivi par Git du clone -- un frere du
#    dossier du clone, jamais un fichier a l'interieur, pour ne jamais fausser
#    le critere de proprete de Survey/autofix/merge_executor.py) : memorise quelle
#    branche de developpement ce clone suit, pour que sync_autofix_clone.ps1
#    (Partie C) n'ait pas a la redeviner -- jamais une hypothese fragile sur
#    "quelle que soit la branche courante du depot operateur au moment du sync"
#    (qui peut avoir change depuis). Reecrit a chaque execution de CE script
#    (simple cache/pointeur, jamais un historique Git a preserver).
# ---------------------------------------------------------------------------

$metaPath = Join-Path (Split-Path -Parent $ClonePath) "$(Split-Path -Leaf $ClonePath).autofix-clone-meta.json"
$meta = [PSCustomObject]@{
    schema_version      = "1.0"
    clone_path          = $ClonePath
    integration_branch  = $IntegrationBranch
    dev_branch          = $DevBranch
    updated_at          = (Get-Date).ToUniversalTime().ToString("o")
}
# [System.IO.File]::WriteAllText avec un UTF8Encoding sans BOM -- Set-Content
# -Encoding UTF8 sous PowerShell 5.1 ecrit un BOM, jamais souhaite ici (convention
# deja etablie dans ce depot, cf. build_orchestration_release.ps1).
$utf8NoBom = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($metaPath, ($meta | ConvertTo-Json), $utf8NoBom)
Write-Log "Metadonnees sync (branche de developpement suivie) ecrites : $metaPath"
