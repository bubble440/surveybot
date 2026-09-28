from __future__ import annotations

"""Phase 13 — validation humaine simplifiée via Telegram (Partie 1 : notifier,
Partie 2 : capturer la décision). Nouveau module, en lecture seule sur les
artefacts des phases précédentes — ne modifie jamais failure_cases/,
diagnoses/, context_selections/, prompts/, ni confidence_scores/. Ne déclenche
jamais lui-même un merge, un commit, ou une modification d'un worktree autofix
(cf. Phases 15/16, hors périmètre).

── Précondition (Partie 1) ─────────────────────────────────────────────────
confidence_score.json (Phase 12, Survey/confidence_score.py — non modifié)
doit exister pour ce case et porter confidence="HIGH" exactement. Le
"résultat des tests" du message repris ici est le champ "criteria" réel de
cette phase (static_validation/extractor_integrity/fix_confirmed/
live_validation, chacun {"value", "detail", "source"}) — jamais un format
inventé indépendamment. Un case sans "criteria" exploitable (garde-fou
défensif, ne devrait pas arriver puisque Phase 12 la produit toujours)
retombe sur "détail non disponible", jamais une valeur devinée.

MEDIUM/REJECT ne déclenchent jamais de notification ici — l'opérateur les voit
déjà via les sorties CLI existantes des phases précédentes.

── Réutilisation Telegram existante ────────────────────────────────────────
Les variables d'environnement telegram_bot_token/telegram_chat_id sont déjà
utilisées partout ailleurs dans ce dépôt (Management/notifier.py, launch.py,
Survey/survey_executor.py, Cash/*.py, platforms/*.py) — réutilisées ici telles
quelles, aucune nouvelle variable introduite. Management/notifier.py n'est pas
réutilisé tel quel : son send_telegram() ne supporte ni reply_markup ni la
récupération du message_id nécessaires à cette phase ; un client HTTP minimal
dédié (stdlib urllib uniquement, une seule stratégie de transport, jamais un
repli conditionnel requests/urllib) est donc défini ici.

── Encodage case_id <-> callback_data (une seule stratégie, sans repli) ────
callback_data est limité par l'API Telegram à 1-64 octets UTF-8 (vérifié :
doc officielle Bots API, InlineKeyboardButton.callback_data). Un case_id de ce
pipeline peut légitimement atteindre 121 caractères (cf. _CASE_ID_RE de
Survey/autofix_worktree.py) — le transmettre tel quel ne tiendrait pas
toujours dans cette limite. La stratégie retenue, appliquée systématiquement
(jamais "brut si court, haché sinon") : un hachage déterministe de taille fixe
(16 caractères hex = 16 octets) du case_id, préfixé par un code d'action court
("hrA:"/"hrR:"). Le "décodage" ne recalcule pas case_id à partir du hachage
(impossible, sens unique) : il recherche, parmi les dossiers déjà connus sous
human_reviews/, celui dont le même hachage correspond — symétrique par
construction, une seule fonction (_encode_case_ref) utilisée des deux côtés.

── Généralisation Phase 16 (merge semi-automatique) ────────────────────────
Telegram ne fournit qu'UN SEUL flux getUpdates par bot : deux pollers
indépendants avec deux offsets indépendants se voleraient mutuellement les
mises à jour dès qu'ils tournent tous les deux. La plomberie Telegram
partagée (client HTTP minimal, encodage/décodage case_id <-> callback_data,
gestion de l'offset persisté, envoi d'un message à deux boutons + persistance
de pending.json) est donc factorisée ici en fonctions internes réutilisables
par deux points d'entrée publics distincts : send_review_request/
check_pending_reviews (Phase 13, comportement inchangé, human_reviews/) et
Survey/merge_review.py::send_merge_confirmation_request (Phase 16,
merge_reviews/), qui importe ces internes telles quelles plutôt que de
réimplémenter un second poller. check_pending_reviews interroge getUpdates
UNE SEULE FOIS par invocation et route chaque callback_query, selon son
préfixe de callback_data, vers human_reviews/ OU merge_reviews/ avec le même
offset persisté — jamais deux invocations séparées de getUpdates. Un préfixe
de callback_data non reconnu (ni Phase 13 ni Phase 16) est ignoré avec un
avertissement, comme avant ce patch.

── Notification simple, sans bouton (send_status_notification) ────────────
Ajoutée pour un appelant qui n'a besoin que de signaler un changement d'état
(ex. étape aval de Survey/autofix_orchestrator.py — merge réussi/conflit/
blocage), sans attendre de décision en retour : réutilise _telegram_api_call
et les mêmes variables d'environnement que _send_two_button_review, jamais un
second client ni une résolution dupliquée. N'écrit ni pending.json ni
decision.json — un simple envoi, la persistance d'un éventuel garde-fou
anti-doublon reste de la responsabilité de l'appelant.
"""

