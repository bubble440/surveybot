LICENSE_KEY = "Wilfried"
# Host public (fly.dev) obligatoire pour une connexion externe via le handler pg_tls de Fly —
# une connexion par IP brute échoue (le proxy route/termine le TLS sur la base du nom d'hôte
# demandé, pas de l'IP, même avec une IP dédiée). Mot de passe percent-encodé (%40 = @, %21 = !)
# car les caractères spéciaux non encodés dans le mot de passe cassent le parsing de l'URI de
# connexion (un deuxième '@' non encodé serait interprété comme le séparateur user:password/host).
DATABASE_URL = "postgres://surveybot_client:p%40ssw0rD%21123@surveybot-db.fly.dev:5432/postgres?sslmode=require"
BOT_VERSION = "1.7.0"   # ← incrémenter à chaque release