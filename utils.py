# -*- coding: utf-8 -*-
import sys
import platform
import threading
import time
import requests

# ─── CROSS-PLATFORM SES ──────────────────
import numpy as np

def _pygame_init():
    try:
        import pygame
        if not pygame.mixer.get_init():
            pygame.mixer.pre_init(44100, -16, 2, 1024)
            pygame.mixer.init()
        return pygame
    except Exception:
        return None

def _make_beep(freq, duration_ms, volume=0.7):
    try:
        pygame = _pygame_init()
        if not pygame:
            return False
        sr       = 44100
        n        = int(sr * duration_ms / 1000)
        t        = np.linspace(0, duration_ms / 1000, n, False)
        mono     = (np.sin(2 * np.pi * freq * t) * volume * 32767).astype(np.int16)
        stereo   = np.column_stack([mono, mono])  # stereo
        sound    = pygame.sndarray.make_sound(stereo)
        sound.play()
        time.sleep(duration_ms / 1000 + 0.05)
        return True
    except Exception:
        return False

def beep(freq=800, duration=400):
    if not _make_beep(freq, duration):
        try:
            if platform.system() == "Windows":
                import winsound
                winsound.Beep(freq, duration)
        except Exception:
            pass

def alarm_thread(times=3, freq=1300, duration=500):
    for _ in range(times):
        _make_beep(freq, duration)
        time.sleep(0.08)
        _make_beep(max(freq - 400, 200), duration - 150)
        time.sleep(0.08)

def sesli_alarm(times=3, freq=1300, duration=500):
    t = threading.Thread(target=alarm_thread, args=(times, freq, duration), daemon=True)
    t.start()

# ─── OPEN FOOD FACTS ─────────────────────
def get_from_openfoodfacts(barkod):
    """
    Önce world.openfoodfacts.net dener, olmazsa .org dener.
    Ürün adı için önce Türkçe alanı, sonra genel alanı kontrol eder.
    """
    urls = [
        f"https://world.openfoodfacts.net/api/v2/product/{barkod}?fields=product_name,product_name_tr,generic_name,categories_tags",
        f"https://world.openfoodfacts.org/api/v2/product/{barkod}.json",
    ]
    headers = {"User-Agent": "AkilliBarkodUltimate/3.0 (envanter@sirket.com)"}

    for url in urls:
        try:
            r = requests.get(url, headers=headers, timeout=8)
            if r.status_code != 200:
                continue
            data = r.json()
            if data.get("status") == 1 and "product" in data:
                p = data["product"]
                name = (
                    p.get("product_name_tr")
                    or p.get("product_name")
                    or p.get("generic_name")
                    or f"Urun-{barkod[-6:]}"
                )
                name = name.strip()
                if not name:
                    name = f"Urun-{barkod[-6:]}"
                tags = p.get("categories_tags", [])
                cat = "Genel"
                if tags:
                    # Türkçe kategori varsa onu al
                    tr_tags = [t for t in tags if t.startswith("tr:")]
                    if tr_tags:
                        cat = tr_tags[0].replace("tr:", "").replace("-", " ").title()
                    else:
                        cat = tags[0].replace("en:", "").replace("-", " ").title()
                return name, cat
        except requests.exceptions.ConnectionError:
            continue  # internet yoksa sonrakini dene
        except requests.exceptions.Timeout:
            continue
        except Exception:
            continue

    return None, None

# ─── RENK PALETİ — Siyah & Yeşil ────────
C = {
    "bg":          "#080d0a",   # derin siyah-yesil
    "panel":       "#0d1410",   # koyu siyah panel
    "card":        "#111c15",   # kart arka plani
    "card2":       "#0f1912",   # alternatif kart
    "border":      "#1a3d24",   # koyu yesil kenar
    "accent":      "#22c55e",   # parlak yesil — ana vurgu
    "accent2":     "#4ade80",   # acik yesil — hover
    "green":       "#22c55e",   # yesil
    "yellow":      "#f0b429",   # uyari sari
    "red":         "#e05252",   # hata kirmizi
    "purple":      "#a78bfa",   # mor
    "cyan":        "#34d399",   # yesil-cyan
    "orange":      "#fb923c",   # orta uyari
    "text":        "#d1fae5",   # ana metin (hafif yesil beyaz)
    "text2":       "#6ee7b7",   # ikincil metin
    "muted":       "#2d5a3d",   # soluk metin
    "white":       "#f0fff4",   # beyaz
    "hover":       "#162a1e",   # hover arka plan
    "success_bg":  "#052e16",   # koyu yesil bg
    "warn_bg":     "#3d2800",   # uyari bg
    "error_bg":    "#3d0f0f",   # hata bg
    "gold_dim":    "#14532d",   # soluk yesil (disabled)
}

FONT_MAIN  = ("Segoe UI", 10)
FONT_BOLD  = ("Segoe UI Semibold", 10)
FONT_LG    = ("Segoe UI Semibold", 13)
FONT_XL    = ("Segoe UI Light", 20, "bold")
FONT_MONO  = ("Consolas", 11)
FONT_SMALL = ("Segoe UI", 9)
FONT_TITLE = ("Segoe UI Light", 15)

ROL_RENK = {
    "admin":          "#22c55e",   # parlak yesil
    "mudur":          "#4ade80",   # acik yesil
    "kasiyer":        "#86efac",   # soluk yesil
    "goruntuleyici":  "#4b7a5a",   # mat yesil
}

ROL_ETIKET = {
    "admin":          "ADMIN",
    "mudur":          "MUDUR",
    "kasiyer":        "KASİYER",
    "goruntuleyici":  "GÖRÜNTÜLEYICI",
}

# ─── SKT YARDIMCI ────────────────────────
from datetime import datetime, date

def kalan_gun(stt_str):
    if not stt_str:
        return None
    try:
        d = datetime.strptime(str(stt_str)[:10], "%Y-%m-%d").date()
        return (d - date.today()).days
    except Exception:
        return None

def stt_renk(gun):
    if gun is None:   return C["muted"]
    if gun < 0:       return C["red"]
    if gun <= 2:      return C["yellow"]
    if gun <= 7:      return C["orange"]
    return C["green"]

def stt_etiket(gun):
    if gun is None:   return "SKT Yok"
    if gun < 0:       return f"{abs(gun)}g once sona erdi"
    if gun == 0:      return "BUGÜN bitiyor!"
    return f"{gun} gun kaldi"