import hashlib
import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from Survey.log_utils import log_debug, log_info

_TAG = "[HUMAN_REVIEW]"

_TELEGRAM_API_TIMEOUT_S = 10.0

_CB_APPROVE_PREFIX = "hrA:"
_CB_REJECT_PREFIX = "hrR:"
_CB_TOKEN_HEX_LEN = 16  # 16 octets, très en-dessous de la limite de 64 octets

# Phase 16 — préfixes distincts pour la confirmation de merge, jamais réutilisés
# tels quels ailleurs : un seul flux getUpdates partagé (cf. docstring du module),
# désambiguïsé uniquement par ce préfixe.
_CB_MERGE_APPROVE_PREFIX = "mrA:"
_CB_MERGE_REJECT_PREFIX = "mrR:"

KIND_HUMAN_REVIEW = "human_review"
KIND_MERGE_REVIEW = "merge_review"

# (kind, decision) par préfixe de callback_data — une seule table, jamais deux
# mécanismes de décodage parallèles pour les deux points d'entrée publics.
_KIND_AND_DECISION_BY_PREFIX = {
    _CB_APPROVE_PREFIX: (KIND_HUMAN_REVIEW, "APPROVED"),
    _CB_REJECT_PREFIX: (KIND_HUMAN_REVIEW, "REJECTED"),
    _CB_MERGE_APPROVE_PREFIX: (KIND_MERGE_REVIEW, "APPROVED"),
    _CB_MERGE_REJECT_PREFIX: (KIND_MERGE_REVIEW, "REJECTED"),
}

# Composant de chemin unique, allowlist conservatrice — même garde-fou que
# Survey/autofix_worktree.py::_CASE_ID_RE, dupliqué volontairement (modules
# indépendants, cf. convention déjà en place pour d'autres petits utilitaires
# de ce pipeline).
import re  # noqa: E402

_CASE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")
_WINDOWS_RESERVED_NAMES = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


class HumanReviewError(Exception):
    """Refus contrôlé — inéligibilité, configuration Telegram absente, ou appel API échoué."""


class HumanReviewExistsError(HumanReviewError):
    """pending.json ou decision.json existe déjà pour ce case (sans --force)."""


def _load_json(path: Path) -> "tuple[Any, Optional[str]]":
    if not path.is_file():
        return None, f"{path} absent"
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except OSError as exc:
        return None, f"{path} illisible ({exc})"
    except ValueError as exc:  # json.JSONDecodeError est une sous-classe de ValueError
        return None, f"{path} JSON invalide ({exc})"


def _is_safe_case_id(case_id: str) -> bool:
    if not case_id or not _CASE_ID_RE.match(case_id):
        return False
    if ".." in case_id:
        return False
    if case_id.lower() in _WINDOWS_RESERVED_NAMES:
        return False
    return True


# ─────────────────────────────────────────────────────────────────────────
# Encodage / décodage case_id <-> callback_data
# ─────────────────────────────────────────────────────────────────────────

def _encode_case_ref(case_id: str) -> str:
    return hashlib.sha256(case_id.encode("utf-8")).hexdigest()[:_CB_TOKEN_HEX_LEN]


