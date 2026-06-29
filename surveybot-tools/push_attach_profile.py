# push_attach_profile.py
# Usage : $env:PYTHONPATH="."; python tools/push_attach_profile.py --account-id topsurveys_bot_001 --port 9222

import argparse, os
from chrome_profile_store import save_profile

PROFILES_ROOT = os.path.join(os.environ.get("TEMP", "/tmp"), "sb_chrome_profiles")

parser = argparse.ArgumentParser()
parser.add_argument("--account-id", required=True)
parser.add_argument("--port", type=int, required=True)
args = parser.parse_args()

src = os.path.join(PROFILES_ROOT, f"chrome_{args.port}")
print(f"[PUSH] {src} → Postgres pour account_id={args.account_id}")
save_profile(args.account_id, src)
print("[PUSH] Done.")