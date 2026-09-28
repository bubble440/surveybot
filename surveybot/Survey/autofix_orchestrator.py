from __future__ import annotations

"""Orchestrateur autofix — lancement automatique de Claude Code (mode headless)
puis enchaînement des phases existantes (8, 9, 11-A, 12, 13) jusqu'à la
notification humaine, pour jusqu'à --max-cases cases éligibles, UN À LA FOIS
(jamais en parallèle dans ce module — cf. RÈGLES STRICTES ci-dessous).

N'orchestre que des phases déjà écrites et déjà validées séparément
(Survey/static_validator.py, Survey/patch_replay.py,
Survey/extractor_integrity_gate.py, Survey/confidence_score.py,
Survey/human_review.py, Survey/parallel_safety.py, et — pour l'étape amont
ci-dessous — Survey/fleet_case_import.py, Survey/failure_diagnosis.py,
Survey/case_grouping.py, Survey/context_selector.py,
Survey/prompt_generator.py, Survey/autofix_worktree.py) — importées et
appelées TELLES QUELLES, jamais réimplémentées ni modifiées. Ne touche à
aucun extracteur ni stratégie de dispatch.

Depuis l'introduction de l'étape amont (cf. section dédiée ci-dessous), la
Phase 7 (préparation du worktree, Survey/autofix_worktree.py) EST déclenchée
par ce module, par défaut, avant l'étape ci-dessous — sauf --no-upstream, qui
restaure exactement le comportement antérieur (worktree.json/prompt.txt déjà
présents restent une précondition satisfaite manuellement en amont). La
section "Éligibilité d'un case" ci-dessous décrit l'étape EXISTANTE
(inchangée) qui part de worktree.json déjà présent ; elle s'exécute, dans la
même invocation, juste après l'étape amont, et découvre donc naturellement
les worktrees que celle-ci vient de créer.

── Éligibilité d'un case (toutes les conditions ensemble) ────────────────────
autofix_worktrees/<case_id>/worktree.json existe (Phase 7 déjà faite) ; ET
prompts/<case_id>/prompt.txt existe RÉELLEMENT (jamais
MANUAL_REVIEW_REQUIRED.txt à sa place) ; ET codex_runs/<case_id>/
run_result.json N'EXISTE PAS ENCORE. Cases triés par case_id (ordre
lexicographique — les case_id de ce pipeline sont des horodatages, donc cet
ordre est aussi chronologique), les --max-cases premiers retenus pour cette
invocation. Aucune tentative de réordonnancement plus intelligent au-delà de
ce tri déterministe : un case durablement bloqué par le contrôle de
parallélisme (point 1 ci-dessous) resterait en tête à chaque invocation —
accepté explicitement, cf. Survey/parallel_safety.py ("l'ordre de traitement
[...] reste une décision humaine").

── Conséquence disclosée de la règle d'éligibilité ────────────────────────────
run_result.json, une fois écrit (succès OU échec de l'invocation Claude
Code), rend le case définitivement non éligible à une reprise AUTOMATIQUE par
une future invocation de ce module — conforme à la lettre de la règle
d'éligibilité ci-dessus, et cohérent avec la RÈGLE STRICTE "jamais de retry
automatique sur échec [...] attend la prochaine invocation planifiée" (une
invocation planifiée reprend un NOUVEAU case, jamais silencieusement le même
en boucle). Une reprise du même case reste possible mais MANUELLE : soit un
humain supprime codex_runs/<case_id>/ après investigation, soit — si
l'invocation Claude Code avait réussi mais qu'une phase suivante a échoué ou
que ce process a été interrompu en cours de chaîne — un humain relance
directement les façades CLI existantes (tools/validate_patch_static.py,
tools/replay_patch.py, tools/check_extractor_integrity.py,
tools/score_patch_confidence.py, tools/notify_human_review.py) sur les
artefacts déjà écrits sur disque : rien n'est perdu, seule l'automatisation
de bout en bout s'arrête à l'endroit exact où ce module s'est arrêté.

── Point 1 : contrôle de sécurité du parallélisme (avant tout lancement) ─────
Réutilise Survey/parallel_safety.py::check_pre_launch_safety (le nom exact
dans le code — pas "check_pre_launch_safety_check" — vérifié en lisant le
module avant d'écrire celui-ci), importée telle quelle, jamais réimplémentée.
Le comportement de ce contrôle lui-même (comparaison au niveau fichier) ne
change pas ; seul l'ensemble des worktrees comparés change (cf. ci-dessous).

Définition resserrée de "en vol" (remplace l'ancienne définition volontairement
large "tout worktree.json présent", documentée comme provisoire dès son
introduction — cf. suite 35 de SURVEYBOT_AUTOFIX_PLAN.md — maintenant que
merge_result.json (Survey/merge_executor.py) existe) : un worktree n'est "en
vol" que s'il peut encore réellement aboutir à un merge. Un worktree dont
l'issue est définitive ne bloque plus aucun autre case. Est définitive,
et SEULEMENT, l'une de ces conditions, vérifiée sur un artefact réellement lu
et bien formé (jamais devinée) :
  - merge_results/<case_id>/merge_result.json (Phase "merge automatique",
    Survey/merge_executor.py) : status="MERGED" ou "ALREADY_MERGED" — un
    status="CONFLICT" reste "en vol" (résolution manuelle encore possible) ;
  - human_reviews/<case_id>/decision.json (Phase 13) : decision="REJECTED" ;
  - merge_reviews/<case_id>/decision.json (Phase 16) : decision="REJECTED"
    (confirmation de merge annulée) ;
  - confidence_scores/<case_id>/confidence_score.json (Phase 12) :
    confidence="REJECT" — confidence="MEDIUM" reste "en vol" (un humain peut
    encore trancher) ;
  - autofix_static_validations/<case_id>/validation_static.json (Phase 8) :
    verdict="REJECTED" ;
  - codex_runs/<case_id>/run_result.json (invocation Claude Code de CE
    module) : status différent de "SUCCESS" (invocation échouée, aucun patch
    produit).
Un artefact absent, illisible, malformé ou dont le champ attendu est absent
laisse le worktree "en vol" — jamais une hypothèse optimiste. Une seule
définition, pas de règles empilées ni de délai d'expiration : un worktree qui
ne remplit AUCUNE de ces conditions reste "en vol" quel que soit son âge.

Pour chaque case candidat, si au moins un autre worktree est "en vol" (au sens
resserré ci-dessus) : compare context_selection.json (Phase 5) du candidat à
celui de chacun de ces autres cases, au niveau fichier (granularité déjà celle
de check_pre_launch_safety). Toute paire non sûre IMPLIQUANT LE CANDIDAT
(jamais une paire non sûre entre deux AUTRES cases en vol, hors de propos ici)
reporte ce case : il n'est pas lancé cette fois, signalé explicitement — avec
le ou les case_id qui bloquent et l'état lu de chacun — dans
autofix_pipeline_runs/<case_id>/pipeline_run.json et dans le résumé imprimé,
mais reste éligible à une invocation future (aucun codex_runs/ écrit pour un
case reporté). Une erreur de lecture d'un context_selection.json (candidat ou
en vol) est traitée de la même façon (jamais deviné sûr par défaut) —
ParallelSafetyError capturée et convertie en report.

── Point 2 : invocation Claude Code en mode headless ─────────────────────────
Syntaxe vérifiée dans `claude --help` (CLI réellement installée dans cet
environnement, version 2.1.283) AVANT d'écrire ce module, jamais supposée :
  - Pas de flag --cwd (n'existe pas dans cette version). "worktree_path EXACT"
    est obtenu par le paramètre `cwd=` du sous-processus Python lui-même —
    équivalent fonctionnel exact, seule façon réellement disponible.
  - --allowedTools <tools...> : "Comma or space-separated list of tool names"
    — une seule chaîne espacée est passée ("Read Edit Write Grep Glob" par
    défaut), vérifié fonctionnel par un appel réel (--allowedTools "Read Edit
    Write Grep Glob" a bien limité l'agent à ces outils lors d'un test réel
    dans ce chantier). Ensemble volontairement restreint : ni Bash, ni accès
    réseau/web, ni sous-agents — seuls les outils nécessaires pour lire et
    modifier des fichiers dans le worktree isolé (le prompt Phase 6 demande
    "identifier la cause racine, appliquer un patch minimal", jamais
    d'exécuter des tests — ceux-ci sont déjà couverts séparément par les
    Phases 8/9/11-A qui suivent).
  - --output-format json (exige --print/-p) : un seul objet JSON en sortie
    standard, vérifié par un appel réel — contient au moins
    is_error/subtype/session_id/result/num_turns. Stocké tel quel (sortie
    brute) dans run_result.json, jamais réinterprété au-delà de ces champs.
  - Transmission du prompt : NI un argument positionnel (risque de limite de
    longueur argv/de quoting shell pour un prompt long et multi-lignes), NI un
    fichier temporaire supplémentaire (le prompt existe déjà sur disque,
    prompts/<case_id>/prompt.txt, mais le lire puis le réécrire ailleurs
    n'apporterait rien) : ENTRÉE STANDARD, vérifiée fonctionnelle par un appel
    réel (aucun argument positionnel donné à `claude -p`, prompt fourni via
    subprocess.run(..., input=prompt_text)). Une seule stratégie, comme les
    Phases 8/9 avant ce module.
  - --permission-mode acceptEdits : passé explicitement plutôt que de compter
    sur un défaut ambiant (potentiellement différent d'un environnement à
    l'autre) — accepte automatiquement les opérations d'édition de fichier
    (Edit/Write, déjà seuls listés dans --allowedTools avec Read/Grep/Glob qui
    ne mutent rien), sans ouvrir la porte à des outils non listés. Vérifié
    fonctionnel par un appel réel (Write a réussi sans blocage avec ce mode).

Un seul mécanisme d'invocation, un seul essai (subprocess.run avec
timeout=claude_timeout_s explicite) — jamais de retry automatique. Un
dépassement de budget (TimeoutExpired) ou un code de sortie non nul arrête la
chaîne ICI pour ce case, jamais les phases suivantes pour LUI, et jamais les
autres cases de cette même invocation (chaque case est indépendant). Le
binaire "claude" est résolu via shutil.which (jamais un chemin codé en dur) ;
son absence sur PATH est un statut ERROR explicite, jamais une exception non
gérée. Écrit systématiquement codex_runs/<case_id>/run_result.json (schéma :
schema_version/case_id/created_at + branch/worktree_path/status/exit_code/
timed_out/session_id/is_error/subtype/command/raw_stdout/raw_stderr/error),
même convention JSON que les phases précédentes. raw_stdout/raw_stderr sont
bornés (_MAX_RAW_OUTPUT_CHARS) par précaution, même si --output-format json
produit normalement une sortie compacte.

── Point 3 : enchaînement des phases existantes, dans l'ordre imposé ─────────
a. Phase 8 (Survey.static_validator.write_static_validation) : verdict
   REJECTED (ou StaticValidationError) arrête la chaîne ici pour ce case.
b. Phase 9 (Survey.patch_replay.write_patch_replay) : continue quel que soit
   l'outcome (CORRECTIF_CONFIRME/BUG_PERSISTANT/NON_CONCLUANT, ou même
   refused=true) — seule une PatchReplayError (précondition d'usage cassée)
   arrête la chaîne ici. Un refused=true est transmis tel quel à la Phase 12,
   qui sait déjà le traiter (CRITERION_INCONCLUSIVE), jamais réinterprété ici.
c. Phase 11-A (Survey.extractor_integrity_gate.write_extractor_integrity_check).
d. Phase 12 (Survey.confidence_score.write_patch_confidence),
   live_validation_path=None explicitement (Phase 10 jamais tentée
   automatiquement par ce module — exige un humain avec un vrai navigateur,
   structurellement hors de portée ici, cf. demande d'origine).
e. confidence="HIGH" -> Phase 13 (Survey.human_review.send_review_request).
   Toute autre valeur (MEDIUM/REJECT) arrête la chaîne ici, sans notification
   — ces cases restent visibles via confidence_scores/<case_id>/
   confidence_score.json (rapports déjà existants), conformément à la Phase
   13 elle-même ("MEDIUM/REJECT ne déclenchent jamais de notification").

── Sortie : résumé par case ───────────────────────────────────────────────────
Pour CHAQUE case retenu dans cette invocation (traité OU reporté),
autofix_pipeline_runs/<case_id>/pipeline_run.json est écrit avec le point
d'arrêt exact (stopped_at), la raison, les chemins des artefacts déjà produits
par les phases atteintes, et si la notification a eu lieu. CONTRAIREMENT à
tous les autres artefacts de ce chantier, ce fichier est TOUJOURS écrasé sans
garde --force : ce n'est pas un artefact consommé comme précondition par une
autre phase (à la différence de worktree.json/validation_static.json/etc.),
seulement un instantané diagnostique de la dernière tentative de ce module
pour ce case — un case reporté peut légitimement être réexaminé plusieurs
fois avant d'être enfin lancé, et chaque réexamen doit remplacer le résumé
précédent, pas être bloqué par lui. Décision documentée explicitivement ici
plutôt que masquée, en écart volontaire avec la convention par défaut du
reste du chantier.

── RÈGLES STRICTES respectées ────────────────────────────────────────────────
Un seul mécanisme d'invocation Claude Code, jamais de fallback. Budget de
temps explicite sur cette invocation ; les budgets internes des phases
réutilisées (Phases 8/9/11-A) sont déjà les leurs, inchangés, jamais modifiés
ici. Traitement strictement séquentiel des cases retenus — une simple boucle
for Python, jamais un thread/process/async lancé par ce module : le contrôle
de sécurité du parallélisme (point 1) reste un filet de sécurité pour une
parallélisation future, hors périmètre de ce patch. Aucune logique de phase
existante réimplémentée : chaque étape n'est qu'un appel direct à la fonction
déjà écrite et déjà testée de la phase correspondante.

── Étape amont : de failure_cases/ jusqu'au worktree prêt (run_upstream_stage) ─
Active par défaut, avant l'étape existante ci-dessus, dans la MÊME invocation
(même simple boucle for séquentielle — jamais de thread/process concurrent,
la Phase 7 modifiant l'état Git du dépôt principal). Désactivée entièrement
par --no-upstream, qui restaure alors le comportement exact d'avant cette
extension.

1. Import fleet optionnel (--import-fleet, désactivé par défaut car il exige
   FLEET_R2_* dans l'environnement) : Survey/fleet_case_import.py::
   import_available_cases(), appelée SANS argument (racine/prefixe/budgets par
   défaut de ce module, non exposés ici — hors périmètre de cette extension).
   Un échec (FleetImportError : credentials R2 absentes ; ou toute autre
   exception, notamment réseau, puisque le listing S3 interne à ce module
   n'est pas lui-même protégé) est un avertissement journalisé (log_info),
   JAMAIS un arrêt de l'invocation.

2. Sélection déterministe des cases amont (discover_upstream_candidates,
   triée par case_id) : dossiers de failure_cases/<case_id>/ (manifest.json
   présent, case_id sûr comme composant de chemin) qui n'ont NI
   autofix_worktrees/<case_id>/worktree.json (Phase 7 déjà faite — le case
   relève alors déjà de l'étape existante ci-dessus), NI
   autofix_pipeline_runs/<case_id>/upstream_run.json avec terminal=true (état
   définitif déjà atteint par une invocation précédente de CETTE étape — cf.
   point 6), ET qui ne sont membres d'AUCUN groupe déjà formé
   (failure_cases/dupgroup_*/group_members.json, cf. point 4 : un membre n'est
   jamais un candidat individuel).

3. Phase 4 (Survey/failure_diagnosis.py::write_diagnosis) pour chaque
   candidat de la sélection ci-dessus SANS diagnoses/<case_id>/diagnosis.json
   — réutilisé tel quel sinon, jamais recalculé. Borné par
   --max-upstream-cases (DEFAULT_MAX_UPSTREAM_CASES) sur le nombre de
   diagnostics RÉELLEMENT NOUVEAUX tentés (succès ou échec ; un case déjà
   diagnostiqué ne consomme jamais ce budget) — cf. DEFAULT_MAX_UPSTREAM_CASES
   pour la justification (coût Chromium réel de la Phase 4 pour
   stage="action"). Un candidat non encore diagnostiqué au moment où ce
   budget est épuisé n'est PAS touché cette invocation (aucun
   upstream_run.json écrit pour lui) : il reste éligible à une invocation
   future, exactement comme un case au-delà de --max-cases pour l'étape
   existante. Un DiagnosisError (échec opérationnel : manifest.json/
   validation_report.json manquant ou invalide) est signalé (upstream_run.json
   terminal=false, is_error=true) — jamais de retry dans cette même invocation.

4. Déduplication, UNE FOIS, après le point 3 et AVANT toute sélection de
   contexte (Survey/case_grouping.py::write_case_groups, sur ses racines par
   défaut — jamais réimplémentée : déjà idempotente par construction, un
   groupe complet sur disque n'est jamais régénéré ni étendu, vérifié dans
   son code avant d'écrire ce point). Chaque membre d'un groupe FORMÉ (nouveau
   ou déjà gelé, recalculé identique par compute_case_groups) reçoit, s'il
   n'en a pas déjà un, un upstream_run.json terminal=true (stopped_at=
   "deduplication") nommant le group_id qui le remplace désormais — c'est
   cet artefact qui, à l'invocation suivante, l'exclut définitivement de la
   sélection du point 2 (en plus de l'exclusion directe déjà faite via
   group_members.json). Le group_id lui-même est un dossier failure_cases/
   ordinaire une fois formé (manifest.json + diagnosis.json déjà copiés par
   Survey/case_grouping.py) : il retraverse naturellement la sélection du
   point 2 et poursuit au point 5 comme n'importe quel case. Un
   CaseGroupingError (racine failure_cases/diagnoses introuvable — véritable
   erreur d'usage, jamais un case précis) est un avertissement : les cases
   continuent individuellement, jamais un arrêt de l'invocation.

5. Pour chaque case retenu (sélection du point 2 RECALCULÉE après le point 4,
   filtrée aux seuls cases ayant désormais un diagnosis.json — ce qui exclut
   naturellement les candidats non diagnostiqués faute de budget au point 3
   et inclut les group_id fraîchement formés) : Phase 5
   (Survey/context_selector.py::write_context_selection), Phase 6
   (Survey/prompt_generator.py::write_prompt), Phase 7
   (Survey/autofix_worktree.py::prepare_autofix_worktree) — chacune appelée
   SEULEMENT si son artefact de sortie n'existe pas déjà (context_selection.json/
   prompt.txt ou MANUAL_REVIEW_REQUIRED.txt/worktree.json), pour permettre une
   reprise après interruption sans jamais recalculer un artefact déjà présent.
   Deux arrêts NORMAUX (terminal=true, is_error=false, jamais une exception) :
   MANUAL_REVIEW_REQUIRED.txt écrit par la Phase 6 (case non éligible à un
   prompt automatique) ; case jugé inéligible par
   Survey/autofix_worktree.py::check_eligibility (nom exact vérifié dans le
   code), appelée SÉPARÉMENT par ce module avant prepare_autofix_worktree —
   seule façon de distinguer une INÉLIGIBILITÉ (stage/verdict/confiance :
   étale les raisons, terminal) d'un ÉCHEC OPÉRATIONNEL git dans
   prepare_autofix_worktree lui-même (AutofixWorktreeError une fois
   l'éligibilité déjà confirmée par ce module : terminal=false, is_error=true,
   jamais de retry automatique — prepare_autofix_worktree ne distingue pas
   ces deux cas par des exceptions différentes, seul un appel préalable à
   check_eligibility le permet).

6. Chaque case touché par les points 3 à 5 écrit
   autofix_pipeline_runs/<case_id>/upstream_run.json (même convention JSON que
   les phases précédentes : schema_version, case_id, created_at, stopped_at,
   stop_reason, terminal, artefacts déjà produits) — TOUJOURS écrasé sans
   garde --force, même raisonnement que pipeline_run.json pour l'étape
   existante (instantané de la dernière tentative, jamais une précondition
   consommée par une autre phase). terminal=true empêche tout retraitement
   AUTOMATIQUE par une invocation future (évite de relancer un Chromium pour
   un résultat déterministe, cf. point 3) ; pour retenter malgré tout :
   suppression manuelle de ce fichier. terminal=false (échec opérationnel)
   n'empêche PAS une invocation future de retenter automatiquement ce même
   case (pas d'exclusion dans discover_upstream_candidates), mais jamais de
   retry AUTOMATIQUE dans la même invocation.

7. --no-upstream désactive entièrement cette étape (comportement exact
   d'avant ce chantier) ; l'étape amont est active PAR DÉFAUT.

Sortie : run_autofix_pipeline() retourne désormais (upstream_summaries,
case_summaries) — les deux imprimés par tools/run_autofix_pipeline.py, la
liste amont en premier, avec pour chaque case son point d'arrêt et sa raison
(dont les cases fusionnés dans un groupe, point 4 ci-dessus).
"""

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from Survey.autofix_worktree import AutofixWorktreeError, check_eligibility, prepare_autofix_worktree
from Survey.case_grouping import CaseGroupingError, write_case_groups
from Survey.confidence_score import (
    CONFIDENCE_HIGH,
    CONFIDENCE_REJECT,
    ConfidenceScoreError,
    write_patch_confidence,
)
from Survey.context_selector import ContextSelectionError, write_context_selection
from Survey.extractor_integrity_gate import (
    IntegrityGateError,
    write_extractor_integrity_check,
)
from Survey.failure_diagnosis import DiagnosisError, write_diagnosis
from Survey.human_review import HumanReviewError, send_review_request
from Survey.log_utils import log_debug, log_info
from Survey.merge_executor import STATUS_ALREADY_MERGED, STATUS_MERGED
from Survey.parallel_safety import ParallelSafetyError, check_pre_launch_safety
from Survey.patch_replay import PatchReplayError, write_patch_replay
from Survey.prompt_generator import (
    MANUAL_REVIEW_FILENAME,
    PROMPT_FILENAME,
    PromptGenerationError,
    write_prompt,
)
from Survey.static_validator import StaticValidationError, write_static_validation

