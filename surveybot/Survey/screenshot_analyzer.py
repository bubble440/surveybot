import openai
import os, base64, time
from PIL import Image  # pip install pillow
from pathlib import Path  # <-- si pas déjà importé en haut du fichier

FINETUNED_MODEL = os.getenv(
    "SURVEY_VISION_MODEL",
    "ft:gpt-4o-2024-08-06:survey-bot:version-2:CLEp48bw"
)


def _compress_image(src_path, max_w=768, quality=70):
    """
    Compresse l'image pour réduire les coûts tokens.
    - Redimensionne en conservant le ratio si largeur > max_w
    - Sauvegarde en JPEG qualité 'quality'
    Retourne le chemin du JPEG compressé.
    """
    try:
        im = Image.open(src_path).convert("RGB")
        w, h = im.size
        if w > max_w:
            new_h = int(h * (max_w / w))
            im = im.resize((max_w, new_h))
        out_path = src_path.rsplit(".", 1)[0] + "_compressed.jpg"
        im.save(
            out_path, format="JPEG", quality=quality, optimize=True, progressive=True
        )
        return out_path
    except Exception as e:
        print(f"⚠️ Compression échouée, fallback image originale: {e}")
        return src_path


def take_screenshot(
    driver, out_path: str = "screenshot.png", full_page: bool = False
) -> str:
    """
    Prend une capture et renvoie le chemin du fichier.
    - full_page=False : viewport uniquement (ancien comportement)
    - full_page=True  : tente CDP plein‑page, puis fallback mosaïque si CDP échoue
    """

    # 📂 1) Dossier screenshots (chemin absolu, ancré au projet)
    try:
        base_dir = Path(__file__).resolve().parent   # dossier du module
        folder = str(base_dir / "screenshots")
    except Exception:
        folder = os.path.abspath("screenshots")
    os.makedirs(folder, exist_ok=True)


    # # 2) Numérotation auto (max + 1, robuste)
    import re  # (OK si déjà importé en haut; sinon laisser ici)
    pngs = []
    try:
        for f in os.listdir(folder):
            m = re.match(r"^screenshot_(\d+)\.png$", f, re.IGNORECASE)
            if m:
                pngs.append(int(m.group(1)))
    except Exception:
        pngs = []
    
    next_num = (max(pngs) + 1) if pngs else 1
    
    # filet de sécurité : si le nom existe déjà, on incrémente jusqu'à un slot libre
    while True:
        filename = f"screenshot_{next_num:03d}.png"
        out_path = os.path.join(folder, filename)
        if not os.path.exists(out_path):
            break
        next_num += 1


    try:
        if not full_page:
            driver.save_screenshot(out_path)
            return out_path

        # 1) Tentative CDP plein‑page (Chrome/Brave)
        try:
            # Mesures du document complet
            metrics = driver.execute_cdp_cmd("Page.getLayoutMetrics", {})
            content_size = metrics.get("contentSize", {})
            width = int(content_size.get("width", 0)) or driver.execute_script(
                "return document.documentElement.clientWidth"
            )
            height = int(content_size.get("height", 0)) or driver.execute_script(
                "return document.body.scrollHeight"
            )

            # Activer un viewport virtuel de la taille du document
            driver.execute_cdp_cmd(
                "Emulation.setDeviceMetricsOverride",
                {
                    "mobile": False,
                    "width": width,
                    "height": height,
                    "deviceScaleFactor": 1,
                    "scale": 1,
                },
            )
            driver.execute_cdp_cmd("Page.enable", {})
            time.sleep(0.1)

            # Capturer
            data = driver.execute_cdp_cmd(
                "Page.captureScreenshot",
                {"fromSurface": True, "captureBeyondViewport": True},
            )
            png_b64 = data.get("data")
            with open(out_path, "wb") as f:
                f.write(base64.b64decode(png_b64))
                print(f"💾 Capture enregistrée (CDP) → {os.path.abspath(out_path)}")
            # Restaurer
            driver.execute_cdp_cmd("Emulation.clearDeviceMetricsOverride", {})
            return out_path
        except Exception as e:
            print(f"⚠️ CDP plein‑page indisponible, fallback mosaïque. Détail: {e}")

        # 2) Fallback mosaïque (scroll + assemblage)
        return _stitch_fullpage(driver, out_path)

    except Exception as e:
        print(f"❌ Erreur capture écran : {e}")
        # dernier filet : viewport simple
        driver.save_screenshot(out_path)
        print(f"💾 Capture enregistrée (fallback) → {os.path.abspath(out_path)}")
        return out_path


