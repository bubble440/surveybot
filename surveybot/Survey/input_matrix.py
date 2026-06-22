"""
input_matrix.py - Gestion des questions matricielles pour input_handler

Ce module contient:
- Détection de matrices (tables, grilles Qualtrics/Dynata/SSI)
- Itération sur les lignes de matrices
- Clic sur cellules par row/col
- Application d'une colonne à toutes les lignes

Dépendances:
- input_utils pour les fonctions utilitaires
"""

def _pw_page(d):
    """Extrait la Page Playwright native depuis un PlaywrightDriverShim ou retourne d tel quel."""
    if hasattr(d, "_page"):
        return d._page
    return d


def _handle(el):
    """Extrait le ElementHandle natif depuis un PlaywrightElementShim (_h) ou retourne el."""
    if hasattr(el, "_h"):
        return el._h
    return el



import unicodedata
import re
import time

# =============================================================================
# CONSTANTES MATRIX
# =============================================================================

MATRIX_COL_SYNONYMS = {
    # FR
    "oui": "oui",
    "non": "non",
    "d'accord": "daccord",
    "pas d'accord": "pas daccord",
    "plutôt d'accord": "plutot daccord",
    "tout à fait d'accord": "tout a fait daccord",
    "tout a fait daccord": "tout a fait daccord",
    "pas du tout d'accord": "pas du tout daccord",
    "ni d'accord ni pas d'accord": "ni daccord ni pas daccord",
    # EN
    "agree": "agree",
    "disagree": "disagree",
    "strongly agree": "strongly agree",
    "strongly disagree": "strongly disagree",
    "neither": "neither",
    "neutral": "neutral",
    "yes": "yes",
    "no": "no",
}


# =============================================================================
# HELPERS MATRIX
# =============================================================================

