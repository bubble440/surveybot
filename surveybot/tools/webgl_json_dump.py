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
    Extrait les données WebGL directement via JS depuis le contexte de la page.
    Deux stratégies :
      1) Intercepter l'objet interne de browserleaks (window._blData ou équivalent)
      2) Fallback : lire le canvas JSON depuis le bouton "JSON Dump" (href data:)
      3) Fallback ultime : reconstruire manuellement via getParameter JS
    """

    # ── Stratégie 1 : lire le href du lien "JSON Dump" (data:application/json;...) ──
    json_href = driver.execute_script("""
        // Le bouton JSON Dump est un <a href="data:application/json;charset=utf-8,...">
        const links = Array.from(document.querySelectorAll('a[href^="data:application/json"]'));
        // Prendre le premier qui contient "webgl" dans l'URL courante ou le premier dispo
        if (links.length > 0) return links[0].href;
        return null;
    """)

    if json_href and json_href.startswith("data:application/json"):
        # Décoder le data URI
        # Format : data:application/json;charset=utf-8,<URL-encoded JSON>
        from urllib.parse import unquote
        prefix = "data:application/json;charset=utf-8,"
        if json_href.startswith(prefix):
            raw = unquote(json_href[len(prefix):])
            try:
                return {"source": "data_uri", "data": json.loads(raw)}
            except json.JSONDecodeError as e:
                print(f"[WGL] JSON decode error sur data URI : {e}")

    print("[WGL] Stratégie 1 (data URI) échouée — tentative stratégie 2 (JS getParameter)")

    # ── Stratégie 2 : reconstruire via JS getParameter directement ──────────────
    raw_params = driver.execute_script("""
        const results = {};

        // Créer un canvas WebGL2 (même contexte que browserleaks utilise)
        const canvas = document.createElement('canvas');
        const gl2 = canvas.getContext('webgl2');
        const gl1 = canvas.getContext('webgl') || canvas.getContext('experimental-webgl');
        const gl  = gl2 || gl1;
        if (!gl) return {error: 'WebGL non disponible'};

        const isWebGL2 = !!gl2;
        results['_context'] = isWebGL2 ? 'webgl2' : 'webgl';

        // Extension WEBGL_debug_renderer_info
        const dbg = gl.getExtension('WEBGL_debug_renderer_info');
        results['UNMASKED_VENDOR_WEBGL']   = dbg ? gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL)   : null;
        results['UNMASKED_RENDERER_WEBGL'] = dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : null;

        // Paramètres scalaires WebGL1
        const SCALAR_PARAMS_1 = {
            MAX_TEXTURE_SIZE:               0x0D33,
            MAX_VIEWPORT_DIMS:              0x0D3A,
            MAX_VERTEX_ATTRIBS:             0x8869,
            MAX_VERTEX_UNIFORM_VECTORS:     0x8DFB,
            MAX_VERTEX_TEXTURE_IMAGE_UNITS: 0x8B4C,
            MAX_VARYING_VECTORS:            0x8DFC,
            MAX_TEXTURE_IMAGE_UNITS:        0x8872,
            MAX_FRAGMENT_UNIFORM_VECTORS:   0x8DFD,
            MAX_CUBE_MAP_TEXTURE_SIZE:      0x851C,
            MAX_RENDERBUFFER_SIZE:          0x84E8,
            MAX_COMBINED_TEXTURE_IMAGE_UNITS: 0x8B4D,
            ALIASED_LINE_WIDTH_RANGE:       0x846E,
            ALIASED_POINT_SIZE_RANGE:       0x846D,
            RED_BITS:    0x0D52,
            GREEN_BITS:  0x0D53,
            BLUE_BITS:   0x0D54,
            ALPHA_BITS:  0x0D55,
            DEPTH_BITS:  0x0D56,
            STENCIL_BITS: 0x0D57,
        };
        for (const [name, pname] of Object.entries(SCALAR_PARAMS_1)) {
            const v = gl.getParameter(pname);
            // Convertir les typed arrays en arrays normaux pour JSON
            results[name] = (v && v.constructor && v.constructor.name.includes('Array'))
                ? Array.from(v) : v;
        }

        // Paramètres WebGL2 uniquement
        if (isWebGL2) {
            const SCALAR_PARAMS_2 = {
                MAX_3D_TEXTURE_SIZE:                     0x8073,
                MAX_ARRAY_TEXTURE_LAYERS:                0x88FF,
                MAX_COLOR_ATTACHMENTS:                   0x8CDF,
                MAX_COMBINED_FRAGMENT_UNIFORM_COMPONENTS:0x8A33,
                MAX_COMBINED_UNIFORM_BLOCKS:             0x8A2E,
                MAX_COMBINED_VERTEX_UNIFORM_COMPONENTS:  0x8A31,
                MAX_DRAW_BUFFERS:                        0x8824,
                MAX_ELEMENT_INDEX:                       0x8D6B,
                MAX_ELEMENTS_INDICES:                    0x80E9,
                MAX_ELEMENTS_VERTICES:                   0x80E8,
                MAX_FRAGMENT_INPUT_COMPONENTS:           0x9125,
                MAX_FRAGMENT_UNIFORM_BLOCKS:             0x8A2D,
                MAX_FRAGMENT_UNIFORM_COMPONENTS:         0x8B49,
                MAX_PROGRAM_TEXEL_OFFSET:                0x8905,
                MAX_SAMPLES:                             0x8D57,
                MAX_SERVER_WAIT_TIMEOUT:                 0x9111,
                MAX_TEXTURE_LOD_BIAS:                    0x84FD,
                MAX_TRANSFORM_FEEDBACK_INTERLEAVED_COMPONENTS: 0x8C8A,
                MAX_TRANSFORM_FEEDBACK_SEPARATE_ATTRIBS: 0x8C8B,
                MAX_TRANSFORM_FEEDBACK_SEPARATE_COMPONENTS: 0x8C80,
                MAX_UNIFORM_BLOCK_SIZE:                  0x8A30,
                MAX_UNIFORM_BUFFER_BINDINGS:             0x8A2F,
                MAX_VARYING_COMPONENTS:                  0x8B4B,
                MAX_VERTEX_OUTPUT_COMPONENTS:            0x9122,
                MAX_VERTEX_UNIFORM_BLOCKS:               0x8A2B,
                MAX_VERTEX_UNIFORM_COMPONENTS:           0x8B4A,
                MIN_PROGRAM_TEXEL_OFFSET:                0x8904,
                UNIFORM_BUFFER_OFFSET_ALIGNMENT:         0x8A34,
            };
            for (const [name, pname] of Object.entries(SCALAR_PARAMS_2)) {
                const v = gl.getParameter(pname);
                results[name] = (v && v.constructor && v.constructor.name.includes('Array'))
                    ? Array.from(v) : v;
            }

            // MAX_VIEWPORT_DIMS pour WebGL2 (override éventuel)
            const vdims = gl.getParameter(0x0D3A);
            results['MAX_VIEWPORT_DIMS_WGL2'] = vdims ? Array.from(vdims) : null;
        }

        // Extensions supportées
        results['SUPPORTED_EXTENSIONS'] = gl.getSupportedExtensions();

        // Anisotropie
        const aniso = gl.getExtension('EXT_texture_filter_anisotropic')
                   || gl.getExtension('WEBKIT_EXT_texture_filter_anisotropic');
        results['MAX_TEXTURE_MAX_ANISOTROPY_EXT'] = aniso
            ? gl.getParameter(aniso.MAX_TEXTURE_MAX_ANISOTROPY_EXT) : null;

        // Shader precision
        const SHADER_TYPES     = [gl.VERTEX_SHADER, gl.FRAGMENT_SHADER];
        const PRECISION_TYPES  = ['LOW_FLOAT','MEDIUM_FLOAT','HIGH_FLOAT','LOW_INT','MEDIUM_INT','HIGH_INT'];
        results['shaderPrecision'] = {};
        for (const st of SHADER_TYPES) {
            const stName = st === gl.VERTEX_SHADER ? 'VERTEX' : 'FRAGMENT';
            results['shaderPrecision'][stName] = {};
            for (const pt of PRECISION_TYPES) {
                const sp = gl.getShaderPrecisionFormat(st, gl[pt]);
                results['shaderPrecision'][stName][pt] = sp
                    ? { rangeMin: sp.rangeMin, rangeMax: sp.rangeMax, precision: sp.precision }
                    : null;
            }
        }

        return results;
    """)

    return {"source": "js_getParameter", "data": raw_params}


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
