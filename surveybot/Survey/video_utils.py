# video_utils.py
import os, time, base64, datetime
from selenium.webdriver.common.by import By

def _now_tag():
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

def _scroll_into_view(driver, el):
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
        time.sleep(0.1)
    except Exception:
        pass

def _has_video_element(driver) -> bool:
    js = "return !!(document.querySelector('.video-js video, video'));"
    try:
        return bool(driver.execute_script(js))
    except Exception:
        return False

def _click_play_controls(driver) -> bool:
    """
    Essaie différents boutons de lecture Video.js/Brightcove, sinon appelle video.play()
    """
    sels = [
        ".vjs-big-play-button",
        ".vjs-play-control",
        "#video-player-PlayVideo .vjs-big-play-button",
        "#video-player-PlayVideo .vjs-play-control",
    ]
    for sel in sels:
        try:
            btns = driver.find_elements(By.CSS_SELECTOR, sel)
            for b in btns:
                if b.is_displayed() and b.rect.get("width", 0) > 5:
                    _scroll_into_view(driver, b)
                    driver.execute_script("arguments[0].click();", b)
                    time.sleep(0.1)
                    return True
        except Exception:
            continue

    # Fallback JS direct
    try:
        js = """
        var v = document.querySelector('.video-js video, video');
        if (!v) return false;
        try { v.muted = false; } catch(e) {}
        var p = v.play && v.play();
        return true;
        """
        return bool(driver.execute_script(js))
    except Exception:
        return False

def _record_audio_async_js():
    """
    JS asynchrone : enregistre l'audio de la vidéo (webm) et renvoie {ok, data(base64), mime, duration}.
    - Essaye d’abord video.captureStream(), sinon WebAudio (MediaElementSource).
    - S’arrête sur l’évènement 'ended' ou au bout de maxSec fourni par Selenium (argument 0).
    """
    return r"""
    const maxSec = arguments[0] || 30;
    const done = arguments[arguments.length - 1];

    (async () => {
      const video = document.querySelector('.video-js video, video');
      if (!video) return done({ok:false, error:'no-video'});

      // Débloque l’audio si nécessaire
      try { video.muted = false; } catch(e) {}

      // Si la vidéo est en pause, tente lecture
      try { if (video.paused) await video.play(); } catch(e) {}

      // Détermine flux audio
      let stream = null;
      try {
        if (video.captureStream) {
          stream = video.captureStream();
        } else if (video.mozCaptureStream) {
          stream = video.mozCaptureStream();
        }
      } catch (_) {}

      if (!stream) {
        // Fallback WebAudio
        try {
          const AudioCtx = window.AudioContext || window.webkitAudioContext;
          const ctx = new AudioCtx();
          const src = ctx.createMediaElementSource(video);
          const dest = ctx.createMediaStreamDestination();
          // Laisse le son audible
          src.connect(ctx.destination);
          src.connect(dest);
          stream = dest.stream;
        } catch(e) {
          return done({ok:false, error:'audiocontext-failed:'+e});
        }
      }

      let mime = 'audio/webm';
      if (!MediaRecorder.isTypeSupported(mime)) {
        mime = 'audio/webm;codecs=opus';
      }
      let rec;
      try {
        rec = new MediaRecorder(stream, {mimeType: mime});
      } catch(e) {
        try { rec = new MediaRecorder(stream); }
        catch(e2) { return done({ok:false, error:'mediarecorder-failed'}); }
      }

      const chunks = [];
      rec.ondataavailable = (ev)=>{ if (ev.data && ev.data.size) chunks.push(ev.data); };

      let stopped = false;
      const finish = () => {
        if (stopped) return;
        stopped = true;
        try { rec.stop(); } catch(_) {}
      };

      const vidEnded = () => setTimeout(finish, 150);
      video.addEventListener('ended', vidEnded, {once:true});

      rec.onstop = async () => {
        video.removeEventListener('ended', vidEnded);
        try {
          const blob = new Blob(chunks, {type: mime});
          const fr = new FileReader();
          fr.onloadend = () => {
            const b64 = (fr.result || "").toString().split(',')[1] || "";
            done({ok:true, data:b64, mime:mime, duration: video.duration || null});
          };
          fr.readAsDataURL(blob);
        } catch(e) {
          done({ok:false, error:'encode-failed'});
        }
      };

      rec.start();

      // Sécurité temps max si pas d'ended
      const maxMs = Math.min((video.duration && isFinite(video.duration) ? video.duration : maxSec) * 1000, maxSec * 1000);
      setTimeout(finish, maxMs + 400);
    })();
    """

def record_video_audio(driver, out_dir="screenshots", max_seconds=30):
    """
    1) Clique lecture si besoin
    2) Enregistre l'audio via JS asynchrone
    3) Sauve .webm sur disque et renvoie chemin + durée
    """
    if not _has_video_element(driver):
        return None

    # Clique lecture
    _click_play_controls(driver)

    # Lance l’enregistrement
    res = driver.execute_async_script(_record_audio_async_js(), max_seconds)
    if not res or not res.get("ok"):
        print(f"[video] record failed: {res}")
        return None

    b64 = res.get("data")
    if not b64:
        return None

    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"audio_{_now_tag()}.webm")
    with open(path, "wb") as f:
        f.write(base64.b64decode(b64))

    return {"path": path, "duration": res.get("duration"), "mime": res.get("mime", "audio/webm")}

# --------- Transcription optionnelle (OpenAI Whisper-1) ----------
def transcribe_audio_file(path: str, api_key: str | None = None, lang_hint: str = "fr") -> str | None:
    """
    Si OPENAI_API_KEY présent (ou api_key fourni), tente Whisper-1.
    Retourne le texte ou None.
    """
    key = api_key or os.getenv("OPENAI_API_KEY") or os.getenv("openai_api_key")
    if not key:
        return None
    try:
        from openai import OpenAI
        client = OpenAI(api_key=key)
        with open(path, "rb") as f:
            resp = client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                language=lang_hint
            )
        txt = getattr(resp, "text", None)
        if txt:
            return txt.strip()
    except Exception as e:
        print(f"[video] Whisper transcription error: {e}")
    return None

def try_watch_and_capture(driver, api_key: str | None = None, max_seconds=30) -> bool:
    """
    Détecte une vidéo → lit + capture audio → mémorise sur driver pour la Q suivante.
    Retourne True si quelque chose a été fait.
    """
    try:
        if not _has_video_element(driver):
            return False

        # Assure qu’on voit le player
        try:
            el = driver.find_element(By.CSS_SELECTOR, ".video-js, video")
            _scroll_into_view(driver, el)
        except Exception:
            pass

        print("🎬 Vidéo détectée → lecture + capture audio…")
        rec = record_video_audio(driver, max_seconds=max_seconds)
        if not rec:
            print("⚠️ Échec capture audio (on continue quand même).")
            return True  # on a tenté quelque chose

        # Transcription (si clé dispo)
        transcript = transcribe_audio_file(rec["path"], api_key=api_key) or ""

        # Mémorisation pour la prochaine question
        try:
            setattr(driver, "_last_video_audio_path", rec["path"])
            setattr(driver, "_last_video_transcript", transcript or "")
        except Exception:
            pass

        print(f"✅ Audio capturé: {rec['path']}  Durée≈{rec.get('duration')}")
        if transcript:
            print(f"📝 Transcript (début): {transcript[:120]}…")

        # Marque un succès d’action in-page (utile pour la boucle)
        try:
            setattr(driver, "last_action_success", True)
        except Exception:
            pass

        return True
    except Exception as e:
        print(f"[video] erreur try_watch_and_capture: {e}")
        return False
