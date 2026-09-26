from Survey.replay_browser import IsolatedReplayBrowser, extract_case_blocks, execute_case_action

CASE = r"failure_cases\20260925_211543_action_validation_failure"

with IsolatedReplayBrowser(headless=False) as rb:
    page = rb.load_case_document(CASE, pre_action=True)
    extraction = extract_case_blocks(page, CASE)
    print("extraction :", extraction.error, len(extraction.blocks), "bloc(s)")

    result = execute_case_action(page, CASE)
    print("statut             :", result.status)
    print("dispatcher_success  :", result.dispatcher_success)
    print("durée / budget      :", result.duration_s, "/", result.budget_s)
    print("raison              :", result.reason)

    input("Regarde l'onglet, puis Entrée pour fermer...")