def _stitch_fullpage(driver, out_path: str) -> str:
    """
    Fallback : scroller par “tuiles” et assembler verticalement.
    Gère les sticky headers en overlap.
    """
    # Se mettre tout en haut
    driver.execute_script("window.scrollTo(0, 0);")
    time.sleep(0.2)

    total_height = int(
        driver.execute_script(
            "return Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)"
        )
    )
    viewport_h = int(driver.execute_script("return window.innerHeight"))
    viewport_w = int(driver.execute_script("return window.innerWidth"))
    step = max(1, int(viewport_h * 0.85))  # overlap ~15% pour éviter les bandes
    slices = []
    y = 0
    i = 0

    tmp_dir = "_shots_tmp"
    os.makedirs(tmp_dir, exist_ok=True)

    while y < total_height:
        driver.execute_script(f"window.scrollTo(0, {y});")
        time.sleep(0.25)  # laisser charger le contenu lazy

        part_path = os.path.join(tmp_dir, f"shot_{i}.png")
        driver.save_screenshot(part_path)
        slices.append(part_path)

        y += step
        i += 1
        # sécurité anti-boucle
        if i > 40:  # ~ 40 écrans, ajuste si besoin
            break

    # Assembler
    images = [Image.open(p) for p in slices if os.path.exists(p)]
    if not images:
        # fallback ultime
        driver.save_screenshot(out_path)
        return out_path

    # Normaliser largeur
    images = [im.convert("RGB") for im in images]
    w = min(im.width for im in images)
    images = [
        im if im.width == w else im.resize((w, int(im.height * w / im.width)))
        for im in images
    ]

    # Calcul hauteur totale avec petit recouvrement supprimé
    # On supprime 10% en haut (sauf le premier) pour gommer les sticky headers
    stitched_h = images[0].height + sum(int(im.height * 0.9) for im in images[1:])
    canvas = Image.new("RGB", (w, stitched_h), (255, 255, 255))

    yoff = 0
    for idx, im in enumerate(images):
        crop = (
            im if idx == 0 else im.crop((0, int(im.height * 0.1), im.width, im.height))
        )
        canvas.paste(crop, (0, yoff))
        yoff += crop.height

    canvas.save(out_path, format="PNG", optimize=True)
    print(f"💾 Capture enregistrée (mosaïque) → {os.path.abspath(out_path)}")
    # Nettoyage
    try:
        for p in slices:
            os.remove(p)
        os.rmdir(tmp_dir)
    except Exception:
        pass

    return out_path


def send_image_to_gpt(image_path, api_key):
    """
    Build the message payload *here* (no global state), optionally include
    previous vs current images, and call GPT-4o cleanly.
    """
    client = openai.OpenAI(api_key=api_key)

    user_content = []
    # if side_context:
    #     side_trim = side_context.strip()
    #     if len(side_trim) > 600:
    #         side_trim = side_trim[:600] + " …"
    #     user_content.append({
    #         "type": "text",
    #         "text": "Mémo utile pour répondre aux questions: " + side_trim
    #     })

    user_content.append({
        "type": "text",
        "text": "Quelle instruction appliquer?"
    })

    # Current image (always)
    curr_small = _compress_image(
        image_path, max_w=1280, quality=75
    )
    with open(curr_small, "rb") as f:
        curr_b64 = base64.b64encode(f.read()).decode("utf-8")
    user_content.append(
        {
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{curr_b64}"},
        }
    )

    # SYSTEM : règles fermes (anti-disqualification + format)
    system_policy = (
        "Tu es un PARTICIPANT HUMAIN dont l’objectif est de TERMINER le questionnaire. "
        """Ta mission: retourner UNE SEULE instruction courte et actionnable pour l’étape immédiate. Ne renvoie qu’une ligne au format:
        <libellé exact> //// <type>> //// <contexte-question>
        ➤ <libellé exact> : la valeur à saisir ou à cliquer (ex. '28', '95000', 'Masculin').
        ➤ <type> : radio | checkbox | dropdown | text | textarea | button.
        ➤ <contexte-question> : le texte associé au champ (ex. 'Quel est ton code postal ?').
        Exemples valides :
        95000 //// text //// Quel est ton code postal ?
        Masculin //// radio //// Êtes-vous de genre masculin ou féminin ?
        Jour //// dropdown //// Quelle est ta date de naissance ?"""
        "Cherche a toujours avamcer dans le survey, donc donne des reponses qui entrainent une evolution du survey."
        "N’envoie jamais une option qui risque de disqualifier. "
        "Règles d’évitement (si ces options existent, NE PAS les choisir) : "
        "  - Réponses d’inéligibilité ou de refus de répondre"
        "  - Extrêmes qui paraissent incohérents. "
        "Ordre d’action : traiter le premier champ OBLIGATOIRE non rempli (haut de page) ; "
        "Pour radio/checkbox/dropdown, tu DOIS choisir UNIQUEMENT parmi les options disponibles."
        "RÈGLES “TEXT” (champs à remplir)"
        "Si la question concerne un code postal / ZIP (FR/EN): renvoyer 95000"
        "Si la question demande l’âge: renvoyer 23."
        "Si la question demande une année de naissance: renvoyer 2002."
        "Si la page demande un champ explicitement numérique, renvoyer des CHIFFRES seulement."
        "Règles de décision (IMPORTANT):"
        "Ne clique pas les éléments de navigation non requis: langue, bannière, politique de confidentialité."
        "Ne renvoie **qu’une seule instruction** par tour (pas de liste, pas d’explications)."
        "Lorsque l'action a effectuer est de cliquer sur un CTA sans texte retourne: Suivant //// button //// CTA"
    )

    messages = [
        {"role": "system", "content": system_policy},
        {"role": "user", "content": user_content},
    ]

    try:
        resp = client.chat.completions.create(
            #model="gpt-4o",
            model=FINETUNED_MODEL,
            temperature=0.2,
            messages=messages,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as e:
        print(f"💥 Erreur GPT vision : {e}")
        return None


# == IGNORE pour entrainement ==
# s'il est possible de choisir plusieurs options, en selectionner 2.
