from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

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

    # Appliquer le filtre « Paiement le plus élevé »
    try:
        flt = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "div[data-test-id='ps-filter-item-by_survey_reward']")))
        driver.execute_script("arguments[0].click();", flt)
        print("💰 Filtre « Paiement le plus élevé » appliqué.")
    except Exception:
        print("⚠️ Impossible d'appliquer le filtre du paiement — on continue.")

    # Cliquer le premier tile
    try:
        first = wait.until(EC.element_to_be_clickable((By.XPATH, "(//div[contains(@class, 'survey-tile')])[1]")))
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", first)
        driver.execute_script("arguments[0].click();", first)
        print("📝 Premier survey cliqué.")
    except Exception as e:
        print("🛑 Exception sélection du survey :", type(e).__name__, "-", e)
