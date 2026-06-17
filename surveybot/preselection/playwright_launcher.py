from __future__ import annotations
import os, time
import subprocess
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium import webdriver
from Survey.functions import _env_truthy

# IS_LOCAL = os.getenv("RUN_ENV", "local") == "local"
IS_LOCAL = os.getenv("RUN_ENV", "local") == "local"

# preselection/playwright_launcher.py
"""
Launcher alternatif : subprocess.Popen lance Chrome directement (sans Playwright),
puis Selenium s'attache via debuggerAddress.
Objectif : éviter la contamination CDP/cdc_* de Selenium à l'attach et supprimer
la bannière "Chrome is being controlled by automated test software".
"""

import json
import random
import logging
import tempfile
from urllib.parse import urlparse

log = logging.getLogger(__name__)


def _detect_chrome_binary() -> str:
    import os, shutil, sys

    # 1) variable explicite
    env_bin = os.getenv("SURVEY_BROWSER_BIN")
    if env_bin and os.path.exists(env_bin):
        return env_bin

    # 2) PATH — sur Linux, priorité aux binaires natifs pour éviter la résolution
    # via l'interop WSL (chrome.exe bind son debug port côté Windows, inaccessible
    # depuis WSL → SessionNotCreatedException à l'attach).
    if sys.platform != "win32":
        for p in ("/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome"):
            if os.path.exists(p):
                return p
        for name in ("chromium", "chromium-browser", "google-chrome", "chrome"):
            path = shutil.which(name)
            if path and not path.endswith(".exe"):
                return path
    else:
        for name in ("chrome", "chrome.exe", "chromium", "chromium-browser"):
            path = shutil.which(name)
            if path:
                return path

    # 3) chemins standards
    candidates = [
        # Windows
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        # Linux
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
    ]
    for p in candidates:
        if os.path.exists(p):
            return p

    raise FileNotFoundError(
        "Chrome/Chromium introuvable. "
        "Installe Chromium (Linux/Docker) ou Chrome (Windows)."
    )

def _parse_proxy_env(config: dict | None = None):
    """
    Récupère le proxy depuis :
    1) config (source principale)
    2) os.environ (fallback CI / debug)
    """

    def _get(key):
        if config and key in config and config[key]:
            return str(config[key]).strip()
        return os.getenv(key)

    proxy_url  = _get("PROXY_URL")
    proxy_user = _get("PROXY_USER")
    proxy_pass = _get("PROXY_PASS")

    if not proxy_url:
        return None, None, None

    if "://" not in proxy_url:
        proxy_url = "http://" + proxy_url

    parsed = urlparse(proxy_url)
    server = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"

    if not proxy_user or not proxy_pass:
        return server, None, None

    return server, proxy_user, proxy_pass

def _want_headless() -> bool:
    """
    Headless si SURVEY_HEADLESS=1 et pas de DISPLAY.
    """
    import sys
    if sys.platform == "win32":
        return os.getenv("SURVEY_HEADLESS", "0") == "1"

    use_display = bool(os.environ.get("DISPLAY"))
    headless_env = os.getenv("SURVEY_HEADLESS", "0") == "1"
    return headless_env or not use_display

def _parse_geo_env():
    """
    Lit GEO_LAT / GEO_LON depuis les variables d'env.
    Fallback: Paris.
    """
    try:
        lat = float(os.getenv("GEO_LAT", "48.8566"))
    except Exception:
        lat = 48.8566
    try:
        lon = float(os.getenv("GEO_LON", "2.3522"))
    except Exception:
        lon = 2.3522
    return {"latitude": lat, "longitude": lon, "accuracy": 50}


def _parse_locale_tz_env():
    """
    Lit SURVEY_LANG / SURVEY_TZ.
    Fallback: fr-FR + Europe/Paris.
    """
    locale = (os.getenv("SURVEY_LANG", "fr-FR") or "fr-FR").strip()
    tz = (os.getenv("SURVEY_TZ", "Europe/Paris") or "Europe/Paris").strip()
    return locale, tz

