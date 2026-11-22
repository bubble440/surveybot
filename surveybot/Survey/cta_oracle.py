from __future__ import annotations

"""Oracle CTA minimal, BDL-5 v2 (étape 2) — OBSERVATION SEULE.

Évalue, pour un seul enregistrement de clic CTA déjà collecté (une ligne
JSONL persistée par ``failure_recorder.record_collected_cta``, voir ce
module pour le format exact ; enveloppe posée par ``cta_trace.py``), un
verdict sur ce seul enregistrement. Fonction pure : aucun appel au
navigateur, aucune I/O, aucune horloge, aucun état global. L'entrée n'est
jamais modifiée. Rejouable hors ligne sur le corpus collecté, et par
l'outil de mesure à venir (BDL-6). Voir
``Utils/BUG_DETECTION_LAYER_TRACKING.md``, section « BDL-5 v2 », pour le
découpage collecte / oracle / mesure et les décisions amont (2026-11-18).

Aucun branchement dans le flux du bot : ce module n'est importé ni par
``cta_trace.py`` ni par ``cta_handler.py`` ; il ne sert qu'au rejeu hors
ligne. Zéro diff sur le cœur protégé (dont le gestionnaire de CTA).

Verdicts possibles : PASS, SUSPECTED, INCONCLUSIVE. BUG_CONFIRMED n'est
jamais produit dans cette version (exigerait une sonde de visibilité du CTA
absente de la collecte v1).

Forme de sortie (même esprit que ``extraction_oracle``) : un dict avec
``oracle_version``, ``verdict``, ``reason`` (liste fermée), ``rule`` (la
règle appliquée) et ``evidence`` (les valeurs lues dans l'enregistrement,
jamais une interprétation libre).

L'oracle ne lit JAMAIS le booléen ``returned`` comme preuve de progression
(même principe que la trace, voir cta_trace.py : il confond un marqueur vide
avec une progression réelle) et n'utilise aucune détection par texte (ni
mot-clé d'erreur, ni langue) : seuls ``marker_status`` et
``signals_changed`` (déjà calculés par la collecte) sont lus, plus une
vérification dédiée des URL persistées pour le seul signal ``url``.

Règles de verdict (appliquées dans cet ordre ci-dessous ; la première qui
correspond gagne) :

1. Enregistrement qui n'est pas un clic observé (ex. un enregistrement de
   couverture « appel sans clic tracé », ``kind="entry"``) -> INCONCLUSIVE.
   Pourquoi : un enregistrement de couverture ne décrit aucun clic réel ;
   lui donner un verdict de clic serait une invention. Raison
   ``unobserved_path`` pour le cas de couverture connu, sinon
   ``not_a_click_record`` (type inconnu ou non prévu par la collecte).
2. Mode différent de « normal » -> INCONCLUSIVE ``intercept_mode_obsolete``.
   Pourquoi : ``CTA_INTERCEPT_ONLY`` est jugé obsolète dans le projet
   (décision du 2026-11-18) ; aucune autre règle ne s'applique sous ce mode.
3. La fonction de clic a levé (``raised`` renseigné) -> INCONCLUSIVE
   ``click_raised``. Pourquoi : un clic qui a levé une exception n'a pas de
   résultat DOM fiable à juger après coup.
4. Au moins un signal fort (``doc``, ``qNodes``, ou ``url`` si les URL
   persistées diffèrent réellement) dans la liste des signaux changés ->
   PASS. Pourquoi : ce sont les seuls signaux qui ne changent pas sur un
   clic raté affichant une erreur (durcissement BDL-5 v2, 2026-11-18). Les
   autres signaux de la liste ne sont jamais recalculés : seul ``url`` l'est,
   parce que la liste fermée ne distingue pas un vrai changement de
   document d'un query/fragment déjà jeté à la collecte.
5. Seuls des signaux faibles (``txt``, ``notifSig``, ``progGridSig``) ->
   INCONCLUSIVE ``weak_signals_only``. Pourquoi : ces signaux changent aussi
   sur un clic raté qui affiche un message d'erreur ; les compter comme
   PASS produirait des faux négatifs par construction.
6. Aucun signal retenu, ``marker_status == "ok"`` -> SUSPECTED
   ``no_signal_marker_ok``. Pourquoi : le marqueur est fiable mais rien n'a
   changé ; probable absence de progression, à relire humainement (jamais
   de promotion automatique, voir BDL-5 v2 / BDL-7).
7. Aucun signal retenu, ``marker_status`` différent de « ok » ->
   INCONCLUSIVE ``marker_unreliable``. Pourquoi : le marqueur lui-même est
   douteux (vide avant et/ou après) ; conclure à partir d'un marqueur non
   fiable serait une interprétation libre.

Jamais d'exception : une entrée qui n'est pas un dict donne None (rien à
juger) ; un enregistrement ``kind="click"`` trop incomplet pour appliquer
les règles 4 à 7 (``signals_changed`` absent ou n'étant pas une liste)
donne INCONCLUSIVE ``malformed_record`` plutôt qu'une exception ou une
valeur inventée.

Limites connues, non résolues ici (cf. BDL-5 v2) :
- Un changement de document peut survenir sur une page qui reste en erreur
  de validation : une page d'erreur peut donc être jugée PASS. Seule la
  relecture humaine du corpus pourra le dire.
- Un clic dont l'action amont a échoué (réponse non saisie) n'est pas
  distinguable ici : l'enregistrement ne dit pas pourquoi le CTA a été
  cliqué.
- Les transitions internes de type slider à navigation interne ne sont pas
  traitées.
- Les chemins CTA hors point de passage observé (``survey_executor.py``,
  ``action_dispatcher.py``) sont invisibles pour l'oracle.
"""

