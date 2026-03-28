"""
tools/failure_pipeline.py — Pipeline d'auto-correction SurveyBot

CLI  : python tools/failure_pipeline.py ./snapshots/<nom_du_snapshot> --step extraction
Import : trigger_pipeline(snapshot_dir, step) appelable depuis le bot (non-bloquant)

Étapes :
  1. Lecture du snapshot (dom_outer.html + question_blocks.json)
  2. OpenAI/ChatGPT génère le question_blocks_expected → validation humaine obligatoire
  3. Écriture patch_request.md + lancement du LLM de patch (PATCH_LLM)
  4. Validation via replay_snapshot.py + diff PASS/FAIL

DÉCLENCHEMENT MANUEL (mode attach uniquement) :
  Créer le fichier pointé par FAILURE_PIPELINE_TRIGGER_FILE pour déclencher le pipeline
  sur la prochaine page traitée par execute_survey_page().
  Exemple : touch C:/tmp/fp_trigger   (bash)
            New-Item C:/tmp/fp_trigger (PowerShell)
  Puis définir : FAILURE_PIPELINE_TRIGGER_FILE=C:/tmp/fp_trigger dans l'environnement.
"""

import argparse
import difflib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional


# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION — à modifier selon votre setup
# ─────────────────────────────────────────────────────────────────────────────

# LLM utilisé pour rédiger le patch (étape 3).
# "claude"  → claude --print --file patch_request.md
# "codex"   → codex  (contenu de patch_request.md passé en prompt)
PATCH_LLM = "claude"  # "claude" | "codex"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers internes
# ─────────────────────────────────────────────────────────────────────────────

def _is_attach_mode() -> bool:
    """Retourne True uniquement en mode attach (BROWSER_MODE=attach + RUN_ENV=local)."""
    try:
        from config import is_attach_mode
        return is_attach_mode()
    except Exception:
        # Fallback si config n'est pas importable (appel direct CLI hors bot)
        return (
            os.getenv("BROWSER_MODE", "").strip().lower() == "attach"
            and os.getenv("RUN_ENV", "local").strip().lower() == "local"
        )


def check_and_consume_manual_trigger() -> bool:
    """
    Vérifie si le fichier de déclenchement manuel existe (FAILURE_PIPELINE_TRIGGER_FILE).
    Si oui, le supprime et retourne True — le pipeline sera déclenché sur la page courante.
    """
    flag_file = os.getenv("FAILURE_PIPELINE_TRIGGER_FILE", "").strip()
    if not flag_file:
        return False
    p = Path(flag_file)
    if p.exists():
        try:
            p.unlink()
        except Exception:
            pass
        print(f"[FAILURE_PIPELINE] Déclenchement manuel détecté ({flag_file})")
        return True
    return False


def _openai_api_key() -> Optional[str]:
    return (
        os.getenv("OPENAI_API_KEY")
        or os.getenv("OPENAI_API_KEY_LOCAL")
    )


# ─────────────────────────────────────────────────────────────────────────────
# Étape 1 — Lecture du snapshot
# ─────────────────────────────────────────────────────────────────────────────

def _read_snapshot(snapshot_dir: str):
    """Lit dom_outer.html et question_blocks.json du snapshot."""
    snapshot_path = Path(snapshot_dir)

    dom_path = snapshot_path / "dom_outer.html"
    blocks_path = snapshot_path / "question_blocks.json"

    if not dom_path.exists():
        raise FileNotFoundError(f"dom_outer.html introuvable dans {snapshot_dir}")

    dom_html = dom_path.read_text(encoding="utf-8", errors="replace")

    question_blocks = []
    if blocks_path.exists():
        try:
            question_blocks = json.loads(blocks_path.read_text(encoding="utf-8"))
        except Exception:
            question_blocks = []

    return dom_html, question_blocks


# ─────────────────────────────────────────────────────────────────────────────
# Étape 2 — Génération du expected via OpenAI/ChatGPT
# ─────────────────────────────────────────────────────────────────────────────

