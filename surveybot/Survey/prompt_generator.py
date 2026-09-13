from __future__ import annotations

"""Génération d'un prompt Codex/Claude Code (Phase 6) à partir d'un diagnostic déjà
produit (Phase 4, diagnosis.json) et d'une sélection de contexte déjà produite
(Phase 5, context_selection.json) — texte brut, prêt à copier-coller.

Lecture seule sur les Phases 2/4/5 : ne modifie jamais failure_cases/, diagnoses/,
ni context_selections/, ne recalcule rien (aucun nouvel appel à
replay_failure_case, failure_diagnosis, ou context_selector) — se contente de LIRE
diagnosis.json et context_selection.json déjà écrits sur disque.

── Garde-fou d'éligibilité (obligatoire avant toute génération) ───────────────
Un prompt normal n'est produit QUE si :
  1. context_selection.json contient au moins un fichier de code (code_files non vide) ;
  2. diagnosis.json indique confidence_global in {"probable", "certain"}.
Dans tous les autres cas — aucun fichier trouvé, confiance "plausible" (ce qui
couvre aussi un case NON_REJOUABLE/NON_REPRODUIT, puisque la Phase 4 plafonne déjà
ces verdicts à "plausible") — ce module produit à la place un signal de revue
manuelle, dans un format et sous un nom de fichier délibérément différents d'un
prompt (MANUAL_REVIEW_REQUIRED.txt vs prompt.txt), pour qu'il ne puisse jamais
être confondu avec un prompt exploitable ni transmis par erreur à Codex.

── Section BUG IDENTIFIÉ : ce qu'elle peut et ne peut pas dire ────────────────
- Symptôme : si le replay a REPRODUIT (mêmes failure_types), les faits concrets de
  symptom.issues d'origine sont utilisables (itype, valeur demandée, qid, décompte
  d'options, etc. — jamais target_id/action_index/block_index, des clés de
  registry internes sans valeur pour un lecteur humain ou un agent de coding).
  Si le replay a donné DIFFERENT, seule la liste des failure_types réellement
  rejoués (replay.replayed_failure_types) est utilisée — pas le rapport d'origine
  resté non reproduit tel quel, et sans détail par champ puisque le replay ne
  conserve pas les issues rejouées en détail (seulement leurs failure_types).
- Comportement attendu : repris de expected_behavior de diagnosis.json (déjà
  produit par la Phase 4), jamais recalculé. "Non documenté" tel quel si absent —
  jamais une description inventée.
- Fichiers concernés : chemins de code_files de context_selection.json, rien
  d'autre (pas la "reason" de chaque entrée, qui contient des détails de pipeline).
- Jamais : case_id, nom du verdict de replay, niveau de confiance, provider_domain,
  chemin de snapshot, ni les identifiants internes du registry (target_id) — cette
  section doit se lire comme un bug rapporté normalement.
- Jamais : mention d'une tentative ou d'un patch antérieur (chaque prompt est une
  conversation neuve), ni prescription de format de données/convention de
  nommage/structure de réponse/logique d'implémentation.

Tout le reste du gabarit (CONTEXTE, Règles de lecture, Variabilité intra-source,
RÈGLE DURE zéro modification, Logs/LOG_LEVEL, CTA/clics, RÈGLES STRICTES, ACTION
REQUISE) est reproduit verbatim, caractère pour caractère — aucune reformulation.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from Survey.log_utils import log_debug, log_info

_TAG = "[PROMPT_GENERATOR]"

ELIGIBLE_CONFIDENCE_LEVELS = {"probable", "certain"}

MANUAL_REVIEW_FILENAME = "MANUAL_REVIEW_REQUIRED.txt"
PROMPT_FILENAME = "prompt.txt"


class PromptGenerationError(Exception):
    """Échec contrôlé de génération."""


class PromptGenerationExistsError(PromptGenerationError):
    """La sortie cible existe déjà et la régénération n'a pas été demandée."""


