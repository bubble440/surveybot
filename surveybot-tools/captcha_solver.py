# captcha_solver.py
import os, time, requests
try:
    import config_loader
    config = config_loader.load_config()
except Exception:
    # config_loader utilise des imports relatifs de package — non disponible
    # en contexte standalone. Les clés sont lues depuis les variables d'env.
    config = {}
TWO_CAPTCHA_KEY = (
    os.getenv("CAPTCHA_API_KEY")
    or os.getenv("TWO_CAPTCHA_KEY")
    or config.get("TWO_CAPTCHA_KEY")
    or config.get("CAPTCHA_API_KEY")
)
CAPSOLVER_API_KEY = (
    os.getenv("CAPSOLVER_API_KEY")
    or config.get("CAPSOLVER_API_KEY")
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

    def solve_recaptcha_v2_enterprise(self, sitekey: str, url: str, invisible: bool = False) -> str:
        """Résout reCAPTCHA V2 Enterprise via RecaptchaV2EnterpriseTaskProxyless."""
        payload = {
            "clientKey": self.api_key,
            "task": {
                "type": "RecaptchaV2EnterpriseTaskProxyless",
                "websiteURL": url,
                "websiteKey": sitekey,
                "isInvisible": bool(invisible),
            }
        }
        r = requests.post(f"{self.base_url}/createTask", json=payload, timeout=30).json()
        if r.get("errorId"):
            raise RuntimeError(f"createTask error: {r}")
        task_id = r["taskId"]

        start = time.time()
        while True:
            if time.time() - start > self.timeout:
                raise TimeoutError("2Captcha délai dépassé (Enterprise Proxyless)")
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
            raise RuntimeError(f"getTaskResult error: {res}")

    def solve_tencent(self, app_id: str, url: str) -> dict:
        """
        Résout Tencent CAPTCHA (slider puzzle) via TencentTaskProxyless.

        Retourne un dict {"ticket": str, "randstr": str}.
        """
        payload = {
            "clientKey": self.api_key,
            "task": {
                "type": "TencentTaskProxyless",
                "websiteURL": url,
                "appId": app_id,
            }
        }
        r = requests.post(f"{self.base_url}/createTask", json=payload, timeout=30).json()
        if r.get("errorId"):
            raise RuntimeError(f"createTask (tencent proxyless) error: {r}")
        task_id = r["taskId"]

        start = time.time()
        while True:
            if time.time() - start > self.timeout:
                raise TimeoutError("2Captcha délai dépassé (TencentTaskProxyless)")
            time.sleep(self.poll_interval)
            res = requests.post(
                f"{self.base_url}/getTaskResult",
                json={"clientKey": self.api_key, "taskId": task_id},
                timeout=30,
            ).json()
            if res.get("status") == "ready":
                sol = res["solution"]
                return {"ticket": sol["ticket"], "randstr": sol["randstr"]}
            if res.get("status") == "processing":
                continue
            raise RuntimeError(f"getTaskResult (tencent proxyless) error: {res}")

    def solve_tencent_with_proxy(
        self,
        app_id: str,
        url: str,
        proxy_type: str,
        proxy_address: str,
        proxy_port: int,
        proxy_login: str = "",
        proxy_password: str = "",
    ) -> dict:
        """
        Résout Tencent CAPTCHA (slider puzzle) via TencentTask (avec proxy).

        Retourne un dict {"ticket": str, "randstr": str}.
        """
        task = {
            "type": "TencentTask",
            "websiteURL": url,
            "appId": app_id,
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
            raise RuntimeError(f"createTask (tencent proxy) error: {r}")
        task_id = r["taskId"]

        start = time.time()
        while True:
            if time.time() - start > self.timeout:
                raise TimeoutError("2Captcha délai dépassé (TencentTask proxy)")
            time.sleep(self.poll_interval)
            res = requests.post(
                f"{self.base_url}/getTaskResult",
                json={"clientKey": self.api_key, "taskId": task_id},
                timeout=30,
            ).json()
            if res.get("status") == "ready":
                sol = res["solution"]
                return {"ticket": sol["ticket"], "randstr": sol["randstr"]}
            if res.get("status") == "processing":
                continue
            raise RuntimeError(f"getTaskResult (tencent proxy) error: {res}")

    def solve_datadome(
        self,
        captcha_url: str,
        website_url: str,
        user_agent: str,
        proxy_type: str,
        proxy_address: str,
        proxy_port: int,
        proxy_login: str = "",
        proxy_password: str = "",
    ) -> str:
        """
        Résout DataDome CAPTCHA via DataDomeSliderTask (toujours avec proxy).

        DataDome n'a pas de variante Proxyless — un proxy est obligatoire.
        Retourne la valeur du cookie datadome (chaîne brute depuis solution.cookie).
        """
        task = {
            "type": "DataDomeSliderTask",
            "websiteURL": website_url,
            "captchaUrl": captcha_url,
            "userAgent": user_agent,
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
            raise RuntimeError(f"createTask (datadome) error: {r}")
        task_id = r["taskId"]

        start = time.time()
        while True:
            if time.time() - start > self.timeout:
                raise TimeoutError("2Captcha délai dépassé (DataDomeSliderTask)")
            time.sleep(self.poll_interval)
            res = requests.post(
                f"{self.base_url}/getTaskResult",
                json={"clientKey": self.api_key, "taskId": task_id},
                timeout=30,
            ).json()
            if res.get("status") == "ready":
                return res["solution"]["cookie"]
            if res.get("status") == "processing":
                continue
            raise RuntimeError(f"getTaskResult (datadome) error: {res}")

    def solve_recaptcha_v2_enterprise_with_proxy(
        self,
        sitekey: str,
        url: str,
        proxy_type: str,
        proxy_address: str,
        proxy_port: int,
        proxy_login: str = "",
        proxy_password: str = "",
        invisible: bool = False,
    ) -> str:
        """Résout reCAPTCHA V2 Enterprise via RecaptchaV2EnterpriseTask (avec proxy)."""
        task = {
            "type": "RecaptchaV2EnterpriseTask",
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
            raise RuntimeError(f"createTask (enterprise proxy) error: {r}")
        task_id = r["taskId"]

        start = time.time()
        while True:
            if time.time() - start > self.timeout:
                raise TimeoutError("2Captcha délai dépassé (Enterprise proxy task)")
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
            raise RuntimeError(f"getTaskResult (enterprise proxy) error: {res}")


class CapSolverClient:
    """Client CapSolver — DataDome + reCAPTCHA v2 (standard, enterprise, proxy/proxyless)."""

    def __init__(self, api_key=None, base_url="https://api.capsolver.com",
                 poll_interval=4, timeout=180):
        self.api_key = CAPSOLVER_API_KEY or api_key
        self.base_url = base_url
        self.poll_interval = poll_interval
        self.timeout = timeout

    def _poll(self, task_id: str, label: str) -> dict:
        start = time.time()
        while True:
            if time.time() - start > self.timeout:
                raise TimeoutError(f"CapSolver délai dépassé ({label})")
            time.sleep(self.poll_interval)
            res = requests.post(
                f"{self.base_url}/getTaskResult",
                json={"clientKey": self.api_key, "taskId": task_id},
                timeout=30,
            ).json()
            if res.get("status") == "ready":
                return res["solution"]
            if res.get("status") == "processing":
                continue
            raise RuntimeError(f"getTaskResult ({label}) error: {res}")

    def solve_recaptcha_v2(self, sitekey: str, url: str, invisible: bool = False) -> str:
        payload = {
            "clientKey": self.api_key,
            "task": {
                "type": "ReCaptchaV2TaskProxyless",
                "websiteURL": url,
                "websiteKey": sitekey,
                "isInvisible": bool(invisible),
            },
        }
        r = requests.post(f"{self.base_url}/createTask", json=payload, timeout=30).json()
        if r.get("errorId"):
            raise RuntimeError(f"createTask error: {r}")
        return self._poll(r["taskId"], "ReCaptchaV2TaskProxyless")["gRecaptchaResponse"]

    def solve_recaptcha_v2_with_proxy(
        self,
        sitekey: str,
        url: str,
        proxy_type: str,
        proxy_address: str,
        proxy_port: int,
        proxy_login: str = "",
        proxy_password: str = "",
        invisible: bool = False,
    ) -> str:
        task = {
            "type": "ReCaptchaV2Task",
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
        return self._poll(r["taskId"], "ReCaptchaV2Task")["gRecaptchaResponse"]

    def solve_recaptcha_v2_enterprise(self, sitekey: str, url: str, invisible: bool = False) -> str:
        payload = {
            "clientKey": self.api_key,
            "task": {
                "type": "ReCaptchaV2EnterpriseTaskProxyless",
                "websiteURL": url,
                "websiteKey": sitekey,
                "isInvisible": bool(invisible),
            },
        }
        r = requests.post(f"{self.base_url}/createTask", json=payload, timeout=30).json()
        if r.get("errorId"):
            raise RuntimeError(f"createTask error: {r}")
        return self._poll(r["taskId"], "ReCaptchaV2EnterpriseTaskProxyless")["gRecaptchaResponse"]

    def solve_recaptcha_v2_enterprise_with_proxy(
        self,
        sitekey: str,
        url: str,
        proxy_type: str,
        proxy_address: str,
        proxy_port: int,
        proxy_login: str = "",
        proxy_password: str = "",
        invisible: bool = False,
    ) -> str:
        task = {
            "type": "ReCaptchaV2EnterpriseTask",
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
            raise RuntimeError(f"createTask (enterprise proxy) error: {r}")
        return self._poll(r["taskId"], "ReCaptchaV2EnterpriseTask")["gRecaptchaResponse"]

    def solve_datadome(
        self,
        captcha_url: str,
        website_url: str,
        user_agent: str,
        proxy_type: str,
        proxy_address: str,
        proxy_port: int,
        proxy_login: str = "",
        proxy_password: str = "",
    ) -> str:
        """
        Résout DataDome CAPTCHA via CapSolver (DataDomeSolverTask, proxy obligatoire).

        Retourne la valeur brute du cookie datadome (chaîne depuis solution.cookie),
        dans le même format que TwoCaptchaClient.solve_datadome().
        """
        scheme = proxy_type.lower()
        if proxy_login and proxy_password:
            proxy_str = f"{scheme}://{proxy_login}:{proxy_password}@{proxy_address}:{proxy_port}"
        else:
            proxy_str = f"{scheme}://{proxy_address}:{proxy_port}"

        task = {
            "type": "DataDomeSolverTask",
            "websiteURL": website_url,
            "captchaUrl": captcha_url,
            "userAgent": user_agent,
            "proxy": proxy_str,
        }

        payload = {"clientKey": self.api_key, "task": task}
        r = requests.post(f"{self.base_url}/createTask", json=payload, timeout=30).json()
        if r.get("errorId"):
            raise RuntimeError(f"createTask (datadome capsolver) error: {r}")
        task_id = r["taskId"]

        start = time.time()
        while True:
            if time.time() - start > self.timeout:
                raise TimeoutError("CapSolver délai dépassé (DataDomeSolverTask)")
            time.sleep(self.poll_interval)
            res = requests.post(
                f"{self.base_url}/getTaskResult",
                json={"clientKey": self.api_key, "taskId": task_id},
                timeout=30,
            ).json()
            if res.get("status") == "ready":
                return res["solution"]["cookie"]
            if res.get("status") == "processing":
                continue
            raise RuntimeError(f"getTaskResult (datadome capsolver) error: {res}")