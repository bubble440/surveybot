from __future__ import annotations

"""Phase 10 — test live attach contrôlé d'un patch NON_CONCLUANT après la Phase 9
(Survey/autofix/patch_replay.py), sur une VRAIE page déjà ouverte par un opérateur humain —
potentiellement une vraie session de répondant. Voie de validation pour les cas que
le replay local (statique ou Chromium isolé, Phases 3A-3C) ne peut pas trancher.

── GARDE-FOU DE SÉCURITÉ, NON NÉGOCIABLE ──────────────────────────────────────
`--remote-debugging-port`/tout CDP exposé est un signal de détection documenté
(BOT_EVOLUTION_MEMORY.md). $env:AUTOFIX_LIVE_VALIDATE doit valoir EXACTEMENT "1"
(égalité stricte — pas le parsing tolérant "1"/"true"/"yes"/"on" déjà utilisé par
SURVEY_OBSERVABILITY en Phase 1A, cf. Survey/page_snapshot.py::_phase1a_enabled :
un garde-fou de sécurité mérite moins d'ambiguïté qu'un simple flag d'observabilité).
Vérifié à DEUX endroits, jamais un seul : (1) tools/validate_patch_live.py::main()
le vérifie EN PREMIER, avant même d'importer argparse ou de construire le parseur —
aucun argument CLI ne peut donc jamais l'atteindre ni le contourner ; (2)
check_preconditions() ci-dessous le revérifie indépendamment (défense en
profondeur si ce module est appelé directement, hors de cette façade CLI). Dans
les deux cas, une valeur absente ou différente de "1" bloque tout — aucune
tentative de connexion CDP n'est jamais atteignable sans elle.

── Lecture seule sur les Phases 4/7/9 ─────────────────────────────────────────
Ne recalcule et ne réexécute jamais rien de ces phases : lit uniquement les
artefacts déjà produits (diagnosis.json, worktree.json, patch_replay.json).
manifest.json n'est jamais rouvert directement par la logique propre à ce module
(même discipline que Survey/autofix/patch_replay.py) : case_id/stage viennent de
diagnosis.json ; le scénario ciblé (target_id des actions, blocs attendus) vient
indirectement des artefacts du case, lus par les mêmes fonctions déjà existantes
qui en ont besoin (extract_case_blocks/execute_case_action). validation_static.json
(Phase 8) n'est pas relu ici : son acceptation est déjà une précondition de
l'existence même de patch_replay.json (Phase 9 refuse sans elle), revérifier son
verdict ici serait redondant avec une chaîne déjà établie en amont.

── Précondition : toutes les conditions ensemble, jamais la première seule ───
(même principe que Survey/autofix/patch_replay.py::check_preconditions)
  - AUTOFIX_LIVE_VALIDATE="1" (garde-fou ci-dessus).
  - patch_replay.json (Phase 9) : refused=False ET outcome="NON_CONCLUANT"
    EXACTEMENT (Survey.autofix.replay_browser.OUTCOME_INCONCLUSIVE, non modifié, jamais
    une chaîne réécrite en dur) — ni CORRECTIF_CONFIRME (déjà validé, inutile de
    consommer une session live), ni BUG_PERSISTANT (patch déjà connu mauvais,
    retour Phase 6/7 attendu).
  - worktree.json (Phase 7) : mêmes champs déjà lus par la Phase 9
    (case_id/branch/worktree_path/base_sha), jamais recalculés ; worktree_path
    doit toujours exister sur disque et ressembler à un worktree Git (.git
    présent) — même vérification que Survey/autofix/patch_replay.py::check_preconditions.
  - diagnosis.json (Phase 4) : stage in {"extraction", "action"} ; stage doit
    être identique à celui déjà porté par patch_replay.json (cohérence entre
    artefacts, jamais supposée).
  - --cdp-endpoint fourni par l'opérateur (http(s):// ou ws(s)://) — cet outil ne
    lance ni ne configure lui-même Chrome ; il suppose qu'un humain a déjà
    positionné manuellement la page dans l'état de l'incident et laissé le port
    ouvert.
  - case_id cohérent entre toutes les sources fournies (failure_case_dir,
    diagnosis_dir, dossiers parents de worktree.json/patch_replay.json,
    case_id porté par chaque JSON) et sûr comme composant de chemin — même
    vérification inline que Survey/autofix/patch_replay.py (jamais l'import d'un
    validateur privé d'un module frère : chaque phase porte sa propre vérification
    minimale, précédent déjà posé par Survey/autofix/patch_replay.py lui-même face à
    Survey/autofix/autofix_worktree.py::_is_safe_case_id).

── Résolution de la racine de paquet du worktree ──────────────────────────────
Réutilise TELLE QUELLE Survey.autofix.static_validator._resolve_package_root (Phase 8,
non modifiée) — exactement comme le fait déjà Survey/autofix/patch_replay.py (Phase 9)
sur ce même worktree_path. Le code exécuté est celui du worktree patché, dans un
SOUS-PROCESSUS Python neuf (sys.executable, jamais un "python" résolu au hasard) :
même raison qu'en Phase 9, ce process-ci a déjà importé Survey.autofix.replay_browser/
Survey.autofix.static_validator depuis le dépôt principal (sys.modules les garde en
cache) — un sous-processus neuf est la seule façon fiable de garantir que le code
réellement exécuté est celui du worktree, sans deviner un mécanisme
d'invalidation de cache fragile.

── Connexion CDP et scénario rejoué : une seule stratégie, jamais devinée ─────
Vérifié avant d'écrire ce module (signatures lues, pas supposées) :
`Survey.autofix.replay_browser.extract_case_blocks(page, case_dir)` et
`execute_case_action(page, case_dir, budget_s, question_blocks=...)` n'exigent
de `page` qu'une API Playwright standard (`page.evaluate`, `page._impl_obj`,
`page._loop` pour le budget d'`execute_case_action`) — attributs présents sur
toute Page Playwright sync, quelle que soit son origine (nouvelle page d'un
navigateur lancé par `launch()`, ou page déjà ouverte d'un navigateur rejoint par
`connect_over_cdp()`). Aucune des deux fonctions n'a donc besoin d'être modifiée
ni d'un changement de signature pour opérer sur une page CDP live — seule la
façon d'OBTENIR `page` change (voir ci-dessous), jamais leur code.

Divergence documentée avec le stage="extraction" de la Phase 9 : Survey/autofix/
patch_replay.py réutilise pour ce stage le replay STATIQUE
(Survey.autofix.failure_replay.replay_failure_case, driver lxml sur un DOM figé) —
mécanisme fondamentalement incompatible avec une page CDP live (il n'y a pas de
snapshot figé à charger, la page EST déjà l'état à observer). « L'équivalent déjà
existant » pour l'extraction, au sens de la demande d'origine, est donc
`extract_case_blocks` seul (sans `execute_case_action`) — exactement le mécanisme
déjà utilisé par Survey/autofix/failure_diagnosis.py::_attempt_real_extraction_replay
(qui l'exécute déjà sur une page de navigateur réelle, via IsolatedReplayBrowser).
Ce module suit ce même appel, en substituant uniquement l'origine de `page`.

Sous-processus dédié (comme Phase 9), un seul script par stage, mais partageant
un même préambule de connexion (une seule implémentation, jamais dupliquée) :
`connect_over_cdp(cdp_endpoint)` (jamais `launch()`/`launch_persistent_context` —
aucun navigateur n'est lancé ni configuré par cet outil), puis récupération de la
page déjà ouverte : toutes les pages de tous les contextes du navigateur distant
sont énumérées (jamais `new_page()`, jamais de navigation) ; une seule page
trouvée → utilisée ; plusieurs → désambiguïsation par correspondance avec l'URL
d'origine du case (meta.json, déjà sanitisée, comparée sans query string) si elle
isole une page unique, sinon refus explicite listant les URLs candidates — jamais
une page devinée. Aucune page trouvée → refus explicite.

── Jamais browser.close()/context.close()/page.close() sur la session CDP ─────
Risque assumé et documenté plutôt que deviné : Playwright ne fournit pas de test
automatisé dans cet environnement pour le vérifier empiriquement (aucun
Chromium/point CDP réel disponible ici), mais la sémantique documentée de
`connect_over_cdp` est que la session ainsi obtenue REJOINT un navigateur déjà en
vie sans en prendre possession — ce module ne fait donc jamais qu'arrêter son
propre driver Playwright local (`sync_playwright().stop()`, qui ferme uniquement
la connexion locale) en fin de script, jamais une fermeture explicite du
navigateur, du contexte ou de la page distante. Cette règle est absolue : une
page potentiellement tenue par un vrai répondant ne doit jamais être fermée par
CE module.

Limite résiduelle neutralisée pour stage="action" : par défaut,
`execute_case_action` (Phase 3C.4) ferme elle-même la PAGE (pas le navigateur)
si son propre budget interne (`budget_s`) est dépassé — comportement conçu à
l'origine pour un Chromium isolé jetable où fermer la page est sans
conséquence, et réel sur une page CDP live (un TIMEOUT du dispatcher fermerait
alors réellement l'onglet de l'opérateur/du répondant). `execute_case_action`
accepte depuis un paramètre additif `close_page_on_timeout` (défaut True,
comportement inchangé pour tout appelant qui ne le fournit pas — Phases
3C.4/9) ; ce module l'appelle explicitement avec `close_page_on_timeout=False`
(voir `_ACTION_RUNNER_SCRIPT` ci-dessous), pour que ce TIMEOUT ne ferme jamais
la page distante. Le dispatcher bloqué ne se débloque alors plus que par ses
propres budgets internes, jamais par ce watchdog (cf. docstring de
Survey/autofix/replay_browser.py, limite (1) d'`execute_case_action`) : c'est le budget
global du sous-processus (voir ci-dessous), pas ce watchdog, qui borne
effectivement l'opération dans ce cas — même mécanisme de repli, déjà en place,
que pour stage="extraction" ci-dessous. Aucun risque équivalent pour
stage="extraction" : `extract_case_blocks` n'effectue aucune mutation (lecture
DOM + validation), une interruption brutale du sous-processus (budget global
dépassé, voir ci-dessous) ne touche jamais la page distante elle-même — seule
NOTRE connexion locale meurt avec le sous-processus.

── Budget de temps explicite sur l'ensemble de l'opération ────────────────────
Un seul réglage exposé à l'opérateur (`budget_s`, connexion + rejeu confondus,
conforme à la demande d'origine — jamais deux budgets qui pourraient s'emboîter
de façon fragile) : transmis tel quel comme `budget_s` d'`execute_case_action`
pour stage="action" (le budget interne du dispatcher EST le budget de
l'opération, la connexion CDP précédente étant rapide en pratique — réseau
local) ; utilisé pour borner le sous-processus dans les deux cas
(`subprocess.run(timeout=budget_s + _SUBPROCESS_MARGIN_S)`, la marge ne couvrant
que la terminaison propre du sous-processus lui-même après un TIMEOUT interne,
jamais une deuxième tentative). Dépassement du budget global (sous-processus tué)
traité comme un échec contrôlé (NON_CONCLUANT), jamais un processus qui pend.

── Une seule tentative, jamais de boucle ──────────────────────────────────────
Un seul sous-processus, un seul essai, comme Survey/autofix/patch_replay.py::
_run_replay_subprocess (stratégie reprise à l'identique, jamais réimplémentée en
parallèle). Aucune boucle interne, aucun deuxième essai automatique : un
opérateur qui veut retenter après une nouvelle correction relance manuellement le
cycle complet (nouvelle invocation de cet outil), ce n'est pas la responsabilité
de ce module.

── Vocabulaire de sortie : réutilisé, jamais une seconde échelle ─────────────
`Survey.autofix.replay_browser.OUTCOME_FIX_CONFIRMED/OUTCOME_BUG_PERSISTS/
OUTCOME_INCONCLUSIVE` (non modifiés). Pour stage="action", cet `outcome` est déjà
calculé par `execute_case_action`/`_action_outcome` (non modifiés) — repris tel
quel, exactement comme le fait déjà Survey/autofix/patch_replay.py. Pour stage=
"extraction", `extract_case_blocks` ne calcule qu'un verdict de fidélité
(REPRODUIT/NON_REPRODUIT/DIFFERENT, `Survey.autofix.failure_replay`, non modifié) : ce
module ajoute la même traduction, strictement symétrique, que celle déjà écrite
par Survey/autofix/patch_replay.py pour SON stage="extraction" (mécanisme différent —
replay statique — mais traduction identique : NON_REPRODUIT -> CORRECTIF_CONFIRME,
REPRODUIT -> BUG_PERSISTANT, tout le reste -> NON_CONCLUANT). Seul
CORRECTIF_CONFIRME valide le patch (`patch_validated=True`) — jamais une absence
de reproduction ambiguë ou un budget dépassé.

── Sortie ──────────────────────────────────────────────────────────────────
Verdict tracé sur disque, même convention JSON que les phases précédentes
(schema_version, horodatage, case_id) : out_root/<case_id>/live_validation.json.
Ne modifie jamais failure_case_dir/diagnosis_dir/worktree.json/patch_replay.json,
ni le worktree Git lui-même, ni (par construction ci-dessus) la page/le
navigateur distant. Refus explicite (jamais un écrasement silencieux) si la
sortie existe déjà et force=False.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional, Tuple

from Survey.autofix.failure_replay import VERDICT_NON_REPRODUIT, VERDICT_REPRODUIT
from Survey.log_utils import log_debug, log_info
from Survey.autofix.replay_browser import (
    OUTCOME_BUG_PERSISTS,
    OUTCOME_FIX_CONFIRMED,
    OUTCOME_INCONCLUSIVE,
    _DEFAULT_DISPATCH_BUDGET_S,
)
from Survey.autofix.static_validator import StaticValidationError, _resolve_package_root

_TAG = "[LIVE_VALIDATOR]"
SCHEMA_VERSION = "1.0"

# Garde-fou de sécurité — cf. docstring du module. Égalité stricte, jamais un
# parsing tolérant ("true"/"yes"/"on") : un garde-fou de sécurité ne doit
# jamais s'activer par accident via une valeur laissée par un autre outil.
ENV_VAR = "AUTOFIX_LIVE_VALIDATE"

# Budget par défaut de l'opération complète (connexion CDP + rejeu). Dérivé de
# _DEFAULT_DISPATCH_BUDGET_S (Phase 3C.4/9, non modifiée, 30.0s) avec une marge
# pour couvrir la connexion CDP elle-même (réseau local, rapide en pratique,
# mais jamais nulle) — jamais une valeur indépendante devinée.
DEFAULT_BUDGET_S = _DEFAULT_DISPATCH_BUDGET_S + 30.0
# Marge du sous-processus par-dessus budget_s — ne sert qu'à laisser le
# sous-processus se terminer proprement après son propre budget interne (le
# watchdog d'execute_case_action, ou l'absence d'équivalent pour l'extraction) ;
# jamais une deuxième tentative, jamais un budget effectif plus généreux.
_SUBPROCESS_MARGIN_S = 30.0

_CDP_ENDPOINT_RE = re.compile(r"^(https?|wss?)://", re.IGNORECASE)


class LiveValidationError(Exception):
    """Erreur d'utilisation (chemins invalides, sortie déjà existante). Distincte
    d'un refus contrôlé (LiveValidationResult.refused=True), qui n'est jamais une
    exception."""


class LiveValidationExistsError(LiveValidationError):
    """La sortie cible existe déjà et la régénération n'a pas été demandée."""


