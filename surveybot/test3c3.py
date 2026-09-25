from Survey.replay_browser import IsolatedReplayBrowser, extract_case_blocks

CASE = r"failure_cases\20260911_160609_extraction_validation_failure"

with IsolatedReplayBrowser(headless=False) as rb:
    page = rb.load_case_document(CASE)
    result = extract_case_blocks(page, CASE)

    print("erreur d'extraction              :", result.error)
    print("blocs extraits                    :", len(result.blocks))
    for b in result.blocks:
        print("  -", b.get("target_id"), b.get("itype"), "options=", len(b.get("options") or []))
    print("comparaison vs question_blocks.json :", result.comparison)
    print("rapport validator rejoué           :", result.validation)
    print("erreur validator                  :", result.validation_error)
    print("comparaison vs validation_report.json :", result.validation_comparison)
    print("question rejouée :", repr(result.blocks[0].get("question")))

    input("Entrée pour fermer...")