from typing import Any, Optional

ORACLE_VERSION = 1

PASS = "PASS"
SUSPECTED = "SUSPECTED"
INCONCLUSIVE = "INCONCLUSIVE"

# Signaux forts dont la présence dans signals_changed suffit telle quelle
# (non recalculés, voir règle 4) ; "url" est traité à part : il n'est fort
# que si les URL persistées diffèrent réellement (cta_trace.SIGNALS).
_STRONG_DIRECT_SIGNALS = ("doc", "qNodes")
_WEAK_SIGNALS = ("txt", "notifSig", "progGridSig")

REASON_UNOBSERVED_PATH = "unobserved_path"
REASON_NOT_A_CLICK_RECORD = "not_a_click_record"
REASON_INTERCEPT_MODE_OBSOLETE = "intercept_mode_obsolete"
REASON_CLICK_RAISED = "click_raised"
REASON_STRONG_SIGNAL_CHANGED = "strong_signal_changed"
REASON_WEAK_SIGNALS_ONLY = "weak_signals_only"
REASON_NO_SIGNAL_MARKER_OK = "no_signal_marker_ok"
REASON_MARKER_UNRELIABLE = "marker_unreliable"
REASON_MALFORMED_RECORD = "malformed_record"

RULE_NOT_A_CLICK_RECORD = "rule_1_not_a_click_record"
RULE_INTERCEPT_MODE = "rule_2_intercept_mode"
RULE_CLICK_RAISED = "rule_3_click_raised"
RULE_STRONG_SIGNAL = "rule_4_strong_signal"
RULE_WEAK_SIGNALS_ONLY = "rule_5_weak_signals_only"
RULE_NO_SIGNAL_MARKER_OK = "rule_6_no_signal_marker_ok"
RULE_MARKER_UNRELIABLE = "rule_7_marker_unreliable"
RULE_MALFORMED_RECORD = "rule_malformed_record"


def _verdict(verdict: str, reason: str, rule: str, evidence: dict) -> dict:
    return {
        "oracle_version": ORACLE_VERSION,
        "verdict": verdict,
        "reason": reason,
        "rule": rule,
        "evidence": evidence,
    }


def _url_really_changed(record: dict) -> bool:
    """True si les URL de page avant/après persistées diffèrent réellement.

    Lit ``page_before``/``page_after`` (lecture indépendante du marqueur,
    toujours tentée par la collecte — voir ``cta_trace._read_page``) plutôt
    que les URL de marqueur : elles restent présentes même quand le
    marqueur est vide (``empty_before``/``empty_after``), ce qui permet au
    signal ``url`` de s'appliquer dans ce cas sans dépendre de la fiabilité
    du marqueur. Les deux URL sont déjà assainies à la collecte
    (``failure_recorder._sanitize_url``) : aucune normalisation ici.
    """
    pb = record.get("page_before")
    pa = record.get("page_after")
    if not isinstance(pb, dict) or not isinstance(pa, dict):
        return False
    before, after = pb.get("url"), pa.get("url")
    if not isinstance(before, str) or not isinstance(after, str) or not before or not after:
        return False
    return before != after


