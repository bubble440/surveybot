#!/usr/bin/env python3
"""Level B CTA probe.

Objectif: valider rapidement qu'un patch n'a pas cassé la chaîne CTA:
1) Détection CTA
2) Clic effectif
3) Effet attendu (submit/navigation/changement d'état)

Usage:
  python surveybot/tools/run_level_b_cta.py --url "https://example.com/survey-step"
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait


CTA_KEYWORDS = {
    "continue",
    "next",
    "submit",
    "validate",
    "start",
    "begin",
    "proceed",
    "suivant",
    "continuer",
    "valider",
    "soumettre",
    "envoyer",
    "terminer",
}

SELECTORS = [
    "button",
    "input[type='submit']",
    "input[type='button']",
    "[role='button']",
    "a[class*='btn']",
    "a[class*='button']",
    "a[class*='cta']",
    "a[role='button']",
]


@dataclass
class Candidate:
    selector: str
    label: str
    tag: str
    score: int


def normalize_label(value: str) -> str:
    return " ".join((value or "").strip().lower().replace("→", " ").replace("»", " ").split())


def score_label(label: str) -> int:
    if not label:
        return 0
    score = 0
    for kw in CTA_KEYWORDS:
        if kw in label:
            score += 10
    if "privacy" in label or "cookie" in label or "policy" in label:
        score -= 20
    return score


def launch_driver(headful: bool) -> webdriver.Chrome:
    options = Options()
    if not headful:
        options.add_argument("--headless=new")
    options.add_argument("--window-size=1366,900")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    return webdriver.Chrome(options=options)


def get_page_fingerprint(driver: webdriver.Chrome) -> Dict[str, Any]:
    body = driver.execute_script(
        "return (document.body && document.body.innerText ? document.body.innerText : '').slice(0, 4000);"
    )
    digest = hashlib.sha256((body or "").encode("utf-8", errors="ignore")).hexdigest()[:16]
    return {
        "url": driver.current_url,
        "title": driver.title,
        "dom_hash": digest,
        "body_len": len(body or ""),
        "resource_count": driver.execute_script("return performance.getEntriesByType('resource').length;"),
    }


def install_probe(driver: webdriver.Chrome) -> None:
    driver.execute_script(
        """
        if (!window.__sbCtaProbe) {
          window.__sbCtaProbe = {
            submitCount: 0,
            clickCount: 0,
            lastClickedLabel: null,
            startedAt: Date.now(),
          };
          document.addEventListener('submit', function () {
            window.__sbCtaProbe.submitCount += 1;
          }, true);
          document.addEventListener('click', function (ev) {
            const t = ev.target && ev.target.closest ? ev.target.closest('button,input[type="submit"],input[type="button"],a,[role="button"]') : null;
            if (!t) return;
            const txt = (t.innerText || t.value || t.getAttribute('aria-label') || '').trim();
            window.__sbCtaProbe.clickCount += 1;
            window.__sbCtaProbe.lastClickedLabel = txt;
          }, true);
        }
        """
    )


def collect_candidates(driver: webdriver.Chrome) -> List[Candidate]:
    items: List[Candidate] = []
    for selector in SELECTORS:
        for el in driver.find_elements(By.CSS_SELECTOR, selector):
            try:
                if not el.is_displayed() or not el.is_enabled():
                    continue
                tag = (el.tag_name or "").lower()
                label = normalize_label(
                    (el.text or el.get_attribute("value") or el.get_attribute("aria-label") or "")
                )
                items.append(
                    Candidate(
                        selector=selector,
                        label=label,
                        tag=tag,
                        score=score_label(label),
                    )
                )
            except Exception:
                continue

    # dédoublonnage approximatif (tag + label)
    dedup: Dict[str, Candidate] = {}
    for it in items:
        key = f"{it.tag}|{it.label}"
        if key not in dedup or it.score > dedup[key].score:
            dedup[key] = it

    return sorted(dedup.values(), key=lambda c: c.score, reverse=True)


def find_cta_element(driver: webdriver.Chrome, best: Candidate):
    for el in driver.find_elements(By.CSS_SELECTOR, best.selector):
        try:
            if not el.is_displayed() or not el.is_enabled():
                continue
            label = normalize_label(
                (el.text or el.get_attribute("value") or el.get_attribute("aria-label") or "")
            )
            if label == best.label:
                return el
        except Exception:
            continue
    return None


def click_cta(driver: webdriver.Chrome, element) -> bool:
    try:
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
        time.sleep(0.1)
        element.click()
        return True
    except Exception:
        try:
            driver.execute_script("arguments[0].click();", element)
            return True
        except Exception:
            return False


def run(url: str, timeout_s: int, headful: bool) -> int:
    driver = launch_driver(headful=headful)
    report: Dict[str, Any] = {"url": url}

    try:
        driver.get(url)
        WebDriverWait(driver, timeout_s).until(lambda d: d.execute_script("return document.readyState") in ("interactive", "complete"))

        install_probe(driver)
        before = get_page_fingerprint(driver)
        report["before"] = before

        candidates = collect_candidates(driver)
        report["top_candidates"] = [c.__dict__ for c in candidates[:5]]

        if not candidates or candidates[0].score <= 0:
            report["status"] = "fail"
            report["reason"] = "no reliable CTA candidate found"
            print(json.dumps(report, indent=2, ensure_ascii=False))
            return 2

        best = candidates[0]
        el = find_cta_element(driver, best)
        if el is None:
            report["status"] = "fail"
            report["reason"] = "CTA candidate not resolvable to DOM element"
            print(json.dumps(report, indent=2, ensure_ascii=False))
            return 2

        click_ok = click_cta(driver, el)
        report["click_executed"] = click_ok
        if not click_ok:
            report["status"] = "fail"
            report["reason"] = "click action failed"
            print(json.dumps(report, indent=2, ensure_ascii=False))
            return 2

        start = time.time()
        changed = False
        after: Optional[Dict[str, Any]] = None
        while time.time() - start < timeout_s:
            try:
                after = get_page_fingerprint(driver)
            except Exception:
                # navigation in progress
                time.sleep(0.2)
                continue

            if (
                after["url"] != before["url"]
                or after["dom_hash"] != before["dom_hash"]
                or after["resource_count"] > before["resource_count"]
            ):
                changed = True
                break
            time.sleep(0.25)

        probe = driver.execute_script("return window.__sbCtaProbe || {}; ")
        report["probe"] = probe
        report["after"] = after or get_page_fingerprint(driver)
        report["effect_observed"] = changed or (probe.get("submitCount", 0) > 0)

        if report["effect_observed"]:
            report["status"] = "pass"
            print(json.dumps(report, indent=2, ensure_ascii=False))
            return 0

        report["status"] = "fail"
        report["reason"] = "CTA clicked but no submit/navigation/state change observed"
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 3

    finally:
        driver.quit()


def main() -> None:
    ap = argparse.ArgumentParser(description="Run level-B CTA non-regression probe on a survey page.")
    ap.add_argument("--url", required=True, help="Survey step URL to probe")
    ap.add_argument("--timeout", type=int, default=10, help="Timeout in seconds for load and post-click observation")
    ap.add_argument("--headful", action="store_true", help="Run with visible browser")
    args = ap.parse_args()

    rc = run(url=args.url, timeout_s=args.timeout, headful=args.headful)
    sys.exit(rc)


if __name__ == "__main__":
    main()