def _generate_expected_via_openai(dom_html: str, question_blocks: list) -> Optional[list]:
    """Appelle OpenAI (gpt-4o) pour générer les question_blocks corrigés."""
    try:
        import openai
    except ImportError:
        print("[FAILURE_PIPELINE] Package 'openai' non disponible. Étape 2 impossible.")
        return None

    api_key = _openai_api_key()
    if not api_key:
        print("[FAILURE_PIPELINE] OPENAI_API_KEY introuvable. Étape 2 impossible.")
        return None

    dom_truncated = dom_html[:80000] if len(dom_html) > 80000 else dom_html
    blocks_json = json.dumps(question_blocks, ensure_ascii=False, indent=2)

    prompt = (
        "Voici le DOM d'une page survey et les question_blocks extraits par le bot. "
        "Génère le question_blocks.json corrigé au format exact : "
        "[{question, itype, options:[{value,label}], max_select, target_id, context}]. "
        "Retourne uniquement du JSON valide, sans markdown.\n\n"
        f"=== DOM ===\n{dom_truncated}\n\n"
        f"=== question_blocks actuels ===\n{blocks_json}"
    )

    client = openai.OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model="gpt-4o",
        max_tokens=8192,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
    )

    raw = response.choices[0].message.content.strip()

    # Supprimer les balises markdown éventuelles
    if raw.startswith("```"):
        lines = [l for l in raw.splitlines() if not l.startswith("```")]
        raw = "\n".join(lines).strip()

    return json.loads(raw)


# ─────────────────────────────────────────────────────────────────────────────
# Étape 3 — Écriture patch_request.md + lancement du LLM de patch
# ─────────────────────────────────────────────────────────────────────────────

def _write_patch_request(snapshot_dir: str, step: str, question_blocks: list, expected_blocks: list) -> str:
    """Écrit snapshot_dir/patch_request.md et retourne son chemin."""
    snapshot_path = Path(snapshot_dir)

    blocks_json = json.dumps(question_blocks, ensure_ascii=False, indent=2)
    expected_json = json.dumps(expected_blocks, ensure_ascii=False, indent=2)

    content = f"""CONTEXTE

PROJECT_ARCHITECTURE.md est le document de référence qui décrit l'architecture, liste les fichiers du projet, leurs rôles, et les fonctions principales.

IMPORTANT (variabilité intra-source)

Même si une page vient d'une même "source/provider" (ex: ipsosinteractive.com), toutes les pages de cette source n'ont pas forcément la même structure DOM.
Donc :
* n'ajoute pas de logique qui suppose une structure unique par source (pas de "if provider == ipsos: alors …" trop large).
* si tu ajoutes un support "spécifique", il doit être déclenché par des critères DOM précis et observables (sélecteurs / patterns) et scopé au minimum nécessaire.
* ne casse pas les modules existants déjà utilisés pour d'autres pages de la même source : le patch doit être additif, avec garde-fous DOM, et rester compatible.

IMPORTANT (LOGS / LOG_LEVEL)

Si tu ajoutes des logs dans un patch :
* Tous les logs "diagnostic détaillé" (debug) DOIVENT être conditionnés par LOG_LEVEL.
  - En Python : utilise Survey.log_utils.log_debug(tag, msg) (ou is_debug() puis log_debug()).
  - Ne fais PAS de print() direct pour du debug.
* Les logs "normaux" (info) peuvent rester non-conditionnés, mais doivent être rares (1–2 lignes max par étape).
  - En Python : utilise Survey.log_utils.log_info(tag, msg).
* Convention : LOG_LEVEL=DEBUG active le debug. Tout autre valeur (ou vide) => pas de debug.

IMPORTANT (CTA / clics)

Si le bug ou le patch implique un clic sur un CTA (Suivant/Continuer/Next/Submit/Start/etc.), conditionner le comportement à CTA_INTERCEPT_ONLY :
* Si activée (1/true/yes/on) : ne jamais soumettre réellement. Intercepter/simuler le clic sans navigation ni side-effects irréversibles.
* Si désactivée : comportement normal (clic réel).
Appliquer cette règle uniquement quand le patch touche la logique CTA / navigation.

BUG IDENTIFIÉ

- Snapshot : {snapshot_dir}
- Étape en échec : {step} (extraction | application | clic_cta | manual)
- Fichier source : Survey/dom_analyzer.py
- question_blocks actuel (produit) :
{blocks_json}
- question_blocks attendu (validé) :
{expected_json}
- DOM de référence : {snapshot_dir}/dom_outer.html

RÈGLES STRICTES

Ne propose jamais plusieurs stratégies : une seule stratégie claire.
1 bug → 1 correction principale (pas d'empilement de fallbacks).
DOM-first : pas de fallback Vision.
Pas de retry infini : si tu ajoutes une boucle, ajoute un budget (max N) et un abandon contrôlé avec logs.
Compat Local + Prod/Docker : pas de input() en prod, pas de chemins locaux.
Patch minimal : pas de refactor gratuit.
Respecte la séparation des responsabilités des modules telle que décrite dans PROJECT_ARCHITECTURE.md.

ACTION REQUISE

1. Identifie la cause racine.
2. Applique un patch minimal et robuste.
3. Vérifie la non-régression sur les DOMs de référence pertinents.
4. Si le patch touche un CTA, applique la règle CTA_INTERCEPT_ONLY.
"""

    patch_path = snapshot_path / "patch_request.md"
    patch_path.write_text(content, encoding="utf-8")
    print(f"[FAILURE_PIPELINE] patch_request.md écrit → {patch_path}")
    return str(patch_path)