def _fingerprint_js() -> str:
    """
    Retourne le JS de spoofing fingerprint à injecter sur chaque nouvelle page.

    Source unique utilisée par apply_fingerprint_overrides_cdp() via CDP Selenium
    (Page.addScriptToEvaluateOnNewDocument), ce qui garantit que le script est
    exécuté avant tout autre JS pour TOUTES les navigations Selenium.

    Patches appliqués :
      - Langue / Timezone
      - navigator.webdriver  (patch robuste sur Navigator.prototype)
      - navigator.platform
      - navigator.userAgentData (vide en prod → spoofer Chrome 149 Windows complet)
      - navigator.plugins    (vide en headless → simuler 3 plugins Chrome réels)
      - navigator.mimeTypes  (lié aux plugins)
      - window.chrome        (absent en headless → injecter l'objet complet)
      - WebGL renderer       (SwiftShader détectable → spoofer Intel)
      - screen dimensions    (cohérent avec --window-size=1920,1080)
      - hardwareConcurrency / deviceMemory
    """
    return """
        // ── Langue ──────────────────────────────────────────────────────────
        // Attach (Windows) retourne ['en-US', 'en'] — prod retournait ['fr-FR', 'fr'].
        // On aligne sur attach pour éviter la contradiction avec Accept-Language HTTP.
        Object.defineProperty(navigator, 'language',  { get: () => 'en-US' });
        Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });

        // ── Timezone ─────────────────────────────────────────────────────────
        const _origResolvedOptions = Intl.DateTimeFormat.prototype.resolvedOptions;
        Intl.DateTimeFormat.prototype.resolvedOptions = function () {
            const opts = _origResolvedOptions.apply(this, arguments);
            opts.timeZone = 'Europe/Paris';
            return opts;
        };

        // ── navigator.webdriver (patch robuste sur le prototype) ─────────────
        // Simple Object.defineProperty(navigator, ...) est contournable via
        // Object.getOwnPropertyDescriptor(Navigator.prototype, 'webdriver').
        try {
            Object.defineProperty(Navigator.prototype, 'webdriver', {
                get: () => undefined,
                configurable: true,
                enumerable: true,
            });
        } catch(e) {}

        // ── Suppression des propriétés cdc_* de ChromeDriver ─────────────────
        // ChromeDriver injecte des propriétés cdc_* dans window à chaque attach.
        // Ces clés sont détectées par tous les SDK anti-bot modernes comme signal
        // d'automation primaire — plus fiable que navigator.webdriver lui-même.
        // On supprime toutes les clés cdc_* présentes ET on pose un getter
        // indéfini via defineProperty pour résister à la ré-injection.
        try {
            for (const key of Object.getOwnPropertyNames(window)) {
                if (key.startsWith('cdc_')) {
                    try { delete window[key]; } catch(e) {}
                    try {
                        Object.defineProperty(window, key, {
                            get: () => undefined,
                            configurable: true,
                        });
                    } catch(e) {}
                }
            }
        } catch(e) {}

        // Variantes legacy (versions antérieures de ChromeDriver)
        try {
            const _legacyKeys = [
                '$chrome_asyncScriptInfo',
                '$cdc_asdjflasutopfhvcZLmcfl_',
            ];
            for (const k of _legacyKeys) {
                try { delete window[k]; } catch(e) {}
                try {
                    Object.defineProperty(window, k, {
                        get: () => undefined,
                        configurable: true,
                    });
                } catch(e) {}
            }
        } catch(e) {}

        // ── Platform ─────────────────────────────────────────────────────────
        Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });

        // ── navigator.userAgentData ───────────────────────────────────────────
        // En prod (Chromium/Linux headless), userAgentData est vide ou absent.
        // En attach (Chrome/Windows), il expose platform, brands, architecture…
        // Cette contradiction est détectée immédiatement par les anti-bots modernes.
        // On spoofé l'objet complet pour correspondre à un Chrome 149 Windows réel.
        try {
            const _uaData = {
                brands: [
                    { brand: 'Chromium',        version: '149' },
                    { brand: 'Google Chrome',   version: '149' },
                    { brand: 'Not-A.Brand',     version: '24'  },
                ],
                mobile: false,
                platform: 'Windows',
                // getHighEntropyValues : appelé par certains SDK fingerprinting
                // pour récupérer architecture, bitness, uaFullVersion, etc.
                getHighEntropyValues: function(hints) {
                    const _full = {
                        architecture:    'x86',
                        bitness:         '64',
                        model:           '',
                        platform:        'Windows',
                        platformVersion: '10.0.0',
                        uaFullVersion:   '149.0.7827.103',
                        fullVersionList: [
                            { brand: 'Chromium',      version: '149.0.7827.103' },
                            { brand: 'Google Chrome', version: '149.0.7827.103' },
                            { brand: 'Not-A.Brand',   version: '24.0.0.0'       },
                        ],
                        wow64: false,
                    };
                    const result = {};
                    (hints || []).forEach(h => { if (h in _full) result[h] = _full[h]; });
                    return Promise.resolve(result);
                },
                toJSON: function() {
                    return { brands: this.brands, mobile: this.mobile, platform: this.platform };
                },
            };
            Object.defineProperty(navigator, 'userAgentData', {
                get: () => _uaData,
                configurable: true,
                enumerable: true,
            });
        } catch(e) {}

        // ── navigator.plugins (vide = signal bot primaire) ───────────────────
        // Attach (Chrome Windows) expose 5 plugins réels.
        // Prod ne retournait que 2 plugins dont mhjfbmdgcfjbbpaeojofohoefgiehjai
        // (hash cryptique Chrome PDF Viewer) sans les autres — divergence détectable.
        // On simule les 5 plugins exacts visibles en mode attach.
        try {
            const _pluginData = [
                { name: 'PDF Viewer',                filename: 'internal-pdf-viewer',             description: 'Portable Document Format' },
                { name: 'Chrome PDF Viewer',         filename: 'internal-pdf-viewer',             description: 'Portable Document Format' },
                { name: 'Chromium PDF Viewer',       filename: 'internal-pdf-viewer',             description: 'Portable Document Format' },
                { name: 'Microsoft Edge PDF Viewer', filename: 'internal-pdf-viewer',             description: 'Portable Document Format' },
                { name: 'WebKit built-in PDF',       filename: 'internal-pdf-viewer',             description: 'Portable Document Format' },
            ];
            const _makePlugin = (d) => {
                const mt = { type: 'application/x-google-chrome-pdf', suffixes: 'pdf', description: d.description };
                const p  = Object.create(Plugin.prototype);
                Object.defineProperties(p, {
                    name:        { value: d.name,        enumerable: true },
                    filename:    { value: d.filename,    enumerable: true },
                    description: { value: d.description, enumerable: true },
                    length:      { value: 1,             enumerable: true },
                    0:           { value: mt,             enumerable: true },
                });
                return p;
            };
            const _pa = Object.create(PluginArray.prototype);
            _pluginData.forEach((d, i) => Object.defineProperty(_pa, i, { value: _makePlugin(d), enumerable: true }));
            Object.defineProperties(_pa, {
                length:    { value: _pluginData.length },
                refresh:   { value: () => {} },
                item:      { value: (i) => _pa[i] },
                namedItem: { value: (n) => { const i = _pluginData.findIndex(p => p.name === n); return i >= 0 ? _pa[i] : null; } },
            });
            Object.defineProperty(navigator, 'plugins', { get: () => _pa });
        } catch(e) {}

        // ── navigator.mimeTypes ───────────────────────────────────────────────
        try {
            const _mta = Object.create(MimeTypeArray.prototype);
            const _mt0 = { type: 'application/x-google-chrome-pdf', suffixes: 'pdf', description: 'Portable Document Format' };
            Object.defineProperty(_mta, 0,        { value: _mt0, enumerable: true });
            Object.defineProperty(_mta, 'length', { value: 1 });
            Object.defineProperty(_mta, 'item',   { value: (i) => _mta[i] });
            Object.defineProperty(navigator, 'mimeTypes', { get: () => _mta });
        } catch(e) {}

        // ── window.chrome (absent en headless) ───────────────────────────────
        if (!window.chrome) {
            const _chrome = {
                app: {
                    isInstalled: false,
                    InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' },
                    RunningState: { CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' },
                },
                runtime: {
                    OnInstalledReason: {}, OnRestartRequiredReason: {},
                    PlatformArch: {}, PlatformNaclArch: {}, PlatformOs: {},
                    RequestUpdateCheckStatus: {},
                },
                loadTimes: function() {
                    return {
                        requestTime: Date.now() / 1000, startLoadTime: Date.now() / 1000,
                        commitLoadTime: Date.now() / 1000, finishDocumentLoadTime: Date.now() / 1000,
                        finishLoadTime: Date.now() / 1000, firstPaintTime: Date.now() / 1000,
                        firstPaintAfterLoadTime: 0, navigationType: 'Other',
                        wasFetchedViaSpdy: false, wasNpnNegotiated: true,
                        npnNegotiatedProtocol: 'h2', wasAlternateProtocolAvailable: false,
                        connectionInfo: 'h2',
                    };
                },
                csi: function() {
                    return {
                        startE: Date.now(), onloadT: Date.now(),
                        pageT: Date.now() - performance.timing.navigationStart,
                        tran: 15,
                    };
                },
            };
            try {
                Object.defineProperty(window, 'chrome', { value: _chrome, writable: false, enumerable: true, configurable: false });
            } catch(e) {}
        }

        // ── WebGL fingerprint (SwiftShader → Intel UHD 617, ANGLE/D3D11) ───────
        // BrowserLeaks et les SDK fingerprinting calculent le WebGL Report Hash
        // sur l'ensemble des valeurs retournées par getParameter().
        // SwiftShader + Xvfb produit un profil qui diverge d'un Intel intégré réel
        // sur une dizaine de paramètres — pas seulement vendor/renderer.
        //
        // Valeurs cibles : mesurées en mode attach sur Chrome headed Windows
        // (Intel UHD Graphics 617, ANGLE Direct3D11 vs_5_0).
        //
        // Paramètres retournant un type typé (Int32Array / Float32Array) :
        //   le proxy retourne le type natif pour ne pas rompre les checks
        //   instanceof des outils de détection.
        //
        // SwiftShader vs cible (valeurs qui divergent) :
        //   UNMASKED_VENDOR_WEBGL             : 'Intel Inc.'        → 'Google Inc. (Intel)'
        //   UNMASKED_RENDERER_WEBGL           : 'Intel(R) Iris(R)'  → 'ANGLE (Intel, Intel(R) UHD Graphics 617 (0x000087C0) Direct3D11 vs_5_0 ps_5_0, D3D11)'
        //   MAX_VIEWPORT_DIMS        (0x0D3A) : [8192,8192]         → [32767,32767]   (SwiftShader est plus petit ici)
        //   ALIASED_POINT_SIZE_RANGE (0x846D) : [1,1023]            → [1,1024]
        //   MAX_TEXTURE_IMAGE_UNITS  (0x8872) : 32                  → 16
        //   MAX_VERTEX_TEXTURE_IMAGE_UNITS (0x8B4C) : 32            → 16
        //   MAX_FRAGMENT_UNIFORM_VECTORS (0x8DFD) : 4096            → 1024
        //   MAX_VARYING_VECTORS      (0x8DFC) : 31                  → 30
        //   MAX_VERTEX_UNIFORM_BLOCKS (0x8A2B) : 14                 → 12
        //   MAX_VERTEX_OUTPUT_COMPONENTS (0x9122) : 128             → 120
        //   MAX_VARYING_COMPONENTS   (0x8B4B) : 124                 → 120
        //   MAX_INTERLEAVED_COMPONENTS (WebGL2 0x8C8A) : 128        → 120
        //   MAX_COMBINED_VERTEX_UNIFORM_COMPONENTS (0x8A31) : 245760 → 212992
        //   MAX_COMBINED_FRAGMENT_UNIFORM_COMPONENTS (0x8A33) : 245760 → 200704
        try {
            const _GL = {
                // Identité GPU
                UNMASKED_VENDOR_WEBGL:                    0x9245,
                UNMASKED_RENDERER_WEBGL:                  0x9246,
                // Vectoriels (retournent un type typé)
                MAX_VIEWPORT_DIMS:                        0x0D3A,
                ALIASED_POINT_SIZE_RANGE:                 0x846D,
                // Scalaires WebGL1
                MAX_TEXTURE_IMAGE_UNITS:                  0x8872,
                MAX_VERTEX_TEXTURE_IMAGE_UNITS:           0x8B4C,
                MAX_FRAGMENT_UNIFORM_VECTORS:             0x8DFD,
                MAX_VARYING_VECTORS:                      0x8DFC,
                // Scalaires WebGL2
                MAX_VERTEX_UNIFORM_BLOCKS:                0x8A2B,
                MAX_VERTEX_OUTPUT_COMPONENTS:             0x9122,
                MAX_VARYING_COMPONENTS:                   0x8B4B,
                MAX_TRANSFORM_FEEDBACK_INTERLEAVED_COMPONENTS: 0x8C8A,
                MAX_COMBINED_VERTEX_UNIFORM_COMPONENTS:   0x8A31,
                MAX_COMBINED_FRAGMENT_UNIFORM_COMPONENTS: 0x8A33,
                // Paramètres WebGL2 supplémentaires (SwiftShader diverge)
                MAX_RENDERBUFFER_SIZE:                    0x84E8,  // 8192  → 16384
                MAX_TEXTURE_SIZE:                         0x0D33,  // 8192  → 16384
                MAX_FRAGMENT_UNIFORM_BLOCKS:              0x8A2D,  // 14    → 12
                MAX_FRAGMENT_INPUT_COMPONENTS:            0x9125,  // 128   → 120
                MAX_TEXTURE_LOD_BIAS:                     0x84FD,  // 15.0  → 2.0
                MAX_UNIFORM_BUFFER_BINDINGS:              0x8A2F,  // 72    → 24
                MAX_COMBINED_UNIFORM_BLOCKS:              0x8A2E,  // 60    → 24
                // Paramètres résiduels non harmonisés (fix 3)
                MAX_COMBINED_TEXTURE_IMAGE_UNITS:         0x8B4D,  // 64    → 70
                MAX_TEXTURE_MAX_ANISOTROPY_EXT:           0x84FF,  // 18    → 16
                // Paramètres divergents (fix 3.4)
                MAX_FRAGMENT_UNIFORM_COMPONENTS:          0x8B49,  // prod=16384 → attach=4096
                MAX_DRAW_BUFFERS:                         0x8824,  // prod=6     → attach=8
                MAX_COLOR_ATTACHMENTS:                    0x8CDF,  // prod=6     → attach=8
                MAX_SAMPLES:                              0x8D57,  // prod=4     → attach=16
            };
            const _glProxy = {
                apply(target, ctx, args) {
                    const p = args[0];
                    switch (p) {
                        // ── Identité GPU ───────────────────────────────────────
                        case _GL.UNMASKED_VENDOR_WEBGL:
                            return 'Google Inc. (Intel)';
                        case _GL.UNMASKED_RENDERER_WEBGL:
                            return 'ANGLE (Intel, Intel(R) UHD Graphics 617 (0x000087C0) Direct3D11 vs_5_0 ps_5_0, D3D11)';
                        // ── Vectoriels (type natif obligatoire) ────────────────
                        case _GL.MAX_VIEWPORT_DIMS:
                            return new Int32Array([32767, 32767]);
                        case _GL.ALIASED_POINT_SIZE_RANGE:
                            return new Float32Array([1, 1024]);
                        // ── Scalaires WebGL1 ───────────────────────────────────
                        case _GL.MAX_TEXTURE_IMAGE_UNITS:
                            return 16;
                        case _GL.MAX_VERTEX_TEXTURE_IMAGE_UNITS:
                            return 16;
                        case _GL.MAX_FRAGMENT_UNIFORM_VECTORS:
                            return 1024;
                        case _GL.MAX_VARYING_VECTORS:
                            return 30;
                        // ── Scalaires WebGL2 ───────────────────────────────────
                        case _GL.MAX_VERTEX_UNIFORM_BLOCKS:
                            return 12;
                        case _GL.MAX_VERTEX_OUTPUT_COMPONENTS:
                            return 120;
                        case _GL.MAX_VARYING_COMPONENTS:
                            return 120;
                        case _GL.MAX_TRANSFORM_FEEDBACK_INTERLEAVED_COMPONENTS:
                            return 120;
                        case _GL.MAX_COMBINED_VERTEX_UNIFORM_COMPONENTS:
                            return 212992;
                        case _GL.MAX_COMBINED_FRAGMENT_UNIFORM_COMPONENTS:
                            return 200704;
                        // ── WebGL2 supplémentaires ─────────────────────────────
                        case _GL.MAX_RENDERBUFFER_SIZE:
                            return 16384;
                        case _GL.MAX_TEXTURE_SIZE:
                            return 16384;
                        case _GL.MAX_FRAGMENT_UNIFORM_BLOCKS:
                            return 12;
                        case _GL.MAX_FRAGMENT_INPUT_COMPONENTS:
                            return 120;
                        case _GL.MAX_TEXTURE_LOD_BIAS:
                            return 2;
                        case _GL.MAX_UNIFORM_BUFFER_BINDINGS:
                            return 24;
                        case _GL.MAX_COMBINED_UNIFORM_BLOCKS:
                            return 24;
                        // ── Résiduels fix 3 ───────────────────────────────────
                        case _GL.MAX_COMBINED_TEXTURE_IMAGE_UNITS:
                            return 32;   // attach=32, prod était 70
                        case _GL.MAX_TEXTURE_MAX_ANISOTROPY_EXT:
                            return 16;
                        // ── Fix 3.4 ───────────────────────────────────────────
                        case _GL.MAX_FRAGMENT_UNIFORM_COMPONENTS:
                            return 4096;
                        case _GL.MAX_DRAW_BUFFERS:
                            return 8;
                        case _GL.MAX_COLOR_ATTACHMENTS:
                            return 8;
                        case _GL.MAX_SAMPLES:
                            return 16;
                        default:
                            return Reflect.apply(target, ctx, args);
                    }
                }
            };
            WebGLRenderingContext.prototype.getParameter  = new Proxy(WebGLRenderingContext.prototype.getParameter,  _glProxy);
            WebGL2RenderingContext.prototype.getParameter = new Proxy(WebGL2RenderingContext.prototype.getParameter, _glProxy);

            // ── getContextAttributes() ────────────────────────────────────────
            // BrowserLeaks appelle gl.getContextAttributes() et intègre alpha,
            // antialias, depth, desynchronized… dans le calcul du Report Hash.
            // SwiftShader/Xvfb retourne antialias=false (pas de MSAA hardware).
            // La référence attach (Windows, GPU réel) a antialias=true.
            // On force les attributs exacts de la référence attach.
            const _CTX_ATTRS_TARGET = {
                alpha:                      true,
                antialias:                  true,
                depth:                      true,
                desynchronized:             false,
                failIfMajorPerformanceCaveat: false,
                powerPreference:            'default',
                premultipliedAlpha:         true,
                preserveDrawingBuffer:      false,
                stencil:                    false,
                xrCompatible:              false,
                drawingBufferColorSpace:    'srgb',
                unpackColorSpace:           'srgb',
            };
            const _patchCtxAttrs = (proto) => {
                const _orig = proto.getContextAttributes;
                if (!_orig) return;
                proto.getContextAttributes = function() {
                    const real = _orig.apply(this, arguments);
                    if (!real) return real;
                    return Object.assign({}, real, _CTX_ATTRS_TARGET);
                };
            };
            _patchCtxAttrs(WebGLRenderingContext.prototype);
            _patchCtxAttrs(WebGL2RenderingContext.prototype);

            // ── getShaderPrecisionFormat() ────────────────────────────────────
            // BrowserLeaks condense les valeurs sous "[-2^127,2^127](23)" (VERTEX/FRAGMENT).
            // En SwiftShader, HIGH_FLOAT retourne rangeMin=127, rangeMax=127, precision=23
            // — identique à la référence attach. Pas de divergence connue ici.
            // Le hook ci-dessous garantit la cohérence si SwiftShader divergeait.
            const _PRECISION_TARGET = {
                HIGH_FLOAT:   { rangeMin: 127, rangeMax: 127, precision: 23 },
                MEDIUM_FLOAT: { rangeMin: 15,  rangeMax: 15,  precision: 10 },
                LOW_FLOAT:    { rangeMin: 15,  rangeMax: 15,  precision: 10 },
                HIGH_INT:     { rangeMin: 31,  rangeMax: 30,  precision: 0  },
                MEDIUM_INT:   { rangeMin: 15,  rangeMax: 14,  precision: 0  },
                LOW_INT:      { rangeMin: 15,  rangeMax: 14,  precision: 0  },
            };
            const _patchShaderPrecision = (proto, glConst) => {
                const _origGSPF = proto.getShaderPrecisionFormat;
                if (!_origGSPF) return;
                proto.getShaderPrecisionFormat = function(shaderType, precisionType) {
                    const real = _origGSPF.apply(this, arguments);
                    // Mapper les constantes GL vers les clés du tableau cible
                    const _PT_MAP = {
                        [glConst.HIGH_FLOAT]:   'HIGH_FLOAT',
                        [glConst.MEDIUM_FLOAT]: 'MEDIUM_FLOAT',
                        [glConst.LOW_FLOAT]:    'LOW_FLOAT',
                        [glConst.HIGH_INT]:     'HIGH_INT',
                        [glConst.MEDIUM_INT]:   'MEDIUM_INT',
                        [glConst.LOW_INT]:      'LOW_INT',
                    };
                    const key = _PT_MAP[precisionType];
                    if (!key || !_PRECISION_TARGET[key]) return real;
                    const t = _PRECISION_TARGET[key];
                    // Retourner un objet calqué sur WebGLShaderPrecisionFormat
                    return { rangeMin: t.rangeMin, rangeMax: t.rangeMax, precision: t.precision };
                };
            };
            try {
                // Accéder aux constantes GL via une instance temporaire
                const _tmpCanvas = document.createElement('canvas');
                const _glTmp = _tmpCanvas.getContext('webgl2') || _tmpCanvas.getContext('webgl');
                if (_glTmp) {
                    _patchShaderPrecision(WebGLRenderingContext.prototype,  _glTmp);
                    _patchShaderPrecision(WebGL2RenderingContext.prototype, _glTmp);
                }
            } catch(e) {}

            // ── Extensions WebGL manquantes en prod (fix 3) ────────────────────
            // Comparaison prod vs attach (browserleaks) :
            // WEBGL_lose_context, WEBGL_debug_shaders, WEBGL_debug_renderer_info
            // sont présentes dans les DEUX modes → ne pas les réinjecter.
            // Fix 3.4 : ajout des extensions présentes en attach, absentes en prod.
            const _EXT_INJECT = [
                'WEBGL_provoking_vertex',
                'EXT_render_snorm',
                'EXT_texture_norm16',
                'KHR_parallel_shader_compile',
                'WEBGL_blend_func_extended',
            ];
            // Extensions présentes en prod (SwiftShader) mais absentes en attach → à masquer
            const _EXT_REMOVE = [
                'WEBGL_compressed_texture_astc',
                'WEBGL_compressed_texture_etc',
                'WEBGL_compressed_texture_etc1',
            ];
            // Ordre exact de la liste attach (référence WebGL Report Hash)
            const _EXT_ORDER = [
                'EXT_clip_control','EXT_color_buffer_float','EXT_color_buffer_half_float',
                'EXT_conservative_depth','EXT_depth_clamp','EXT_disjoint_timer_query_webgl2',
                'EXT_float_blend','EXT_polygon_offset_clamp','EXT_render_snorm',
                'EXT_texture_compression_bptc','EXT_texture_compression_rgtc',
                'EXT_texture_filter_anisotropic','EXT_texture_mirror_clamp_to_edge',
                'EXT_texture_norm16','KHR_parallel_shader_compile',
                'NV_shader_noperspective_interpolation','OES_draw_buffers_indexed',
                'OES_sample_variables','OES_shader_multisample_interpolation',
                'OES_texture_float_linear','OVR_multiview2','WEBGL_blend_func_extended',
                'WEBGL_clip_cull_distance','WEBGL_compressed_texture_s3tc',
                'WEBGL_compressed_texture_s3tc_srgb','WEBGL_debug_renderer_info',
                'WEBGL_debug_shaders','WEBGL_lose_context','WEBGL_multi_draw',
                'WEBGL_polygon_mode','WEBGL_provoking_vertex','WEBGL_stencil_texturing',
            ];
            const _patchExtensions = (proto) => {
                const _origGetSupported = proto.getSupportedExtensions;
                proto.getSupportedExtensions = function() {
                    let list = _origGetSupported.apply(this, arguments) || [];
                    // Supprimer les extensions SwiftShader absentes en attach
                    list = list.filter(e => !_EXT_REMOVE.includes(e));
                    // Injecter les extensions manquantes
                    _EXT_INJECT.forEach(e => { if (!list.includes(e)) list.push(e); });
                    // Trier selon l'ordre exact de la référence attach
                    list.sort((a, b) => {
                        const ia = _EXT_ORDER.indexOf(a);
                        const ib = _EXT_ORDER.indexOf(b);
                        if (ia === -1 && ib === -1) return 0;
                        if (ia === -1) return 1;
                        if (ib === -1) return -1;
                        return ia - ib;
                    });
                    return list;
                };
                const _origGetExt = proto.getExtension;
                proto.getExtension = function(name) {
                    if (_EXT_REMOVE.includes(name)) return null;
                    const real = _origGetExt.apply(this, arguments);
                    if (real) return real;
                    // Retourner un objet vide pour les extensions injectées :
                    // présence détectable, comportement neutre.
                    if (_EXT_INJECT.includes(name)) return {};
                    return null;
                };
            };
            _patchExtensions(WebGLRenderingContext.prototype);
            _patchExtensions(WebGL2RenderingContext.prototype);
        } catch(e) {}

        // ── screen dimensions (cohérent avec --window-size=1920,1080) ────────
        try {
            Object.defineProperty(screen, 'width',       { get: () => 1920 });
            Object.defineProperty(screen, 'height',      { get: () => 1080 });
            Object.defineProperty(screen, 'availWidth',  { get: () => 1920 });
            Object.defineProperty(screen, 'availHeight', { get: () => 1040 });
            Object.defineProperty(screen, 'colorDepth',  { get: () => 32 });
            Object.defineProperty(screen, 'pixelDepth',  { get: () => 32 });
        } catch(e) {}

        // ── Hardware hints (cohérents avec un laptop standard) ───────────────
        // deviceMemory DOIT être une puissance de 2 entre 0.25 et 8 (spec W3C).
        // La valeur 1.6 retournée en prod est impossible sur un vrai appareil
        // et constitue un signal bot primaire détectable immédiatement.
        // Attach retourne 16 (non-standard aussi, Chrome le clampe à 8 en lecture).
        // On utilise 8 qui est la valeur max légale et cohérente avec un desktop.
        try {
            Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 4 });
            Object.defineProperty(navigator, 'deviceMemory',        { get: () => 8 });
        } catch(e) {}

        // ── Canvas Fingerprint (Linux → Windows) ─────────────────────────────
        // En prod (Linux/Xvfb), le rendu GPU produit un canvas hash différent
        // de Windows (anti-aliasing, pipeline couleur). BrowserLeaks détecte
        // "GNU/Linux" via la signature canvas alors que l'UA annonce Windows —
        // contradiction immédiate pour tout système anti-bot.
        //
        // Fix : hooker toDataURL, toBlob et getImageData sur le canvas 220×30
        // utilisé par les outils de fingerprinting (dimensions BrowserLeaks).
        // La dataURL de référence doit être extraite depuis le mode attach :
        //   F12 → Console → document.querySelector('canvas').toDataURL()
        // puis collée dans CANVAS_DATA_URL_WINDOWS ci-dessous.
        //
        // Tous les autres canvas (taille ≠ 220×30) passent sans interférence.
        (function() {
            // ⚠️ REMPLACER cette valeur par la dataURL extraite du canvas attach Windows
            // (220×30, 8 bits/sample, truecolor+alpha, hash C8FBD3F8…)
            const CANVAS_DATA_URL_WINDOWS = "data:image/png;base64,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";

            // Retourne true si le canvas correspond aux dimensions du test fingerprinting
            function _isFingerprint(canvas) {
                return canvas.width === 220 && canvas.height === 30;
            }

            // Hook toDataURL : retourne la référence Windows pour le canvas 220×30
            const _origToDataURL = HTMLCanvasElement.prototype.toDataURL;
            HTMLCanvasElement.prototype.toDataURL = function(type, quality) {
                if (_isFingerprint(this) && CANVAS_DATA_URL_WINDOWS !== "REPLACE_WITH_ATTACH_CANVAS_DATAURL") {
                    return CANVAS_DATA_URL_WINDOWS;
                }
                return _origToDataURL.apply(this, arguments);
            };

            // Hook toBlob : convertit la dataURL de référence en Blob
            const _origToBlob = HTMLCanvasElement.prototype.toBlob;
            HTMLCanvasElement.prototype.toBlob = function(callback, type, quality) {
                if (_isFingerprint(this) && CANVAS_DATA_URL_WINDOWS !== "REPLACE_WITH_ATTACH_CANVAS_DATAURL") {
                    // Décoder la dataURL base64 → Uint8Array → Blob
                    const parts = CANVAS_DATA_URL_WINDOWS.split(",");
                    const mime  = (parts[0].match(/:(.*?);/) || [])[1] || "image/png";
                    const bin   = atob(parts[1] || "");
                    const buf   = new Uint8Array(bin.length);
                    for (let i = 0; i < bin.length; i++) buf[i] = bin.charCodeAt(i);
                    callback(new Blob([buf], { type: mime }));
                    return;
                }
                return _origToBlob.apply(this, arguments);
            };

            // Hook getImageData : retourner des pixels cohérents avec le rendu Windows
            // Seuls les appels sur le canvas 220×30 sont interceptés ; on retourne
            // un ImageData plat (RGBA=0) de même dimension pour ne pas déclencher
            // d'erreur tout en cassant l'entropie du hash Linux.
            const _origGetContext = HTMLCanvasElement.prototype.getContext;
            HTMLCanvasElement.prototype.getContext = function(contextType, contextAttributes) {
                const ctx = _origGetContext.apply(this, arguments);
                if (!ctx || contextType !== "2d" || !_isFingerprint(this)) return ctx;

                const _origGetImageData = ctx.getImageData.bind(ctx);
                ctx.getImageData = function(sx, sy, sw, sh) {
                    if (CANVAS_DATA_URL_WINDOWS === "REPLACE_WITH_ATTACH_CANVAS_DATAURL") {
                        return _origGetImageData(sx, sy, sw, sh);
                    }
                    // Retourner un ImageData vide de même dimension : hash neutre
                    return new ImageData(sw, sh);
                };
                return ctx;
            };
        })();

        // ── Audio Sample Rate (fix 4) ─────────────────────────────────────────
        // En prod (Linux), AudioContext.sampleRate retourne 44100.
        // En attach (Windows), il retourne 48000.
        // Ce signal est détectable via AudioContext fingerprinting.
        // On surcharge le constructeur AudioContext pour forcer 48000.
        try {
            const _OrigAudioContext = window.AudioContext || window.webkitAudioContext;
            if (_OrigAudioContext) {
                const _PatchedAudioContext = function(...args) {
                    const ctx = new _OrigAudioContext(...args);
                    Object.defineProperty(ctx, 'sampleRate', { get: () => 48000 });
                    return ctx;
                };
                // Préserver la chaîne prototype pour les instanceof checks
                _PatchedAudioContext.prototype = _OrigAudioContext.prototype;
                window.AudioContext = window.webkitAudioContext = _PatchedAudioContext;
            }
        } catch(e) {}
    """ + ("""
        // ── WebRTC suppression (prod uniquement) ────────────────────────────
        // Fallback JS : supprime RTCPeerConnection si les flags Chrome ne
        // suffisent pas à bloquer le STUN/ICE sur ce build.
        try {
            Object.defineProperty(window, 'RTCPeerConnection',       { value: undefined, writable: false });
            Object.defineProperty(window, 'webkitRTCPeerConnection', { value: undefined, writable: false });
        } catch(e) {}
    """ if not IS_LOCAL else "")


