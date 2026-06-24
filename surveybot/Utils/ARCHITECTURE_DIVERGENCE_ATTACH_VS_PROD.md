# Divergence architecturale — Mode attach vs Mode prod
# Mis à jour : 2026-06-23
# Usage : référence pour toute IA ou développeur reprenant l'investigation.
# Statut du sujet de disqualification Cint (#15 dans disqualification_reasons.txt) : RÉSOLU, hors scope ici.

================================================================================
OBJECTIF DE CE DOCUMENT
================================================================================

Le mode attach (local, Windows, debug) et le mode prod (Fly.io/ECS, Xvfb)
n'utilisent PAS le même chemin de lancement du navigateur. Cette divergence
empêche le mode attach de servir de réplique fiable de la prod pour le
diagnostic de bugs d'interaction DOM (clics, stabilité, timing Playwright).

But : faire converger les deux chemins vers une architecture commune, afin que
tout test reproduit en attach soit représentatif de ce qui se passe en prod.

Ce document ne traite PAS de fingerprinting anti-bot (voir
disqualification_reasons.txt, piste #15 — déjà résolue, cause = CDP/
--remote-debugging-port détecté par DataDome, fix = passage à Playwright
pipe mode). Le sujet ici est purement architectural / reproductibilité de test.

================================================================================
CONSTAT — DEUX FONCTIONS DE LANCEMENT DISTINCTES
================================================================================

Fichier concerné : preselection/playwright_launcher.py

Mode prod (Fly.io/ECS, Xvfb :99) :
    → launch_browser_playwright()
    → Playwright natif, "pipe mode" (pas de --remote-debugging-port)
    → Proxy passé nativement à Playwright (pas de relay local)
    → Fingerprint injecté via context.add_init_script() (couvre toutes les
      navigations futures dès le chargement du document)
    → headless = _want_headless() → True si pas de DISPLAY, sinon False
      (en prod avec Xvfb : DISPLAY=:99 existe → headless=False, mais rendu
      sur écran virtuel)
    → Si pas headless ET DISPLAY défini ET binaire non-Windows :
          --use-gl=angle --use-angle=swiftshader   (rendu logiciel forcé)

Mode attach (local, Windows, debug) :
    → launch_browser() avec ATTACH_DEBUGGER_ADDRESS défini
    → Ne lance RIEN — se contente d'un webdriver.Chrome(debuggerAddress=...)
      sur un Chrome déjà ouvert manuellement par l'utilisateur
    → Ce Chrome tourne avec GPU matériel natif (Intel UHD 617 ou équivalent),
      écran réel, compositeur Chrome standard (pas SwiftShader, pas Xvfb)
    → Aucun argument Chrome n'est appliqué par le bot (le navigateur est déjà
      lancé avec sa propre configuration avant l'attach)
    → Fingerprint patché après coup via driver.execute_script() seulement sur
      la page courante (pas via CDP addScriptToEvaluateOnNewDocument pour les
      navigations futures, contrairement au mode prod)

CONSÉQUENCE DIRECTE : un bug constaté en prod et lié au rendu (timing,
stabilité Playwright wait_for_element_state, paint/composite) peut être
INVISIBLE en attach, car l'attach ne reproduit ni le rendu logiciel
(SwiftShader), ni l'environnement Xvfb, ni le cycle de fingerprinting CDP
identique à la prod.

Exemple concret déjà observé : wait_for_element_state("stable") timeout de
façon répétée et reproductible en prod sur des checkboxes parfaitement
stables géométriquement (logs DIAG_STABILITY positifs), alors que le même
code (select_checkbox_answers, inchangé) ne présente AUCUN timeout en attach.
Hypothèse retenue : rendu logiciel SwiftShader + compositeur Xvfb produisent
des cycles paint/composite plus lents/irréguliers que sur GPU matériel natif,
faisant échouer le critère de stabilité strict de Playwright sans que la
position de l'élément ne change (donc invisible à un diagnostic géométrique
seul, et invisible en attach puisque l'attach n'a pas ce rendu logiciel).

================================================================================
TABLEAU DES DIVERGENCES PRÉCISES
================================================================================

