# captcha_solver.py
import os, time, requests
import preselection.config_loader

config = preselection.config_loader.load_config()
TWO_CAPTCHA_KEY = (
    os.getenv("CAPTCHA_API_KEY")
    or os.getenv("TWO_CAPTCHA_KEY")
    or config.get("TWO_CAPTCHA_KEY")
    or config.get("CAPTCHA_API_KEY")
)

class TwoCaptchaClient:
    def __init__(self, api_key=None, base_url="https://api.2captcha.com",
                 poll_interval=4, timeout=180):
        self.api_key = TWO_CAPTCHA_KEY or api_key
        self.base_url = base_url
        self.poll_interval = poll_interval
        self.timeout = timeout

    def solve_image_to_text(self, image_base64: str) -> str:
        """
        Soumet une image CAPTCHA encodée en base64 à 2Captcha (ImageToTextTask)
        et retourne le texte reconnu.
        """
        payload = {
            "clientKey": self.api_key,
            "task": {
                "type": "ImageToTextTask",
                "body": image_base64,
            }
        }
        r = requests.post(f"{self.base_url}/createTask", json=payload, timeout=30).json()
        if r.get("errorId"):
            raise RuntimeError(f"createTask error: {r}")
        task_id = r["taskId"]

        start = time.time()
        while True:
            if time.time() - start > self.timeout:
                raise TimeoutError("2Captcha délai dépassé (ImageToTextTask)")
            time.sleep(self.poll_interval)
            res = requests.post(
                f"{self.base_url}/getTaskResult",
                json={"clientKey": self.api_key, "taskId": task_id},
                timeout=30,
            ).json()
            if res.get("status") == "ready":
                return res["solution"]["text"]
            if res.get("status") == "processing":
                continue
            raise RuntimeError(f"getTaskResult error: {res}")

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

    def solve_recaptcha_v2_with_proxy(
        self,
        sitekey: str,
        url: str,
        proxy_type: str,       # "http" | "socks4" | "socks5"
        proxy_address: str,    # "1.2.3.4" ou "hostname"
        proxy_port: int,       # ex: 8080
        proxy_login: str = "", # optionnel
        proxy_password: str = "",
        invisible: bool = False,
    ) -> str:
        """
        Résout reCAPTCHA v2 via RecaptchaV2Task (avec proxy).

        POURQUOI : certaines plateformes (Decipher) valident côté serveur que le token
        a été généré depuis la même IP que la soumission du formulaire.
        RecaptchaV2TaskProxyless utilise l'IP de 2Captcha → token rejeté.
        RecaptchaV2Task transmet notre proxy → 2Captcha résout depuis notre IP → accepté.

        Le proxy doit être le même que celui utilisé par le Chrome du bot.
        """
        task = {
            "type": "RecaptchaV2Task",
            "websiteURL": url,
            "websiteKey": sitekey,
            "isInvisible": bool(invisible),
            "proxyType": proxy_type,
            "proxyAddress": proxy_address,
            "proxyPort": int(proxy_port),
        }
        if proxy_login:
            task["proxyLogin"] = proxy_login
        if proxy_password:
            task["proxyPassword"] = proxy_password

        payload = {"clientKey": self.api_key, "task": task}
        r = requests.post(f"{self.base_url}/createTask", json=payload, timeout=30).json()
        if r.get("errorId"):
            raise RuntimeError(f"createTask (proxy) error: {r}")
        task_id = r["taskId"]

        start = time.time()
        while True:
            if time.time() - start > self.timeout:
                raise TimeoutError("2Captcha délai dépassé (proxy task)")
            time.sleep(self.poll_interval)
            res = requests.post(
                f"{self.base_url}/getTaskResult",
                json={"clientKey": self.api_key, "taskId": task_id},
                timeout=30,
            ).json()
            if res.get("status") == "ready":
                return res["solution"]["gRecaptchaResponse"]
            if res.get("status") == "processing":
                continue
            raise RuntimeError(f"getTaskResult (proxy) error: {res}")