def _parse_callback_data(data: str) -> "Optional[tuple[str, str, str]]":
    """Retourne (kind, decision, token) ou None si le préfixe n'est pas reconnu
    (ni Phase 13, ni Phase 16)."""
    for prefix, (kind, decision) in _KIND_AND_DECISION_BY_PREFIX.items():
        if data.startswith(prefix):
            return kind, decision, data[len(prefix):]
    return None


def _find_case_id_by_token(token: str, *, out_root: Path) -> Optional[str]:
    """Décodage symétrique : recalcule _encode_case_ref() pour chaque dossier de
    case déjà connu sous out_root et retourne celui qui correspond. None si
    aucun ne correspond (case inconnu ou message obsolète)."""
    if not out_root.is_dir():
        return None
    for entry in sorted(out_root.iterdir()):
        if not entry.is_dir():
            continue
        if _encode_case_ref(entry.name) == token:
            return entry.name
    return None


# ─────────────────────────────────────────────────────────────────────────
# Client Telegram minimal (stdlib urllib uniquement, budget de temps explicite)
# ─────────────────────────────────────────────────────────────────────────

def _telegram_api_call(method: str, token: str, payload: dict) -> Any:
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = json.dumps(payload).encode("utf-8")
    req = Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    log_debug(_TAG, f"Telegram {method} (budget={_TELEGRAM_API_TIMEOUT_S}s)")
    try:
        with urlopen(req, timeout=_TELEGRAM_API_TIMEOUT_S) as resp:
            body = resp.read().decode("utf-8")
    except HTTPError as exc:
        try:
            parsed_err = json.loads(exc.read().decode("utf-8"))
            desc = parsed_err.get("description", str(exc))
        except Exception:
            desc = str(exc)
        raise HumanReviewError(f"Telegram {method} HTTP {exc.code} : {desc}") from exc
    except (URLError, OSError) as exc:
        raise HumanReviewError(f"Telegram {method} injoignable/expiré : {exc}") from exc

    try:
        parsed = json.loads(body)
    except ValueError as exc:
        raise HumanReviewError(f"Telegram {method} : réponse illisible ({exc})") from exc

    if not parsed.get("ok"):
        raise HumanReviewError(f"Telegram {method} a répondu ok=false : {parsed.get('description')}")
    return parsed.get("result")


def send_status_notification(text: str) -> None:
    """Envoi d'un message Telegram SIMPLE (sans bouton, sans persistance de
    pending.json/decision.json) — additif, pour un appelant qui n'a besoin que
    de signaler un changement d'état (ex. étape aval de
    Survey/autofix_orchestrator.py : merge réussi/conflit/blocage). Réutilise
    le client HTTP minimal (_telegram_api_call) et la résolution des mêmes
    variables d'environnement (telegram_bot_token/telegram_chat_id) que
    _send_two_button_review, sans les dupliquer. Lève HumanReviewError si la
    configuration Telegram est absente ou si l'appel échoue — à l'appelant de
    décider comment traiter un échec de notification (jamais bloquant par
    nature ici)."""
    tg_token = os.getenv("telegram_bot_token", "").strip()
    tg_chat = os.getenv("telegram_chat_id", "").strip()
    if not tg_token or not tg_chat:
        raise HumanReviewError(
            "Telegram non configuré (telegram_bot_token/telegram_chat_id absents) — "
            "notification impossible"
        )
    _telegram_api_call("sendMessage", tg_token, {"chat_id": tg_chat, "text": text})


# ─────────────────────────────────────────────────────────────────────────
# Partie 1 — notifier
# ─────────────────────────────────────────────────────────────────────────

@dataclass
class ReviewEligibility:
    eligible: bool
    case_id: Optional[str]
    reasons: "list[str]" = field(default_factory=list)