_TAG = "[AUTOFIX_ORCHESTRATOR]"
SCHEMA_VERSION = "1.0"

DEFAULT_MAX_CASES = 5
DEFAULT_CLAUDE_TIMEOUT_S = 600.0
DEFAULT_ALLOWED_TOOLS = "Read Edit Write Grep Glob"
DEFAULT_PERMISSION_MODE = "acceptEdits"

# Borne conservatrice, distincte de DEFAULT_MAX_CASES : la Phase 4 (diagnostic)
# lance un vrai Chromium isolé pour tout case stage="action" avec
# real_dispatch_replay (Survey/replay_browser.py::execute_case_action) — un
# coût par case bien plus élevé qu'un simple calcul JSON. Ne borne QUE le
# nombre de cases NOUVELLEMENT diagnostiqués par invocation (cf. docstring de
# run_upstream_stage) : un case déjà diagnostiqué lors d'une invocation
# précédente continue d'avancer (Phases 5/6/7) sans être compté ici.
DEFAULT_MAX_UPSTREAM_CASES = 3

# Bornes de stockage pour la sortie brute de l'invocation Claude Code —
# --output-format json produit normalement une sortie compacte, mais jamais
# de croissance non bornée par précaution (même philosophie que
# Survey/static_validator.py::_check_tests, err.strip()[-4000:]).
_MAX_RAW_OUTPUT_CHARS = 200_000

