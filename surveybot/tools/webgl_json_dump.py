"""
webgl_json_dump.py
------------------
Capture le JSON dump WebGL de browserleaks.com/webgl depuis la machine Fly.io
(mode prod, avec tous les overrides fingerprint actifs).

Sauvegarde : /tmp/prod_webgl_dump.json

Usage (depuis la machine Fly.io après SSH) :
  DISPLAY=:99 PROXY_URL="..." PROXY_USER="..." PROXY_PASS="..." \\
  ACCOUNT_ID=topsurveys_bot_001 python tools/webgl_json_dump.py
"""

import json
import os
import sys
import time

WEBGL_URL      = "https://browserleaks.com/webgl"
WAIT_AFTER_LOAD = 8   # browserleaks charge ses données en XHR, laisser le temps


def _extract_webgl_json(driver) -> dict:
    """
    Extrait le dump WebGL complet depuis le contexte de la page.

    Stratégie 1 : lire le href data: URI du bouton "JSON Dump" de BrowserLeaks
                  (le plus fidèle — produit exactement le même JSON que le bouton).
    Stratégie 2 : reconstruire via JS getParameter sur deux canvas indépendants
                  (webgl2 + webgl1), en reproduisant la structure du dump attach :
                  {"webgl2": {...}, "webgl": {...}}.

    La structure de sortie est identique dans les deux cas afin de permettre
    une comparaison directe avec le dump attach.
    """

    # ── Stratégie 1 : data URI du bouton JSON Dump ───────────────────────────────
    json_href = driver.execute_script("""
        const links = Array.from(document.querySelectorAll('a[href^="data:application/json"]'));
        return links.length > 0 ? links[0].href : null;
    """)

    if json_href and json_href.startswith("data:application/json"):
        from urllib.parse import unquote
        prefix = "data:application/json;charset=utf-8,"
        if json_href.startswith(prefix):
            raw = unquote(json_href[len(prefix):])
            try:
                return {"source": "data_uri", "data": json.loads(raw)}
            except json.JSONDecodeError as e:
                print(f"[WGL] JSON decode error sur data URI : {e}")

    print("[WGL] Stratégie 1 (data URI) échouée — reconstruction JS getParameter")

    # ── Stratégie 2 : reconstruction manuelle, structure {"webgl2":{}, "webgl":{}} ─
    raw = driver.execute_script("""
        // ── Helper : lit un getParameter et normalise les typed arrays ───────────
        function _get(gl, pname) {
            try {
                const v = gl.getParameter(pname);
                if (v === null || v === undefined) return v;
                if (v && v.constructor && v.constructor.name.includes('Array')) return Array.from(v);
                return v;
            } catch(e) { return null; }
        }

        // ── Helper : shader precision ─────────────────────────────────────────────
        function _shaderPrecision(gl) {
            const SHADER_TYPES    = [gl.VERTEX_SHADER, gl.FRAGMENT_SHADER];
            const PRECISION_TYPES = ['LOW_FLOAT','MEDIUM_FLOAT','HIGH_FLOAT','LOW_INT','MEDIUM_INT','HIGH_INT'];
            const out = {};
            for (const st of SHADER_TYPES) {
                const stName = (st === gl.VERTEX_SHADER) ? 'VERTEX' : 'FRAGMENT';
                out[stName] = {};
                for (const pt of PRECISION_TYPES) {
                    try {
                        const sp = gl.getShaderPrecisionFormat(st, gl[pt]);
                        out[stName][pt] = sp
                            ? { rangeMin: sp.rangeMin, rangeMax: sp.rangeMax, precision: sp.precision }
                            : null;
                    } catch(e) { out[stName][pt] = null; }
                }
            }
            return out;
        }

        // ── Helper : dump complet d'un contexte (webgl ou webgl2) ────────────────
        function _dumpContext(gl, isGL2) {
            const r = {};

            // Chaînes de version
            r['VERSION']                  = _get(gl, gl.VERSION);
            r['SHADING_LANGUAGE_VERSION'] = _get(gl, gl.SHADING_LANGUAGE_VERSION);
            r['VENDOR']                   = _get(gl, gl.VENDOR);
            r['RENDERER']                 = _get(gl, gl.RENDERER);

            // Paramètres communs WebGL1 + WebGL2
            const COMMON = {
                MAX_VERTEX_ATTRIBS:               0x8869,
                MAX_VERTEX_UNIFORM_VECTORS:       0x8DFB,
                MAX_VERTEX_TEXTURE_IMAGE_UNITS:   0x8B4C,
                MAX_VARYING_VECTORS:              0x8DFC,
                ALIASED_LINE_WIDTH_RANGE:         0x846E,
                ALIASED_POINT_SIZE_RANGE:         0x846D,
                MAX_FRAGMENT_UNIFORM_VECTORS:     0x8DFD,
                MAX_TEXTURE_IMAGE_UNITS:          0x8872,
                MAX_RENDERBUFFER_SIZE:            0x84E8,
                MAX_VIEWPORT_DIMS:                0x0D3A,
                RED_BITS:                         0x0D52,
                GREEN_BITS:                       0x0D53,
                BLUE_BITS:                        0x0D54,
                ALPHA_BITS:                       0x0D55,
                DEPTH_BITS:                       0x0D56,
                STENCIL_BITS:                     0x0D57,
                MAX_TEXTURE_SIZE:                 0x0D33,
                MAX_CUBE_MAP_TEXTURE_SIZE:        0x851C,
                MAX_COMBINED_TEXTURE_IMAGE_UNITS: 0x8B4D,
            };
            for (const [name, pname] of Object.entries(COMMON)) r[name] = _get(gl, pname);

            // Paramètres exclusifs WebGL2
            if (isGL2) {
                const WGL2 = {
                    MAX_VERTEX_UNIFORM_COMPONENTS:                0x8B4A,
                    MAX_VERTEX_UNIFORM_BLOCKS:                    0x8A2B,
                    MAX_VERTEX_OUTPUT_COMPONENTS:                 0x9122,
                    MAX_VARYING_COMPONENTS:                       0x8B4B,
                    MAX_TRANSFORM_FEEDBACK_INTERLEAVED_COMPONENTS:0x8C8A,
                    MAX_TRANSFORM_FEEDBACK_SEPARATE_ATTRIBS:     0x8C8B,
                    MAX_TRANSFORM_FEEDBACK_SEPARATE_COMPONENTS:  0x8C80,
                    MAX_FRAGMENT_UNIFORM_COMPONENTS:              0x8B49,
                    MAX_FRAGMENT_UNIFORM_BLOCKS:                  0x8A2D,
                    MAX_FRAGMENT_INPUT_COMPONENTS:                0x9125,
                    MIN_PROGRAM_TEXEL_OFFSET:                     0x8904,
                    MAX_PROGRAM_TEXEL_OFFSET:                     0x8905,
                    MAX_DRAW_BUFFERS:                             0x8824,
                    MAX_COLOR_ATTACHMENTS:                        0x8CDF,
                    MAX_SAMPLES:                                  0x8D57,
                    MAX_RENDERBUFFER_SIZE:                        0x84E8,
                    MAX_3D_TEXTURE_SIZE:                          0x8073,
                    MAX_ARRAY_TEXTURE_LAYERS:                     0x88FF,
                    MAX_TEXTURE_LOD_BIAS:                         0x84FD,
                    MAX_UNIFORM_BUFFER_BINDINGS:                  0x8A2F,
                    MAX_UNIFORM_BLOCK_SIZE:                       0x8A30,
                    UNIFORM_BUFFER_OFFSET_ALIGNMENT:              0x8A34,
                    MAX_COMBINED_UNIFORM_BLOCKS:                  0x8A2E,
                    MAX_COMBINED_VERTEX_UNIFORM_COMPONENTS:       0x8A31,
                    MAX_COMBINED_FRAGMENT_UNIFORM_COMPONENTS:     0x8A33,
                    MAX_ELEMENT_INDEX:                            0x8D6B,
                    MAX_ELEMENTS_INDICES:                         0x80E9,
                    MAX_ELEMENTS_VERTICES:                        0x80E8,
                    MAX_SERVER_WAIT_TIMEOUT:                      0x9111,
                };
                for (const [name, pname] of Object.entries(WGL2)) r[name] = _get(gl, pname);
            }

            // Context attributes (getContextAttributes)
            try {
                const attrs = gl.getContextAttributes() || {};
                const ATTR_KEYS = ['alpha','antialias','depth','desynchronized',
                                   'failIfMajorPerformanceCaveat','powerPreference',
                                   'premultipliedAlpha','preserveDrawingBuffer',
                                   'stencil','xrCompatible','drawingBufferColorSpace',
                                   'unpackColorSpace'];
                for (const k of ATTR_KEYS) {
                    if (k in attrs) r[k] = attrs[k];
                }
            } catch(e) {}

            // UNMASKED vendor / renderer
            try {
                const dbg = gl.getExtension('WEBGL_debug_renderer_info');
                r['UNMASKED_VENDOR_WEBGL']   = dbg ? _get(gl, dbg.UNMASKED_VENDOR_WEBGL)   : null;
                r['UNMASKED_RENDERER_WEBGL'] = dbg ? _get(gl, dbg.UNMASKED_RENDERER_WEBGL) : null;
            } catch(e) {}

            // Shader precision (strings résumées comme BrowserLeaks : "[-2^127,2^127](23)")
            const sp = _shaderPrecision(gl);
            const _fmt = (p) => p ? `[-2^${p.rangeMin},2^${p.rangeMax}](${p.precision})` : null;
            r['VERTEX_SHADER']      = _fmt(sp['VERTEX']   && sp['VERTEX']['HIGH_FLOAT']);
            r['FRAGMENT_SHADER']    = _fmt(sp['FRAGMENT'] && sp['FRAGMENT']['HIGH_FLOAT']);
            r['HIGH_FLOAT_HIGH_INT'] = (() => {
                const vHF = sp['VERTEX']  && sp['VERTEX']['HIGH_FLOAT'];
                const fHI = sp['FRAGMENT']&& sp['FRAGMENT']['HIGH_INT'];
                if (!vHF || !fHI) return null;
                const toLabel = (p) => p.rangeMax >= 127 ? 'highp' : (p.rangeMax >= 15 ? 'mediump' : 'lowp');
                return toLabel(vHF) + '/' + toLabel(fHI);
            })();

            // Anisotropie
            try {
                const aniso = gl.getExtension('EXT_texture_filter_anisotropic')
                           || gl.getExtension('WEBKIT_EXT_texture_filter_anisotropic');
                r['MAX_TEXTURE_MAX_ANISOTROPY_EXT'] = aniso
                    ? _get(gl, aniso.MAX_TEXTURE_MAX_ANISOTROPY_EXT) : null;
            } catch(e) {}

            // WebGL1-only : MAX_DRAW_BUFFERS via extension WEBGL_draw_buffers
            if (!isGL2) {
                try {
                    const wdb = gl.getExtension('WEBGL_draw_buffers');
                    r['MAX_DRAW_BUFFERS_WEBGL'] = wdb ? _get(gl, wdb.MAX_DRAW_BUFFERS_WEBGL) : null;
                } catch(e) {}
            }

            // Extensions supportées (liste triée)
            r['extensions'] = gl.getSupportedExtensions() || [];

            return r;
        }

        // ── Canvas WebGL2 ────────────────────────────────────────────────────────
        const c2 = document.createElement('canvas');
        const gl2 = c2.getContext('webgl2');

        // ── Canvas WebGL1 (canvas séparé pour éviter la contamination de contexte)
        const c1 = document.createElement('canvas');
        const gl1 = c1.getContext('webgl') || c1.getContext('experimental-webgl');

        return {
            webgl2: gl2 ? _dumpContext(gl2, true)  : null,
            webgl:  gl1 ? _dumpContext(gl1, false) : null,
        };
    """)

    return {"source": "js_getParameter", "data": raw}