# ─────────────────────────────────────────────────────────────────────────────
# Gabarit verbatim (hors BUG IDENTIFIÉ) — ne pas reformuler une seule ligne.
# ─────────────────────────────────────────────────────────────────────────────
_TEMPLATE = """CONTEXTE
BOT_EVOLUTION_MEMORY.md est le fichier de mémoire des extracteurs et fonctions critiques. Lis-le avant tout diagnostic.

Règles de lecture obligatoires
Les extracteurs présents dans le code mais absents de ce fichier existent depuis avant sa création. Ne pas les modifier sans raison DOM explicite.
Un extracteur qui échoue sur un DOM donné n'est pas forcément cassé : vérifie d'abord que le DOM correspond aux "Patterns couverts". Si non, ajoute un extracteur, ne réécris pas l'existant.
Les "Patterns exclus" sont des frontières strictes.
Le fichier doit être mis à jour en fin de patch validé (sur demande explicite).

Variabilité intra-source

Une même source peut avoir des structures DOM différentes selon les pages. Donc :
- Pas de logique supposant une structure unique par source.
- Tout support spécifique doit être déclenché par des critères DOM précis et scopé au minimum.

RÈGLE DURE — ZÉRO MODIFICATION D'EXTRACTEUR EXISTANT :
Le patch ne doit JAMAIS modifier le corps d'une fonction d'extraction existante.
Si un DOM n'est pas couvert : créer une nouvelle fonction d'extraction avec un garde-fou
DOM strict (sélecteur CSS ou attribut discriminant obligatoire), et l'enregistrer dans
le pipeline d'appel existant après les extracteurs existants (ordre additif).
Si un bug est dans un extracteur existant ET confirmé sur son DOM de référence :
demander une validation explicite avant toute modification, avec diff minimal.
Même règle pour les stratégies d'insertion/sélection : ne jamais modifier une stratégie existante, ajouter une stratégie nommée distincte.

Logs / LOG_LEVEL
Logs debug : conditionnés par $env:LOG_LEVEL — utiliser log_debug(tag, msg), jamais print().
Logs info : non conditionnés, mais rares (1–2 lignes max) — utiliser log_info(tag, msg).
N'ajoute des logs que s'ils aident réellement le diagnostic.

CTA / clics
Si le patch touche un CTA (Suivant/Next/Submit…), conditionner au flag CTA_INTERCEPT_ONLY :
Activé : intercepter sans navigation ni side-effects, avec log clair (trouvé+interception OK / impossible / introuvable).
Désactivé : clic réel.

BUG IDENTIFIÉ
{bug_identifie}

RÈGLES STRICTES
Une seule stratégie, une seule correction principale — pas de fallbacks empilés.
Pas de fallback Vision (DOM-first uniquement).
Toute boucle a un budget max N avec abandon contrôlé et logs.
Pas de input() en prod, pas de chemins locaux, pas d'hypothèses fragiles.
Patch minimal : pas de refactor gratuit.
Respecter la séparation des responsabilités (PROJECT_ARCHITECTURE.md), sauf si le bug l'exige explicitement.

ACTION REQUISE
Identifier la cause racine.
Appliquer un patch minimal et robuste.
Vérifier la non-régression sur les DOMs de référence pertinents.
Si le patch touche un CTA, appliquer la règle CTA_INTERCEPT_ONLY.
Donne un nom à mettre comme titre du commit git.
"""

# Champs d'un issue considérés lisibles/utiles pour un lecteur humain ou un
# agent de coding (comportement observable). target_id/action_index/block_index
# sont des clés de registry internes, sans signification hors de ce pipeline —
# jamais affichées.
_SAFE_ISSUE_FIELDS = (
    "itype", "value", "qid", "question", "options_count", "known_options_count",
    "max_select", "min_select", "dom_signal", "observed", "actions_count",
)


def _load_json(path: Path) -> "tuple[Any, Optional[str]]":
    if not path.is_file():
        return None, f"{path.name} absent"
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"{path.name} illisible/invalide ({exc})"


def _expected_behavior_lookup(diagnosis: dict) -> "dict[str, dict]":
    out = {}
    for entry in diagnosis.get("expected_behavior") or []:
        if isinstance(entry, dict) and entry.get("failure_type"):
            out[entry["failure_type"]] = entry
    return out


def _describe_expected(eb_lookup: dict, failure_type: str) -> str:
    entry = eb_lookup.get(failure_type)
    desc = entry.get("description") if entry else None
    if desc:
        return desc
    return "le comportement attendu n'est pas documenté pour ce type d'anomalie dans le diagnostic disponible"


