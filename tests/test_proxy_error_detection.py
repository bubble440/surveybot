import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "surveybot"))

from surveybot.preselection.auth_handler import (
    get_proxy_error_code,
    is_proxy_error_page,
)


class _DriverStub:
    def __init__(self, *, url: str = "https://example.org", src: str = ""):
        self.current_url = url
        self.page_source = src


def test_get_proxy_error_code_extracts_known_code():
    drv = _DriverStub(src="<html><body>ERR_TUNNEL_CONNECTION_FAILED</body></html>")
    assert get_proxy_error_code(drv) == "ERR_TUNNEL_CONNECTION_FAILED"


def test_is_proxy_error_page_true_on_chrome_error_scheme():
    drv = _DriverStub(url="chrome-error://chromewebdata/")
    assert is_proxy_error_page(drv) is True


def test_is_proxy_error_page_true_on_known_error_code():
    drv = _DriverStub(src="<html><body>err_name_not_resolved</body></html>")
    assert is_proxy_error_page(drv) is True


def test_is_proxy_error_page_false_on_non_network_err_code():
    drv = _DriverStub(src="<html><body>ERR_CERT_DATE_INVALID</body></html>")
    assert is_proxy_error_page(drv) is False
