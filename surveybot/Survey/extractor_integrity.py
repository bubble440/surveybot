"""
extractor_integrity.py - Verification d'integrite des fonctions "figees" (BEM)

But
---
Complement deterministe (hash) a la regle "zero modification d'extracteur
existant" du template de prompt Claude Code. Le respect de cette regle par
un LLM reste probabiliste : ce script detecte, de facon fiable, si le corps
d'une fonction listee comme protegee a change depuis son dernier
enregistrement valide.

Independant du build : peut etre lance a tout moment pendant le dev local
(apres un fix, avant un git push, etc.), pas seulement au moment de
build_release_zip.ps1.

Usage
-----
  # Enregistrer (ou re-enregistrer) le hash d'une fonction validee.
  # A faire UNIQUEMENT apres validation explicite du patch (meme discipline
  # que la mise a jour de BOT_EVOLUTION_MEMORY.md : jamais a titre preventif).
  python extractor_integrity.py record input_slider.py set_sliderpoints

  # Enregistrer plusieurs fonctions d'un coup (ex: seed initial du registre a
  # partir d'une liste extraite du BEM existant, relue/validee au prealable).
  # Fichier texte : une ligne "fichier.py::nom_fonction" par entree.
  # Toute ligne dont la fonction est introuvable est signalee et ignoree —
  # aucun hash bidon n'est jamais ecrit dans le registre.
  python extractor_integrity.py record-batch candidates.txt

  # Verifier toutes les fonctions enregistrees.
  python extractor_integrity.py check

  # Verifier seulement un sous-ensemble (utile pendant un fix cible sur un fichier).
  python extractor_integrity.py check --file input_slider.py

  # Lister le registre actuel.
  python extractor_integrity.py list

Le registre est stocke dans extractor_integrity.json a la racine du projet
(--root pour pointer ailleurs). A committer/synchroniser comme
BOT_EVOLUTION_MEMORY.md : c'est l'etat de reference partage.

Code de sortie
--------------
0 si tout est conforme (ou registre vide), 1 si au moins une fonction
enregistree a change ou est introuvable. Utilisable dans un hook git ou
un pipeline CI plus tard si besoin, sans rien changer au script.
"""

import argparse
import ast
import hashlib
import json
import sys
from pathlib import Path

REGISTRY_FILENAME = "extractor_integrity.json"