def _load_json(path: Path) -> Tuple[Any, Optional[str]]:
    if not path.is_file():
        return None, f"{path} absent"
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except OSError as exc:
        return None, f"{path} illisible ({exc})"
    except ValueError as exc:  # json.JSONDecodeError est une sous-classe de ValueError
        return None, f"{path} JSON invalide ({exc})"


def _live_validate_enabled() -> bool:
    return os.environ.get(ENV_VAR) == "1"


@dataclass
class PreconditionResult:
    satisfied: bool
    case_id: Optional[str]
    stage: Optional[str]
    reasons: List[str] = field(default_factory=list)
    worktree: Optional[dict] = None
    diagnosis: Optional[dict] = None
    patch_replay: Optional[dict] = None


def check_preconditions(
    *,
    failure_case_dir: "str | Path",
    diagnosis_dir: "str | Path",
    worktree_manifest_path: "str | Path",
    patch_replay_path: "str | Path",
    cdp_endpoint: Optional[str],
) -> PreconditionResult:
    """Vérifie toutes les conditions ensemble ; ne s'arrête jamais à la première
    raison rencontrée — liste complète retournée, comme Survey/autofix/patch_replay.py::
    check_preconditions (Phase 9), jamais un résultat partiel. Lecture seule : ne
    recalcule ni la Phase 4, ni la Phase 7, ni la Phase 9."""
    failure_case_dir = Path(failure_case_dir)
    diagnosis_dir = Path(diagnosis_dir)
    worktree_manifest_path = Path(worktree_manifest_path)
    patch_replay_path = Path(patch_replay_path)
    reasons: List[str] = []

    if not _live_validate_enabled():
        reasons.append(
            f"{ENV_VAR} doit valoir exactement \"1\" — garde-fou de sécurité non contournable "
            "par un argument CLI (connexion à une VRAIE page, potentiellement une vraie session "
            "de répondant ; cf. docstring du module)"
        )

    if not cdp_endpoint or not _CDP_ENDPOINT_RE.match(cdp_endpoint):
        reasons.append(
            f"--cdp-endpoint invalide ou absent ({cdp_endpoint!r}) — point de connexion CDP "
            "explicite requis (http(s):// ou ws(s)://), jamais lancé/configuré par cet outil"
        )

    patch_replay, pr_err = _load_json(patch_replay_path)
    if pr_err or not isinstance(patch_replay, dict):
        reasons.append(f"patch_replay.json (Phase 9) {pr_err or 'ne contient pas un objet JSON'}")
    else:
        if patch_replay.get("refused") is not False:
            reasons.append(f"patch_replay.json.refused={patch_replay.get('refused')!r} — False requis")
        outcome = patch_replay.get("outcome")
        if outcome != OUTCOME_INCONCLUSIVE:
            reasons.append(
                f"patch_replay.json.outcome={outcome!r} — {OUTCOME_INCONCLUSIVE!r} requis exactement "
                f"(ni {OUTCOME_FIX_CONFIRMED!r} : déjà validé, ni {OUTCOME_BUG_PERSISTS!r} : patch déjà "
                "connu mauvais — aucun des deux ne justifie de consommer une session live)"
            )

    worktree, wt_err = _load_json(worktree_manifest_path)
    if wt_err or not isinstance(worktree, dict):
        reasons.append(f"worktree.json (Phase 7) {wt_err or 'ne contient pas un objet JSON'}")
    else:
        for key in ("case_id", "branch", "worktree_path", "base_sha"):
            if not worktree.get(key):
                reasons.append(f"worktree.json.{key} absent ou vide")
        wt_path = worktree.get("worktree_path")
        if isinstance(wt_path, str) and wt_path:
            wt_dir = Path(wt_path)
            if not wt_dir.is_dir():
                reasons.append(f"worktree_path {wt_path!r} n'existe pas ou n'est pas un dossier")
            elif not (wt_dir / ".git").exists():
                reasons.append(f"worktree_path {wt_path!r} ne ressemble pas à un worktree Git (.git absent)")

    diagnosis, diag_err = _load_json(diagnosis_dir / "diagnosis.json")
    stage: Optional[str] = None
    if diag_err or not isinstance(diagnosis, dict):
        reasons.append(f"diagnosis.json (Phase 4) {diag_err or 'ne contient pas un objet JSON'}")
    else:
        stage = str(diagnosis.get("stage") or "")
        if stage not in ("extraction", "action"):
            reasons.append(f"diagnosis.json.stage={stage!r} — seuls \"extraction\" et \"action\" sont dans le périmètre")
        if isinstance(patch_replay, dict) and patch_replay.get("stage") not in (None, stage):
            reasons.append(
                f"stage incohérent : diagnosis.json.stage={stage!r} vs "
                f"patch_replay.json.stage={patch_replay.get('stage')!r}"
            )

    if not failure_case_dir.is_dir():
        reasons.append(f"failure_case_dir introuvable : {failure_case_dir}")

    # case_id cohérent entre toutes les sources — même principe que Survey/
    # patch_replay.py::check_preconditions (dict complet, jamais la première
    # incohérence seule).
    case_ids = {
        "diagnosis.json": str(diagnosis.get("case_id") or "") if isinstance(diagnosis, dict) else "",
        "worktree.json": str(worktree.get("case_id") or "") if isinstance(worktree, dict) else "",
        "patch_replay.json": str(patch_replay.get("case_id") or "") if isinstance(patch_replay, dict) else "",
        "failure_case_dir": failure_case_dir.name,
        "diagnosis_dir": diagnosis_dir.name,
        "worktree_manifest_path.parent": worktree_manifest_path.parent.name,
        "patch_replay_path.parent": patch_replay_path.parent.name,
    }
    distinct = set(case_ids.values())
    resolved_case_id: Optional[str] = None
    if len(distinct) != 1 or "" in distinct:
        reasons.append(f"case_id incohérent entre les sources : {case_ids}")
    else:
        resolved_case_id = next(iter(distinct))
        if any(sep in resolved_case_id for sep in ("/", "\\")) or ".." in resolved_case_id:
            reasons.append(f"case_id {resolved_case_id!r} n'est pas un composant de chemin sûr")

    return PreconditionResult(
        satisfied=not reasons,
        case_id=resolved_case_id,
        stage=stage,
        reasons=reasons,
        worktree=worktree if isinstance(worktree, dict) else None,
        diagnosis=diagnosis if isinstance(diagnosis, dict) else None,
        patch_replay=patch_replay if isinstance(patch_replay, dict) else None,
    )