STATUS_SUCCESS = "SUCCESS"
STATUS_FAILURE = "FAILURE"
STATUS_TIMEOUT = "TIMEOUT"
STATUS_ERROR = "ERROR"

STAGE_PARALLEL_SAFETY = "parallel_safety"
STAGE_CLAUDE_INVOCATION = "claude_invocation"
STAGE_STATIC_VALIDATION = "static_validation"
STAGE_PATCH_REPLAY = "patch_replay"
STAGE_EXTRACTOR_INTEGRITY = "extractor_integrity"
STAGE_CONFIDENCE_SCORE = "confidence_score"
STAGE_HUMAN_REVIEW = "human_review"

# Étapes de l'étape amont (failure_cases/ -> worktree prêt), distinctes des
# étapes ci-dessus (qui commencent, elles, une fois worktree.json déjà là).
STAGE_UPSTREAM_DIAGNOSIS = "diagnosis"
STAGE_UPSTREAM_DEDUPLICATION = "deduplication"
STAGE_UPSTREAM_CONTEXT_SELECTION = "context_selection"
STAGE_UPSTREAM_PROMPT = "prompt_generation"
STAGE_UPSTREAM_WORKTREE_ELIGIBILITY = "worktree_eligibility"
STAGE_UPSTREAM_WORKTREE = "worktree"

# Composant de chemin unique, allowlist conservatrice — même garde-fou que
# Survey/autofix_worktree.py::_CASE_ID_RE, dupliqué volontairement (modules
# indépendants, cf. convention déjà en place ailleurs dans ce chantier pour
# ce même petit utilitaire).
_CASE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")
_WINDOWS_RESERVED_NAMES = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


class AutofixOrchestratorError(Exception):
    """Erreur d'usage bloquante (racine invalide, --max-cases invalide). Jamais
    levée pour l'échec/le report d'un case particulier — cf. CaseRunSummary."""


class AutofixOrchestratorExistsError(AutofixOrchestratorError):
    """codex_runs/<case_id>/run_result.json existe déjà et force=False."""


def _is_safe_case_id(case_id: str) -> bool:
    if not case_id or not _CASE_ID_RE.match(case_id):
        return False
    if ".." in case_id:
        return False
    if case_id.lower() in _WINDOWS_RESERVED_NAMES:
        return False
    return True


def _load_json(path: Path) -> "tuple[Any, Optional[str]]":
    if not path.is_file():
        return None, f"{path} absent"
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except OSError as exc:
        return None, f"{path} illisible ({exc})"
    except ValueError as exc:  # json.JSONDecodeError est une sous-classe de ValueError
        return None, f"{path} JSON invalide ({exc})"


