# browser/attach.py
import os
import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

def attach_to_existing_chrome():
    addr = (os.getenv("ATTACH_DEBUGGER_ADDRESS") or "127.0.0.1:9222").strip()

    options = Options()
    options.add_experimental_option("debuggerAddress", addr)

    t0 = time.perf_counter()
    driver = webdriver.Chrome(options=options)
    dt = time.perf_counter() - t0

    print(f"🟡 ATTACHED TO EXISTING CHROME SESSION ({addr}) in {dt:.2f}s")
    return driver