def _save_json(data: dict, label: str) -> str:
    path = f"/tmp/prod_{label}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)
    print(f"[WGL] Sauvegardé : {path} ({os.path.getsize(path)} bytes)")
    return path


def main():
    sys.path.insert(0, os.getcwd())

    from preselection.playwright_launcher import launch_browser
    print("[WGL] Lancement Chrome prod ...")
    driver = launch_browser()

    try:
        print(f"[WGL] Navigation vers {WEBGL_URL} ...")
        driver.get(WEBGL_URL)

        print(f"[WGL] Attente {WAIT_AFTER_LOAD}s (chargement XHR browserleaks) ...")
        time.sleep(WAIT_AFTER_LOAD)

        print("[WGL] Extraction JSON WebGL ...")
        result = _extract_webgl_json(driver)

        # Ajouter des métadonnées utiles
        result["meta"] = {
            "mode":       "prod",
            "timestamp":  time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "user_agent": driver.execute_script("return navigator.userAgent"),
            "url":        driver.current_url,
        }

        path = _save_json(result, "webgl_dump")
        print(f"\n[WGL] ✓ JSON dump sauvegardé dans {path}")
        print("\n[WGL] Pour récupérer le fichier (depuis PowerShell) :")
        print("  flyctl ssh sftp get /tmp/prod_webgl_dump.json -a surveybot-bot")

    finally:
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    main()