def _render_issue_facts(issue: dict) -> str:
    """Rend les champs sûrs d'un issue en une phrase factuelle, sans invention."""
    parts = []
    for key in _SAFE_ISSUE_FIELDS:
        if key not in issue or issue[key] in (None, "", []):
            continue
        val = issue[key]
        if key == "observed" and isinstance(val, dict):
            val = ", ".join(f"{k}={v}" for k, v in val.items())
        if key == "value":
            parts.append(f"valeur concernée : {val!r}")
        elif key == "itype":
            parts.append(f"type de champ : {val}")
        elif key == "qid":
            parts.append(f"question : {val}")
        elif key in ("options_count", "known_options_count"):
            parts.append(f"{key.replace('_', ' ')} : {val}")
        elif key in ("max_select", "min_select"):
            parts.append(f"{key.replace('_', ' ')} : {val}")
        elif key == "dom_signal":
            parts.append(f"signal DOM observé : {val}")
        elif key == "observed":
            parts.append(f"état observé : {val}")
        elif key == "actions_count":
            parts.append(f"nombre d'actions du plan : {val}")
        elif key == "question":
            parts.append(f"intitulé : {val!r}")
    return "; ".join(parts)


def _build_bug_identifie(diagnosis: dict, code_files: "list[str]") -> str:
    stage = diagnosis.get("stage")
    itype = diagnosis.get("itype")
    replay = diagnosis.get("replay") or {}
    verdict = replay.get("verdict")
    original_types = diagnosis.get("symptom", {}).get("failure_types") or []
    replayed_types = replay.get("replayed_failure_types") or []
    issues = diagnosis.get("symptom", {}).get("issues") or []
    eb_lookup = _expected_behavior_lookup(diagnosis)

    # failure_types réellement décrits : ceux confirmés par le replay quand le
    # verdict est DIFFERENT (pas ceux du rapport d'origine resté non reproduit
    # tel quel), sinon ceux du rapport (identiques aux rejoués si REPRODUIT).
    if verdict == "DIFFERENT" and replayed_types:
        described_types = replayed_types
        use_original_issue_detail = False
    else:
        described_types = original_types or replayed_types
        use_original_issue_detail = True

    lines: list[str] = []

    if stage == "action":
        lines.append(
            "Lors d'une tentative de sélection/saisie de réponse sur une page de survey, "
            "le contrôle qui vérifie que l'action a bien été appliquée signale une anomalie."
        )
    elif stage == "extraction":
        lines.append(
            "Lors de l'extraction d'une question sur une page de survey, le contrôle qui "
            "vérifie la structure des blocs extraits signale une anomalie."
        )
    else:
        lines.append("Un contrôle du pipeline d'analyse de page signale une anomalie.")

    if itype:
        lines.append(f"Le champ/bloc concerné est de type « {itype} ».")

    lines.append("")
    lines.append("Comportement attendu :")
    for ft in described_types:
        lines.append(f"- {_describe_expected(eb_lookup, ft)}")

    lines.append("")
    lines.append("Symptôme observé :")
    if use_original_issue_detail and issues:
        any_facts = False
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            facts = _render_issue_facts(issue)
            if facts:
                lines.append(f"- {facts}")
                any_facts = True
        if not any_facts:
            lines.append("- le contrôle a signalé une anomalie sans détail supplémentaire exploitable.")
        lines.append(
            "Cette anomalie a été confirmée en rejouant l'extraction/la validation sur "
            "l'état de page figé : le même problème se reproduit à l'identique."
        )
    else:
        lines.append(
            "En rejouant l'extraction/la validation sur l'état de page figé, le problème "
            "persiste mais sous une forme différente de ce que le rapport initial décrivait : "
            "le contrôle signale désormais le(s) point(s) suivant(s) :"
        )
        for ft in replayed_types:
            lines.append(f"- {_describe_expected(eb_lookup, ft)}")

    lines.append("")
    lines.append("Fichiers probablement concernés :")
    for f in code_files:
        lines.append(f"- {f}")

    return "\n".join(lines)


def _suggest_commit_title(diagnosis: dict, code_files: "list[str]") -> str:
    scope = "survey"
    if code_files:
        stem = Path(code_files[0]).stem
        scope = stem
    itype = diagnosis.get("itype")
    desc = itype or "anomalie"
    return f"fix({scope}): corriger l'anomalie détectée sur un champ {desc}" if itype else f"fix({scope}): corriger l'anomalie détectée"


@dataclass
class PromptGenerationResult:
    eligible: bool
    case_id: str
    content: str
    reason: Optional[str] = None
    commit_title: Optional[str] = None


