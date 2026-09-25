from Survey.replay_browser import IsolatedReplayBrowser

CASE = r"failure_cases\20260925_211543_action_validation_failure"

def read_state(page):
    return page.evaluate("""
        () => {
            const input = document.getElementById('ans1151.0.0');
            const label = document.querySelector('div[for="0"]');
            const iconContainer = label ? label.previousElementSibling : null;
            const svg = iconContainer ? iconContainer.querySelector('svg') : null;
            return {
                native_checked: input ? input.checked : null,
                svg_opacity: svg ? getComputedStyle(svg).opacity : null,
            };
        }
    """)

with IsolatedReplayBrowser(headless=False) as rb:
    # Test A : reproduit exactement ce que fait le dispatcher actuel — clic
    # sur le <label> natif, caché dans le tableau qarts.
    page_a = rb.load_case_document(CASE)
    page_a.wait_for_timeout(500)
    print("A) avant                          :", read_state(page_a))
    label = page_a.query_selector("label[for='ans1151.0.0']")
    try:
        label.click(timeout=1000)
        print("A) clic natif Playwright RÉUSSI (élément jugé actionnable)")
    except Exception as exc:
        print(f"A) clic natif Playwright ÉCHOUÉ ({type(exc).__name__}) -> repli JS .click()")
        page_a.evaluate("(el) => el.click()", label)
    page_a.wait_for_timeout(300)
    print("A) après clic sur le label caché   :", read_state(page_a))

    # Test B : clic sur ce qu'un vrai utilisateur clique réellement — le
    # texte visible de l'option, scopé au widget "rp" visible (évite les
    # 3 matches ambigus : span visible, label caché, th de légende caché).
    page_b = rb.load_case_document(CASE)
    page_b.on("console", lambda m: print("  [console B]", m.type, m.text))
    page_b.on("pageerror", lambda e: print("  [pageerror B]", e))
    page_b.wait_for_timeout(500)
    print("B) avant                          :", read_state(page_b))

    input("Clique toi-même sur 'Oui' dans l'onglet B (celui de droite), puis Entrée...")
    print("B) après TON clic manuel           :", read_state(page_b))

    before_blocked = list(rb.blocked_requests)
    page_b.locator('[data-test="main-contain"] div[tabindex="0"]').nth(0).click()
    page_b.wait_for_timeout(1500)
    print("B) après clic scripté (en plus)     :", read_state(page_b))
    new_blocked = [r for r in rb.blocked_requests if r not in before_blocked]
    print("B) requêtes bloquées déclenchées PAR le clic scripté :", new_blocked)

    print("B) React a-t-il pris le conteneur ? :", page_b.evaluate("""
        () => {
            const container = document.querySelector('[data-test="main-contain"]');
            if (!container) return 'conteneur introuvable';
            const keys = Object.keys(container).filter(k =>
                k.startsWith('__reactFiber') || k.startsWith('__reactProps') || k.startsWith('__reactEvents')
            );
            return keys.length ? `oui (${keys[0]})` : 'non — aucune propriété React sur ce nœud';
        }
    """))

    input("Entrée pour voir le détail des requêtes bloquées...")
    print("Détail complet des requêtes bloquées (chargement des 2 pages compris) :")
    for r in rb.blocked_requests:
        print(" -", r)

    input("Entrée pour fermer...")