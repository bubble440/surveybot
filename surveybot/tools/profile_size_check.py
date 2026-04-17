"""
profile_size_check.py — Analyse la taille du profil Chrome par dossier.
Usage : python tools/profile_size_check.py --port 9222
"""
import os, argparse

PROFILES_ROOT = os.path.join(os.environ.get("TEMP", "/tmp"), "sb_chrome_profiles")

parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, required=True)
parser.add_argument("--subdir", type=str, default=None, help="Sous-dossier à analyser (ex: Default)")
args = parser.parse_args()

src = os.path.join(PROFILES_ROOT, f"chrome_{args.port}")
if args.subdir:
    src = os.path.join(src, args.subdir)
print(f"\nProfil : {src}\n")

# Taille totale par sous-dossier de premier niveau
entries = []
for name in os.listdir(src):
    path = os.path.join(src, name)
    if os.path.isdir(path):
        total = 0
        for root, dirs, files in os.walk(path):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except Exception:
                    pass
        entries.append((total, name))
    else:
        try:
            entries.append((os.path.getsize(path), name))
        except Exception:
            entries.append((0, name))

entries.sort(reverse=True)

print(f"{'Taille':>12}  Nom")
print("-" * 40)
total_all = 0
for size, name in entries:
    if size > 0:
        print(f"{size/1024/1024:>10.2f}MB  {name}")
    total_all += size

print("-" * 40)
print(f"{total_all/1024/1024:>10.1f}MB  TOTAL")