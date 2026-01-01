# captcha_solver.py
import os, time, requests
import preselection.config_loader

config = preselection.config_loader.load_config()
TWO_CAPTCHA_KEY = config.get("TWO_CAPTCHA_KEY")

class TwoCaptchaClient:
    def __init__(self, api_key=None, base_url="https://api.2captcha.com",
                 poll_interval=5, timeout=120):
        self.api_key = TWO_CAPTCHA_KEY or api_key
        self.base_url = base_url
        self.poll_interval = poll_interval
        self.timeout = timeout

    def solve_recaptcha_v2(self, sitekey: str, url: str, invisible: bool=False) -> str:
        # 1) createTask
        payload = {
            "clientKey": self.api_key,
            "task": {
                "type": "RecaptchaV2TaskProxyless",
                "websiteURL": url,
                "websiteKey": sitekey,
                "isInvisible": bool(invisible)
            }
        }
        r = requests.post(f"{self.base_url}/createTask", json=payload, timeout=30).json()
        if r.get("errorId"):
            raise RuntimeError(f"createTask error: {r}")
        task_id = r["taskId"]

        # 2) poll jusqu’à ready
        start = time.time()
        while True:
            if time.time() - start > self.timeout:
                raise TimeoutError("2Captcha délai dépassé")
            time.sleep(self.poll_interval)
            res = requests.post(f"{self.base_url}/getTaskResult",
                                json={"clientKey": self.api_key, "taskId": task_id},
                                timeout=30).json()
            if res.get("status") == "ready":
                return res["solution"]["gRecaptchaResponse"]
            if res.get("status") == "processing":
                continue
            raise RuntimeError(f"getTaskResult error: {res}")
