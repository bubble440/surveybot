# Survey/dom_context_mapper.py
from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains


# =========================
# Utils
# =========================

def _norm(s: str) -> str:
    """Normalisation souple (comparaison robuste)."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s).replace("\u00a0", " ").strip().lower()
    s = re.sub(r"[»«“”\"'›→·•:…]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _overlap_1d(a0: float, a1: float, b0: float, b1: float) -> float:
    """Taux d’overlap entre 2 segments (0..1)."""
    inter = max(0.0, min(a1, b1) - max(a0, b0))
    denom = max(1.0, min(a1 - a0, b1 - b0))
    return inter / denom


def _soft_sim(a: str, b: str) -> float:
    """Similarité cheap (pas de lib externe)."""
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.92
    aw, bw = set(a.split()), set(b.split())
    if not aw or not bw:
        return 0.0
    return len(aw & bw) / len(aw | bw)


@dataclass
class _Node:
    uid: str
    text: str
    rect: Dict[str, float]   # {x,y,w,h,cx,cy}


@dataclass
class _InputNode:
    uid: str
    kind: str               # radio/checkbox/aria-radio/aria-checkbox
    rect: Dict[str, float]


@dataclass
class _Cluster:
    key: float              # centre (x ou y)
    members: List[int]      # indices d’inputs
    span_min: float
    span_max: float


# =========================
# JS collecteurs
# =========================

_JS_COLLECT = r"""
(function(){
  function visible(el){
    try{
      const st = window.getComputedStyle(el);
      if(!st) return false;
      if(st.display==='none' || st.visibility==='hidden' || st.opacity==='0') return false;
      const r = el.getBoundingClientRect();
      if(!r || r.width<6 || r.height<6) return false;
      // offsetParent null => souvent invisible, mais certains fixed/absolute le sont quand même
      // on garde si rect ok
      return true;
    }catch(e){ return false; }
  }

  function norm(txt){
    return (txt||'').replace(/\u00A0/g,' ').replace(/\s+/g,' ').trim();
  }

  function tag(el, uid){
    try{ el.setAttribute('data-survey-uid', uid); }catch(e){}
  }

  // 1) Inputs
  const inputs = [];
  const q = [
    "input[type='radio']",
    "input[type='checkbox']",
    "[role='radio']",
    "[role='checkbox']"
  ].join(",");

  const els = Array.from(document.querySelectorAll(q));
  let i = 0;
  for(const el of els){
    if(!visible(el)) continue;
    const r = el.getBoundingClientRect();
    const uid = "i" + (i++);
    tag(el, uid);
    let kind = "";
    const tn = (el.tagName||"").toLowerCase();
    if(tn === "input"){
      const tp = (el.getAttribute("type")||"").toLowerCase();
      kind = tp === "checkbox" ? "checkbox" : "radio";
    }else{
      const role = (el.getAttribute("role")||"").toLowerCase();
      kind = role === "checkbox" ? "aria-checkbox" : "aria-radio";
    }
    inputs.push({
      uid,
      kind,
      rect: {x:r.x,y:r.y,w:r.width,h:r.height,cx:r.x+r.width/2,cy:r.y+r.height/2}
    });
  }

  // 2) Contextes texte (candidats)
  // On cible des tags “souvent contextuels” + cellules
  const textSel = [
    "th","td","label","legend","p","span","div","li"
  ];
  const cand = Array.from(document.querySelectorAll(textSel.join(",")));

  const nodes = [];
  let c = 0;
  for(const el of cand){
    if(!visible(el)) continue;
    const txt = norm(el.innerText || el.textContent || "");
    if(!txt) continue;
    // couper très longs paragraphes (souvent bruit)
    if(txt.length > 120) continue;
    // ignorer textes trop courts
    if(txt.length < 1) continue;

    const r = el.getBoundingClientRect();
    const uid = "c" + (c++);
    tag(el, uid);
    nodes.push({
      uid,
      text: txt,
      rect: {x:r.x,y:r.y,w:r.width,h:r.height,cx:r.x+r.width/2,cy:r.y+r.height/2}
    });
  }

  return { inputs, nodes, ts: Date.now(), url: location.href };
})();
"""


def _collect(driver) -> Tuple[List[_InputNode], List[_Node], Dict[str, Any]]:
    """Collecte via JS (zéro OCR, cheap)."""
    try:
        data = driver.execute_script(_JS_COLLECT)
    except Exception:
        return [], [], {}

    inputs = []
    for it in (data.get("inputs") or []):
        inputs.append(_InputNode(uid=it["uid"], kind=it["kind"], rect=it["rect"]))

    nodes = []
    for n in (data.get("nodes") or []):
        nodes.append(_Node(uid=n["uid"], text=n["text"], rect=n["rect"]))

    meta = {"url": data.get("url", ""), "ts": data.get("ts", 0)}
    return inputs, nodes, meta


# =========================
# Clustering (cheap)
# =========================

def _cluster_1d(values: List[float], *, tol: float) -> List[int]:
    """
    Retourne un cluster_id par valeur.
    tol en pixels (écart max entre centres).
    """
    if not values:
        return []
    pairs = sorted([(v, idx) for idx, v in enumerate(values)], key=lambda x: x[0])
    cluster_id = [-1] * len(values)
    cur = 0
    cluster_id[pairs[0][1]] = cur
    last = pairs[0][0]
    for v, idx in pairs[1:]:
        if abs(v - last) > tol:
            cur += 1
        cluster_id[idx] = cur
        last = v
    return cluster_id


def _build_clusters(inputs: List[_InputNode], axis: str, tol: float) -> List[_Cluster]:
    vals = [inp.rect["cx"] if axis == "x" else inp.rect["cy"] for inp in inputs]
    ids = _cluster_1d(vals, tol=tol)
    if not ids:
        return []

    clusters: Dict[int, List[int]] = {}
    for i, cid in enumerate(ids):
        clusters.setdefault(cid, []).append(i)

    out: List[_Cluster] = []
    for cid, members in clusters.items():
        centers = [vals[i] for i in members]
        key = sum(centers) / len(centers)
        # span = min/max orthogonal (utile pour overlap)
        if axis == "x":
            mins = [inputs[i].rect["x"] for i in members]
            maxs = [inputs[i].rect["x"] + inputs[i].rect["w"] for i in members]
        else:
            mins = [inputs[i].rect["y"] for i in members]
            maxs = [inputs[i].rect["y"] + inputs[i].rect["h"] for i in members]
        out.append(_Cluster(key=key, members=members, span_min=min(mins), span_max=max(maxs)))

    out.sort(key=lambda c: c.key)
    return out


# =========================
# Mapping “tableau visuel”
# =========================

def _find_best_row_label(nodes: List[_Node], row: _Cluster, inputs: List[_InputNode]) -> Optional[_Node]:
    """
    Cherche un label texte à GAUCHE de la row, aligné verticalement.
    """
    # bbox row (vertical)
    y0, y1 = row.span_min, row.span_max
    # bord gauche de la row = min x de ses inputs
    min_x = min(inputs[i].rect["x"] for i in row.members)

    best, best_sc = None, -1.0
    for n in nodes:
        nx0, nx1 = n.rect["x"], n.rect["x"] + n.rect["w"]
        ny0, ny1 = n.rect["y"], n.rect["y"] + n.rect["h"]

        # doit être plutôt à gauche
        if nx1 > (min_x - 6):
            continue

        # overlap vertical nécessaire
        ov = _overlap_1d(ny0, ny1, y0, y1)
        if ov < 0.15:
            continue

        # score : proximité + overlap + longueur texte
        dist = abs(n.rect["cy"] - (y0 + y1) / 2)
        sc = (ov * 1.5) + max(0.0, 1.0 - dist / 300.0)
        if len(n.text) >= 2:
            sc += 0.1
        if sc > best_sc:
            best_sc = sc
            best = n

    return best


def _find_best_col_label(nodes: List[_Node], col: _Cluster, inputs: List[_InputNode]) -> Optional[_Node]:
    """
    Cherche un label texte AU-DESSUS de la colonne, aligné horizontalement.
    """
    # bbox col (horizontal)
    x0, x1 = col.span_min, col.span_max
    min_y = min(inputs[i].rect["y"] for i in col.members)

    best, best_sc = None, -1.0
    for n in nodes:
        nx0, nx1 = n.rect["x"], n.rect["x"] + n.rect["w"]
        ny0, ny1 = n.rect["y"], n.rect["y"] + n.rect["h"]

        # doit être au-dessus
        if ny1 > (min_y - 6):
            continue

        # overlap horizontal nécessaire
        ov = _overlap_1d(nx0, nx1, x0, x1)
        if ov < 0.15:
            continue

        dist = abs(n.rect["cx"] - (x0 + x1) / 2)
        sc = (ov * 1.5) + max(0.0, 1.0 - dist / 350.0)
        if len(n.text) >= 1:
            sc += 0.1
        if sc > best_sc:
            best_sc = sc
            best = n

    return best


def _build_visual_matrix_map(driver, *, force: bool = False, max_age_s: float = 2.0) -> Dict[str, Any]:
    """
    Construit (ou réutilise) un mapping visuel:
      rows: [(row_text, row_cluster_index)]
      cols: [(col_text, col_cluster_index)]
      cell: dict[(row_idx,col_idx)] -> uid_input
    """
    try:
        cache = getattr(driver, "_visual_matrix_cache", None)
    except Exception:
        cache = None

    now = time.time()
    cur_url = ""
    try:
        cur_url = driver.current_url
    except Exception:
        pass

    if not force and cache:
        try:
            if cache.get("url") == cur_url and (now - cache.get("built_ts", 0)) <= max_age_s:
                return cache
        except Exception:
            pass

    inputs, nodes, meta = _collect(driver)
    if len(inputs) < 6:
        # trop peu d’inputs → pas un tableau probable
        built = {"url": cur_url, "built_ts": now, "ok": False, "why": "too_few_inputs"}
        try:
            driver._visual_matrix_cache = built
        except Exception:
            pass
        return built

    # clusters: tol adaptés (UI responsive)
    cols = _build_clusters(inputs, axis="x", tol=35.0)
    rows = _build_clusters(inputs, axis="y", tol=28.0)

    if len(cols) < 2 or len(rows) < 2:
        built = {"url": cur_url, "built_ts": now, "ok": False, "why": "not_enough_clusters"}
        try:
            driver._visual_matrix_cache = built
        except Exception:
            pass
        return built

    row_labels: List[Tuple[str, int]] = []
    col_labels: List[Tuple[str, int]] = []

    for ridx, r in enumerate(rows):
        lab = _find_best_row_label(nodes, r, inputs)
        if lab and lab.text:
            row_labels.append((lab.text, ridx))

    for cidx, c in enumerate(cols):
        lab = _find_best_col_label(nodes, c, inputs)
        if lab and lab.text:
            col_labels.append((lab.text, cidx))

    # cell mapping par appartenance (input centre proche cluster x/y)
    cell: Dict[Tuple[int, int], str] = {}
    for i, inp in enumerate(inputs):
        # col idx
        cidx = min(range(len(cols)), key=lambda k: abs(inp.rect["cx"] - cols[k].key))
        ridx = min(range(len(rows)), key=lambda k: abs(inp.rect["cy"] - rows[k].key))
        cell[(ridx, cidx)] = inp.uid

    built = {
        "url": cur_url,
        "built_ts": now,
        "ok": True,
        "rows": row_labels,
        "cols": col_labels,
        "cell": cell,
    }

    try:
        driver._visual_matrix_cache = built
    except Exception:
        pass

    return built


def _find_best_label_idx(pairs: List[Tuple[str, int]], wanted: str) -> Optional[int]:
    """Match texte voulu -> index cluster via similarité souple."""
    w = _norm(wanted)
    if not w:
        return None
    best_idx, best_sc = None, 0.0
    for txt, idx in pairs:
        sc = _soft_sim(txt, w)
        if sc > best_sc:
            best_sc = sc
            best_idx = idx
    # seuil pour éviter des clics erronés
    return best_idx if (best_idx is not None and best_sc >= 0.55) else None


def try_click_matrix_by_visual_mapping(
    driver,
    *,
    row_label: str,
    col_label: str,
    debug: bool = True,
) -> bool:
    """
    Clique la cellule (row_label x col_label) en utilisant une détection VISUELLE,
    même si le DOM sépare contexte et inputs.
    """
    mp = _build_visual_matrix_map(driver)
    if not mp.get("ok"):
        if debug:
            print(f"[DOMMAP] mapping visuel indisponible: {mp.get('why')}")
        return False

    ridx = _find_best_label_idx(mp.get("rows", []), row_label)
    cidx = _find_best_label_idx(mp.get("cols", []), col_label)

    if ridx is None or cidx is None:
        if debug:
            print(f"[DOMMAP] row/col introuvables | row={row_label!r} col={col_label!r}")
            if debug:
                print(f"[DOMMAP] rows candidats: {[t for t,_ in mp.get('rows',[])][:12]}")
                print(f"[DOMMAP] cols candidats: {[t for t,_ in mp.get('cols',[])][:12]}")
        return False

    uid = (mp.get("cell") or {}).get((ridx, cidx))
    if not uid:
        if debug:
            print(f"[DOMMAP] cellule non mappée ridx={ridx} cidx={cidx}")
        return False

    # récupérer l’élément par uid
    try:
        el = driver.find_element(By.CSS_SELECTOR, f"[data-survey-uid='{uid}']")
    except Exception as e:
        if debug:
            print(f"[DOMMAP] element introuvable pour uid={uid}: {e}")
        return False

    # clic robuste (js + actionchains + click)
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
        time.sleep(0.12)
    except Exception:
        pass

    for mode in ("js", "ac", "native"):
        try:
            if mode == "js":
                driver.execute_script("arguments[0].click();", el)
            elif mode == "ac":
                ActionChains(driver).move_to_element(el).click().perform()
            else:
                el.click()
            time.sleep(0.12)
            # post-check simple (si input type=radio/checkbox)
            try:
                tag = (el.tag_name or "").lower()
                if tag == "input":
                    tp = (el.get_attribute("type") or "").lower()
                    if tp in ("radio", "checkbox"):
                        if el.is_selected():
                            if debug:
                                print(f"[DOMMAP] ✅ cellule cliquée ({mode}) row={row_label!r} col={col_label!r}")
                            return True
                # aria cases
                aria = (el.get_attribute("aria-checked") or "").lower()
                if aria == "true":
                    if debug:
                        print(f"[DOMMAP] ✅ cellule cliquée aria ({mode}) row={row_label!r} col={col_label!r}")
                    return True
            except Exception:
                # si on ne peut pas vérifier, on considère succès (best effort)
                if debug:
                    print(f"[DOMMAP] ✅ clic effectué ({mode}) row={row_label!r} col={col_label!r} (post-check indispo)")
                return True
        except Exception:
            continue

    if debug:
        print(f"[DOMMAP] ❌ échec clic sur cellule row={row_label!r} col={col_label!r}")
    return False
