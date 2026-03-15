from selenium import webdriver
from selenium.webdriver.chrome.options import Options

def launch_new_chrome():
    options = Options()
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--start-maximized")

    driver = webdriver.Chrome(options=options)
    print("🟢 LAUNCHED NEW CHROME SESSION")
    return driver