def _run_patch_with_llm(patch_request_path: str):
    """
    Lance le LLM de patch selon PATCH_LLM :
      "claude" → claude --print --file <patch_request.md>
      "codex"  → codex  (contenu passé en prompt)
    """
    if PATCH_LLM == "codex":
        try:
            content = Path(patch_request_path).read_text(encoding="utf-8")
            result = subprocess.run(
                ["codex", "--approval-mode", "full-auto", content],
                check=False,
            )
            if result.returncode != 0:
                print(f"[FAILURE_PIPELINE] codex s'est terminé avec le code {result.returncode}")
        except FileNotFoundError:
            print("[FAILURE_PIPELINE] Commande 'codex' introuvable. Lancer manuellement :")
            print(f"  codex \"$(cat {patch_request_path})\"")
        except Exception as e:
            print(f"[FAILURE_PIPELINE] Erreur lancement codex : {e}")
    else:
        # Par défaut : "claude"
        try:
            subprocess.run(
                ["claude", "--print", "--file", patch_request_path],
                check=False,
            )
        except FileNotFoundError:
            print("[FAILURE_PIPELINE] Commande 'claude' introuvable. Lancer manuellement :")
            print(f"  claude --print --file {patch_request_path}")
        except Exception as e:
            print(f"[FAILURE_PIPELINE] Erreur lancement claude : {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Étape 4 — Validation
# ─────────────────────────────────────────────────────────────────────────────

def _validate_step(snapshot_dir: str):
    """Lance replay_snapshot.py et compare produced vs expected."""
    snapshot_path = Path(snapshot_dir)
    tools_dir = Path(__file__).parent
    replay_path = tools_dir / "replay_snapshot.py"

    if not replay_path.exists():
        print("[FAILURE_PIPELINE] replay_snapshot.py introuvable → validation ignorée.")
        return

    try:
        result = subprocess.run(
            [sys.executable, str(replay_path), snapshot_dir, "--use-dom-outer"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.stdout:
            print(result.stdout)
        if result.stderr:
            print(result.stderr)
    except subprocess.TimeoutExpired:
        print("[FAILURE_PIPELINE] replay_snapshot.py timeout → validation ignorée.")
        return
    except Exception as e:
        print(f"[FAILURE_PIPELINE] replay_snapshot.py erreur : {e} → validation ignorée.")
        return

    out_path = snapshot_path / "dom_analyzer.out.json"
    expected_path = snapshot_path / "question_blocks_expected.json"

    if not out_path.exists():
        print("[FAILURE_PIPELINE] dom_analyzer.out.json absent → SKIP validation")
        return
    if not expected_path.exists():
        print("[FAILURE_PIPELINE] question_blocks_expected.json absent → SKIP validation")
        return

    try:
        produced = json.loads(out_path.read_text(encoding="utf-8"))
        expected = json.loads(expected_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[FAILURE_PIPELINE] Erreur lecture JSON validation : {e}")
        return

    if produced == expected:
        print("[FAILURE_PIPELINE] ✅ PASS — question_blocks produit == attendu")
    else:
        diff = difflib.unified_diff(
            json.dumps(expected, indent=2, ensure_ascii=False).splitlines(),
            json.dumps(produced, indent=2, ensure_ascii=False).splitlines(),
            fromfile="expected",
            tofile="produced",
            lineterm="",
        )
        print("[FAILURE_PIPELINE] ❌ FAIL — diff :")
        print("\n".join(diff))


# ─────────────────────────────────────────────────────────────────────────────
# Orchestration principale
# ─────────────────────────────────────────────────────────────────────────────

def run_pipeline(snapshot_dir: str, step: str):
    """
    Pipeline complet avec interaction humaine (mode CLI ou local attach).
    """
    print(f"\n[FAILURE_PIPELINE] === Démarrage : snapshot={snapshot_dir} step={step} | patch_llm={PATCH_LLM} ===")

    # Étape 1
    try:
        dom_html, question_blocks = _read_snapshot(snapshot_dir)
        print(f"[FAILURE_PIPELINE] DOM : {len(dom_html)} chars | question_blocks : {len(question_blocks)} blocs")
    except Exception as e:
        print(f"[FAILURE_PIPELINE] Erreur lecture snapshot : {e}")
        return

    # Étape 2 — OpenAI
    print("[FAILURE_PIPELINE] Génération du expected via OpenAI (gpt-4o)...")
    try:
        expected_blocks = _generate_expected_via_openai(dom_html, question_blocks)
    except Exception as e:
        print(f"[FAILURE_PIPELINE] Erreur OpenAI : {e}")
        return

    if expected_blocks is None:
        print("[FAILURE_PIPELINE] Impossible de générer l'expected. Abandon.")
        return

    print("\n[FAILURE_PIPELINE] === question_blocks CORRIGÉ (proposition OpenAI) ===")
    print(json.dumps(expected_blocks, ensure_ascii=False, indent=2))

    confirm = input("\nValider ce expected ? [o/n] ").strip().lower()
    if confirm not in ("o", "oui", "y", "yes"):
        print("[FAILURE_PIPELINE] Validation refusée. Abandon.")
        return

    expected_path = Path(snapshot_dir) / "question_blocks_expected.json"
    expected_path.write_text(json.dumps(expected_blocks, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[FAILURE_PIPELINE] question_blocks_expected.json écrit → {expected_path}")

    # Étape 3 — patch via PATCH_LLM
    patch_path = _write_patch_request(snapshot_dir, step, question_blocks, expected_blocks)
    print(f"[FAILURE_PIPELINE] Lancement du patch via '{PATCH_LLM}'...")
    _run_patch_with_llm(patch_path)

    # Étape 4 — validation
    print("[FAILURE_PIPELINE] Validation du patch...")
    _validate_step(snapshot_dir)


def trigger_pipeline(snapshot_dir: str, step: str):
    """
    Point d'entrée non-bloquant appelable depuis le bot.
    - Actif UNIQUEMENT en mode attach (is_attach_mode() == True).
    - Ne fait jamais planter le bot en cas d'erreur interne.
    """
    try:
        if not _is_attach_mode():
            return
        run_pipeline(snapshot_dir, step)
    except Exception as e:
        print(f"[FAILURE_PIPELINE] Erreur non-bloquante ({step}) : {e}")


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SurveyBot failure pipeline d'auto-correction")
    parser.add_argument("snapshot_dir", help="Chemin vers le répertoire snapshot")
    parser.add_argument(
        "--step",
        choices=["extraction", "application", "clic_cta", "manual"],
        default="extraction",
        help="Étape en échec",
    )
    args = parser.parse_args()
    # En CLI direct, on bypass la vérification is_attach_mode
    run_pipeline(args.snapshot_dir, args.step)