def apply_fingerprint_overrides_cdp(driver) -> None:
    """
    Injecte le JS de spoofing fingerprint via CDP Selenium.

    Utilise Page.addScriptToEvaluateOnNewDocument : le script est exécuté
    avant tout autre JS pour TOUTES les navigations futures du processus Chrome,
    indépendamment de qui navigue (Playwright ou Selenium).

    Doit être appelé une seule fois, juste après l'attach Selenium et avant
    tout driver.get().
    """
    # Surcharge User-Agent via CDP : élimine "HeadlessChrome" et "Linux x86_64"
    # qui sont les signaux de détection les plus triviaux des anti-bots.
    # Doit correspondre à un vrai Chrome Windows pour rester cohérent avec
    # les patches JS (platform=Win32, screen=1920x1080).
    try:
        import re as _re
        raw_ua = driver.execute_script("return navigator.userAgent") or ""
        # Remplacer HeadlessChrome → Chrome, supprimer "X11; " et "Linux x86_64" → Windows NT
        spoofed_ua = _re.sub(r"HeadlessChrome", "Chrome", raw_ua)
        spoofed_ua = _re.sub(r"X11;\s*", "", spoofed_ua)
        spoofed_ua = _re.sub(r"Linux x86_64", "Windows NT 10.0; Win64; x64", spoofed_ua)
        # Forcer la version Chrome à 149 pour cohérence avec uaFullVersion/fullVersionList
        spoofed_ua = _re.sub(r"Chrome/\d+\.", "Chrome/149.", spoofed_ua)
        driver.execute_cdp_cmd("Network.setUserAgentOverride", {
            "userAgent": spoofed_ua,
            "platform": "Win32",
        })
        log.info("[FP][CDP] User-Agent surchargé : %s", spoofed_ua)
    except Exception as e:
        log.warning("[FP][CDP][WARN] Échec User-Agent override : %s", e)

    # ── PATCH Client Hints : Emulation.setUserAgentOverride + userAgentMetadata ──
    # Network.setUserAgentOverride ne transmet pas userAgentMetadata → Chrome
    # cesse d'émettre automatiquement les low-entropy Client Hints (sec-ch-ua,
    # sec-ch-ua-mobile, sec-ch-ua-platform). Emulation.setUserAgentOverride
    # avec userAgentMetadata complet restaure leur émission automatique.
    _UA_CH_STRING = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/149.0.0.0 Safari/537.36"
    )
    _UA_CH_METADATA = {
        "brands": [
            {"brand": "Google Chrome",  "version": "149"},
            {"brand": "Chromium",       "version": "149"},
            {"brand": "Not)A;Brand",    "version": "24"},
        ],
        "fullVersionList": [
            {"brand": "Google Chrome",  "version": "149.0.7827.103"},
            {"brand": "Chromium",       "version": "149.0.7827.103"},
            {"brand": "Not)A;Brand",    "version": "24.0.0.0"},
        ],
        "platform":        "Windows",
        "platformVersion": "19.0.0",
        "architecture":    "x86",
        "model":           "",
        "bitness":         "64",
        "wow64":           False,
        "mobile":          False,
    }
    try:
        driver.execute_cdp_cmd("Emulation.setUserAgentOverride", {
            "userAgent":         _UA_CH_STRING,
            "acceptLanguage":    "en-US,en;q=0.9",
            "platform":          "Win32",
            "userAgentMetadata": _UA_CH_METADATA,
        })
        log.info("[FP][CDP] Emulation.setUserAgentOverride OK — Client Hints low-entropy restaures.")
    except Exception as e:
        print(f"[FP][CDP][ERROR] Emulation.setUserAgentOverride echoue : {e!r}")
        log.warning("[FP][CDP][WARN] Emulation.setUserAgentOverride echoue (non bloquant) : %s", e)
    # ── FIN PATCH Client Hints ────────────────────────────────────────────────

    try:
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": _fingerprint_js()}
        )
        log.info("[FP][CDP] Fingerprint overrides enregistres via CDP.")
    except Exception as e:
        log.warning("[FP][CDP][WARN] Echec enregistrement fingerprint CDP : %s", e)