def _resolve_worktree_package_root(worktree_path: Path) -> Tuple[Optional[Path], Optional[str]]:
    """Réutilise TELLE QUELLE Survey.autofix.static_validator._resolve_package_root
    (Phase 8, non modifiée) — jamais une seconde implémentation. Seule la
    conversion exception -> (None, raison) est propre à ce module (même
    convention locale que Survey/autofix/patch_replay.py::_resolve_worktree_package_root,
    chaque phase porte son propre petit adaptateur plutôt que de chaîner un
    import d'un symbole privé d'un module de phase frère)."""
    try:
        return _resolve_package_root(worktree_path), None
    except StaticValidationError as exc:
        return None, str(exc)


# Un seul préambule de connexion, partagé par les deux scripts de stage (jamais
# dupliqué) : connect_over_cdp (jamais launch()/launch_persistent_context),
# jamais de nouvelle page, jamais de navigation. Page unique -> utilisée ;
# plusieurs -> désambiguïsation par l'URL d'origine du case (meta.json), sinon
# refus explicite listant les candidates. Ne ferme jamais browser/context/page.
_CDP_CONNECT_PREAMBLE = r"""
def _connect_and_get_page(sync_playwright, cdp_endpoint, case_dir):
    import json as _json
    from pathlib import Path as _Path
    from urllib.parse import urlsplit as _urlsplit

    pw = sync_playwright().start()
    try:
        browser = pw.chromium.connect_over_cdp(cdp_endpoint)
    except Exception:
        pw.stop()
        raise

    pages = [p for ctx in browser.contexts for p in ctx.pages]
    if not pages:
        pw.stop()
        raise RuntimeError("aucune page ouverte trouvee sur ce point de connexion CDP")
    if len(pages) == 1:
        return pw, pages[0]

    meta_path = _Path(case_dir) / "artifacts" / "meta.json"
    original_prefix = None
    try:
        meta = _json.loads(meta_path.read_text(encoding="utf-8"))
        url = meta.get("url") if isinstance(meta, dict) else None
        if isinstance(url, str) and url:
            parts = _urlsplit(url)
            original_prefix = (parts.scheme, parts.netloc, parts.path)
    except Exception:
        original_prefix = None

    matches = pages
    if original_prefix is not None:
        def _pfx(p):
            try:
                u = _urlsplit(p.url)
                return (u.scheme, u.netloc, u.path)
            except Exception:
                return None
        matches = [p for p in pages if _pfx(p) == original_prefix]

    if len(matches) != 1:
        urls = [getattr(p, "url", "?") for p in pages]
        pw.stop()
        raise RuntimeError(
            f"page ambigue : {len(pages)} page(s) ouverte(s) sur ce point CDP ({urls!r}), "
            "aucune correspondance unique avec l'URL d'origine du case — jamais devinee"
        )
    return pw, matches[0]
"""