def generate_prompt(diagnosis_dir: "str | Path", context_selection_dir: "str | Path") -> PromptGenerationResult:
    diagnosis_dir = Path(diagnosis_dir)
    context_selection_dir = Path(context_selection_dir)
    case_id = diagnosis_dir.name

    diagnosis, diag_err = _load_json(diagnosis_dir / "diagnosis.json")
    if diag_err or not isinstance(diagnosis, dict):
        raise PromptGenerationError(f"diagnosis.json {diag_err or 'invalide'}")

    selection, sel_err = _load_json(context_selection_dir / "context_selection.json")
    if sel_err or not isinstance(selection, dict):
        raise PromptGenerationError(f"context_selection.json {sel_err or 'invalide'}")

    code_files = [
        e.get("file") for e in (selection.get("code_files") or [])
        if isinstance(e, dict) and e.get("file")
    ]
    confidence = diagnosis.get("confidence_global")

    reasons = []
    if not code_files:
        reasons.append("aucun fichier de code n'a été retenu par la sélection de contexte (Phase 5)")
    if confidence not in ELIGIBLE_CONFIDENCE_LEVELS:
        reasons.append(
            f"le niveau de confiance global du diagnostic ('{confidence}') est insuffisant "
            f"pour une génération automatique (requis : {sorted(ELIGIBLE_CONFIDENCE_LEVELS)})"
        )
        replay_verdict = (diagnosis.get("replay") or {}).get("verdict")
        if replay_verdict:
            reasons.append(f"verdict de replay associé : {replay_verdict}")

    if reasons:
        content = (
            "=== REVUE MANUELLE REQUISE — NE PAS TRANSMETTRE À CODEX ===\n\n"
            f"case_id : {case_id}\n"
            f"fichiers de code trouvés : {len(code_files)}\n"
            f"confidence_global (diagnosis.json) : {confidence}\n\n"
            "Raison précise :\n"
            + "\n".join(f"- {r}" for r in reasons)
            + "\n\n"
            "Ce case n'est pas éligible à la génération automatique d'un prompt Codex. "
            "Consulter manuellement diagnosis.json et context_selection.json avant toute "
            "action — ne pas construire de prompt à partir de ce fichier.\n"
            "=== FIN — CECI N'EST PAS UN PROMPT ==="
        )
        log_debug(_TAG, f"case={case_id} non éligible : {reasons}")
        return PromptGenerationResult(eligible=False, case_id=case_id, content=content, reason="; ".join(reasons))

    bug_identifie = _build_bug_identifie(diagnosis, code_files)
    prompt_text = _TEMPLATE.format(bug_identifie=bug_identifie)
    commit_title = _suggest_commit_title(diagnosis, code_files)
    prompt_text += f"\nTitre de commit suggéré : {commit_title}\n"

    return PromptGenerationResult(
        eligible=True, case_id=case_id, content=prompt_text, commit_title=commit_title,
    )


def write_prompt(
    diagnosis_dir: "str | Path",
    context_selection_dir: "str | Path",
    *,
    out_root: "str | Path" = "prompts",
    force: bool = False,
) -> Path:
    """Génère la sortie (prompt ou revue manuelle) sous out_root/<case_id>/.

    Ne modifie jamais diagnosis_dir ni context_selection_dir. Lève
    PromptGenerationExistsError si une sortie existe déjà et force=False.
    """
    diagnosis_dir = Path(diagnosis_dir)
    out_root = Path(out_root)
    out_dir = out_root / diagnosis_dir.name
    prompt_file = out_dir / PROMPT_FILENAME
    manual_file = out_dir / MANUAL_REVIEW_FILENAME

    if out_dir.exists():
        if not force:
            raise PromptGenerationExistsError(
                f"sortie déjà existante : {out_dir} (utiliser --force pour régénérer)"
            )
        if not prompt_file.is_file() and not manual_file.is_file():
            raise PromptGenerationError(
                f"{out_dir} existe mais ne ressemble pas à une sortie générée par cet outil "
                f"(ni {PROMPT_FILENAME} ni {MANUAL_REVIEW_FILENAME}) — suppression refusée, "
                "vérifier manuellement"
            )
        import shutil
        log_info(_TAG, f"régénération forcée : suppression de {out_dir}")
        shutil.rmtree(out_dir)

    result = generate_prompt(diagnosis_dir, context_selection_dir)

    out_dir.mkdir(parents=True, exist_ok=False)
    out_file = prompt_file if result.eligible else manual_file
    out_file.write_text(result.content, encoding="utf-8")

    log_info(
        _TAG,
        f"sortie créée case={result.case_id} eligible={result.eligible} -> {out_file}",
    )
    return out_file
