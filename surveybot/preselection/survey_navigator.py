from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time
import re
from preselection.auth_handler import snap

def go_to_best_paid_survey(driver):
    wait_short = WebDriverWait(driver, 8)
    wait = WebDriverWait(driver, 15)

    def _click_enquetes():
        # 1) XPATH texte
        try:
            tab = wait_short.until(EC.element_to_be_clickable((By.XPATH, "//span[normalize-space()='Enquêtes']")))
            driver.execute_script("arguments[0].click();", tab)
            print("🗂️  Onglet « Enquêtes » cliqué. [xpath texte]")
            return True
        except Exception:
            pass
        # 2) data-test-id si dispo
        try:
            tab = wait_short.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "[data-test-id='ps-side-menu-surveys']")))
            driver.execute_script("arguments[0].click();", tab)
            print("🗂️  Onglet « Enquêtes » cliqué. [data-test-id]")
            return True
        except Exception:
            pass
        return False

    if not _click_enquetes():
        # 3) fallback: navigation directe
        try:
            driver.get("https://app.topsurveys.app/surveys")
            print("↪️  Navigation directe /surveys")
            # vérifier qu'on est bien sur la page
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "[data-test-id='ps-surveys-root']")))
        except Exception as e:
            print("🛑 Exception navigation :", type(e).__name__, "-", e)
            return

    time.sleep(15)  # laisser le temps au contenu de charger

    time.sleep(10)  # laisser le temps au filtre de s'appliquer
    snap(driver, "after_filter_best_paid")

    def _parse_minutes(raw_text):
        if not raw_text:
            return None
        match = re.search(r"(\d+(?:[\.,]\d+)?)", raw_text)
        if not match:
            return None
        minutes = float(match.group(1).replace(",", "."))
        return minutes if minutes > 0 else None

    def _parse_euros(raw_text):
        if not raw_text:
            return None
        match = re.search(r"(\d+(?:[\.,]\d+)?)", raw_text.replace(" ", ""))
        if not match:
            return None
        return float(match.group(1).replace(",", "."))

    # Parcourir les tiles visibles et choisir le meilleur ratio €/min
    try:
        wait.until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, "div[data-test-id^='ps-survey-'] div.survey-tile")))
        tiles = driver.find_elements(By.CSS_SELECTOR, "div[data-test-id^='ps-survey-'] div.survey-tile")

        best_tile = None
        best_ratio = -1.0

        for idx, tile in enumerate(tiles, start=1):
            try:
                time_text = tile.find_element(By.CSS_SELECTOR, "span[data-test-id='ps-survey-item-time']").text.strip()
                reward_text = tile.find_element(By.CSS_SELECTOR, "span[data-test-id='ps-reward-amount']").text.strip()
                minutes = _parse_minutes(time_text)
                reward = _parse_euros(reward_text)

                if minutes is None or reward is None:
                    print(f"⚠️ Survey #{idx} ignoré (données illisibles): durée='{time_text}' récompense='{reward_text}'")
                    continue

                ratio = reward / minutes
                print(f"📊 Survey #{idx}: {reward:.2f}€ / {minutes:.2f} min = {ratio:.4f} €/min")

                if ratio > best_ratio:
                    best_ratio = ratio
                    best_tile = tile
            except Exception as tile_error:
                print(f"⚠️ Survey #{idx} ignoré (extraction impossible): {type(tile_error).__name__} - {tile_error}")

        if best_tile is None:
            print("🛑 Aucun survey valide trouvé pour calculer le meilleur ratio €/min — abandon.")
            return

        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", best_tile)
        driver.execute_script("arguments[0].click();", best_tile)
        print(f"📝 Survey au meilleur ratio cliqué ({best_ratio:.4f} €/min).")
    except Exception as e:
        print("🛑 Exception sélection du survey :", type(e).__name__, "-", e)