def evaluate_cta_click(record: Any) -> Optional[dict]:
    """Verdict sur un seul enregistrement CTA déjà collecté.

    Retourne None si ``record`` n'est pas un dict (rien à juger). Ne lève
    jamais : un enregistrement mal formé donne INCONCLUSIVE avec une raison
    dédiée plutôt qu'une exception ou une valeur inventée.
    """
    if not isinstance(record, dict):
        return None

    kind = record.get("kind")
    if kind != "click":
        # Règle 1 : un enregistrement de couverture (kind="entry",
        # unobserved_path) ou tout autre type ne décrit aucun clic observé.
        if kind == "entry" and record.get("outcome") == "unobserved_path":
            return _verdict(
                INCONCLUSIVE, REASON_UNOBSERVED_PATH, RULE_NOT_A_CLICK_RECORD,
                {"kind": kind, "outcome": record.get("outcome")},
            )
        return _verdict(
            INCONCLUSIVE, REASON_NOT_A_CLICK_RECORD, RULE_NOT_A_CLICK_RECORD,
            {"kind": kind},
        )

    mode = record.get("mode")
    if mode != "normal":
        # Règle 2 : CTA_INTERCEPT_ONLY (ou tout mode non "normal") est hors
        # périmètre de l'oracle (décision du 2026-11-18) ; priorité sur les
        # règles suivantes même si des signaux forts sont présents.
        return _verdict(
            INCONCLUSIVE, REASON_INTERCEPT_MODE_OBSOLETE, RULE_INTERCEPT_MODE,
            {"mode": mode},
        )

    raised = record.get("raised")
    if raised:
        # Règle 3 : exception levée par la fonction de clic -> pas de DOM
        # fiable à juger après coup, quels que soient les signaux.
        return _verdict(
            INCONCLUSIVE, REASON_CLICK_RAISED, RULE_CLICK_RAISED,
            {"raised": raised},
        )

    signals = record.get("signals_changed")
    if not isinstance(signals, (list, tuple)):
        # Mal formé : sans liste de signaux exploitable, les règles 4 à 7
        # ne peuvent s'appliquer sans inventer une valeur par défaut.
        return _verdict(
            INCONCLUSIVE, REASON_MALFORMED_RECORD, RULE_MALFORMED_RECORD,
            {"signals_changed": signals},
        )
    signals = list(signals)

    strong_direct = [s for s in signals if s in _STRONG_DIRECT_SIGNALS]
    url_changed = "url" in signals and _url_really_changed(record)
    if strong_direct or url_changed:
        # Règle 4 : un seul signal fort suffit ; "doc"/"qNodes" sont pris
        # tels quels (pas de recalcul), "url" est vérifié (voir ci-dessus).
        return _verdict(
            PASS, REASON_STRONG_SIGNAL_CHANGED, RULE_STRONG_SIGNAL,
            {"signals_changed": signals, "url_changed": url_changed},
        )

    if any(s in _WEAK_SIGNALS for s in signals):
        # Règle 5 : signal(s) faible(s) seul(s) restant(s) — "url" listé
        # mais ignoré (règle 4) ne compte pas comme un signal faible.
        return _verdict(
            INCONCLUSIVE, REASON_WEAK_SIGNALS_ONLY, RULE_WEAK_SIGNALS_ONLY,
            {"signals_changed": signals},
        )

    marker_status = record.get("marker_status")
    if marker_status == "ok":
        # Règle 6 : marqueur fiable, aucun signal retenu -> suspect, jamais
        # promu automatiquement (BDL-5 v2 / BDL-7).
        return _verdict(
            SUSPECTED, REASON_NO_SIGNAL_MARKER_OK, RULE_NO_SIGNAL_MARKER_OK,
            {"marker_status": marker_status, "signals_changed": signals},
        )

    # Règle 7 : ni signal retenu, ni marqueur fiable -> abstention.
    return _verdict(
        INCONCLUSIVE, REASON_MARKER_UNRELIABLE, RULE_MARKER_UNRELIABLE,
        {"marker_status": marker_status, "signals_changed": signals},
    )