def _load_registry(root: Path) -> dict:
    """Charge le registre JSON existant, ou {} si absent (premier run)."""
    path = root / REGISTRY_FILENAME
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_registry(root: Path, registry: dict) -> None:
    """Ecrit le registre, trie par cle pour un diff git lisible et stable."""
    path = root / REGISTRY_FILENAME
    ordered = dict(sorted(registry.items()))
    path.write_text(json.dumps(ordered, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _find_function_source(source: str, function_name: str):
    """
    Localise la premiere fonction/methode nommee `function_name` dans le
    fichier (top-level ou methode de classe) et retourne son segment source
    EXACT, decorateurs inclus (un decorateur change = comportement
    potentiellement change, donc il doit faire partie du hash).

    Retourne None si la fonction est introuvable (supprimee, renommee,
    erreur de frappe dans le registre...).
    """
    tree = ast.parse(source)
    lines = source.splitlines()

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name:
            start_line = node.decorator_list[0].lineno if node.decorator_list else node.lineno
            end_line = node.end_lineno
            return "\n".join(lines[start_line - 1:end_line])

    return None


def _hash_function(root: Path, file_name: str, function_name: str) -> str:
    """Calcule le SHA256 du segment source d'une fonction donnee."""
    file_path = root / file_name
    if not file_path.exists():
        raise FileNotFoundError(f"{file_name} introuvable dans {root}")

    # utf-8-sig : absorbe le BOM (U+FEFF) si present en tete de fichier
    # (frequent sur des .py sauvegardes sous Windows), sans effet sinon.
    source = file_path.read_text(encoding="utf-8-sig")
    segment = _find_function_source(source, function_name)
    if segment is None:
        raise LookupError(f"Fonction '{function_name}' introuvable dans {file_name}")

    return hashlib.sha256(segment.encode("utf-8")).hexdigest()


def cmd_record(args, root: Path) -> int:
    """Enregistre/actualise le hash de reference d'une fonction validee."""
    key = f"{args.file}::{args.function}"
    try:
        current_hash = _hash_function(root, args.file, args.function)
    except (FileNotFoundError, LookupError) as e:
        print(f"ERREUR : {e}")
        return 1

    registry = _load_registry(root)
    previous_hash = registry.get(key, {}).get("hash")
    registry[key] = {"hash": current_hash}
    _save_registry(root, registry)

    if previous_hash and previous_hash != current_hash:
        print(f"MIS A JOUR : {key} (hash different de l'enregistrement precedent)")
    else:
        print(f"ENREGISTRE : {key}")
    return 0


def cmd_record_batch(args, root: Path) -> int:
    """
    Enregistre plusieurs fonctions d'un coup a partir d'un fichier texte
    (une ligne "fichier.py::nom_fonction" par entree, lignes vides et
    commentaires '#' ignores).

    Chaque ligne est traitee independamment : une fonction introuvable ou un
    fichier absent ne bloque pas les autres lignes, elle est juste reportee
    en erreur en fin d'execution. Aucun hash n'est jamais enregistre pour une
    ligne en erreur -> impossible de polluer le registre avec un faux positif
    issu d'une liste candidate mal extraite (ex: parsing automatique du BEM).
    """
    list_path = Path(args.list_file)
    if not list_path.exists():
        print(f"ERREUR : fichier de liste introuvable : {list_path}")
        return 1

    registry = _load_registry(root)
    recorded, updated, failed = [], [], []

    for raw_line in list_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "::" not in line:
            failed.append((line, "format invalide (attendu 'fichier.py::fonction')"))
            continue

        file_name, function_name = line.split("::", 1)
        file_name, function_name = file_name.strip(), function_name.strip()
        key = f"{file_name}::{function_name}"

        try:
            current_hash = _hash_function(root, file_name, function_name)
        except (FileNotFoundError, LookupError) as e:
            failed.append((key, str(e)))
            continue

        previous_hash = registry.get(key, {}).get("hash")
        registry[key] = {"hash": current_hash}
        (updated if previous_hash and previous_hash != current_hash else recorded).append(key)

    _save_registry(root, registry)

    print(f"=== record-batch termine ===")
    print(f"  Nouvellement enregistrees : {len(recorded)}")
    print(f"  Mises a jour (hash change) : {len(updated)}")
    print(f"  Echecs (non enregistrees)  : {len(failed)}")

    if failed:
        print("\nA CORRIGER DANS LA LISTE (fonction/fichier non trouve tel quel) :")
        for key, reason in failed:
            print(f"  ? {key} -- {reason}")

    return 1 if failed else 0


def cmd_check(args, root: Path) -> int:
    """Compare l'etat actuel des fonctions enregistrees au registre de reference."""
    registry = _load_registry(root)
    if not registry:
        print("Registre vide - rien a verifier (utiliser 'record' d'abord).")
        return 0

    if args.file:
        registry = {k: v for k, v in registry.items() if k.startswith(f"{args.file}::")}
        if not registry:
            print(f"Aucune entree enregistree pour {args.file}.")
            return 0

    mismatches = []
    errors = []

    for key, entry in registry.items():
        file_name, function_name = key.split("::", 1)
        try:
            current_hash = _hash_function(root, file_name, function_name)
        except FileNotFoundError:
            errors.append((key, "fichier introuvable"))
            continue
        except LookupError:
            errors.append((key, "fonction introuvable (renommee/supprimee ?)"))
            continue

        if current_hash != entry["hash"]:
            mismatches.append(key)

    total = len(registry)
    ok_count = total - len(mismatches) - len(errors)
    print(f"=== Verification integrite : {ok_count}/{total} OK ===")

    if mismatches:
        print("\nFONCTIONS MODIFIEES (hash different du registre) :")
        for key in mismatches:
            print(f"  ! {key}")

    if errors:
        print("\nENTREES EN ERREUR :")
        for key, reason in errors:
            print(f"  ? {key} -- {reason}")

    if mismatches or errors:
        print(
            "\nSi ces changements sont voulus et valides, re-enregistrer avec :"
            "\n  python extractor_integrity.py record <fichier> <fonction>"
        )
        return 1

    return 0


def cmd_list(args, root: Path) -> int:
    """Affiche toutes les fonctions actuellement enregistrees dans le registre."""
    registry = _load_registry(root)
    if not registry:
        print("Registre vide.")
        return 0
    for key in sorted(registry):
        print(key)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verification d'integrite des extracteurs/fonctions proteges (BEM)."
    )
    parser.add_argument("--root", default=".", help="Racine du projet (defaut: dossier courant)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_record = sub.add_parser("record", help="Enregistrer/mettre a jour le hash d'une fonction validee")
    p_record.add_argument("file", help="Nom du fichier (ex: input_slider.py)")
    p_record.add_argument("function", help="Nom de la fonction (ex: set_sliderpoints)")
    p_record.set_defaults(func=cmd_record)

    p_batch = sub.add_parser("record-batch", help="Enregistrer plusieurs fonctions depuis un fichier liste")
    p_batch.add_argument("list_file", help="Fichier texte : une ligne 'fichier.py::fonction' par entree")
    p_batch.set_defaults(func=cmd_record_batch)

    p_check = sub.add_parser("check", help="Verifier toutes les fonctions enregistrees")
    p_check.add_argument("--file", help="Limiter la verification a un seul fichier")
    p_check.set_defaults(func=cmd_check)

    p_list = sub.add_parser("list", help="Lister les fonctions enregistrees")
    p_list.set_defaults(func=cmd_list)

    args = parser.parse_args()
    root = Path(args.root).resolve()
    return args.func(args, root)


if __name__ == "__main__":
    sys.exit(main())