def check_review_eligibility(
    *,
    confidence_score_dir: "str | Path",
    diagnosis_dir: "str | Path",
) -> ReviewEligibility:
    """Vérifie toutes les conditions ensemble ; ne s'arrête jamais à la première
    raison de refus rencontrée."""
    confidence_score_dir = Path(confidence_score_dir)
    diagnosis_dir = Path(diagnosis_dir)
    reasons: "list[str]" = []

    confidence_score, cs_err = _load_json(confidence_score_dir / "confidence_score.json")
    if cs_err or not isinstance(confidence_score, dict):
        reasons.append(f"confidence_score.json {cs_err or 'ne contient pas un objet JSON'}")

    diagnosis, diag_err = _load_json(diagnosis_dir / "diagnosis.json")
    if diag_err or not isinstance(diagnosis, dict):
        reasons.append(f"diagnosis.json {diag_err or 'ne contient pas un objet JSON'}")

    if cs_err or diag_err or not isinstance(confidence_score, dict) or not isinstance(diagnosis, dict):
        return ReviewEligibility(eligible=False, case_id=None, reasons=reasons)

    case_ids = {
        "confidence_score.json": str(confidence_score.get("case_id") or ""),
        "diagnosis.json": str(diagnosis.get("case_id") or ""),
        "confidence_score_dir": confidence_score_dir.name,
        "diagnosis_dir": diagnosis_dir.name,
    }
    distinct = set(case_ids.values())
    resolved_case_id: Optional[str]
    if len(distinct) != 1 or "" in distinct:
        reasons.append(f"case_id incohérent entre les sources : {case_ids}")
        resolved_case_id = None
    else:
        resolved_case_id = next(iter(distinct))
        if not _is_safe_case_id(resolved_case_id):
            reasons.append(
                f"case_id {resolved_case_id!r} n'est pas utilisable sans transformation comme "
                "composant de chemin"
            )

    confidence = str(confidence_score.get("confidence") or "")
    if confidence != "HIGH":
        reasons.append(
            f"confidence={confidence!r} — seule confidence=\"HIGH\" déclenche une notification "
            "(MEDIUM/REJECT sont déjà visibles via les sorties CLI existantes)"
        )

    return ReviewEligibility(eligible=not reasons, case_id=resolved_case_id, reasons=reasons)


# Ordre et libellés d'affichage des quatre critères de Survey/confidence_score.py
# (static_validation/extractor_integrity/fix_confirmed/live_validation) — noms de
# clés repris tels quels de ce module (non modifié), jamais réinventés.
_CRITERIA_ORDER = ("static_validation", "extractor_integrity", "fix_confirmed", "live_validation")
_CRITERIA_LABELS = {
    "static_validation": "Validation statique (Phase 8)",
    "extractor_integrity": "Intégrité fonctions gelées (Phase 11-A)",
    "fix_confirmed": "Correctif confirmé",
    "live_validation": "Validation live (Phase 10)",
}


def _compose_message(*, case_id: str, diagnosis: dict, confidence_score: dict) -> str:
    """Symptôme, cause probable, tests, confiance — jamais le diff du patch, jamais
    une donnée brute d'un vrai répondant. Utilise volontairement
    symptom.failure_types (liste de noms de failure_type) plutôt que
    symptom.issues : ce dernier recopie les issues de validation_report.json
    (Phase 4), et rester sur les seuls noms de failure_type évite toute
    dépendance à leur éventuel contenu, sanitisé ou non, en amont."""
    symptom = diagnosis.get("symptom") if isinstance(diagnosis.get("symptom"), dict) else {}
    failure_types = symptom.get("failure_types") or []
    symptom_line = ", ".join(str(x) for x in failure_types) if failure_types else "non documenté"

    cause = diagnosis.get("cause") if isinstance(diagnosis.get("cause"), dict) else {}
    cause_line = str(cause.get("justification") or "non documentée")

    criteria = confidence_score.get("criteria")
    tests_block = "détail non disponible"
    if isinstance(criteria, dict) and criteria:
        lines = []
        for key in _CRITERIA_ORDER:
            entry = criteria.get(key)
            if isinstance(entry, dict):
                lines.append(f"- {_CRITERIA_LABELS.get(key, key)} : {entry.get('value', '?')}")
        if lines:
            tests_block = "\n".join(lines)

    confidence = str(confidence_score.get("confidence") or "?")

    return (
        f"🔎 Revue humaine requise — case {case_id}\n\n"
        f"Symptôme : {symptom_line}\n"
        f"Cause probable : {cause_line}\n\n"
        f"Tests :\n{tests_block}\n\n"
        f"Confiance : {confidence}"
    )