def _detect_chrome_major_version(chrome_bin: str) -> int | None:
    """Retourne le numéro de version majeure de Chrome (ex: 145), ou None si échec."""
    import subprocess, re, sys

    def _extract(text):
        m = re.search(r"(\d+)\.\d+\.\d+", text)
        return int(m.group(1)) if m else None

    # Méthode 1 : PowerShell (Windows — fiable)
    if sys.platform == "win32":
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f"(Get-Item '{chrome_bin}').VersionInfo.FileVersion"],
                capture_output=True, text=True, timeout=8
            )
            v = _extract(result.stdout.strip())
            if v:
                return v
        except Exception:
            pass

    # Méthode 2 : --version (Linux/Mac)
    try:
        result = subprocess.run(
            [chrome_bin, "--version"],
            capture_output=True, text=True, timeout=5
        )
        v = _extract(result.stdout + result.stderr)
        if v:
            return v
    except Exception:
        pass

    return None


def _start_proxy_relay(proxy_server: str, proxy_user: str, proxy_pass: str, bind_host: str = "127.0.0.1"):
    """
    Relay HTTP CONNECT en Python pur (sans dépendance externe).
    Écoute sur bind_host:<local_port>, intercepte les requêtes CONNECT de Chrome,
    et les relaie vers le proxy ISP upstream avec Proxy-Authorization: Basic.
    Chrome reçoit --proxy-server=http://<bind_host>:<local_port> sans credentials.
    Retourne (relay_handle, local_port) — relay_handle a une méthode terminate().
    """
    import socket
    import threading
    import base64

    parsed = urlparse(proxy_server if "://" in proxy_server else "http://" + proxy_server)
    proxy_host = parsed.hostname
    proxy_port = parsed.port or 8080

    local_port = random.randint(34000, 44000)
    auth_b64 = base64.b64encode(f"{proxy_user}:{proxy_pass}".encode()).decode()
    stop_event = threading.Event()
    _ready_event = threading.Event()

    def _pipe(src: socket.socket, dst: socket.socket) -> None:
        try:
            while not stop_event.is_set():
                data = src.recv(65536)
                if not data:
                    break
                dst.sendall(data)
        except Exception:
            pass
        finally:
            try:
                dst.shutdown(socket.SHUT_WR)
            except Exception:
                pass

    def _handle(client_sock: socket.socket) -> None:
        upstream_sock = None
        try:
            # Lire la requête complète de Chrome (CONNECT ou HTTP directe)
            buf = b""
            while b"\r\n\r\n" not in buf:
                chunk = client_sock.recv(4096)
                if not chunk:
                    return
                buf += chunk

            first_line = buf.split(b"\r\n", 1)[0]  # ex: b"CONNECT host:443 HTTP/1.1"

            # Connexion TCP vers le proxy ISP upstream
            upstream_sock = socket.create_connection((proxy_host, proxy_port), timeout=15)

            if first_line.upper().startswith(b"CONNECT "):
                # ── Tunnel HTTPS (CONNECT) ───────────────────────────────────
                connect_req = (
                    first_line + b"\r\n"
                    + b"Proxy-Authorization: Basic " + auth_b64.encode() + b"\r\n"
                    + b"\r\n"
                )
                upstream_sock.sendall(connect_req)

                # Lire la réponse upstream (ex: "HTTP/1.1 200 Connection established")
                resp = b""
                while b"\r\n\r\n" not in resp:
                    chunk = upstream_sock.recv(4096)
                    if not chunk:
                        break
                    resp += chunk

                # Transmettre la réponse à Chrome
                client_sock.sendall(resp)
            else:
                # ── Requête HTTP directe (non-CONNECT) ──────────────────────
                # Sur Windows, Chrome émet des GET/POST directs au démarrage
                # (update, sync, NTP). Injecter Proxy-Authorization après la
                # première ligne et transmettre au proxy upstream.
                first_line_end = buf.index(b"\r\n")
                req_with_auth = (
                    buf[:first_line_end + 2]
                    + b"Proxy-Authorization: Basic " + auth_b64.encode() + b"\r\n"
                    + buf[first_line_end + 2:]
                )
                upstream_sock.sendall(req_with_auth)

            # Relay bidirectionnel (CONNECT : après établissement du tunnel ;
            # HTTP directe : relaie la réponse upstream → Chrome)
            t1 = threading.Thread(target=_pipe, args=(client_sock, upstream_sock), daemon=True)
            t2 = threading.Thread(target=_pipe, args=(upstream_sock, client_sock), daemon=True)
            t1.start()
            t2.start()
        except Exception:
            if upstream_sock:
                try:
                    upstream_sock.close()
                except Exception:
                    pass
            try:
                client_sock.close()
            except Exception:
                pass

    def _serve() -> None:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((bind_host, local_port))
        srv.listen(64)
        # Signal readiness exactly here: OS will queue incoming connections from now on.
        _ready_event.set()
        srv.settimeout(1.0)
        try:
            while not stop_event.is_set():
                try:
                    client_sock, _ = srv.accept()
                    threading.Thread(target=_handle, args=(client_sock,), daemon=True).start()
                except socket.timeout:
                    continue
                except Exception:
                    break
        finally:
            srv.close()

    threading.Thread(target=_serve, daemon=True).start()

    class _RelayHandle:
        def terminate(self) -> None:
            stop_event.set()

    # Phase 1 — attendre que listen() soit effectif (Event positionné par _serve).
    # Garantit que l'OS peut désormais mettre des connexions en file d'attente.
    if not _ready_event.wait(timeout=5.0):
        stop_event.set()
        raise RuntimeError(
            f"[RELAY] listen() n'a pas abouti sur le port {local_port} après 5s"
        )

    # Phase 2 — sonde TCP active : vérifier que le relay reçoit effectivement
    # des connexions (et non que le thread est mort juste après listen()).
    # Budget explicite : 20 tentatives × 50 ms = 1 s max.
    _PROBE_HOST = "127.0.0.1"
    _PROBE_MAX = 20
    for _attempt in range(_PROBE_MAX):
        try:
            with socket.create_connection((_PROBE_HOST, local_port), timeout=0.3):
                break
        except OSError:
            if _attempt == _PROBE_MAX - 1:
                stop_event.set()
                raise RuntimeError(
                    f"[RELAY] relay non joignable sur {_PROBE_HOST}:{local_port} "
                    f"après {_PROBE_MAX} tentatives"
                )
            time.sleep(0.05)

    log.info("[LAUNCH][RELAY] relay HTTP CONNECT prêt sur port %d → %s:%d",
             local_port, proxy_host, proxy_port)
    return _RelayHandle(), local_port