# ═══════════════════════ Étape amont : failure_cases/ -> worktree prêt ═══════


@dataclass
class UpstreamCaseSummary:
    case_id: str
    stopped_at: str
    stop_reason: Optional[str]
    terminal: bool
    is_error: bool
    artifacts: "dict[str, str]" = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "case_id": self.case_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "stopped_at": self.stopped_at,
            "stop_reason": self.stop_reason,
            "terminal": self.terminal,
            "is_error": self.is_error,
            "artifacts": self.artifacts,
        }


def _upstream_run_paths(out_root: Path, case_id: str) -> "tuple[Path, Path]":
    out_dir = out_root / case_id
    return out_dir, out_dir / "upstream_run.json"


def write_upstream_run_result(
    summary: UpstreamCaseSummary,
    *,
    out_root: "str | Path" = "autofix_pipeline_runs",
) -> Path:
    """TOUJOURS écrasé, sans garde --force — même raisonnement que
    write_pipeline_run_summary (instantané de la dernière tentative de
    l'étape amont pour ce case, jamais une précondition consommée par une
    autre phase). Vit dans le même dossier que pipeline_run.json (nom de
    fichier distinct : upstream_run.json), sans conflit."""
    out_root = Path(out_root)
    out_dir, out_file = _upstream_run_paths(out_root, summary.case_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(summary.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    log_info(
        _TAG,
        f"case={summary.case_id} [amont] stopped_at={summary.stopped_at!r} "
        f"terminal={summary.terminal} is_error={summary.is_error} -> {out_file}",
    )
    return out_file


def _group_members_on_disk(failure_cases_root: Path) -> "set[str]":
    """Union des membres de TOUS les groupes déjà formés
    (failure_cases/dupgroup_*/group_members.json) — ces membres ne sont
    jamais des candidats individuels (cf. Survey/case_grouping.py, "le
    group_id est traité comme un case ordinaire"). Un group_members.json
    absent/illisible pour un dossier dupgroup_* présent est ignoré pour ce
    groupe (aucun membre compté pour lui) plutôt que de bloquer la
    découverte des autres cases — jamais une exception ici."""
    from Survey.case_grouping import GROUP_ID_PREFIX

    members: "set[str]" = set()
    if not failure_cases_root.is_dir():
        return members
    for entry in failure_cases_root.iterdir():
        if not entry.is_dir() or not entry.name.startswith(GROUP_ID_PREFIX):
            continue
        data, _err = _load_json(entry / "group_members.json")
        if isinstance(data, dict):
            for m in data.get("members") or []:
                if isinstance(m, str):
                    members.add(m)
    return members


def discover_upstream_candidates(
    *,
    failure_cases_root: Path,
    worktrees_root: Path,
    pipeline_runs_root: Path,
) -> "list[str]":
    """cf. docstring du module ("Étape amont", point 2) : dossiers de
    failure_cases/ (manifest.json présent, case_id sûr) sans worktree.json
    (Phase 7 déjà faite), sans upstream_run.json terminal=true (issue
    définitive déjà atteinte), et qui ne sont membres d'aucun groupe déjà
    formé. Triés par case_id (ordre déterministe). Lecture seule."""
    if not failure_cases_root.is_dir():
        return []
    grouped_members = _group_members_on_disk(failure_cases_root)

    out: "list[str]" = []
    for entry in sorted(failure_cases_root.iterdir()):
        if not entry.is_dir():
            continue
        case_id = entry.name
        if not _is_safe_case_id(case_id):
            log_debug(_TAG, f"case_id ignoré (non sûr comme composant de chemin) : {case_id!r}")
            continue
        if not (entry / "manifest.json").is_file():
            continue
        if case_id in grouped_members:
            continue
        if (worktrees_root / case_id / "worktree.json").is_file():
            continue
        upstream_data, _err = _load_json(pipeline_runs_root / case_id / "upstream_run.json")
        if isinstance(upstream_data, dict) and upstream_data.get("terminal"):
            continue
        out.append(case_id)
    return out


def _run_fleet_import() -> None:
    """--import-fleet uniquement (désactivé par défaut). Réutilise
    Survey/fleet_case_import.py::import_available_cases telle quelle, avec
    ses racines/budgets par défaut. Import lazy (dépendance boto3
    optionnelle, seulement nécessaire pour --import-fleet) : un échec de
    n'importe quelle nature (config R2 manquante, dépendance absente,
    réseau) est un avertissement journalisé, jamais un arrêt de
    l'invocation — cf. docstring du module."""
    try:
        from Survey.fleet_case_import import FleetImportError, import_available_cases
    except Exception as exc:  # dépendance optionnelle potentiellement absente
        log_info(_TAG, f"avertissement : import fleet indisponible (dépendance manquante ?) : {exc}")
        return

    try:
        results, warnings = import_available_cases()
    except FleetImportError as exc:
        log_info(_TAG, f"avertissement : import fleet échoué (FLEET_R2_* absentes ?) : {exc}")
        return
    except Exception as exc:
        log_info(_TAG, f"avertissement : import fleet échoué (réseau ?) : {type(exc).__name__}: {exc}")
        return

    imported = sum(1 for r in results if r.status == "IMPORTED")
    log_info(
        _TAG,
        f"import fleet : {imported}/{len(results)} case(s) importé(s), {len(warnings)} avertissement(s)",
    )


def _process_upstream_case(
    case_id: str,
    *,
    failure_cases_root: Path,
    diagnoses_root: Path,
    context_selections_root: Path,
    prompts_root: Path,
    worktrees_root: Path,
) -> UpstreamCaseSummary:
    """Phases 5, 6, 7 pour UN case déjà diagnostiqué (diagnosis.json présent —
    garanti par l'appelant). Chaque étape est sautée si son artefact de
    sortie existe déjà (reprise après interruption, cf. docstring du
    module)."""
    failure_case_dir = failure_cases_root / case_id
    diagnosis_dir = diagnoses_root / case_id
    artifacts: "dict[str, str]" = {}

    # ── Phase 5 ──────────────────────────────────────────────────────────────
    context_selection_dir = context_selections_root / case_id
    context_selection_path = context_selection_dir / "context_selection.json"
    if context_selection_path.is_file():
        artifacts[STAGE_UPSTREAM_CONTEXT_SELECTION] = str(context_selection_path)
    else:
        try:
            path = write_context_selection(
                diagnosis_dir, failure_cases_root=failure_cases_root,
                out_root=context_selections_root, force=False,
            )
        except ContextSelectionError as exc:
            return UpstreamCaseSummary(
                case_id=case_id, stopped_at=STAGE_UPSTREAM_CONTEXT_SELECTION,
                stop_reason=str(exc), terminal=False, is_error=True, artifacts=artifacts,
            )
        artifacts[STAGE_UPSTREAM_CONTEXT_SELECTION] = str(path)

    # ── Phase 6 ──────────────────────────────────────────────────────────────
    prompt_dir = prompts_root / case_id
    prompt_file = prompt_dir / PROMPT_FILENAME
    manual_review_file = prompt_dir / MANUAL_REVIEW_FILENAME
    if prompt_file.is_file() or manual_review_file.is_file():
        artifacts[STAGE_UPSTREAM_PROMPT] = str(prompt_file if prompt_file.is_file() else manual_review_file)
    else:
        try:
            path = write_prompt(diagnosis_dir, context_selection_dir, out_root=prompts_root, force=False)
        except PromptGenerationError as exc:
            return UpstreamCaseSummary(
                case_id=case_id, stopped_at=STAGE_UPSTREAM_PROMPT,
                stop_reason=str(exc), terminal=False, is_error=True, artifacts=artifacts,
            )
        artifacts[STAGE_UPSTREAM_PROMPT] = str(path)

    if manual_review_file.is_file():
        return UpstreamCaseSummary(
            case_id=case_id, stopped_at=STAGE_UPSTREAM_PROMPT,
            stop_reason=(
                f"{MANUAL_REVIEW_FILENAME} (Phase 6) — case non éligible à la génération "
                "automatique de prompt, revue manuelle requise"
            ),
            terminal=True, is_error=False, artifacts=artifacts,
        )

    # ── Phase 7 ──────────────────────────────────────────────────────────────
    worktree_manifest_path = worktrees_root / case_id / "worktree.json"
    if worktree_manifest_path.is_file():
        artifacts[STAGE_UPSTREAM_WORKTREE] = str(worktree_manifest_path)
        return UpstreamCaseSummary(
            case_id=case_id, stopped_at=STAGE_UPSTREAM_WORKTREE, stop_reason=None,
            terminal=True, is_error=False, artifacts=artifacts,
        )

    # check_eligibility (nom exact) appelée SÉPARÉMENT de prepare_autofix_worktree
    # (qui l'appelle aussi en interne, mais ne distingue jamais par des
    # exceptions différentes une inéligibilité d'un échec opérationnel git) —
    # seule façon de distinguer les deux, cf. docstring du module.
    eligibility = check_eligibility(
        failure_case_dir=failure_case_dir, diagnosis_dir=diagnosis_dir, prompt_dir=prompt_dir,
    )
    if not eligibility.eligible:
        return UpstreamCaseSummary(
            case_id=case_id, stopped_at=STAGE_UPSTREAM_WORKTREE_ELIGIBILITY,
            stop_reason="; ".join(eligibility.reasons), terminal=True, is_error=False, artifacts=artifacts,
        )

    try:
        prepare_autofix_worktree(
            failure_case_dir=failure_case_dir, diagnosis_dir=diagnosis_dir, prompt_dir=prompt_dir,
            out_root=worktrees_root, force=False,
        )
    except AutofixWorktreeError as exc:
        return UpstreamCaseSummary(
            case_id=case_id, stopped_at=STAGE_UPSTREAM_WORKTREE,
            stop_reason=str(exc), terminal=False, is_error=True, artifacts=artifacts,
        )

    artifacts[STAGE_UPSTREAM_WORKTREE] = str(worktree_manifest_path)
    return UpstreamCaseSummary(
        case_id=case_id, stopped_at=STAGE_UPSTREAM_WORKTREE, stop_reason=None,
        terminal=True, is_error=False, artifacts=artifacts,
    )


def run_upstream_stage(
    *,
    failure_cases_root: "str | Path" = "failure_cases",
    diagnoses_root: "str | Path" = "diagnoses",
    context_selections_root: "str | Path" = "context_selections",
    prompts_root: "str | Path" = "prompts",
    worktrees_root: "str | Path" = "autofix_worktrees",
    pipeline_runs_root: "str | Path" = "autofix_pipeline_runs",
    import_fleet: bool = False,
    max_upstream_cases: int = DEFAULT_MAX_UPSTREAM_CASES,
) -> "list[UpstreamCaseSummary]":
    """Point d'entrée de l'étape amont — cf. docstring du module pour le
    détail point par point. Désactivée par --no-upstream côté
    run_autofix_pipeline (cette fonction n'est alors jamais appelée)."""
    failure_cases_root = Path(failure_cases_root)
    diagnoses_root = Path(diagnoses_root)
    context_selections_root = Path(context_selections_root)
    prompts_root = Path(prompts_root)
    worktrees_root = Path(worktrees_root)
    pipeline_runs_root = Path(pipeline_runs_root)

    summaries: "list[UpstreamCaseSummary]" = []

    # ── Point 1 ────────────────────────────────────────────────────────────
    if import_fleet:
        _run_fleet_import()

    # ── Points 2/3 : sélection + Phase 4 bornée aux diagnostics NOUVEAUX ────
    candidates = discover_upstream_candidates(
        failure_cases_root=failure_cases_root, worktrees_root=worktrees_root,
        pipeline_runs_root=pipeline_runs_root,
    )
    new_diagnoses = 0
    for case_id in candidates:
        diagnosis_path = diagnoses_root / case_id / "diagnosis.json"
        if diagnosis_path.is_file():
            continue  # réutilisé tel quel, jamais recalculé
        if new_diagnoses >= max_upstream_cases:
            log_debug(
                _TAG,
                f"case={case_id} : diagnostic (Phase 4) reporté "
                f"(--max-upstream-cases={max_upstream_cases} atteint pour cette invocation)",
            )
            continue
        new_diagnoses += 1
        try:
            write_diagnosis(failure_cases_root / case_id, out_root=diagnoses_root, force=False)
        except DiagnosisError as exc:
            summary = UpstreamCaseSummary(
                case_id=case_id, stopped_at=STAGE_UPSTREAM_DIAGNOSIS,
                stop_reason=str(exc), terminal=False, is_error=True,
            )
            write_upstream_run_result(summary, out_root=pipeline_runs_root)
            summaries.append(summary)

    # ── Point 4 : déduplication, une fois, avant toute sélection de contexte ─
    grouping_result = None
    try:
        grouping_result, _report_path = write_case_groups(
            diagnoses_root=diagnoses_root, failure_cases_root=failure_cases_root,
            context_selections_root=context_selections_root,
        )
    except CaseGroupingError as exc:
        log_info(
            _TAG,
            f"avertissement : regroupement (déduplication) échoué, cases traités individuellement : {exc}",
        )

    if grouping_result is not None:
        for group in grouping_result.groups:
            for member_id in group.members:
                if (pipeline_runs_root / member_id / "upstream_run.json").is_file():
                    continue  # déjà signalé par une invocation précédente
                summary = UpstreamCaseSummary(
                    case_id=member_id, stopped_at=STAGE_UPSTREAM_DEDUPLICATION,
                    stop_reason=(
                        f"fusionné dans le groupe {group.group_id} "
                        f"(représentant={group.representative_case_id}) — traité désormais comme ce "
                        "seul case, jamais individuellement"
                    ),
                    terminal=True, is_error=False,
                    artifacts={"case_group": str(failure_cases_root / group.group_id)},
                )
                write_upstream_run_result(summary, out_root=pipeline_runs_root)
                summaries.append(summary)

    # ── Point 5 : Phases 5/6/7, sélection recalculée + filtrée aux diagnostiqués ─
    final_ids = [
        cid for cid in discover_upstream_candidates(
            failure_cases_root=failure_cases_root, worktrees_root=worktrees_root,
            pipeline_runs_root=pipeline_runs_root,
        )
        if (diagnoses_root / cid / "diagnosis.json").is_file()
    ]
    for case_id in final_ids:
        summary = _process_upstream_case(
            case_id,
            failure_cases_root=failure_cases_root, diagnoses_root=diagnoses_root,
            context_selections_root=context_selections_root, prompts_root=prompts_root,
            worktrees_root=worktrees_root,
        )
        write_upstream_run_result(summary, out_root=pipeline_runs_root)
        summaries.append(summary)

    return summaries


# ═══════════════════════ Découverte des cases éligibles ══════════════════════


@dataclass
class EligibleCase:
    case_id: str
    worktree_manifest_path: Path
    prompt_path: Path


def discover_eligible_cases(
    *,
    worktrees_root: "str | Path",
    prompts_root: "str | Path",
    codex_runs_root: "str | Path",
) -> "list[EligibleCase]":
    """Toutes les conditions ensemble (cf. docstring du module) ; triés par
    case_id (ordre déterministe). Lecture seule : ne modifie rien."""
    worktrees_root = Path(worktrees_root)
    prompts_root = Path(prompts_root)
    codex_runs_root = Path(codex_runs_root)

    if not worktrees_root.is_dir():
        return []

    eligible: "list[EligibleCase]" = []
    for entry in sorted(worktrees_root.iterdir()):
        if not entry.is_dir():
            continue
        case_id = entry.name
        if not _is_safe_case_id(case_id):
            log_debug(_TAG, f"case_id ignoré (non sûr comme composant de chemin) : {case_id!r}")
            continue

        worktree_manifest_path = entry / "worktree.json"
        if not worktree_manifest_path.is_file():
            continue

        prompt_path = prompts_root / case_id / "prompt.txt"
        manual_review_path = prompts_root / case_id / "MANUAL_REVIEW_REQUIRED.txt"
        if not prompt_path.is_file() or manual_review_path.is_file():
            continue

        if (codex_runs_root / case_id / "run_result.json").is_file():
            continue

        eligible.append(EligibleCase(
            case_id=case_id,
            worktree_manifest_path=worktree_manifest_path,
            prompt_path=prompt_path,
        ))

    return eligible


# ═══════════════════════ Point 1 — sécurité du parallélisme ══════════════════


@dataclass
class SafetyOutcome:
    checked: bool
    safe: bool
    reason: Optional[str]


def _worktree_terminal_state(
    case_id: str,
    *,
    merge_results_root: Path,
    human_reviews_root: Path,
    merge_reviews_root: Path,
    confidence_scores_root: Path,
    static_validations_root: Path,
    codex_runs_root: Path,
) -> "tuple[bool, str]":
    """Détermine si le worktree case_id a atteint une issue définitive (donc
    n'est plus "en vol") — cf. docstring du module (Point 1) pour la
    définition exacte et sa justification. Retourne (definitif, état_lu) :
    état_lu résume la valeur lue de chaque source (ou None si absente/
    illisible), pour que l'appelant puisse exposer l'état exact d'un case
    bloquant. Un artefact absent, illisible, malformé, ou dont le champ
    attendu est absent, ne compte JAMAIS comme définitif — le worktree reste
    "en vol" dans ce cas (jamais une hypothèse optimiste)."""
    states: "dict[str, Any]" = {}

    merge_result, _ = _load_json(merge_results_root / case_id / "merge_result.json")
    merge_status = merge_result.get("status") if isinstance(merge_result, dict) else None
    states["merge_result.status"] = merge_status
    if merge_status in (STATUS_MERGED, STATUS_ALREADY_MERGED):
        return True, f"merge_result.status={merge_status!r}"

    human_decision_data, _ = _load_json(human_reviews_root / case_id / "decision.json")
    human_decision = human_decision_data.get("decision") if isinstance(human_decision_data, dict) else None
    states["human_review.decision"] = human_decision
    if human_decision == "REJECTED":
        return True, f"human_review.decision={human_decision!r}"

    merge_review_data, _ = _load_json(merge_reviews_root / case_id / "decision.json")
    merge_review_decision = merge_review_data.get("decision") if isinstance(merge_review_data, dict) else None
    states["merge_review.decision"] = merge_review_decision
    if merge_review_decision == "REJECTED":
        return True, f"merge_review.decision={merge_review_decision!r}"

    confidence_data, _ = _load_json(confidence_scores_root / case_id / "confidence_score.json")
    confidence = confidence_data.get("confidence") if isinstance(confidence_data, dict) else None
    states["confidence_score.confidence"] = confidence
    if confidence == CONFIDENCE_REJECT:
        return True, f"confidence_score.confidence={confidence!r}"

    static_data, _ = _load_json(static_validations_root / case_id / "validation_static.json")
    static_verdict = static_data.get("verdict") if isinstance(static_data, dict) else None
    states["validation_static.verdict"] = static_verdict
    if static_verdict == "REJECTED":
        return True, f"validation_static.verdict={static_verdict!r}"

    run_result_data, _ = _load_json(codex_runs_root / case_id / "run_result.json")
    run_status = run_result_data.get("status") if isinstance(run_result_data, dict) else None
    states["run_result.status"] = run_status
    if run_status is not None and run_status != STATUS_SUCCESS:
        return True, f"run_result.status={run_status!r}"

    return False, "en vol (" + ", ".join(f"{k}={v!r}" for k, v in states.items()) + ")"


def _check_case_parallel_safety(
    case_id: str,
    *,
    context_selections_root: Path,
    worktrees_root: Path,
    merge_results_root: Path,
    human_reviews_root: Path,
    merge_reviews_root: Path,
    confidence_scores_root: Path,
    static_validations_root: Path,
    codex_runs_root: Path,
) -> SafetyOutcome:
    """cf. docstring du module (Point 1) pour la définition retenue de "en
    vol" et sa justification. N'écrit rien : lecture seule, jamais de
    décision automatique au-delà du report du seul case candidat."""
    all_other_ids = []
    if worktrees_root.is_dir():
        all_other_ids = sorted(
            p.name for p in worktrees_root.iterdir()
            if p.is_dir() and p.name != case_id and (p / "worktree.json").is_file()
        )

    in_flight_states: "dict[str, str]" = {}
    for other_id in all_other_ids:
        is_terminal, state_label = _worktree_terminal_state(
            other_id,
            merge_results_root=merge_results_root,
            human_reviews_root=human_reviews_root,
            merge_reviews_root=merge_reviews_root,
            confidence_scores_root=confidence_scores_root,
            static_validations_root=static_validations_root,
            codex_runs_root=codex_runs_root,
        )
        if is_terminal:
            log_debug(_TAG, f"case={other_id} : worktree exclu du contrôle (issue définitive — {state_label})")
            continue
        in_flight_states[other_id] = state_label

    in_flight_ids = sorted(in_flight_states)

    if not in_flight_ids:
        log_debug(_TAG, f"case={case_id} : aucun autre worktree en vol — contrôle trivialement sûr")
        return SafetyOutcome(checked=False, safe=True, reason=None)

    paths = [context_selections_root / case_id / "context_selection.json"] + [
        context_selections_root / cid / "context_selection.json" for cid in in_flight_ids
    ]

    try:
        result = check_pre_launch_safety(paths)
    except ParallelSafetyError as exc:
        return SafetyOutcome(
            checked=True, safe=False,
            reason=f"contrôle de sécurité du parallélisme impossible (Survey/parallel_safety.py) : {exc}",
        )

    unsafe_for_candidate = [
        pair for pair in result.unsafe_pairs
        if case_id in (pair.case_id_a, pair.case_id_b)
    ]
    if unsafe_for_candidate:
        detail = "; ".join(
            f"{p.case_id_a}<->{p.case_id_b} fichier(s) partagé(s)={p.shared_files}"
            for p in unsafe_for_candidate
        )
        blocker_ids = sorted({
            (pair.case_id_b if pair.case_id_a == case_id else pair.case_id_a)
            for pair in unsafe_for_candidate
        })
        blockers = "; ".join(f"{bid} [{in_flight_states[bid]}]" for bid in blocker_ids)
        return SafetyOutcome(
            checked=True, safe=False,
            reason=(
                f"fichier(s) candidat(s) partagé(s) avec au moins un case en vol : {detail} "
                f"— bloqué par : {blockers}"
            ),
        )

    return SafetyOutcome(checked=True, safe=True, reason=None)


# ═══════════════════════ Point 2 — invocation Claude Code ════════════════════


@dataclass
class ClaudeInvocationResult:
    case_id: str
    branch: str
    worktree_path: str
    status: str
    exit_code: Optional[int]
    timed_out: bool
    session_id: Optional[str]
    is_error: Optional[bool]
    subtype: Optional[str]
    command: "list[str]"
    raw_stdout: str
    raw_stderr: str
    error: Optional[str]
    warnings: "list[str]" = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "case_id": self.case_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "branch": self.branch,
            "worktree_path": self.worktree_path,
            "status": self.status,
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "session_id": self.session_id,
            "is_error": self.is_error,
            "subtype": self.subtype,
            "command": self.command,
            "raw_stdout": self.raw_stdout,
            "raw_stderr": self.raw_stderr,
            "error": self.error,
            "warnings": self.warnings,
        }


def _decode(part: Any) -> str:
    if isinstance(part, bytes):
        return part.decode("utf-8", errors="replace")
    return part or ""


def invoke_claude_headless(
    *,
    case_id: str,
    branch: str,
    worktree_path: Path,
    prompt_text: str,
    allowed_tools: str = DEFAULT_ALLOWED_TOOLS,
    permission_mode: str = DEFAULT_PERMISSION_MODE,
    timeout_s: float = DEFAULT_CLAUDE_TIMEOUT_S,
) -> ClaudeInvocationResult:
    """Un seul mécanisme, un seul essai — jamais de retry. cf. docstring du
    module (Point 2) pour la justification de chaque flag, vérifiée avant
    d'écrire cette fonction (claude --help + appels réels)."""
    claude_bin = shutil.which("claude")
    if not claude_bin:
        return ClaudeInvocationResult(
            case_id=case_id, branch=branch, worktree_path=str(worktree_path),
            status=STATUS_ERROR, exit_code=None, timed_out=False,
            session_id=None, is_error=None, subtype=None, command=[],
            raw_stdout="", raw_stderr="",
            error="binaire 'claude' introuvable sur PATH — invocation impossible",
        )

    cmd = [
        claude_bin, "-p",
        "--output-format", "json",
        "--allowedTools", allowed_tools,
        "--permission-mode", permission_mode,
    ]
    log_debug(_TAG, f"case={case_id} : {' '.join(cmd)} (cwd={worktree_path}, timeout={timeout_s}s)")

    try:
        proc = subprocess.run(
            cmd, cwd=str(worktree_path), input=prompt_text,
            capture_output=True, text=True, timeout=timeout_s, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return ClaudeInvocationResult(
            case_id=case_id, branch=branch, worktree_path=str(worktree_path),
            status=STATUS_TIMEOUT, exit_code=None, timed_out=True,
            session_id=None, is_error=None, subtype=None, command=cmd,
            raw_stdout=_decode(exc.stdout)[-_MAX_RAW_OUTPUT_CHARS:],
            raw_stderr=_decode(exc.stderr)[-_MAX_RAW_OUTPUT_CHARS:],
            error=f"invocation Claude Code expirée après {timeout_s:.1f}s (budget dépassé)",
        )
    except OSError as exc:
        return ClaudeInvocationResult(
            case_id=case_id, branch=branch, worktree_path=str(worktree_path),
            status=STATUS_ERROR, exit_code=None, timed_out=False,
            session_id=None, is_error=None, subtype=None, command=cmd,
            raw_stdout="", raw_stderr="", error=f"exécution impossible : {exc}",
        )

    raw_stdout = (proc.stdout or "")[-_MAX_RAW_OUTPUT_CHARS:]
    raw_stderr = (proc.stderr or "")[-_MAX_RAW_OUTPUT_CHARS:]

    parsed: Optional[dict] = None
    parse_error: Optional[str] = None
    stripped = (proc.stdout or "").strip()
    if not stripped:
        parse_error = "sortie standard vide — aucun résultat JSON exploitable"
    else:
        try:
            candidate = json.loads(stripped)
        except ValueError as exc:
            parse_error = f"sortie standard non-JSON exploitable ({exc})"
        else:
            if isinstance(candidate, dict):
                parsed = candidate
            else:
                parse_error = "sortie standard JSON valide mais pas un objet"

    session_id = parsed.get("session_id") if parsed else None
    is_error = parsed.get("is_error") if parsed else None
    subtype = parsed.get("subtype") if parsed else None

    if proc.returncode != 0:
        status = STATUS_FAILURE
        error = f"code de sortie non nul ({proc.returncode})" + (f" ; {parse_error}" if parse_error else "")
    elif parsed is None:
        status = STATUS_ERROR
        error = parse_error
    elif is_error is False:
        status = STATUS_SUCCESS
        error = None
    else:
        status = STATUS_FAILURE
        error = f"Claude Code a rapporté is_error={is_error!r} (subtype={subtype!r})"

    return ClaudeInvocationResult(
        case_id=case_id, branch=branch, worktree_path=str(worktree_path),
        status=status, exit_code=proc.returncode, timed_out=False,
        session_id=session_id, is_error=is_error, subtype=subtype,
        command=cmd, raw_stdout=raw_stdout, raw_stderr=raw_stderr, error=error,
    )


def _run_result_paths(out_root: Path, case_id: str) -> "tuple[Path, Path]":
    out_dir = out_root / case_id
    return out_dir, out_dir / "run_result.json"


def write_run_result(
    result: ClaudeInvocationResult,
    *,
    out_root: "str | Path" = "codex_runs",
    force: bool = False,
) -> Path:
    """Persiste result sous out_root/<case_id>/run_result.json. Refuse un
    écrasement silencieux (sauf --force) — même si, en usage normal via
    run_autofix_pipeline, la découverte garantit déjà que ce chemin n'existe
    pas encore pour un case retenu (défense en profondeur, jamais une
    hypothèse fragile)."""
    out_root = Path(out_root)
    out_dir, out_file = _run_result_paths(out_root, result.case_id)

    if out_dir.exists():
        if not force:
            raise AutofixOrchestratorExistsError(
                f"run_result.json déjà existant : {out_dir} (utiliser --force pour régénérer)"
            )
        if not out_file.is_file():
            raise AutofixOrchestratorError(
                f"{out_dir} existe mais ne ressemble pas à une sortie générée par cet outil "
                "(pas de run_result.json) — suppression refusée, vérifier manuellement"
            )
        log_debug(_TAG, f"régénération forcée : suppression de {out_dir}")
        shutil.rmtree(out_dir)

    out_dir.mkdir(parents=True, exist_ok=False)
    out_file.write_text(json.dumps(result.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    log_info(_TAG, f"case={result.case_id} invocation Claude Code status={result.status} -> {out_file}")
    return out_file


# ═══════════════════════ Résumé de chaîne par case ════════════════════════════


@dataclass
class CaseRunSummary:
    case_id: str
    deferred: bool
    stopped_at: Optional[str]
    stop_reason: Optional[str]
    is_error: bool
    artifacts: "dict[str, str]" = field(default_factory=dict)
    confidence: Optional[str] = None
    notified: bool = False
    warnings: "list[str]" = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "case_id": self.case_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "deferred": self.deferred,
            "stopped_at": self.stopped_at,
            "stop_reason": self.stop_reason,
            "is_error": self.is_error,
            "artifacts": self.artifacts,
            "confidence": self.confidence,
            "notified": self.notified,
            "warnings": self.warnings,
        }


def _pipeline_run_paths(out_root: Path, case_id: str) -> "tuple[Path, Path]":
    out_dir = out_root / case_id
    return out_dir, out_dir / "pipeline_run.json"


def write_pipeline_run_summary(
    summary: CaseRunSummary,
    *,
    out_root: "str | Path" = "autofix_pipeline_runs",
) -> Path:
    """TOUJOURS écrasé, sans garde --force — cf. docstring du module ("Sortie
    : résumé par case") pour la justification de cet écart volontaire avec la
    convention --force du reste de ce chantier."""
    out_root = Path(out_root)
    out_dir, out_file = _pipeline_run_paths(out_root, summary.case_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(summary.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    log_info(
        _TAG,
        f"case={summary.case_id} stopped_at={summary.stopped_at!r} "
        f"notified={summary.notified} is_error={summary.is_error} -> {out_file}",
    )
    return out_file


# ═══════════════════════ Traitement d'un case ═════════════════════════════════


def process_case(
    case: EligibleCase,
    *,
    failure_cases_root: Path,
    diagnoses_root: Path,
    context_selections_root: Path,
    worktrees_root: Path,
    codex_runs_root: Path,
    static_validations_root: Path,
    patch_replays_root: Path,
    extractor_integrity_checks_root: Path,
    confidence_scores_root: Path,
    human_reviews_root: Path,
    merge_results_root: Path,
    merge_reviews_root: Path,
    claude_timeout_s: float,
    allowed_tools: str,
    permission_mode: str,
    force: bool,
) -> CaseRunSummary:
    """Traite UN case, du contrôle de parallélisme jusqu'à la notification
    humaine (ou l'arrêt contrôlé le plus loin possible dans cet ordre).
    Ne lève jamais d'exception vers l'appelant pour un problème propre à ce
    case : toute erreur contrôlée devient un CaseRunSummary avec is_error=True
    et stopped_at pointant l'étape concernée."""
    case_id = case.case_id
    artifacts: "dict[str, str]" = {}
    warnings: "list[str]" = []

    worktree_data, wt_err = _load_json(case.worktree_manifest_path)
    if wt_err or not isinstance(worktree_data, dict):
        return CaseRunSummary(
            case_id=case_id, deferred=True, stopped_at=None,
            stop_reason=f"worktree.json (Phase 7) {wt_err or 'ne contient pas un objet JSON'}",
            is_error=True, artifacts=artifacts, warnings=warnings,
        )
    if str(worktree_data.get("case_id") or "") != case_id:
        return CaseRunSummary(
            case_id=case_id, deferred=True, stopped_at=None,
            stop_reason=(
                f"worktree.json.case_id={worktree_data.get('case_id')!r} incohérent avec le "
                f"dossier {case_id!r}"
            ),
            is_error=True, artifacts=artifacts, warnings=warnings,
        )
    branch = str(worktree_data.get("branch") or "")
    worktree_path = Path(str(worktree_data.get("worktree_path") or ""))

    # ── Point 1 ────────────────────────────────────────────────────────────
    safety = _check_case_parallel_safety(
        case_id,
        context_selections_root=context_selections_root,
        worktrees_root=worktrees_root,
        merge_results_root=merge_results_root,
        human_reviews_root=human_reviews_root,
        merge_reviews_root=merge_reviews_root,
        confidence_scores_root=confidence_scores_root,
        static_validations_root=static_validations_root,
        codex_runs_root=codex_runs_root,
    )
    if not safety.safe:
        return CaseRunSummary(
            case_id=case_id, deferred=True, stopped_at=STAGE_PARALLEL_SAFETY,
            stop_reason=safety.reason, is_error=False, artifacts=artifacts, warnings=warnings,
        )

    # ── Point 2 ────────────────────────────────────────────────────────────
    prompt_text = case.prompt_path.read_text(encoding="utf-8")
    invocation = invoke_claude_headless(
        case_id=case_id, branch=branch, worktree_path=worktree_path,
        prompt_text=prompt_text, allowed_tools=allowed_tools,
        permission_mode=permission_mode, timeout_s=claude_timeout_s,
    )
    run_result_path = write_run_result(invocation, out_root=codex_runs_root, force=force)
    artifacts[STAGE_CLAUDE_INVOCATION] = str(run_result_path)

    if invocation.status != STATUS_SUCCESS:
        return CaseRunSummary(
            case_id=case_id, deferred=False, stopped_at=STAGE_CLAUDE_INVOCATION,
            stop_reason=invocation.error or f"status={invocation.status}",
            is_error=True, artifacts=artifacts, warnings=warnings,
        )

    # ── Point 3a — Phase 8 ──────────────────────────────────────────────────
    try:
        static_validation_path = write_static_validation(
            case.worktree_manifest_path, out_root=static_validations_root, force=force,
        )
    except StaticValidationError as exc:
        return CaseRunSummary(
            case_id=case_id, deferred=False, stopped_at=STAGE_STATIC_VALIDATION,
            stop_reason=str(exc), is_error=True, artifacts=artifacts, warnings=warnings,
        )
    artifacts[STAGE_STATIC_VALIDATION] = str(static_validation_path)

    static_data, _ = _load_json(static_validation_path)
    static_verdict = (static_data or {}).get("verdict")
    if static_verdict != "ACCEPTED":
        return CaseRunSummary(
            case_id=case_id, deferred=False, stopped_at=STAGE_STATIC_VALIDATION,
            stop_reason=f"validation_static.json.verdict={static_verdict!r} (\"ACCEPTED\" requis)",
            is_error=False, artifacts=artifacts, warnings=warnings,
        )

    # ── Point 3b — Phase 9 (continue quel que soit l'outcome) ──────────────
    try:
        patch_replay_path = write_patch_replay(
            failure_case_dir=failure_cases_root / case_id,
            diagnosis_dir=diagnoses_root / case_id,
            worktree_manifest_path=case.worktree_manifest_path,
            validation_static_path=static_validation_path,
            out_root=patch_replays_root, force=force,
        )
    except PatchReplayError as exc:
        return CaseRunSummary(
            case_id=case_id, deferred=False, stopped_at=STAGE_PATCH_REPLAY,
            stop_reason=str(exc), is_error=True, artifacts=artifacts, warnings=warnings,
        )
    artifacts[STAGE_PATCH_REPLAY] = str(patch_replay_path)

    # ── Point 3c — Phase 11-A ────────────────────────────────────────────────
    try:
        integrity_path = write_extractor_integrity_check(
            case.worktree_manifest_path, static_validation_path,
            out_root=extractor_integrity_checks_root, force=force,
        )
    except IntegrityGateError as exc:
        return CaseRunSummary(
            case_id=case_id, deferred=False, stopped_at=STAGE_EXTRACTOR_INTEGRITY,
            stop_reason=str(exc), is_error=True, artifacts=artifacts, warnings=warnings,
        )
    artifacts[STAGE_EXTRACTOR_INTEGRITY] = str(integrity_path)

    # ── Point 3d — Phase 12 (live_validation_path=None, jamais automatique) ─
    try:
        confidence_path = write_patch_confidence(
            worktree_manifest_path=case.worktree_manifest_path,
            validation_static_path=static_validation_path,
            patch_replay_path=patch_replay_path,
            extractor_integrity_check_path=integrity_path,
            live_validation_path=None,
            out_root=confidence_scores_root, force=force,
        )
    except ConfidenceScoreError as exc:
        return CaseRunSummary(
            case_id=case_id, deferred=False, stopped_at=STAGE_CONFIDENCE_SCORE,
            stop_reason=str(exc), is_error=True, artifacts=artifacts, warnings=warnings,
        )
    artifacts[STAGE_CONFIDENCE_SCORE] = str(confidence_path)

    confidence_data, _ = _load_json(confidence_path)
    confidence = (confidence_data or {}).get("confidence")

    if confidence != CONFIDENCE_HIGH:
        return CaseRunSummary(
            case_id=case_id, deferred=False, stopped_at=STAGE_CONFIDENCE_SCORE,
            stop_reason=f"confidence={confidence!r} (\"HIGH\" requis pour notifier)",
            is_error=False, artifacts=artifacts, confidence=confidence, warnings=warnings,
        )

    # ── Point 3e — Phase 13 ─────────────────────────────────────────────────
    try:
        review_result = send_review_request(
            confidence_score_dir=confidence_scores_root / case_id,
            diagnosis_dir=diagnoses_root / case_id,
            out_root=human_reviews_root, force=force,
        )
    except HumanReviewError as exc:
        return CaseRunSummary(
            case_id=case_id, deferred=False, stopped_at=STAGE_HUMAN_REVIEW,
            stop_reason=str(exc), is_error=True, artifacts=artifacts,
            confidence=confidence, warnings=warnings,
        )
    artifacts[STAGE_HUMAN_REVIEW] = str(review_result.pending_path)

    return CaseRunSummary(
        case_id=case_id, deferred=False, stopped_at=STAGE_HUMAN_REVIEW, stop_reason=None,
        is_error=False, artifacts=artifacts, confidence=confidence, notified=True, warnings=warnings,
    )


# ═══════════════════════ Orchestration de l'invocation complète ══════════════


def run_autofix_pipeline(
    *,
    failure_cases_root: "str | Path" = "failure_cases",
    diagnoses_root: "str | Path" = "diagnoses",
    context_selections_root: "str | Path" = "context_selections",
    prompts_root: "str | Path" = "prompts",
    worktrees_root: "str | Path" = "autofix_worktrees",
    codex_runs_root: "str | Path" = "codex_runs",
    static_validations_root: "str | Path" = "autofix_static_validations",
    patch_replays_root: "str | Path" = "patch_replays",
    extractor_integrity_checks_root: "str | Path" = "extractor_integrity_checks",
    confidence_scores_root: "str | Path" = "confidence_scores",
    human_reviews_root: "str | Path" = "human_reviews",
    merge_results_root: "str | Path" = "merge_results",
    merge_reviews_root: "str | Path" = "merge_reviews",
    pipeline_runs_root: "str | Path" = "autofix_pipeline_runs",
    max_cases: int = DEFAULT_MAX_CASES,
    claude_timeout_s: float = DEFAULT_CLAUDE_TIMEOUT_S,
    allowed_tools: str = DEFAULT_ALLOWED_TOOLS,
    permission_mode: str = DEFAULT_PERMISSION_MODE,
    force: bool = False,
    run_upstream: bool = True,
    import_fleet: bool = False,
    max_upstream_cases: int = DEFAULT_MAX_UPSTREAM_CASES,
) -> "tuple[list[UpstreamCaseSummary], list[CaseRunSummary]]":
    """Point d'entrée unique. Lance d'abord l'étape amont (failure_cases/ ->
    worktree prêt, cf. docstring du module) sauf run_upstream=False
    (--no-upstream), PUIS découvre jusqu'à max_cases cases éligibles pour
    l'étape existante (triés par case_id, worktrees fraîchement créés par
    l'étape amont inclus) et les traite un à la fois, strictement
    séquentiellement (aucun thread/process concurrent lancé par ce module)."""
    if max_cases < 1:
        raise AutofixOrchestratorError(f"--max-cases doit être >= 1 ({max_cases} fourni)")
    if max_upstream_cases < 1:
        raise AutofixOrchestratorError(f"--max-upstream-cases doit être >= 1 ({max_upstream_cases} fourni)")

    failure_cases_root = Path(failure_cases_root)
    diagnoses_root = Path(diagnoses_root)
    context_selections_root = Path(context_selections_root)
    prompts_root = Path(prompts_root)
    worktrees_root = Path(worktrees_root)
    codex_runs_root = Path(codex_runs_root)
    static_validations_root = Path(static_validations_root)
    patch_replays_root = Path(patch_replays_root)
    extractor_integrity_checks_root = Path(extractor_integrity_checks_root)
    confidence_scores_root = Path(confidence_scores_root)
    human_reviews_root = Path(human_reviews_root)
    merge_results_root = Path(merge_results_root)
    merge_reviews_root = Path(merge_reviews_root)
    pipeline_runs_root = Path(pipeline_runs_root)

    upstream_summaries: "list[UpstreamCaseSummary]" = []
    if run_upstream:
        upstream_summaries = run_upstream_stage(
            failure_cases_root=failure_cases_root,
            diagnoses_root=diagnoses_root,
            context_selections_root=context_selections_root,
            prompts_root=prompts_root,
            worktrees_root=worktrees_root,
            pipeline_runs_root=pipeline_runs_root,
            import_fleet=import_fleet,
            max_upstream_cases=max_upstream_cases,
        )

    eligible = discover_eligible_cases(
        worktrees_root=worktrees_root, prompts_root=prompts_root, codex_runs_root=codex_runs_root,
    )
    batch = eligible[:max_cases]
    log_info(
        _TAG,
        f"{len(eligible)} case(s) éligible(s), {len(batch)} retenu(s) pour cette invocation "
        f"(--max-cases={max_cases})",
    )

    summaries: "list[CaseRunSummary]" = []
    for case in batch:
        summary = process_case(
            case,
            failure_cases_root=failure_cases_root,
            diagnoses_root=diagnoses_root,
            context_selections_root=context_selections_root,
            worktrees_root=worktrees_root,
            codex_runs_root=codex_runs_root,
            static_validations_root=static_validations_root,
            patch_replays_root=patch_replays_root,
            extractor_integrity_checks_root=extractor_integrity_checks_root,
            confidence_scores_root=confidence_scores_root,
            human_reviews_root=human_reviews_root,
            merge_results_root=merge_results_root,
            merge_reviews_root=merge_reviews_root,
            claude_timeout_s=claude_timeout_s,
            allowed_tools=allowed_tools,
            permission_mode=permission_mode,
            force=force,
        )
        write_pipeline_run_summary(summary, out_root=pipeline_runs_root)
        summaries.append(summary)

    return upstream_summaries, summaries