_EXTRACTION_RUNNER_SCRIPT = _CDP_CONNECT_PREAMBLE + """
import json
import sys

worktree_root, case_dir, cdp_endpoint = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, worktree_root)

try:
    from playwright.sync_api import sync_playwright
    from Survey.autofix.replay_browser import extract_case_blocks

    pw, page = _connect_and_get_page(sync_playwright, cdp_endpoint, case_dir)
    try:
        extraction = extract_case_blocks(page, case_dir)
        result = {
            "blocks_count": len(extraction.blocks),
            "error": extraction.error,
            "comparison": extraction.comparison,
            "validation_comparison": extraction.validation_comparison,
            "validation_error": extraction.validation_error,
        }
    finally:
        # Jamais browser.close()/page.close() : on ne ferme que NOTRE driver
        # Playwright local (deconnexion), jamais le navigateur/la page distants.
        pw.stop()
except Exception as exc:
    result = {"error": f"{type(exc).__name__}: {exc}"}

print(json.dumps(result))
"""

_ACTION_RUNNER_SCRIPT = _CDP_CONNECT_PREAMBLE + """
import json
import sys

worktree_root, case_dir, cdp_endpoint, budget_s = sys.argv[1], sys.argv[2], sys.argv[3], float(sys.argv[4])
sys.path.insert(0, worktree_root)

try:
    from playwright.sync_api import sync_playwright
    from Survey.autofix.replay_browser import execute_case_action, extract_case_blocks

    pw, page = _connect_and_get_page(sync_playwright, cdp_endpoint, case_dir)
    try:
        extraction = extract_case_blocks(page, case_dir)
        execution = execute_case_action(
            page, case_dir, budget_s=budget_s, question_blocks=extraction.blocks,
            close_page_on_timeout=False,
        )
        result = {
            "status": execution.status,
            "reason": execution.reason,
            "dispatcher_success": execution.dispatcher_success,
            "duration_s": execution.duration_s,
            "budget_s": execution.budget_s,
            "extraction_error": extraction.error,
            "validation_comparison": execution.validation_comparison,
            "validation_error": execution.validation_error,
            "trace_replay": execution.trace_replay,
            "dispatcher_steps": execution.dispatcher_steps,
        }
    finally:
        # Jamais browser.close() ici non plus : execute_case_action est appelee
        # avec close_page_on_timeout=False (cf. docstring du module) donc un
        # TIMEOUT ne ferme pas la page distante ; on ne ferme dans tous les cas
        # que notre driver Playwright local, jamais la page, jamais le navigateur.
        pw.stop()
except Exception as exc:
    result = {"status": None, "error": f"{type(exc).__name__}: {exc}"}

print(json.dumps(result))
"""


