from selenium import webdriver
from selenium.webdriver.chrome.options import Options

def attach_to_existing_chrome():
    options = Options()
    options.add_experimental_option(
        "debuggerAddress", "127.0.0.1:9222"
    )

    driver = webdriver.Chrome(options=options)

    print("🟡 ATTACHED TO EXISTING CHROME SESSION")
    return driver