@dataclass
class ReviewRequestResult:
    case_id: str
    chat_id: Any
    message_id: int
    pending_path: Path


def _send_two_button_review(
    *,
    out_root: Path,
    case_id: str,
    text: str,
    approve_prefix: str,
    approve_label: str,
    reject_prefix: str,
    reject_label: str,
    force: bool,
    log_tag: str = _TAG,
) -> ReviewRequestResult:
    """Plomberie Telegram partagée (Phase 13 ET Phase 16, cf. docstring du
    module) : vérifie la configuration Telegram, refuse un envoi en double
    (pending.json/decision.json déjà présents, sauf force=True), envoie `text`
    avec un clavier inline à deux boutons dont les callback_data sont préfixés
    par approve_prefix/reject_prefix, puis persiste out_root/<case_id>/
    pending.json (même schéma pour les deux points d'entrée). Ne calcule
    aucune éligibilité ni aucun contenu de message — l'appelant reste seul
    responsable de ces deux choix, spécifiques à sa phase. `log_tag` (défaut :
    le tag de ce module, comportement Phase 13 inchangé) permet à l'appelant
    de journaliser sous son propre tag plutôt que [HUMAN_REVIEW]."""
    tg_token = os.getenv("telegram_bot_token", "").strip()
    tg_chat = os.getenv("telegram_chat_id", "").strip()
    if not tg_token or not tg_chat:
        raise HumanReviewError(
            "Telegram non configuré (telegram_bot_token/telegram_chat_id absents) — "
            "notification impossible"
        )

    out_dir = out_root / case_id
    pending_file = out_dir / "pending.json"
    decision_file = out_dir / "decision.json"
    if pending_file.is_file() or decision_file.is_file():
        if not force:
            existing = "decision.json" if decision_file.is_file() else "pending.json"
            raise HumanReviewExistsError(
                f"{existing} déjà présent dans {out_dir} (utiliser --force pour renotifier — "
                "jamais un envoi silencieux en double)"
            )
        log_info(log_tag, f"--force : suppression de {out_dir} avant renvoi")
        shutil.rmtree(out_dir)

    token = _encode_case_ref(case_id)
    reply_markup = {
        "inline_keyboard": [[
            {"text": approve_label, "callback_data": f"{approve_prefix}{token}"},
            {"text": reject_label, "callback_data": f"{reject_prefix}{token}"},
        ]]
    }

    result = _telegram_api_call("sendMessage", tg_token, {
        "chat_id": tg_chat,
        "text": text,
        "reply_markup": reply_markup,
    })
    if not isinstance(result, dict) or "message_id" not in result:
        raise HumanReviewError(f"réponse Telegram sendMessage inattendue : {result!r}")
    message_id = result["message_id"]
    chat_id = (result.get("chat") or {}).get("id", tg_chat)

    out_dir.mkdir(parents=True, exist_ok=False)
    pending_file.write_text(
        json.dumps({
            "schema_version": 1,
            "case_id": case_id,
            "chat_id": chat_id,
            "message_id": message_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    log_info(log_tag, f"notification envoyée case={case_id} chat_id={chat_id} message_id={message_id}")

    return ReviewRequestResult(case_id=case_id, chat_id=chat_id, message_id=message_id, pending_path=pending_file)


def send_review_request(
    *,
    confidence_score_dir: "str | Path",
    diagnosis_dir: "str | Path",
    out_root: "str | Path" = "human_reviews",
    force: bool = False,
) -> ReviewRequestResult:
    confidence_score_dir = Path(confidence_score_dir)
    diagnosis_dir = Path(diagnosis_dir)
    out_root = Path(out_root)

    eligibility = check_review_eligibility(
        confidence_score_dir=confidence_score_dir,
        diagnosis_dir=diagnosis_dir,
    )
    if not eligibility.eligible:
        raise HumanReviewError(
            "case non éligible à la notification (Phase 13) : " + " ; ".join(eligibility.reasons)
        )
    case_id = eligibility.case_id
    assert case_id is not None  # garanti par eligible=True

    confidence_score, _ = _load_json(confidence_score_dir / "confidence_score.json")
    diagnosis, _ = _load_json(diagnosis_dir / "diagnosis.json")
    text = _compose_message(case_id=case_id, diagnosis=diagnosis, confidence_score=confidence_score)

    return _send_two_button_review(
        out_root=out_root,
        case_id=case_id,
        text=text,
        approve_prefix=_CB_APPROVE_PREFIX,
        approve_label="✅ Approuver",
        reject_prefix=_CB_REJECT_PREFIX,
        reject_label="❌ Rejeter",
        force=force,
    )


# ─────────────────────────────────────────────────────────────────────────
# Partie 2 — capturer les décisions en attente
# ─────────────────────────────────────────────────────────────────────────

def _best_effort_finalize_message(tg_token: str, *, callback: dict, decision_label: str) -> None:
    """Retire les boutons et affiche la décision prise sur le message d'origine.
    Best-effort explicite : n'importe quel échec (message trop ancien, réseau,
    droits) est journalisé en debug et n'affecte jamais l'écriture déjà faite de
    decision.json."""
    message = callback.get("message") if isinstance(callback.get("message"), dict) else {}
    chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
    chat_id = chat.get("id")
    message_id = message.get("message_id")
    if chat_id is None or message_id is None:
        return
    label = "✅ APPROUVÉ" if decision_label == "APPROVED" else "❌ REJETÉ"
    original_text = message.get("text")
    new_text = f"{original_text}\n\n— Décision : {label}" if original_text else f"Décision : {label}"
    try:
        _telegram_api_call("editMessageText", tg_token, {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": new_text,
            "reply_markup": {"inline_keyboard": []},
        })
    except HumanReviewError as exc:
        log_debug(_TAG, f"édition du message Telegram échouée, ignorée (décision déjà enregistrée) : {exc}")


def _best_effort_answer_callback(tg_token: str, callback_id: Any) -> None:
    try:
        _telegram_api_call("answerCallbackQuery", tg_token, {"callback_query_id": callback_id})
    except HumanReviewError as exc:
        log_debug(_TAG, f"answerCallbackQuery échoué, ignoré : {exc}")


@dataclass
class ProcessedCallback:
    kind: str  # "human_review" | "merge_review"
    case_id: Optional[str]
    decision: str
    outcome: str  # "written" | "ignored_duplicate" | "unknown_case"


@dataclass
class CheckResult:
    updates_fetched: int
    callback_queries_seen: int
    processed: "list[ProcessedCallback]"
    next_offset: int


def _load_offset(offset_file: Path) -> int:
    data, _ = _load_json(offset_file)
    if isinstance(data, dict):
        try:
            return int(data.get("next_offset") or 0)
        except (TypeError, ValueError):
            return 0
    return 0


def _root_for_kind(kind: str, *, human_out_root: Path, merge_out_root: Optional[Path]) -> Optional[Path]:
    if kind == KIND_HUMAN_REVIEW:
        return human_out_root
    if kind == KIND_MERGE_REVIEW:
        return merge_out_root
    return None


def check_pending_reviews(
    *,
    out_root: "str | Path" = "human_reviews",
    merge_out_root: "Optional[str | Path]" = "merge_reviews",
    offset_file: "Optional[str | Path]" = None,
) -> CheckResult:
    """Interroge getUpdates UNE SEULE FOIS et route chaque callback_query du
    lot, selon son préfixe de callback_data, vers human_reviews/ (Phase 13) OU
    merge_reviews/ (Phase 16) — même offset persisté pour les deux, jamais
    deux invocations séparées de getUpdates (cf. docstring du module).
    L'offset persisté n'avance qu'après que toutes les décisions du lot ont
    été durablement écrites ou explicitement ignorées — jamais avant."""
    out_root = Path(out_root)
    merge_root = Path(merge_out_root) if merge_out_root is not None else None
    offset_path = Path(offset_file) if offset_file else out_root / "_telegram_offset.json"

    tg_token = os.getenv("telegram_bot_token", "").strip()
    if not tg_token:
        raise HumanReviewError("Telegram non configuré (telegram_bot_token absent)")

    current_offset = _load_offset(offset_path)

    payload: dict = {"timeout": 0, "allowed_updates": ["callback_query"]}
    if current_offset:
        payload["offset"] = current_offset

    updates = _telegram_api_call("getUpdates", tg_token, payload)
    if not isinstance(updates, list):
        raise HumanReviewError(f"réponse Telegram getUpdates inattendue : {updates!r}")

    processed: "list[ProcessedCallback]" = []
    max_update_id = current_offset - 1 if current_offset else -1
    callback_count = 0

    for update in updates:
        if not isinstance(update, dict):
            continue
        update_id = update.get("update_id")
        if isinstance(update_id, int) and update_id > max_update_id:
            max_update_id = update_id

        callback = update.get("callback_query")
        if not isinstance(callback, dict):
            continue
        callback_count += 1

        data = str(callback.get("data") or "")
        parsed = _parse_callback_data(data)
        if parsed is None:
            log_info(_TAG, f"avertissement : callback_data non reconnu, ignoré ({data!r})")
            continue
        kind, decision_label, token = parsed

        kind_root = _root_for_kind(kind, human_out_root=out_root, merge_out_root=merge_root)
        if kind_root is None:
            log_info(
                _TAG,
                f"avertissement : callback_data reconnu (kind={kind}) mais aucun dossier configuré pour "
                "ce type dans cette invocation, ignoré",
            )
            continue

        case_id = _find_case_id_by_token(token, out_root=kind_root)
        if case_id is None:
            log_info(_TAG, f"avertissement : callback reçu pour un case inconnu (kind={kind}, token={token}) — ignoré")
            processed.append(ProcessedCallback(kind=kind, case_id=None, decision=decision_label, outcome="unknown_case"))
            callback_id = callback.get("id")
            if callback_id:
                _best_effort_answer_callback(tg_token, callback_id)
            continue

        case_dir = kind_root / case_id
        decision_file = case_dir / "decision.json"
        if decision_file.is_file():
            log_info(
                _TAG,
                f"avertissement : décision déjà enregistrée pour kind={kind} case={case_id} — "
                "callback ignoré (première décision fait foi)",
            )
            processed.append(ProcessedCallback(kind=kind, case_id=case_id, decision=decision_label, outcome="ignored_duplicate"))
            callback_id = callback.get("id")
            if callback_id:
                _best_effort_answer_callback(tg_token, callback_id)
            continue

        from_user = callback.get("from") if isinstance(callback.get("from"), dict) else {}
        case_dir.mkdir(parents=True, exist_ok=True)
        decision_file.write_text(
            json.dumps({
                "schema_version": 1,
                "case_id": case_id,
                "decision": decision_label,
                "decided_at": datetime.now(timezone.utc).isoformat(),
                "telegram_user_id": from_user.get("id"),
                "telegram_username": from_user.get("username"),
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        log_info(_TAG, f"décision enregistrée kind={kind} case={case_id} decision={decision_label}")
        processed.append(ProcessedCallback(kind=kind, case_id=case_id, decision=decision_label, outcome="written"))

        _best_effort_finalize_message(tg_token, callback=callback, decision_label=decision_label)
        callback_id = callback.get("id")
        if callback_id:
            _best_effort_answer_callback(tg_token, callback_id)

    next_offset = max_update_id + 1 if max_update_id >= 0 else current_offset

    offset_path.parent.mkdir(parents=True, exist_ok=True)
    offset_path.write_text(
        json.dumps({
            "schema_version": 1,
            "next_offset": next_offset,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return CheckResult(
        updates_fetched=len(updates),
        callback_queries_seen=callback_count,
        processed=processed,
        next_offset=next_offset,
    )