def _run_live_subprocess(script: str, args: List[str], timeout_s: float) -> Tuple[Optional[dict], Optional[str]]:
    """Stratégie d'exécution UNIQUE, reprise à l'identique de Survey/autofix/patch_replay.py::
    _run_replay_subprocess (jamais réimplémentée en parallèle) : un seul
    sous-processus Python (sys.executable), un seul essai, un budget de temps
    explicite. (résultat JSON, None) en cas de succès, (None, raison) sinon —
    jamais d'exception propagée, jamais de processus laissé pendre."""
    fd, tmp_path = tempfile.mkstemp(suffix=".py", prefix="live_validator_runner_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(script)
        try:
            completed = subprocess.run(
                [sys.executable, "-X", "utf8", tmp_path, *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired:
            log_debug(_TAG, f"sous-processus de validation live expiré après {timeout_s}s — abandon contrôlé")
            return None, f"sous-processus de validation live expiré après {timeout_s:.1f}s (budget dépassé)"
        except OSError as exc:
            return None, f"sous-processus de validation live indisponible ({exc})"
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    stdout, stderr = completed.stdout or "", completed.stderr or ""
    if "\ufffd" in stdout or "\ufffd" in stderr:
        return None, "sortie UTF-8 invalide du sous-processus de validation live"
    if completed.returncode != 0:
        return None, (
            f"sous-processus de validation live terminé avec le code {completed.returncode} : "
            f"{stderr.strip()[-2000:]}"
        )
    try:
        return json.loads(stdout.strip().splitlines()[-1]), None
    except (ValueError, IndexError) as exc:
        return None, f"sortie du sous-processus non JSON ({exc}) : {stdout[-500:]!r}"


def _extraction_outcome(after: dict) -> str:
    """Traduction stage=\"extraction\" propre à ce module — Survey/autofix/patch_replay.py
    (Phase 9) traduit ce même verdict de fidélité pour SON stage=\"extraction\",
    mais depuis le replay STATIQUE (Survey.autofix.failure_replay), incompatible avec une
    page CDP live (cf. docstring du module) : ce module réutilise la même table de
    traduction, appliquée au verdict déjà calculé par extract_case_blocks
    (Survey.autofix.replay_browser, non modifié) sur la page live — strictement
    symétrique, jamais une seconde échelle de confiance."""
    if not after.get("error"):
        verdict = (after.get("validation_comparison") or {}).get("verdict")
        if verdict == VERDICT_NON_REPRODUIT:
            return OUTCOME_FIX_CONFIRMED
        if verdict == VERDICT_REPRODUIT:
            return OUTCOME_BUG_PERSISTS
    return OUTCOME_INCONCLUSIVE


@dataclass
class LiveValidationResult:
    case_id: str
    stage: str
    cdp_endpoint: Optional[str]
    refused: bool
    refusal_reasons: List[str] = field(default_factory=list)
    patch_replay_outcome: Optional[str] = None
    live_replay: Optional[dict] = None
    live_replay_error: Optional[str] = None
    outcome: Optional[str] = None
    patch_validated: bool = False
    worktree_path: Optional[str] = None
    branch: Optional[str] = None
    base_sha: Optional[str] = None
    budget_s: Optional[float] = None
    warnings: List[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "case_id": self.case_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "stage": self.stage,
            "cdp_endpoint": self.cdp_endpoint,
            "refused": self.refused,
            "refusal_reasons": self.refusal_reasons,
            "patch_replay_outcome": self.patch_replay_outcome,
            "live_replay": self.live_replay,
            "live_replay_error": self.live_replay_error,
            "outcome": self.outcome,
            "patch_validated": self.patch_validated,
            "worktree": {
                "path": self.worktree_path,
                "branch": self.branch,
                "base_sha": self.base_sha,
            },
            "budget_s": self.budget_s,
            "warnings": self.warnings,
        }


def validate_patch_live(
    *,
    failure_case_dir: "str | Path",
    diagnosis_dir: "str | Path",
    worktree_manifest_path: "str | Path",
    patch_replay_path: "str | Path",
    cdp_endpoint: Optional[str],
    budget_s: Optional[float] = None,
) -> LiveValidationResult:
    """Rejoue, sur la page CDP live fournie, EXACTEMENT le scénario ciblé par le
    case (extraction seule, ou extraction + dispatcher réel pour stage="action"),
    et compare au vocabulaire outcome déjà établi par Phase 9/Survey.autofix.replay_browser.
    Ne valide le patch (patch_validated=True) que si l'outcome confirme ACTIVEMENT
    la correction (CORRECTIF_CONFIRME) — jamais une absence ambiguë ou un budget
    dépassé."""
    pre = check_preconditions(
        failure_case_dir=failure_case_dir,
        diagnosis_dir=diagnosis_dir,
        worktree_manifest_path=worktree_manifest_path,
        patch_replay_path=patch_replay_path,
        cdp_endpoint=cdp_endpoint,
    )
    if not pre.satisfied:
        log_debug(_TAG, f"refus contrôlé, aucune tentative de connexion CDP : {pre.reasons}")
        return LiveValidationResult(
            case_id=pre.case_id or Path(failure_case_dir).name,
            stage=pre.stage or "unknown",
            cdp_endpoint=cdp_endpoint,
            refused=True,
            refusal_reasons=pre.reasons,
        )

    worktree = pre.worktree or {}
    patch_replay = pre.patch_replay or {}
    worktree_path = str(worktree["worktree_path"])
    stage = pre.stage or "unknown"
    case_id = pre.case_id or Path(failure_case_dir).name
    case_dir_str = str(Path(failure_case_dir).resolve())
    effective_budget_s = float(budget_s) if budget_s else DEFAULT_BUDGET_S

    result = LiveValidationResult(
        case_id=case_id,
        stage=stage,
        cdp_endpoint=cdp_endpoint,
        refused=False,
        patch_replay_outcome=patch_replay.get("outcome"),
        worktree_path=worktree_path,
        branch=worktree.get("branch"),
        base_sha=worktree.get("base_sha"),
        budget_s=effective_budget_s,
    )

    package_root, resolve_err = _resolve_worktree_package_root(Path(worktree_path))
    if resolve_err:
        result.live_replay_error = resolve_err
        result.outcome = OUTCOME_INCONCLUSIVE
        log_info(_TAG, f"case={case_id} validation live non concluante ({resolve_err[:150]})")
        return result

    subprocess_timeout_s = effective_budget_s + _SUBPROCESS_MARGIN_S
    if stage == "extraction":
        after, err = _run_live_subprocess(
            _EXTRACTION_RUNNER_SCRIPT,
            [str(package_root), case_dir_str, str(cdp_endpoint)],
            subprocess_timeout_s,
        )
    else:  # stage == "action" (seules deux valeurs possibles après check_preconditions)
        after, err = _run_live_subprocess(
            _ACTION_RUNNER_SCRIPT,
            [str(package_root), case_dir_str, str(cdp_endpoint), str(effective_budget_s)],
            subprocess_timeout_s,
        )

    if err:
        result.live_replay_error = err
        result.outcome = OUTCOME_INCONCLUSIVE
        result.patch_validated = False
        log_info(_TAG, f"case={case_id} validation live non concluante ({err[:150]})")
        return result

    result.live_replay = after
    if stage == "extraction":
        outcome = _extraction_outcome(after or {})
    else:
        status = (after or {}).get("status")
        comparison = (after or {}).get("validation_comparison")
        if status in ("SUCCESS", "FAILURE") and isinstance(comparison, dict) and comparison.get("outcome"):
            outcome = comparison["outcome"]
        else:
            outcome = OUTCOME_INCONCLUSIVE
            if status == "TIMEOUT":
                result.warnings.append(
                    "budget de temps dépassé pendant la validation live — traité comme non concluant, "
                    "même si trace_replay porte un signal favorable (jamais une confirmation active) ; "
                    "la page live n'a PAS été fermée par execute_case_action (appelée avec "
                    "close_page_on_timeout=False, voir docstring du module) — mais son dispatcher peut "
                    "rester bloqué jusqu'à l'expiration du budget global du sous-processus"
                )

    result.outcome = outcome
    result.patch_validated = outcome == OUTCOME_FIX_CONFIRMED
    log_info(
        _TAG,
        f"case={case_id} stage={stage} validation live outcome={outcome} patch_validated={result.patch_validated}",
    )
    return result


def _live_validation_paths(out_root: Path, case_id: str) -> Tuple[Path, Path]:
    out_dir = out_root / case_id
    return out_dir, out_dir / "live_validation.json"


def write_live_validation(
    *,
    failure_case_dir: "str | Path",
    diagnosis_dir: "str | Path",
    worktree_manifest_path: "str | Path",
    patch_replay_path: "str | Path",
    cdp_endpoint: Optional[str],
    out_root: "str | Path" = "live_validations",
    force: bool = False,
    budget_s: Optional[float] = None,
) -> Path:
    """Exécute validate_patch_live et écrit out_root/<case_id>/live_validation.json.
    Lève LiveValidationExistsError si la sortie existe déjà et force=False — jamais
    d'écrasement silencieux (même convention que Survey/autofix/patch_replay.py)."""
    failure_case_dir = Path(failure_case_dir)
    out_root = Path(out_root)
    out_dir, out_file = _live_validation_paths(out_root, failure_case_dir.name)

    if out_dir.exists():
        if not force:
            raise LiveValidationExistsError(
                f"résultat déjà existant : {out_dir} (utiliser --force pour régénérer)"
            )
        if not out_file.is_file():
            raise LiveValidationError(
                f"{out_dir} existe mais ne ressemble pas à une sortie générée par cet outil "
                "(pas de live_validation.json) — suppression refusée, vérifier manuellement"
            )
        import shutil
        log_info(_TAG, f"régénération forcée : suppression de {out_dir}")
        shutil.rmtree(out_dir)

    result = validate_patch_live(
        failure_case_dir=failure_case_dir,
        diagnosis_dir=diagnosis_dir,
        worktree_manifest_path=worktree_manifest_path,
        patch_replay_path=patch_replay_path,
        cdp_endpoint=cdp_endpoint,
        budget_s=budget_s,
    )

    out_dir.mkdir(parents=True, exist_ok=False)
    out_file.write_text(json.dumps(result.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    log_info(
        _TAG,
        f"résultat écrit case={result.case_id} refused={result.refused} outcome={result.outcome} "
        f"-> {out_file}",
    )
    return out_file