def launch_browser(config: dict | None = None):
    """
    Chemin unique : subprocess.Popen lance Chrome, puis Selenium s'attache via
    debuggerAddress — identique en local et en prod.
    Exception : ATTACH_DEBUGGER_ADDRESS (attach externe) reste inchangé.
    """
    chrome_bin = _detect_chrome_binary()

    attach_addr = os.getenv("ATTACH_DEBUGGER_ADDRESS", "").strip()
    if attach_addr:
        print(f"⚠️ ATTACH MODE → {attach_addr}")
        opts = webdriver.ChromeOptions()
        opts.add_experimental_option("debuggerAddress", attach_addr)
        opts.page_load_strategy = "eager"
        return webdriver.Chrome(options=opts, service=Service(log_output=subprocess.DEVNULL))

    proxy_server, proxy_user, proxy_pass = _parse_proxy_env(config)
    headless = _want_headless()

    # Port remote debugging (Selenium va s'attacher dessus)
    debug_port = int(os.getenv("REMOTE_DEBUG_PORT", 0)) or random.randint(42000, 52000)
    debug_address = os.getenv("REMOTE_DEBUG_ADDRESS", "").strip()

    # Profil isolé (évite collisions + garde la session propre)
    # Si ACCOUNT_ID + DATABASE_URL sont définis, on utilise un répertoire fixe et
    # on charge le profil persisté depuis Postgres (anti-bot : évite le profil vierge).
    # Sinon : comportement original (mkdtemp éphémère, ou %TEMP% pour Chrome Windows).
    _persist_account_id = os.getenv("ACCOUNT_ID", "").strip()
    _persist_db_url = os.getenv("DATABASE_URL", "").strip()

    if _persist_account_id and _persist_db_url:
        user_data_dir = f"/tmp/chrome_profile_{_persist_account_id}"
        os.makedirs(user_data_dir, exist_ok=True)
        from preselection.chrome_profile_store import load_profile
        load_profile(_persist_account_id, user_data_dir)
    elif ".exe" in chrome_bin.lower():
        # Si chrome_bin est un binaire Windows, créer le profil sous %TEMP% Windows natif
        # (wslpath -w produit un chemin UNC \\wsl.localhost\... rejeté par Chrome comme profil).
        try:
            win_temp = subprocess.check_output(
                ["cmd.exe", "/c", "echo %TEMP%"], text=True
            ).strip()
            import uuid
            user_data_dir = win_temp + "\\chrome_profile_" + uuid.uuid4().hex[:8]
            os.makedirs(user_data_dir, exist_ok=True)
        except Exception:
            user_data_dir = tempfile.mkdtemp(prefix="chrome_profile_")
    else:
        user_data_dir = tempfile.mkdtemp(prefix="chrome_profile_")

    print(f"[LAUNCH] chrome_bin={chrome_bin}")
    print(f"[LAUNCH] headless={headless}")
    print(f"[LAUNCH] debug_port={debug_port}")
    print(f"[LAUNCH] user_data_dir={user_data_dir}")

    if proxy_server:
        log.info("[LAUNCH][PROXY] server=%s user=%s pass=%s",
                 proxy_server, "yes" if proxy_user else "no", "yes" if proxy_pass else "no")
    else:
        log.info("[LAUNCH][PROXY] aucun proxy (PROXY_URL vide)")

    locale, tz = _parse_locale_tz_env()
    print(f"[LAUNCH][LOCALE] {locale}  [LAUNCH][TZ] {tz}")

    # ── Arguments Chrome ──────────────────────────────────────────────────────
    # NOTE: --disable-gpu SUPPRIMÉ volontairement.
    #   Avec Xvfb (DISPLAY=:99), Chrome tourne en mode headed sur un écran
    #   virtuel et n'a pas besoin de désactiver le GPU.
    #   --disable-gpu forçait SwiftShader comme renderer WebGL, une signature
    #   de bot détectée par ThreatMetrix/Datadome dès le premier chargement.
    cmd = [
        chrome_bin,
        f"--remote-debugging-port={debug_port}",
        "--remote-debugging-allow-origins=*",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-dev-shm-usage",
        *( ["--no-sandbox"] if (not hasattr(os, "getuid") or os.getuid() == 0) else [] ),
        # ── Réseau interne Chrome — neutralisation complète ────────────────────
        # Ces connexions surviennent dès le lancement, avant le premier driver.get(),
        # et saturent/contournent le relay proxy, déclenchant la popup d'auth native.
        "--disable-background-networking",       # déjà présent — base
        "--disable-component-update",            # déjà présent — base
        "--disable-sync",                        # Google Sync (contacts, bookmarks…)
        "--no-pings",                            # hyperlink auditing pings
        "--disable-domain-reliability",          # rapports d'erreurs réseau → Google
        "--disable-client-side-phishing-detection",  # modèle ML local, pas de réseau
        "--safebrowsing-disable-auto-update",    # stoppe le téléchargement des listes Safe Browsing
        "--disable-features=Translate,OptimizationHints,SafeBrowsingProtections,"
            "SafeBrowsingRealTimeUrlLookupEnabled,ChromeWhatsNewUI,"
            "NetworkService,MediaRouter,DialMediaRouteProvider",
        # ── NTP / background fetch ────────────────────────────────────────────
        "--ash-no-nudges",                       # supprime les popups Ash (ChromeOS no-op sur Linux)
        "--disable-ntp-most-likely-favicons-from-server",  # NTP : pas de fetch favicon
        "--disable-search-engine-choice-screen",  # pas de requête réseau au démarrage
        # ── Extensions & notifications (pas de connexion background) ─────────
        "--disable-extensions",
        "--disable-notifications",
        # ── Anti-fingerprint / automation ────────────────────────────────────
        # NOTE IMPORTANTE : --disable-blink-features=AutomationControlled est
        # intentionnellement ABSENT de la ligne de commande.
        #
        # Ce flag est contre-productif : il supprime navigator.webdriver=true
        # mais Chrome >= 112 affiche une banniere "You are using an unsupported
        # command-line flag" qui est elle-meme un signal d'automation primaire
        # (visible en screenshot, detecte par les SDK anti-bot).
        #
        # La suppression de navigator.webdriver est assuree exclusivement par
        # apply_fingerprint_overrides_cdp() via Page.addScriptToEvaluateOnNewDocument
        # (patch sur Navigator.prototype avant tout JS de la page).
        # Idem pour les proprietes cdc_* de ChromeDriver.
        "--window-size=1920,1080",
        "--lang=en-US",  # aligné sur navigator.language = 'en-US' (cohérence JS ↔ HTTP headers)
    ]

    # En prod (Fly.io), désactiver WebRTC au niveau Chrome pour éliminer toute
    # fuite d'IP datacenter ou locale via le handshake ICE/STUN.
    # Non appliqué en local : comportement natif conservé pour éviter toute
    # divergence de profil Chrome entre local et prod.
    if not IS_LOCAL:
        cmd += [
            "--disable-features=WebRTC",
            "--enforce-webrtc-ip-permission-check",
            "--webrtc-ip-handling-policy=disable_non_proxied_udp",
        ]

    relay_proc = None
    if proxy_server and proxy_user and proxy_pass:
        # Relay local pproxy : Chrome reçoit un proxy sans credentials 
        # Chrome Windows (WSL) ne peut pas atteindre 127.0.0.1 WSL — on bind sur 0.0.0.0
        # et on passe l'IP du bridge WSL (hostname -I) à la place de 127.0.0.1.
        if ".exe" in chrome_bin.lower():
            try:
                wsl_ip = subprocess.check_output(["hostname", "-I"], text=True).strip().split()[0]
            except Exception:
                wsl_ip = "127.0.0.1"
            relay_proc, local_port = _start_proxy_relay(proxy_server, proxy_user, proxy_pass, bind_host="0.0.0.0")
            cmd.append(f"--proxy-server=http://{wsl_ip}:{local_port}")
        else:
            relay_proc, local_port = _start_proxy_relay(proxy_server, proxy_user, proxy_pass)
            cmd.append(f"--proxy-server=http://127.0.0.1:{local_port}")
    elif proxy_server:
        # Proxy sans auth : on passe directement
        cmd.append(f"--proxy-server={proxy_server}")

    if debug_address:
        cmd.append(f"--remote-debugging-address={debug_address}")

    if headless:
        # Fallback si Xvfb indisponible (ex: test local sans DISPLAY).
        # En prod normale, DISPLAY=:99 est positionné par entrypoint.sh
        # et headless=False → ce bloc ne s'exécute pas.
        cmd.append("--headless=new")
    elif os.environ.get("DISPLAY") and ".exe" not in chrome_bin.lower():
        # Mode Xvfb (prod Linux) : activer WebGL via ANGLE/SwiftShader.
        # Sans GPU physique, Chrome ne crée pas de contexte WebGL sans ces flags.
        cmd.extend(["--use-gl=angle", "--use-angle=swiftshader"])

    # ── Env subprocess : TZ pour la timezone ─────────────────────────────────
    proc_env = os.environ.copy()
    proc_env["TZ"] = tz

    _LOCK_FILES = ["SingletonLock", "lockfile", "CrashpadMetrics-active.pma"]
    for _lf in _LOCK_FILES:
        _lf_path = os.path.join(user_data_dir, _lf)
        if os.path.exists(_lf_path):
            try:
                os.remove(_lf_path)
                print(f"[LAUNCH] Lock file supprimé: {_lf}")
            except Exception as _e:
                print(f"[LAUNCH][WARN] Impossible de supprimer {_lf}: {_e}")

    # --- 1) Lancer Chrome via subprocess.Popen ---
    import threading as _threading
    chrome_proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        env=proc_env,
        start_new_session=True
    )
    print(f"[LAUNCH] Chrome PID={chrome_proc.pid}")

    # Drain stderr en thread daemon : évite le blocage pipe-buffer et rend
    # les messages Chrome (OOM, crash, profil lock) visibles en cas de mort.
    _stderr_lines: list[str] = []

    def _drain_stderr(proc):
        try:
            for raw in proc.stderr:
                _stderr_lines.append(raw.decode(errors="replace").rstrip())
        except Exception:
            pass

    _threading.Thread(target=_drain_stderr, args=(chrome_proc,), daemon=True).start()

    # --- Relay socat : expose le debug port sur 0.0.0.0 ---
    # (Playwright forçait Chrome sur 127.0.0.1 ; subprocess respecte
    #  --remote-debugging-address mais socat reste utile en Docker)
    if debug_address == "0.0.0.0":
        relay_port = debug_port + 1
        subprocess.Popen(
            ["socat",
             f"TCP-LISTEN:{relay_port},fork,reuseaddr,bind=0.0.0.0",
             f"TCP:127.0.0.1:{debug_port}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
        )
        print(f"[LAUNCH] socat relay 0.0.0.0:{relay_port} → 127.0.0.1:{debug_port}")

    # --- 2) Attacher Selenium au Chrome déjà lancé ---
    # Attendre que Chrome expose son debug port (jusqu'à 60s).
    # À chaque itération : vérifier que Chrome est toujours vivant avant de réessayer.
    import urllib.request
    for attempt in range(120):
        ret = chrome_proc.poll()
        if ret is not None:
            stderr_tail = "\n".join(_stderr_lines[-30:])
            print(f"[LAUNCH][FATAL] Chrome mort (code={ret}) après {attempt * 0.5:.1f}s.\nstderr:\n{stderr_tail}")
            raise RuntimeError(f"Chrome a quitté avec code={ret}")
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{debug_port}/json", timeout=1)
            print(f"[LAUNCH] Debug port prêt après {attempt * 0.5:.1f}s")
            break
        except Exception:
            time.sleep(0.5)
    else:
        stderr_tail = "\n".join(_stderr_lines[-30:])
        print(f"[LAUNCH][WARN] Debug port toujours indisponible après 60s.\nstderr:\n{stderr_tail}")

    opts = webdriver.ChromeOptions()
    opts.add_experimental_option("debuggerAddress", f"127.0.0.1:{debug_port}")
    opts.page_load_strategy = "eager"
    driver = webdriver.Chrome(options=opts, service=Service(log_output=subprocess.DEVNULL))

    # Fingerprint spoofing via CDP Selenium.
    # Injecté AVANT toute navigation pour que le script soit actif dès la première
    # vraie navigation. Page.addScriptToEvaluateOnNewDocument persiste pour
    # toutes les navigations futures du processus Chrome.
    apply_fingerprint_overrides_cdp(driver)
    print("[LAUNCH][OVERRIDE] Fingerprint overrides enregistrés via CDP Selenium.")

    # ── Suppression immédiate du flag d'automation sur la page COURANTE ────────
    # Page.addScriptToEvaluateOnNewDocument ne couvre PAS la page déjà chargée
    # au moment de l'attach Selenium. On applique les patches critiques de façon
    # synchrone via execute_script pour éliminer les signaux visibles immédiatement.
    #
    # 1) navigator.webdriver → undefined  (sur Navigator.prototype pour couvrir
    #    les checks via Object.getOwnPropertyDescriptor)
    # 2) Suppression des propriétés cdc_* injectées par ChromeDriver dans window
    #    (cdc_adoQpoasnfa76pfcZLmcfl_Array, cdc_adoQpoasnfa76pfcZLmcfl_Promise…)
    try:
        driver.execute_script("""
            // Patch navigator.webdriver sur le prototype (robuste)
            try {
                Object.defineProperty(Navigator.prototype, 'webdriver', {
                    get: () => undefined,
                    configurable: true,
                    enumerable: true,
                });
            } catch(e) {}

            // Supprimer les propriétés cdc_* de ChromeDriver dans window
            try {
                for (const key of Object.getOwnPropertyNames(window)) {
                    if (key.startsWith('cdc_')) {
                        try { delete window[key]; } catch(e) {}
                        try { Object.defineProperty(window, key, { get: () => undefined, configurable: true }); } catch(e) {}
                    }
                }
            } catch(e) {}

            // Supprimer $chrome_asyncScriptInfo et $cdc_asdjflasutopfhvcZLmcfl_
            // (variantes selon version ChromeDriver)
            const _legacyKeys = [
                '$chrome_asyncScriptInfo',
                '$cdc_asdjflasutopfhvcZLmcfl_',
            ];
            for (const k of _legacyKeys) {
                try { delete window[k]; } catch(e) {}
                try { Object.defineProperty(window, k, { get: () => undefined, configurable: true }); } catch(e) {}
            }

            // Patch deviceMemory et hardwareConcurrency sur la page courante
            // (Page.addScriptToEvaluateOnNewDocument ne couvre pas la page déjà chargée)
            try {
                Object.defineProperty(navigator, 'deviceMemory',        { get: () => 8 });
                Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 4  });
            } catch(e) {}
        """)
        log.info("[FP][IMMEDIATE] Flag automation supprimé sur la page courante.")
    except Exception as e:
        log.warning("[FP][IMMEDIATE][WARN] Échec suppression flag automation : %s", e)

    # ── Dump fingerprint POST-spoofing ───────────────────────────────────────
    # Page.addScriptToEvaluateOnNewDocument ne s'applique qu'aux navigations
    # futures, pas à la page déjà chargée au moment de l'attach Selenium.
    # On force une navigation about:blank pour que tous les overrides (userAgentData,
    # platform, plugins, WebGL…) soient actifs avant de lire les valeurs.
    # Ce dump reflète exactement ce que les sites tiers verront.
    try:
        driver.get("about:blank")
        fingerprint = driver.execute_script("""
            const uad = navigator.userAgentData;
            return {
                language: navigator.language,
                languages: navigator.languages,
                timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
                platform: navigator.platform,
                webdriver: navigator.webdriver,
                userAgent: navigator.userAgent,
                geolocation: !!navigator.geolocation,
                userAgentData: uad ? {
                    platform: uad.platform,
                    mobile:   uad.mobile,
                    brands:   uad.brands
                } : null
            };
        """)
        print("[FP][BROWSER]", json.dumps(fingerprint, indent=2))
    except Exception as e:
        print("[FP][ERROR]", e)

    # Attacher le processus Chrome et le profil au driver pour nettoyage dans main.py
    driver._chrome_proc = chrome_proc
    driver._chrome_user_data_dir = user_data_dir
    if relay_proc is not None:
        driver._proxy_relay_proc = relay_proc

    # Pause manuelle en local non-unattended : permet la navigation préalable avant
    # que le bot prenne la main. Skippé si LOCAL_UNATTENDED=1 ou en prod (IS_LOCAL=False).
    if IS_LOCAL and os.getenv("LOCAL_UNATTENDED", "") != "1":
        print(
            f"\n[LAUNCH] Chrome lancé sur port {debug_port}.\n"
            f"  → Navigue manuellement vers la page cible dans Chrome.\n"
            f"  → Appuie sur Entrée ici quand tu es prêt à continuer."
        )
        input("[LAUNCH] Appuie sur Entrée pour continuer... ")

    return driver