| Aspect                          | Mode prod (launch_browser_playwright) | Mode attach (launch_browser + ATTACH_DEBUGGER_ADDRESS) |
|----------------------------------|----------------------------------------|----------------------------------------------------------|
| Qui lance Chrome                | Le bot (Playwright launch_persistent_context) | L'utilisateur, manuellement, avant l'attach |
| Mécanisme d'attache              | Aucun — Playwright a lancé le process  | webdriver.Chrome(debuggerAddress=...) sur Chrome existant |
| Rendu graphique                  | Xvfb virtuel + SwiftShader (logiciel)  | GPU matériel natif (écran réel) |
| --use-gl / --use-angle           | Appliqué (swiftshader)                 | Absent (config native du Chrome déjà lancé) |
| Arguments Chrome (cmd line)      | Liste figée dans chrome_args           | Inconnus / dépendent de comment l'utilisateur a lancé Chrome |
| Injection fingerprint            | context.add_init_script() — couvre toutes navigations futures dès le départ | execute_script() une fois, sur la page courante seulement |
| Proxy                            | Passé nativement à Playwright (proxy=) | Dépend de la config Chrome de l'utilisateur (souvent relay local pproxy via launch_browser, non actif si Chrome lancé hors bot) |
| user_data_dir                    | Géré par le bot (tempfile ou persisté Postgres) | Profil de l'utilisateur, hors contrôle du bot |
| Process tracking (driver._chrome_proc) | Présent (subprocess.Popen)        | Absent (rien à tuer, Chrome n'a pas été lancé par le bot) |
| headless réel                    | False techniquement (Xvfb), mais rendu logiciel | False, rendu matériel réel |

================================================================================
PISTE DE CONVERGENCE PROPOSÉE
================================================================================

Objectif : permettre un mode attach qui reproduit fidèlement les conditions
de rendu prod, sans perdre la commodité d'observation manuelle en local.

Options à évaluer (à valider techniquement, non encore tranchées) :

  A) Lancer le Chrome local AVEC les mêmes arguments que launch_browser_playwright()
     (notamment --use-gl=angle --use-angle=swiftshader) avant l'attach manuel,
     pour forcer le même rendu logiciel en local malgré le GPU disponible.
     Risque : nécessite que l'utilisateur lance Chrome avec une ligne de
     commande précise plutôt qu'un raccourci standard.

  B) Créer une fonction dédiée type launch_browser_playwright_debug_xvfb()
     qui réutilise exactement chrome_args de launch_browser_playwright() (y
     compris swiftshader) mais avec headless=False et fenêtre visible, pour
     un test local qui partage l'architecture de lancement prod (Playwright
     natif, mêmes flags, même injection fingerprint via add_init_script)
     sans dépendre d'un Chrome pré-lancé manuellement.
     Note : launch_browser_playwright_debug() existe déjà mais N'APPLIQUE PAS
     --use-gl=angle --use-angle=swiftshader (elle suppose headless=False sans
     jamais vérifier la condition DISPLAY+swiftshader présente dans les deux
     autres fonctions) — c'est la fonction la plus proche d'une convergence,
     mais elle a actuellement cette omission.

  C) Si Xvfb est disponible en local (WSL2 ou VM Linux), lancer le test local
     dans les mêmes conditions Xvfb + Chrome headed que la prod, via
     launch_browser_playwright() directement, en pointant DISPLAY vers un
     Xvfb local. Reproduction la plus fidèle possible, mais demande une
     installation Linux/Xvfb locale plutôt que Windows natif.

Aucune de ces options n'a encore été implémentée ni validée à la date de ce
document. Elles sont listées comme pistes de travail pour la prochaine
itération.

================================================================================
CE QUI N'EST PAS EN CAUSE ICI
================================================================================

- La logique de select_checkbox_answers, click_next_button, et globalement
  tout le code dans response_executor.py : identique dans les deux modes,
  ce n'est pas une histoire de chemin de code applicatif différent.
- Le sujet de disqualification Cint / DataDome (#15) : résolu, cause
  identifiée (CDP/--remote-debugging-port), fix validé (Playwright pipe
  mode). Ce document ne remet pas ce point en question.
