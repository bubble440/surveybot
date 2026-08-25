# launch_all.ps1
# Lancement des bots depuis accounts.json, en session Windows interactive normale
# (compositeur DWM actif, GPU reel) - PAS via NSSM (Session 0, sans DWM, invisible en
# RDP). Cf. Utils/ORCHESTRATION_TRACKING.md section 9 pour l'historique et section 18
# pour la decision de bascule NSSM -> PID/launch_all.ps1.
#
# Deux usages :
#   - Tache planifiee au logon de l'operateur (-AccountId omis) : lance TOUS les
#     comptes de accounts.json. C'est le mecanisme de demarrage de parc.
#   - Manuel et ponctuel (-AccountId fourni) : un seul compte, ex. test isole ou
#     relance ciblee. Utilise aussi par check_zombie_bots.ps1/wake_scheduler.ps1 (via
#     un sous-process powershell.exe dedie) pour relancer un bot precis.
#
# ISOLATION DE GROUPE DE PROCESSUS (cf. bug fix "isolation console lancement manuel") :
# le process bot est cree via CreateProcess (Win32, P/Invoke) avec le flag
# CREATE_NEW_PROCESS_GROUP, PAS via [System.Diagnostics.Process]::Start (qui n'expose
# aucun moyen de definir ce flag). Sans cette isolation, le bot herite du groupe de
# processus de la console PowerShell qui l'a lance ; un CTRL_BREAK_EVENT cible (envoye
# par stop_bot.ps1, meme mecanisme que `nssm stop`) risquerait alors d'atteindre TOUS
# les process attaches a cette console (le lanceur PowerShell, et tout autre bot lance
# manuellement depuis le meme terminal reste ouvert). Avec ce flag, chaque bot devient
# la racine de son propre groupe (id de groupe = son propre PID), ce qui permet a
# stop_bot.ps1 de cibler un seul bot sans effet de bord. Voir stop_bot.ps1.
#
# FENETRAGE DETERMINISTE : abandonne (cf. Utils/ORCHESTRATION_TRACKING.md). Chaque bot
# recevait auparavant une position/taille de fenetre Chrome deduite de son index dans
# accounts.json (SURVEYBOT_WINDOW_X/Y/W/H) pour que l'operateur connecte en RDP repere
# le bon compte visuellement. Retire : un fenetrage reduit par compte peut tomber sous
# le seuil responsive de certains sites et bloquer des clics valides en desktop (cas
# observe en preselection TopSurveys) - preselection/playwright_launcher.py utilise
# desormais --start-maximized inconditionnel en non-headless. L'identification du bon
# compte en RDP repose maintenant sur le dummy plug HDMI par machine, voir
# Utils/DEPLOIEMENT_BAREMETAL_DECISIONS.md.
#
# Usage :
#   .\launch_all.ps1                                  # tous les comptes de accounts.json
#   .\launch_all.ps1 -AccountId "topsurveys_bot_001"   # un seul compte
#
# Prerequis :
#   - accounts.json dans le meme dossier que ce script (C:\surveybot\, la racine).
#   - venv\ contenant l'interpreteur Python + dependances (requirements.txt).
#   - code\ contenant les sources (main.py, _license_config.py, global_config.py...).
#     Ce dossier est remplace en entier par l'auto-update (update_checker.py) : il ne
#     doit contenir QUE du code source, jamais de donnees persistantes.
#   - Dossiers profiles\ crees manuellement (un par bot).
#   - Dossier pids\ cree automatiquement au premier lancement.
#
# IMPORTANT : le processus est lance avec WorkingDirectory = la racine (PSScriptRoot),
# jamais code\. Sur Windows, un dossier qui est le repertoire courant d'un process ne
# peut pas etre renomme/supprime - si le cwd etait code\, l'auto-update ne pourrait
# jamais swapper ce dossier tant que le bot tourne.

