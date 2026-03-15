from config import is_attach_mode
from browser.attach import attach_to_existing_chrome
from browser.launch import launch_new_chrome

def get_driver():
    if is_attach_mode():
        print("⚠️ RUNNING IN ATTACH MODE (LOCAL ONLY)")
        return attach_to_existing_chrome()

    return launch_new_chrome()
