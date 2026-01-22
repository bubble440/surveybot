import os
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

def attach_to_existing_chrome():
    addr = (os.getenv("ATTACH_DEBUGGER_ADDRESS") or "127.0.0.1:9222").strip()

    options = Options()
    options.add_experimental_option("debuggerAddress", addr)

    driver = webdriver.Chrome(options=options)

    print(f"🟡 ATTACHED TO EXISTING CHROME SESSION ({addr})")
    return driver