def _norm(s: str) -> str:
    """Normalisation pour comparaison de textes."""
    s = unicodedata.normalize("NFKC", s or "").replace("\u00A0", " ")
    s = s.lower()
    s = re.sub(r"[»«""\"''›→·•:…]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def looks_like_matrix(driver) -> bool:
    """
    Heuristique simple : présence d'un tableau <table> avec input[radio|checkbox] par cellule
    ou de lignes .q-matrix/.Matrix (Qualtrics).
    """
    # Tables HTML
    tables = driver.find_elements(
        "xpath", "//table[.//input[@type='radio' or @type='checkbox'] or .//select]"
    )
    if tables:
        return True
    label_tables = driver.find_elements(
        "xpath", "//table[.//thead//th and .//tbody//label[@for]]"
    )
    if label_tables:
        return True
    # Matrices Qualtrics/dynata styles (div grids)
    grids = driver.find_elements(
        "css selector", ".q-matrix, .Matrix, .grid, .question-matrix, .matrix"
    )
    for g in grids:
        try:
            if g.find_elements(
                "css selector",
                "input[type='radio'], input[type='checkbox'], select, [role='radio'], [role='checkbox']",
            ):
                return True
        except:
            continue
    return False


def iter_matrix_rows(driver):
    """
    Retourne une liste de tuples (row_element, row_label_text).
    Compatible <tr> et structures <div>.
    """
    rows = []
    # try <tr>
    for tr in driver.find_elements("xpath", "//table//tr"):
        try:
            # label à gauche, souvent dans th/td[1]
            lbl_el = None
            for css in ["th", "td[1]", "td[1]//label", "td[1]//div"]:
                try:
                    lbl_el = tr.find_element("xpath", f".//{css}")
                    if lbl_el.inner_text().strip():
                        break
                except:
                    continue
            try:
                lbl = (lbl_el.inner_text() if lbl_el else "").strip()
            except Exception:
                lbl = ""
            # ignorer header row
            if tr.find_elements(
                "xpath", ".//input[@type='radio' or @type='checkbox'] | .//select"
            ):
                rows.append((tr, lbl))
        except:
            continue

    if rows:
        return rows

    # fallback div-based
    grids = driver.find_elements(
        "css selector", ".q-matrix, .Matrix, .grid, .question-matrix, .matrix"
    )
    for g in grids:
        try:
            candidates = g.find_elements(
                "xpath",
                ".//*[self::div or self::li][.//input[@type='radio' or @type='checkbox'] or .//select]",
            )
            for row in candidates:
                lbl = ""
                try:
                    lbl = row.find_element(
                        "xpath",
                        ".//label | .//*[self::div or self::span][normalize-space(.)!='']",
                    ).inner_text().strip()
                except:
                    pass
                rows.append((row, lbl))
        except:
            continue
    return rows


def get_matrix_columns(driver):
    """
    Retourne la liste des libellés d'en-tête de colonnes (normalisés) pour matching.
    """
    headers = []
    # head <th>
    for th in driver.find_elements("xpath", "//table//th[normalize-space(.)!='']"):
        try:
            headers.append(_norm(th.inner_text()))
        except:
            continue
    if headers:
        return headers
    # fallback: première ligne header simulée
    try:
        first_row = driver.find_element("xpath", "(//table//tr)[1]")
        for td in first_row.find_elements("xpath", ".//td[normalize-space(.)!='']"):
            try:
                headers.append(_norm(td.inner_text()))
            except Exception:
                pass
    except:
        pass
    # div-based header
    for h in driver.find_elements(
        "css selector", ".matrix thead, .q-matrix thead, .Matrix thead"
    ):
        for th in h.find_elements("xpath", ".//*[normalize-space(.)!='']"):
            try:
                headers.append(_norm(th.inner_text()))
            except Exception:
                pass
    return headers


def select_cell_action(cell, preferred_col_norm):
    """
    Dans une cellule (row x col), déclenche l'action selon le type :
    - radio : coche si pas déjà sélectionné
    - checkbox : coche si non cochée (idempotent)
    - select : ouvre et choisit l'option correspondant à la colonne
    """
    # radio
    try:
        r = cell.find_element("css selector", "input[type='radio'], [role='radio']")
        if hasattr(r, "is_selected") and r.is_selected():
            return True
        try:
            r.click()
            return True
        except:
            try:
                _handle(r).hover(); _handle(r).click()
                return True
            except:
                try:
                    _handle(r).click()
                    return True
                except:
                    pass
    except:
        pass

    # checkbox
    try:
        cb = cell.find_element(
            "css selector", "input[type='checkbox'], [role='checkbox']"
        )
        try:
            checked = False
            try:
                checked = cb.is_selected()
            except:
                aria = (cb.get_attribute("aria-checked") or "").lower()
                checked = aria == "true" or aria == "mixed"
            if checked:
                return True
            cb.click()
            return True
        except:
            try:
                _handle(cb).hover(); _handle(cb).click()
                return True
            except:
                try:
                    _handle(cb).click()
                    return True
                except:
                    pass
    except:
        pass

    # select
    try:
        sel = cell.find_element("tag name", "select")
        _sel_el_mx = _handle(sel)
        options = _sel_el_mx.evaluate(
            "el => [...el.options].map(o => ({text: o.text, value: o.value}))"
        )
        for opt in options:
            if _norm(opt["text"]) == preferred_col_norm or preferred_col_norm in _norm(opt["text"]):
                _sel_el_mx.select_option(label=opt["text"])
                return True
        for opt in options:
            if _norm(opt["value"] or "") == preferred_col_norm:
                _sel_el_mx.select_option(value=opt["value"])
                return True
    except:
        pass

    return False


# =============================================================================
# FONCTION PRINCIPALE CLICK_MATRIX_CELL_BY_ROW_AND_COL
# =============================================================================

def click_matrix_cell_by_row_and_col(driver, row_label: str, col_label: str) -> bool:
    """
    Cible une cellule de matrice en croisant la ligne (row_label) et la colonne (col_label).
    Compatible <table> et grilles div-based.
    
    Args:
        driver: WebDriver
        row_label: texte identifiant la ligne
        col_label: texte identifiant la colonne
    
    Returns:
        True si cellule cliquée avec succès
    """
    print("lancement de click_matrix_cell_by_row_and_col")
    if not looks_like_matrix(driver):
        return False
    rneedle = _norm(row_label)
    cneedle = _norm(col_label)

    def _resolve_click_target(cell, target):
        """
        Si l'input est masqué via classe `fir-hidden` (pattern Decipher/FocusVision),
        cliquer son wrapper interactif au lieu de l'input.
        """
        try:
            classes = _norm(target.get_attribute("class") or "")
        except Exception:
            classes = ""
        if "fir-hidden" not in classes:
            return target

        for css in ["td.clickableCell", ".clickableCell", "span.fir-icon"]:
            try:
                return cell.find_element("css selector", css)
            except Exception:
                continue
        return target

    # 1) Tenter les <table> classiques
    try:
        # a) récupérer index de colonne
        headers = get_matrix_columns(driver)
        col_idx = None
        for i, h in enumerate(headers):
            if cneedle == h or cneedle in h or h in cneedle:
                col_idx = i
                break
        
        # b) trouver la ligne par son label
        for tr in driver.find_elements("xpath", "//table//tr"):
            try:
                lbl = ""
                tds = tr.find_elements("xpath", "./td")
                if tds:
                    for td in tds[:3]:
                        try:
                            has_input = bool(td.find_elements(
                                "xpath",
                                ".//input[@type='radio' or @type='checkbox'] | .//*[@role='radio' or @role='checkbox']"
                            ))
                            try:
                                raw = td.inner_text().strip()
                            except Exception:
                                raw = (td.get_attribute("innerText") or "").strip()
                            if raw and not has_input:
                                lbl = _norm(raw)
                                break
                        except Exception:
                            continue
                
                if not lbl:
                    for xp in ["./th", "./td[1]", "./td[2]", "./td[1]//label", "./td[2]//label", "./td[1]//div", "./td[2]//div"]:
                        try:
                            t = tr.find_element("xpath", xp).inner_text().strip()
                            if t:
                                lbl = _norm(t)
                                break
                        except Exception:
                            continue
                
                if not lbl:
                    continue
                
                if not (rneedle == lbl or rneedle in lbl or lbl in rneedle):
                    continue

                tds = tr.find_elements("xpath", "./td")

                def _is_most(s: str) -> bool:
                    s = _norm(s).lower()
                    return any(k in s for k in ["plus", "most", "best", "max", "right", "droite"])

                def _is_least(s: str) -> bool:
                    s = _norm(s).lower()
                    return any(k in s for k in ["moins", "least", "worst", "min", "left", "gauche"])

                want_most = _is_most(col_label)
                want_least = _is_least(col_label)

                cand_cells = []
                if col_idx is not None and len(tds) > 0:
                    if len(tds) > col_idx:
                        cand_cells.append(tds[col_idx])
                    if len(tds) > col_idx + 1:
                        cand_cells.append(tds[col_idx + 1])
                if not cand_cells:
                    cand_cells = tds

                best_cell, best_score = None, -1e9
                for idx, cell in enumerate(cand_cells):
                    try:
                        has_input = False
                        inp = None
                        for xp in [".//input[@type='radio']", ".//input[@type='checkbox']", ".//*[@role='radio']", ".//*[@role='checkbox']"]:
                            try:
                                inp = cell.find_element("xpath", xp)
                                has_input = True
                                break
                            except Exception:
                                continue
                        if not has_input:
                            try:
                                inp = cell.find_element("xpath", ".//label[@for]")
                            except Exception:
                                inp = None
                        if inp is None:
                            continue
                        
                        sc = 0.0
                        sig = " ".join([
                            _norm(cell.get_attribute("class") or ""),
                            _norm(inp.get_attribute("class") or ""),
                            _norm(inp.get_attribute("name") or ""),
                            _norm(inp.get_attribute("aria-label") or ""),
                        ]).lower()
                        if want_most:
                            if any(k in sig for k in ["most","best","max"]): sc += 3.0
                            sc += idx * 0.5
                        if want_least:
                            if any(k in sig for k in ["least","worst","min"]): sc += 3.0
                            sc += (len(cand_cells) - idx - 1) * 0.5

                        if not want_most and not want_least:
                            sc += idx * 0.25

                        if sc > best_score:
                            best_cell, best_score = cell, sc
                    except Exception:
                        continue
                    
                if best_cell is not None:
                    try:
                        tgt = None
                        for xp in [".//input[@type='radio']", ".//input[@type='checkbox']", ".//*[@role='radio']", ".//*[@role='checkbox']", ".//label[@for]"]:
                            try:
                                tgt = best_cell.find_element("xpath", xp)
                                break
                            except Exception:
                                continue
                        if tgt is not None:
                            click_target = _resolve_click_target(best_cell, tgt)
                            _pw_page(driver).evaluate("(el) => el.scrollIntoView({block:\'center\'})", _handle(click_target))
                            time.sleep(0.05)
                            try:
                                click_target.click()
                            except Exception:
                                _handle(click_target).hover(); _handle(click_target).click()
                            try:
                                setattr(driver, "last_action_success", True)
                                setattr(driver, "_post_action_t0", time.time())
                            except Exception:
                                pass
                            side = "most/plus" if want_most else ("least/moins" if want_least else "unknown")
                            print(f"✓ Matrice: '{row_label}' → '{col_label}' (table, {side}). source: input_matrix.py")
                            return True
                    except Exception:
                        pass
                    
            except Exception:
                continue
    except Exception:
        pass

    # 2) Grilles "div-based" (Qualtrics/Dynata/SSI…)
    try:
        grids = driver.find_elements("css selector", ".q-matrix, .Matrix, .grid, .question-matrix, .matrix")
        for g in grids:
            try:
                rows = g.find_elements("xpath", ".//*[self::div or self::li][.//input[@type='radio' or @type='checkbox'] or .//select]")
                row = None
                for r in rows:
                    txt = ""
                    try:
                        txt = _norm(r.inner_text()).strip()
                    except Exception:
                        txt = ""
                    if txt and (rneedle == txt or rneedle in txt or txt in rneedle):
                        row = r
                        break
                if row is None:
                    continue

                cells = row.find_elements("xpath", ".//div|.//li|.//span|.//td")
                best = None
                best_score = -1
                for cell in cells:
                    try:
                        try:
                            sig = _norm(cell.inner_text())
                        except Exception:
                            sig = _norm(cell.get_attribute("innerText") or "")
                        sc = 1.0 if (cneedle and (cneedle == sig or cneedle in sig or sig in cneedle)) else 0.0
                        if sc > best_score and cell.find_elements("xpath", ".//input[@type='radio' or @type='checkbox'] | .//*[@role='radio' or @role='checkbox']"):
                            best = cell
                            best_score = sc
                    except Exception:
                        continue
                if best is None:
                    continue

                try:
                    tgt = None
                    for xp in [".//input[@type='radio']", ".//input[@type='checkbox']", ".//*[@role='radio']", ".//*[@role='checkbox']"]:
                        try:
                            tgt = best.find_element("xpath", xp)
                            break
                        except Exception:
                            continue
                    if not tgt:
                        continue
                    click_target = _resolve_click_target(best, tgt)
                    _pw_page(driver).evaluate("(el) => el.scrollIntoView({block:\'center\'})", _handle(click_target))
                    time.sleep(0.05)
                    try:
                        click_target.click()
                    except Exception:
                        _handle(click_target).hover(); _handle(click_target).click()
                    try:
                        setattr(driver, "last_action_success", True)
                        setattr(driver, "_post_action_t0", time.time())
                    except Exception:
                        pass
                    print(f"✓ Matrice: '{row_label}' → '{col_label}' (grid). source: input_matrix.py")
                    return True
                except Exception:
                    continue
            except Exception:
                continue
    except Exception:
        pass
    
    print("rien n'a fonctionné")
    return False


# =============================================================================
# APPLY_MATRIX_COLUMN_TO_ALL_ROWS
# =============================================================================

def apply_matrix_column_to_all_rows(driver, column_label: str) -> bool:
    """
    Si l'IA renvoie uniquement un EN-TÊTE DE COLONNE (ex: 'Oui', 'Agree', '5'),
    alors on coche/sélectionne cette colonne pour TOUTES LES LIGNES NON RÉPONDUES.
    
    Args:
        driver: WebDriver
        column_label: texte de la colonne à appliquer
    
    Returns:
        True si au moins une ligne a été remplie
    """
    if not looks_like_matrix(driver):
        return False

    target = _norm(column_label)
    target = MATRIX_COL_SYNONYMS.get(target, target)

    rows = iter_matrix_rows(driver)
    if not rows:
        return False

    headers = get_matrix_columns(driver)
    col_idx = None
    if headers:
        for i, h in enumerate(headers):
            h_norm = MATRIX_COL_SYNONYMS.get(h, h)
            if target == h_norm or target in h_norm or h_norm in target:
                col_idx = i
                break

    success_any = False
    for row_el, _lbl in rows:
        try:
            try:
                _row_tag = row_el.evaluate("el => el.tagName.toLowerCase()")
            except Exception:
                _row_tag = ""
            if col_idx is not None and _row_tag == "tr":
                tds = row_el.find_elements("xpath", ".//td")
                cell_candidates = []
                if len(tds) > col_idx:
                    cell_candidates.append(tds[col_idx])
                if len(tds) > col_idx + 1:
                    cell_candidates.append(tds[col_idx + 1])

                for cell in cell_candidates:
                    if select_cell_action(cell, target):
                        success_any = True
                        break
                if success_any:
                    continue

            # fallback div-based
            try:
                label_cell = row_el.find_element(
                    "xpath",
                    ".//*[contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'), '{}')]".format(
                        target
                    ),
                )
                parent = label_cell.find_element(
                    "xpath", "ancestor::*[self::td or self::div or self::li][1]"
                )
                if select_cell_action(parent, target):
                    success_any = True
                    continue
            except:
                pass

            try:
                first_cell = row_el.find_element(
                    "xpath",
                    ".//td[.//input[@type='radio' or @type='checkbox'] or .//select] | .//*[.//input[@type='radio' or @type='checkbox'] or .//select]",
                )
                if select_cell_action(first_cell, target):
                    success_any = True
                    continue
            except:
                pass
        except:
            continue

    return success_any