param(
    [string]$AccountId    = "",     # vide = tous les comptes de accounts.json
    [string]$AccountsFile = "$PSScriptRoot\accounts.json",
    [string]$PythonExe    = "$PSScriptRoot\venv\Scripts\python.exe",
    [string]$MainScript   = "$PSScriptRoot\code\main.py",
    [string]$PidsDir      = "$PSScriptRoot\pids",
    [string]$LogDir       = "$PSScriptRoot\logs"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# Nombre de cycles de lancement passes conserves en historique de logs, au-dela
# du cycle courant (bot_$id.log.1 = cycle precedent, ... .10 = plus ancien
# conserve). Permet l'analyse retrospective de comportements intermittents.
$LOG_HISTORY_CYCLES = 10

# Garde-fou boucle : abandon si accounts.json est anormalement grand (meme convention
# que wake_scheduler.ps1/nssm_setup_bot.ps1).
$MAX_ACCOUNTS = 200

# ---------------------------------------------------------------------------
# Creation de process isole (CREATE_NEW_PROCESS_GROUP) - cf. note d'isolation en
# tete de fichier. Redirection stdout+stderr directement vers le fichier log via un
# handle Win32 (CreateFile inheritable), sans pipes ni threads de pompage - plus
# simple et suffisant, le seul besoin etant "ecrire dans le fichier log", pas de
# traitement en temps reel du flux cote PowerShell.
# ---------------------------------------------------------------------------

# Idempotence intra-session : un type C# charge via Add-Type reste resident pour
# toute la duree de vie du process powershell.exe (CLR/AppDomain unique, pas de
# redefinition possible). Si ce script est relance dans la meme fenetre sans
# redemarrer powershell.exe (usage manuel normal), Add-Type echouerait sur ce
# meme type deja charge (TYPE_ALREADY_EXISTS) - inoffensif dans les faits (le
# type existant reste utilisable tel quel) mais l'erreur s'affiche quand meme
# selon la version/build de PowerShell. Ce garde evite l'appel redondant, sans
# dependre du caractere bloquant ou non de cette erreur.
if (-not ([System.Management.Automation.PSTypeName]'SurveyBotIsolatedLauncher').Type) {
Add-Type -Language CSharp -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
using System.ComponentModel;

public static class SurveyBotIsolatedLauncher
{
    [StructLayout(LayoutKind.Sequential)]
    private struct SECURITY_ATTRIBUTES
    {
        public int nLength;
        public IntPtr lpSecurityDescriptor;
        public bool bInheritHandle;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct STARTUPINFO
    {
        public int cb;
        public string lpReserved;
        public string lpDesktop;
        public string lpTitle;
        public int dwX; public int dwY; public int dwXSize; public int dwYSize;
        public int dwXCountChars; public int dwYCountChars; public int dwFillAttribute;
        public int dwFlags;
        public short wShowWindow;
        public short cbReserved2;
        public IntPtr lpReserved2;
        public IntPtr hStdInput;
        public IntPtr hStdOutput;
        public IntPtr hStdError;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct PROCESS_INFORMATION
    {
        public IntPtr hProcess;
        public IntPtr hThread;
        public int dwProcessId;
        public int dwThreadId;
    }

    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    private static extern bool CreateProcess(
        string lpApplicationName, string lpCommandLine,
        IntPtr lpProcessAttributes, IntPtr lpThreadAttributes,
        bool bInheritHandles, uint dwCreationFlags,
        IntPtr lpEnvironment, string lpCurrentDirectory,
        ref STARTUPINFO lpStartupInfo, out PROCESS_INFORMATION lpProcessInformation);

    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    private static extern IntPtr CreateFile(
        string lpFileName, uint dwDesiredAccess, uint dwShareMode,
        ref SECURITY_ATTRIBUTES lpSecurityAttributes, uint dwCreationDisposition,
        uint dwFlagsAndAttributes, IntPtr hTemplateFile);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool CloseHandle(IntPtr hObject);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool GetProcessTimes(IntPtr hProcess, out long lpCreationTime, out long lpExitTime, out long lpKernelTime, out long lpUserTime);

    private const uint GENERIC_WRITE = 0x40000000;
    private const uint FILE_SHARE_READ = 0x00000001;
    private const uint FILE_SHARE_WRITE = 0x00000002;
    // Sans ce flag, la rotation (Move-Item vers .log.1 dans Start-Bot) echoue avec
    // ERROR_SHARING_VIOLATION tant que ce handle (herite par le process bot pour
    // stdout/stderr) reste ouvert - meme apres un kill force, l'OS met un instant a le
    // liberer. Move-Item echouait alors silencieusement (catch + WARN dans Start-Bot)
    // et le CreateFile suivant (CREATE_ALWAYS) tronquait le log de la session precedente
    // au lieu de la roter. FILE_SHARE_DELETE autorise le rename pendant que ce handle
    // est encore ouvert, qui est precisement ce que la rotation suppose deja pouvoir faire.
    private const uint FILE_SHARE_DELETE = 0x00000004;
    private const uint CREATE_ALWAYS = 2;
    private const uint FILE_ATTRIBUTE_NORMAL = 0x80;
    private const uint STARTF_USESTDHANDLES = 0x00000100;
    private const uint CREATE_NO_WINDOW = 0x08000000;
    // Flag racine du correctif : place le nouveau process a la tete de son PROPRE
    // groupe de processus console (id de groupe = son propre PID), au lieu d'heriter
    // du groupe de la console PowerShell appelante. Seul moyen de cibler un CTRL_BREAK
    // sur un unique bot manuel sans affecter les autres process de la meme console.
    private const uint CREATE_NEW_PROCESS_GROUP = 0x00000200;
    private const uint CREATE_UNICODE_ENVIRONMENT = 0x00000400;

    // Sortie : PID + heure de demarrage (ticks), meme calcul que Process.StartTime
    // cote .NET (DateTime.FromFileTime sur GetProcessTimes) - garantit la compatibilite
    // avec le format existant pids\bot_<id>.pid ("PID|StartTicks") et avec
    // Test-BotProcessAlive (launch_all.ps1 / stop_bot.ps1), inchanges.
    public static int ProcessId;
    public static long StartTimeTicks;

    public static void Start(string exePath, string arguments, string workingDir, string[] envEntries, string logPath)
    {
        var fileSa = new SECURITY_ATTRIBUTES
        {
            nLength = Marshal.SizeOf(typeof(SECURITY_ATTRIBUTES)),
            bInheritHandle = true,
            lpSecurityDescriptor = IntPtr.Zero
        };

        IntPtr logHandle = CreateFile(
            logPath, GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
            ref fileSa, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, IntPtr.Zero);

        if (logHandle == new IntPtr(-1))
            throw new Win32Exception(Marshal.GetLastWin32Error(), "CreateFile(log) a echoue : " + logPath);

        var si = new STARTUPINFO();
        si.cb = Marshal.SizeOf(typeof(STARTUPINFO));
        si.dwFlags = (int)STARTF_USESTDHANDLES;
        // Meme handle pour stdout et stderr (equivalent shell "2>&1") : un seul fichier
        // log fusionne, comme le comportement existant (les deux flux y ecrivaient deja).
        si.hStdOutput = logHandle;
        si.hStdError = logHandle;
        si.hStdInput = IntPtr.Zero;

        var envBlock = new System.Text.StringBuilder();
        foreach (var entry in envEntries)
            envBlock.Append(entry).Append('\0');
        envBlock.Append('\0');
        IntPtr envPtr = Marshal.StringToHGlobalUni(envBlock.ToString());

        string cmdLine = "\"" + exePath + "\" " + arguments;
        uint flags = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP | CREATE_UNICODE_ENVIRONMENT;

        PROCESS_INFORMATION pi;
        bool ok;
        try
        {
            ok = CreateProcess(null, cmdLine, IntPtr.Zero, IntPtr.Zero, true, flags, envPtr, workingDir, ref si, out pi);
        }
        finally
        {
            Marshal.FreeHGlobal(envPtr);
            CloseHandle(logHandle);
        }

        if (!ok)
            throw new Win32Exception(Marshal.GetLastWin32Error(), "CreateProcess a echoue pour " + exePath);

        CloseHandle(pi.hThread);

        long creationTime, exitTime, kernelTime, userTime;
        GetProcessTimes(pi.hProcess, out creationTime, out exitTime, out kernelTime, out userTime);
        CloseHandle(pi.hProcess);

        ProcessId = pi.dwProcessId;
        StartTimeTicks = DateTime.FromFileTime(creationTime).Ticks;
    }
}
"@
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

function Write-Log {
    param([string]$Message)
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] $Message"
    Write-Host $line
    try {
        Add-Content -Path "$LogDir\launch_all.log" -Value $line -Encoding UTF8
    } catch {}
}

function Get-PidPath {
    param([string]$AccountId)
    return Join-Path $PidsDir "bot_$AccountId.pid"
}

function Test-NssmServiceExists {
    # Empeche un double lancement (meme profil Chrome/proxy) si un service NSSM
    # surveybot_<id> est deja installe pour ce compte, quel que soit son statut
    # (running ou stoppe - meme stoppe, il reste le chemin de supervision attendu).
    param([string]$AccountId)
    $svcName = "surveybot_$AccountId"
    try {
        & nssm status $svcName 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch {
        # nssm.exe absent du PATH (ex: machine de test isolee sans NSSM) - pas de
        # service possible, donc pas de conflit a craindre.
        return $false
    }
}

function Test-BotProcessAlive {
    # Un PID Windows est recyclable : une fois le process du bot termine, l'OS peut
    # reattribuer ce meme PID a un process totalement different peu apres. Verifier
    # seulement l'existence du PID (ex: via tasklist) est donc insuffisant - il faut
    # aussi confirmer que c'est bien le MEME process (heure de demarrage identique).
    param(
        [int]$ProcessId,
        [long]$ExpectedStartTicks
    )
    try {
        $p = Get-Process -Id $ProcessId -ErrorAction Stop
        return ($p.StartTime.Ticks -eq $ExpectedStartTicks)
    } catch {
        # PID absent ou inaccessible (process systeme protege, etc.)
        return $false
    }
}

function Start-Bot {
    param([hashtable]$Bot)

    $id          = $Bot.account_id
    $profileDir  = $Bot.profile_dir

    # Verification du dossier profil Chrome
    if (-not (Test-Path $profileDir)) {
        Write-Log "SKIP $id - profile_dir introuvable : $profileDir"
        return
    }

    # Variables d'environnement passees au processus
    # LICENSE_KEY et DATABASE_URL sont embarquees dans le compile - absentes ici.
    $env_vars = @{
        "ACCOUNT_ID"        = $id
        "EMAIL"             = $Bot.email
        "PASSWORD"          = $Bot.password
        "PROXY_URL"         = $Bot.proxy_url
        "PROXY_USER"        = $Bot.proxy_user
        "PROXY_PASS"        = $Bot.proxy_pass
        "CHROME_PROFILE_DIR"= $profileDir
        "RUN_ENV"           = "prod"
        "GEO_LAT"           = if ($Bot.ContainsKey("geo_lat"))     { $Bot.geo_lat }     else { "48.8566" }
        "GEO_LON"           = if ($Bot.ContainsKey("geo_lon"))     { $Bot.geo_lon }     else { "2.3522" }
        "SURVEY_LANG"       = if ($Bot.ContainsKey("survey_lang")) { $Bot.survey_lang } else { "fr-FR" }
        "SURVEY_TZ"         = if ($Bot.ContainsKey("survey_tz"))   { $Bot.survey_tz }   else { "Europe/Paris" }
        # Sans ca, print() d'un caractere hors cp1252 (emoji, etc.) plante le process
        # des que stdout est redirige vers un pipe (cas de ce script) au lieu d'un
        # vrai terminal -- deja present dans nssm_setup_bot.ps1, manquait ici.
        "PYTHONIOENCODING"  = "utf-8"
        "PYTHONUTF8"        = "1"
    }

    # Construire le bloc d'environnement pour Start-Process
    # On modifie une copie de l'env courant pour ne pas polluer le processus lanceur
    $envBlock = [System.Collections.Specialized.StringDictionary]::new()
    foreach ($kv in [System.Environment]::GetEnvironmentVariables().GetEnumerator()) {
        $envBlock[$kv.Key] = $kv.Value
    }
    foreach ($kv in $env_vars.GetEnumerator()) {
        $envBlock[$kv.Key] = $kv.Value
    }

    # Sous-dossier dedie a ce bot, distinct des logs d'orchestration transverses
    # (launch_all.log, ...) qui restent a plat dans $LogDir - meme convention que
    # nssm_setup_bot.ps1 pour la lisibilite/nettoyage sur un parc a plusieurs bots.
    $botLogDir = Join-Path $LogDir $id
    if (-not (Test-Path $botLogDir)) {
        New-Item -ItemType Directory -Path $botLogDir -Force | Out-Null
    }
    $logFile = Join-Path $botLogDir "bot_$id.log"

    # Rotation : conserve un historique borne de $LOG_HISTORY_CYCLES cycles de
    # lancement passes (bot_$id.log.1 = precedent, ... .N = plus ancien), au-dela
    # du cycle courant. Chaque decalage est independant : une interruption en
    # plein cycle perd au pire un cran d'historique, sans etat intermediaire
    # casse ni croissance illimitee (le plus ancien est ecrase a chaque tour).
    for ($i = $LOG_HISTORY_CYCLES - 1; $i -ge 1; $i--) {
        $src = "$logFile.$i"
        $dst = "$logFile.$($i + 1)"
        if (Test-Path $src) {
            try {
                Move-Item -Path $src -Destination $dst -Force
            } catch {
                Write-Log "WARN $id - rotation log echouee ($src -> $dst) : $_"
            }
        }
    }
    try {
        if (Test-Path $logFile) {
            Move-Item -Path $logFile -Destination "$logFile.1" -Force
        }
    } catch {
        Write-Log "WARN $id - rotation log echouee : $_"
    }

    # Cree le process bot isole dans son propre groupe de processus console
    # (CREATE_NEW_PROCESS_GROUP - cf. SurveyBotIsolatedLauncher et note d'isolation en
    # tete de fichier). [System.Diagnostics.Process]::Start ne permet pas de definir ce
    # flag, d'ou le recours a CreateProcess (Win32) directement.
    $envEntries = @()
    foreach ($kv in $envBlock.GetEnumerator()) {
        $envEntries += "$($kv.Key)=$($kv.Value)"
    }

    try {
        [SurveyBotIsolatedLauncher]::Start(
            $PythonExe,
            "`"$MainScript`"",
            $PSScriptRoot,
            $envEntries,
            $logFile
        )
    } catch {
        Write-Log "ERREUR $id - echec creation process isole : $_"
        return
    }

    $newPid    = [SurveyBotIsolatedLauncher]::ProcessId
    $newTicks  = [SurveyBotIsolatedLauncher]::StartTimeTicks

    # Ecriture du PID + heure de demarrage (ticks) - le couple sert a distinguer ce
    # process precis d'un futur process sans rapport qui recyclerait le meme PID.
    # Le bot ecrit aussi son propre PID via write_pid_file - double securite.
    # Meme format qu'avant ("PID|StartTicks") : Test-BotProcessAlive et stop_bot.ps1
    # restent inchanges.
    $pidPath = Get-PidPath $id
    "$newPid|$newTicks" | Out-File -FilePath $pidPath -Encoding ASCII -NoNewline

    Write-Log "START $id - PID=$newPid log=$logFile (groupe de processus isole)"
}

# ---------------------------------------------------------------------------
# Init dossiers
# ---------------------------------------------------------------------------

foreach ($dir in @($PidsDir, $LogDir)) {
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir | Out-Null
    }
}

# ---------------------------------------------------------------------------
# Lecture accounts.json
# ---------------------------------------------------------------------------

if (-not (Test-Path $AccountsFile)) {
    Write-Log "ERREUR - accounts.json introuvable : $AccountsFile"
    exit 1
}

if (-not (Test-Path $PythonExe)) {
    Write-Log "ERREUR - python.exe introuvable : $PythonExe"
    exit 1
}

if (-not (Test-Path $MainScript)) {
    Write-Log "ERREUR - code\main.py introuvable : $MainScript"
    exit 1
}

$raw      = Get-Content -Path $AccountsFile -Raw -Encoding UTF8
# IMPORTANT : ne PAS ecrire "@($raw | ConvertFrom-Json)" ici. ConvertFrom-Json emet
# tout son resultat comme un seul objet de pipeline (pas d'enumeration element par
# element) ; quand accounts.json est un tableau JSON (cas normal), ce resultat est
# deja un System.Object[] - @() autour du PIPE le re-emballe alors dans un second
# tableau (allAccounts[0] devient un System.Object[] au lieu du PSCustomObject du
# compte). L'acces simple a une propriete (ex. .account_id) "traverse" silencieusement
# un tableau a un seul element et masque le bug, mais .PSObject.Properties (utilise
# plus bas pour construire $bot) ne le fait pas : il reflete alors les proprietes du
# TABLEAU (Length, Rank, IsFixedSize...) au lieu des champs du compte, et plus aucune
# cle attendue n'existe -> PropertyNotFoundStrict sous Set-StrictMode des le premier
# compte traite. Correction (confirmee empiriquement) : @() doit envelopper la
# VARIABLE deja assignee, pas l'appel au pipe - @() ne rajoute alors pas de niveau
# supplementaire si c'est deja un tableau, et enveloppe correctement un objet JSON
# unique (accounts.json malforme, un seul compte hors tableau) en tableau a 1 element.
$allAccounts = $raw | ConvertFrom-Json
$allAccounts = @($allAccounts)

if ($AccountId) {
    $accounts = @($allAccounts | Where-Object { $_.account_id -eq $AccountId })
    if ($accounts.Count -eq 0) {
        Write-Log "ERREUR - aucun compte trouve pour AccountId='$AccountId' dans $AccountsFile"
        exit 1
    }
    Write-Log "=== launch_all.ps1 demarrage - lancement manuel ponctuel de '$AccountId' ==="
} else {
    $accounts = $allAccounts
    if ($accounts.Count -eq 0) {
        Write-Log "ERREUR - accounts.json est vide : $AccountsFile"
        exit 1
    }
    if ($accounts.Count -gt $MAX_ACCOUNTS) {
        Write-Log "ERREUR - $($accounts.Count) comptes > MAX_ACCOUNTS ($MAX_ACCOUNTS) - abort (verifier accounts.json)."
        exit 1
    }
    Write-Log "=== launch_all.ps1 demarrage - lancement de tous les comptes ($($accounts.Count)) ==="
}

# ---------------------------------------------------------------------------
# Boucle principale
# ---------------------------------------------------------------------------

$processed = 0
foreach ($account in $accounts) {
    if ($processed -ge $MAX_ACCOUNTS) {
        Write-Log "AVERTISSEMENT - budget MAX_ACCOUNTS ($MAX_ACCOUNTS) atteint - arret de la boucle."
        break
    }
    $processed++

    $bot = @{}
    $account.PSObject.Properties | ForEach-Object { $bot[$_.Name] = $_.Value }

    $id      = $bot.account_id
    $pidPath = Get-PidPath $id

    # --- Cas 0 : service NSSM deja installe pour ce compte ---
    # Garde-fou de transition (NSSM pas encore decommissionne, cf. decommission_nssm.ps1) :
    # tant qu'un service surveybot_<id> existe pour ce compte, refuser le lancement ici
    # eviterait un double process sur le meme profil Chrome/proxy.
    if (Test-NssmServiceExists -AccountId $id) {
        Write-Log "ABORT $id - service NSSM surveybot_$id deja installe - lancement manuel refuse (double lancement meme profil/proxy). Utiliser 'nssm start surveybot_$id' ou verifier son statut a la place."
        continue
    }

    # --- Cas 1 : fichier PID present ---
    if (Test-Path $pidPath) {
        $pidRaw = (Get-Content -Path $pidPath -Raw).Trim()
        $parts  = $pidRaw -split '\|'
        $pidInt = 0
        $startTicks = 0L

        if ($parts.Count -eq 2 -and [int]::TryParse($parts[0], [ref]$pidInt) -and $pidInt -gt 0 -and [long]::TryParse($parts[1], [ref]$startTicks)) {
            if (Test-BotProcessAlive -ProcessId $pidInt -ExpectedStartTicks $startTicks) {
                Write-Log "SKIP $id - deja actif (PID=$pidInt)"
                continue
            } else {
                # PID stale ou recycle par un process sans rapport (start time different)
                Write-Log "STALE $id - PID=$pidInt mort ou recycle, nettoyage + relance"
                Remove-Item -Path $pidPath -Force
            }
        } else {
            # Fichier PID corrompu ou ancien format (sans heure de demarrage)
            Write-Log "CORRUPT $id - fichier PID illisible/obsolete, nettoyage + relance"
            Remove-Item -Path $pidPath -Force
        }
    }

    # --- Cas 2 : lancer le bot ---
    try {
        Start-Bot -Bot $bot
    } catch {
        Write-Log "ERREUR $id - echec lancement : $_"
    }
}

Write-Log "=== launch_all.ps1 termine ==="