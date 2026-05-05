# -*- coding: utf-8 -*-
import os, hashlib, functools, requests, traceback, sys
import psycopg2, psycopg2.extras
from datetime import datetime, date
from flask import Flask, render_template_string, request, redirect, session, jsonify


# ── Firebase Admin ──────────────────────────
import firebase_admin
from firebase_admin import credentials as _fb_creds, auth as _fb_auth

_fb_app = None
def _get_fb():
    global _fb_app
    if _fb_app is None and os.environ.get("FIREBASE_PROJECT_ID"):
        try:
            _fb_app = firebase_admin.initialize_app(_fb_creds.Certificate({
                "type": "service_account",
                "project_id":   os.environ["FIREBASE_PROJECT_ID"],
                "private_key":  os.environ["FIREBASE_PRIVATE_KEY"].replace("\\n","\n"),
                "client_email": os.environ["FIREBASE_CLIENT_EMAIL"],
                "token_uri":    "https://oauth2.googleapis.com/token",
            }))
        except ValueError:
            pass  # already initialized
    return _fb_app
# ────────────────────────────────────────────

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "nexstock_secret_2024")

@app.errorhandler(Exception)
def handle_error(e):
    tb = traceback.format_exc()
    print(f"[HATA] {e}\n{tb}", file=sys.stderr, flush=True)
    return f"<pre style='color:red;background:#111;padding:20px;font-size:14px'>{tb}</pre>", 500

# ═══════════════════════════════════════════════════
#  VERİTABANI  (PostgreSQL / Supabase)
# ═══════════════════════════════════════════════════
class _PGConn:
    """sqlite3-compatible thin wrapper around a psycopg2 connection."""
    def __init__(self, conn):
        self._conn = conn
        self._cur  = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    def execute(self, sql, params=()):
        self._cur.execute(sql, params)
        return self._cur

    def commit(self):
        self._conn.commit()

    def close(self):
        try: self._cur.close()
        except Exception: pass
        try: self._conn.close()
        except Exception: pass

def get_db():
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    return _PGConn(conn)

def init_db():
    c = get_db()
    try:
        c.execute("""
        CREATE TABLE IF NOT EXISTS kullanicilar (
            id SERIAL PRIMARY KEY,
            kullanici_adi TEXT UNIQUE NOT NULL,
            firebase_uid  TEXT UNIQUE,
            email         TEXT,
            sifre_hash TEXT NOT NULL,
            tam_ad TEXT,
            rol TEXT NOT NULL DEFAULT 'kasiyer',
            aktif INTEGER DEFAULT 1,
            son_giris TIMESTAMPTZ
        )""")
        c.execute("""
        CREATE TABLE IF NOT EXISTS urunler (
            barkod TEXT PRIMARY KEY,
            urun_adi TEXT NOT NULL,
            kategori TEXT DEFAULT 'Genel',
            min_stok INTEGER DEFAULT 5,
            fiyat REAL DEFAULT 0.0,
            aciklama TEXT,
            eklenme_tarihi TIMESTAMPTZ DEFAULT NOW(),
            son_guncelleme TIMESTAMPTZ DEFAULT NOW()
        )""")
        c.execute("""
        CREATE TABLE IF NOT EXISTS partiler (
            parti_id SERIAL PRIMARY KEY,
            barkod TEXT NOT NULL,
            stt DATE,
            miktar INTEGER DEFAULT 0,
            eklenme_tarihi TIMESTAMPTZ DEFAULT NOW(),
            ekleyen TEXT DEFAULT 'sistem',
            FOREIGN KEY (barkod) REFERENCES urunler(barkod)
        )""")
        c.execute("""
        CREATE TABLE IF NOT EXISTS stok_hareketleri (
            hareket_id SERIAL PRIMARY KEY,
            barkod TEXT,
            urun_adi TEXT,
            hareket_tipi TEXT NOT NULL,
            miktar INTEGER NOT NULL,
            onceki_stok INTEGER,
            sonraki_stok INTEGER,
            tarih TIMESTAMPTZ DEFAULT NOW(),
            kullanici TEXT DEFAULT 'sistem',
            aciklama TEXT
        )""")
        c.execute("""
        CREATE TABLE IF NOT EXISTS tedarikciler (
            id SERIAL PRIMARY KEY,
            ad TEXT NOT NULL,
            telefon TEXT, email TEXT, adres TEXT, not_ TEXT,
            aktif INTEGER DEFAULT 1
        )""")
        # Nutrition columns migration (safe - IF NOT EXISTS)
        for col, typ in [
            ("kalori","REAL"), ("protein","REAL"), ("yag","REAL"),
            ("karbonhidrat","REAL"), ("seker","REAL"), ("tuz","REAL"),
            ("lif","REAL"), ("icindekiler","TEXT"),
            ("allerjenler","TEXT"), ("katki_maddeleri","TEXT")
        ]:
            try:
                c.execute(f"ALTER TABLE urunler ADD COLUMN IF NOT EXISTS {col} {typ}")
                c.commit()
            except Exception:
                pass
        # Firebase + profil kolonları migration
        for _col, _typ in [("firebase_uid","TEXT"), ("email","TEXT"),
                           ("hastaliklar","TEXT"), ("yeme_aliskanlik","TEXT")]:
            try:
                c.execute(f"ALTER TABLE kullanicilar ADD COLUMN IF NOT EXISTS {_col} {_typ}")
                c.commit()
            except Exception:
                pass
        c.commit()
        h = hashlib.sha256("admin123".encode()).hexdigest()
        c.execute(
            "INSERT INTO kullanicilar (kullanici_adi,sifre_hash,tam_ad,rol) VALUES (%s,%s,%s,%s)"
            " ON CONFLICT DO NOTHING",
            ("admin", h, "Sistem Yoneticisi", "admin")
        )
        c.commit()
    finally:
        c.close()

init_db()

# ═══════════════════════════════════════════════════
#  YARDIMCI
# ═══════════════════════════════════════════════════
def sh(s):
    return hashlib.sha256(s.encode()).hexdigest()

def kalan_gun(stt):
    if not stt:
        return None
    try:
        return (datetime.strptime(str(stt)[:10], "%Y-%m-%d").date() - date.today()).days
    except:
        return None

def stt_etiket(gun):
    if gun is None:
        return "SKT Belirtilmemis"
    if gun < 0:
        return f"TARIHI GECMIS ({abs(gun)} gun once!)"
    if gun == 0:
        return "BUGUN bitiyor!"
    if gun <= 3:
        return f"{gun} gun kaldi — Dikkat!"
    return f"{gun} gun kaldi"

def stt_renk(gun):
    if gun is None:
        return "#525252"
    if gun < 0:
        return "#e05252"
    if gun <= 3:
        return "#f0b429"
    if gun <= 7:
        return "#fb923c"
    return "#ffffff"

def openfoodfacts(barkod):
    """
    FIX: Wrapped in a single try/except with a combined timeout budget.
    Returns (name, category) or (None, None).
    """
    urls = [
        f"https://world.openfoodfacts.net/api/v2/product/{barkod}?fields=product_name,product_name_tr,generic_name,categories_tags",
        f"https://world.openfoodfacts.org/api/v2/product/{barkod}.json",
    ]
    headers = {"User-Agent": "NexStock/3.0 (github.com/nexstock)"}
    for url in urls:
        try:
            r = requests.get(url, headers=headers, timeout=3)
            if r.status_code != 200:
                continue
            data = r.json()
            if data.get("status") == 1 and "product" in data:
                p = data["product"]
                name = (p.get("product_name_tr") or p.get("product_name")
                        or p.get("generic_name") or "").strip()
                if not name:
                    name = f"Urun-{barkod[-6:]}"
                tags = p.get("categories_tags", [])
                cat = "Genel"
                if tags:
                    tr = [t for t in tags if t.startswith("tr:")]
                    cat = (tr[0].replace("tr:", "") if tr else tags[0].replace("en:", "")).replace("-", " ").title()
                return name, cat
        except Exception:
            continue
    return None, None


def go_upc(barkod):
    """
    Görev-4: go-upc.com API – ikinci barkod kaynağı.
    GO_UPC_KEY env variable yoksa sessizce atlanır.
    """
    api_key = os.environ.get("GO_UPC_KEY", "")
    if not api_key:
        return None, None
    try:
        r = requests.get(
            f"https://go-upc.com/api/v1/code/{barkod}",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=4,
        )
        if r.status_code != 200:
            return None, None
        data = r.json()
        product = data.get("product") or {}
        name = (product.get("name") or "").strip()
        cat  = (product.get("category") or "Genel").strip() or "Genel"
        return (name or None), cat
    except Exception:
        return None, None


def off_allerjen(barkod):
    """Misafir görünümü için OFF'tan alerjen, besin ve Nutri-Score bilgisi çeker."""
    ALLERJEN_TR = {
        "gluten": "Gluten", "wheat": "Buğday (Gluten)", "rye": "Çavdar (Gluten)",
        "barley": "Arpa (Gluten)", "oats": "Yulaf (Gluten)",
        "milk": "Süt / Laktoz", "eggs": "Yumurta",
        "peanuts": "Yer Fıstığı", "nuts": "Kabuklu Yemiş",
        "almonds": "Badem", "hazelnuts": "Fındık", "walnuts": "Ceviz",
        "cashews": "Kaju", "pistachios": "Antep Fıstığı",
        "fish": "Balık", "shellfish": "Kabuklu Deniz Ürünleri",
        "crustaceans": "Kabuklular", "molluscs": "Yumuşakçalar",
        "soybeans": "Soya", "celery": "Kereviz",
        "mustard": "Hardal", "sesame-seeds": "Susam", "sesame": "Susam",
        "sulphur-dioxide": "Sülfür Dioksit", "sulphites": "Sülfit",
        "lupin": "Acı Bakla",
    }
    LABEL_TR = {
        "en:gluten-free": "Glutensiz", "en:vegan": "Vegan",
        "en:vegetarian": "Vejeteryan", "en:organic": "Organik",
        "en:fair-trade": "Adil Ticaret", "en:no-additives": "Katkısız",
        "en:no-preservatives": "Koruyucusuz", "en:lactose-free": "Laktozsuz",
        "en:low-fat": "Az Yağlı", "en:low-sugar": "Az Şekerli",
    }
    url = (f"https://world.openfoodfacts.org/api/v2/product/{barkod}"
           "?fields=allergens_tags,traces_tags,nutriments,nutriscore_grade,"
           "nova_group,labels_tags,ingredients_text_tr,ingredients_text")
    headers = {"User-Agent": "NexStock/3.0 (github.com/nexstock)"}
    try:
        r = requests.get(url, headers=headers, timeout=4)
        if r.status_code != 200:
            return None
        data = r.json()
        if data.get("status") != 1 or "product" not in data:
            return None
        p = data["product"]
        def tag_tr(tag):
            key = tag.split(":", 1)[-1].lower()
            return ALLERJEN_TR.get(key, key.replace("-", " ").title())
        allerjenler = [tag_tr(t) for t in p.get("allergens_tags", []) if ":" in t]
        izler       = [tag_tr(t) for t in p.get("traces_tags",   []) if ":" in t]
        etiketler   = [LABEL_TR[lb] for lb in p.get("labels_tags", []) if lb in LABEL_TR]
        n = p.get("nutriments", {})
        beslenme = {
            "enerji":       n.get("energy-kcal_100g"),
            "yag":          n.get("fat_100g"),
            "doymus_yag":   n.get("saturated-fat_100g"),
            "karbonhidrat": n.get("carbohydrates_100g"),
            "seker":        n.get("sugars_100g"),
            "protein":      n.get("proteins_100g"),
            "tuz":          n.get("salt_100g"),
            "lif":          n.get("fiber_100g"),
        }
        icerik = (p.get("ingredients_text_tr") or p.get("ingredients_text") or "").strip()
        if len(icerik) > 500:
            icerik = icerik[:500] + "…"
        return {
            "allerjenler": allerjenler,
            "izler":       izler,
            "etiketler":   etiketler,
            "nutriscore":  (p.get("nutriscore_grade") or "").upper(),
            "nova":        p.get("nova_group"),
            "beslenme":    beslenme,
            "icerik":      icerik,
        }
    except Exception:
        return None


def get_toplam_stok(c, barkod):
    row = c.execute("SELECT COALESCE(SUM(miktar),0) AS total FROM partiler WHERE barkod=%s", (barkod,)).fetchone()
    return row["total"]

def get_en_yakin_stt(c, barkod):
    row = c.execute(
        "SELECT MIN(stt) AS mstt FROM partiler WHERE barkod=%s AND miktar>0 AND stt IS NOT NULL",
        (barkod,)
    ).fetchone()
    return row["mstt"] if row else None

def log_hareket(barkod, urun_adi, tip, miktar, aciklama, kullanici, onceki_override=None):
    """
    Opens and closes its own DB connection cleanly.
    Does NOT touch partiler for Cikis/Okutma here — that's handled by the callers.
    onceki_override: pass pre-deduction stock to get accurate log entries.
    """
    c = get_db()
    try:
        onceki = onceki_override if onceki_override is not None else get_toplam_stok(c, barkod)

        if tip == "Giris":
            sonraki = onceki + miktar
        elif tip in ("Cikis", "Okutma"):
            sonraki = max(0, onceki - miktar)
        else:
            sonraki = onceki

        c.execute(
            "INSERT INTO stok_hareketleri "
            "(barkod,urun_adi,hareket_tipi,miktar,onceki_stok,sonraki_stok,kullanici,aciklama) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (barkod, urun_adi, tip, miktar, onceki, sonraki, kullanici, aciklama)
        )
        c.commit()
    finally:
        c.close()

# ═══════════════════════════════════════════════════
#  AUTH
# ═══════════════════════════════════════════════════
def misafir_yap():
    session["user"]   = "misafir"
    session["rol"]    = "misafir"
    session["tam_ad"] = "Misafir"

def giris_gerekli(f):
    @functools.wraps(f)
    def dec(*a, **kw):
        if not session.get("user"):
            misafir_yap()
        return f(*a, **kw)
    return dec

def yetkili_giris(f):
    @functools.wraps(f)
    def dec(*a, **kw):
        if not session.get("user") or session.get("rol") in ("misafir", "goruntuleyici"):
            return redirect("/giris")
        return f(*a, **kw)
    return dec

# ═══════════════════════════════════════════════════
#  HTML ŞABLONU  (unchanged from original)
# ═══════════════════════════════════════════════════
BASE = r"""<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>NexStock — {{ title }}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Syne:wght@400;600;700;800&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
<style>
:root{
  --g:#ffffff;--g2:#e5e5e5;--bg:#060606;--panel:#0e0e0e;
  --card:#111111;--border:#222;--text:#f5f5f5;--sub:#d4d4d4;--muted:#525252;
  --accent:rgba(165,216,255,1);
}
*{box-sizing:border-box;margin:0;padding:0}
html{scroll-behavior:smooth}
html{overflow-x:hidden}
body{background:var(--bg);color:var(--text);font-family:'Syne',sans-serif;min-height:100vh}

body::after{content:'';position:fixed;inset:0;pointer-events:none;z-index:50;
  background-image:url("data:image/svg+xml,%3Csvg viewBox='0 0 512 512' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.75' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='.035'/%3E%3C/svg%3E");
  opacity:.45}

.main{animation:pageIn .6s cubic-bezier(.16,1,.3,1) both}
@keyframes pageIn{from{opacity:0;transform:translateY(16px)}to{opacity:1;transform:none}}

#loader{
  position:fixed;inset:0;z-index:9500;
  background:#030308;
  display:flex;flex-direction:column;align-items:center;justify-content:center;
  overflow:hidden;
}
#loader.phase-out{animation:loaderOut 1.2s cubic-bezier(.7,0,.3,1) forwards}
@keyframes loaderOut{0%{clip-path:inset(0 0 0 0)}100%{clip-path:inset(0 0 100% 0)}}
#loader-canvas{position:absolute;inset:0;width:100%;height:100%}
.ld-wrap{position:relative;z-index:1;display:flex;flex-direction:column;align-items:center;gap:0}
.ld-logo{
  font-family:'Bebas Neue',sans-serif;
  font-size:clamp(3.5rem,9vw,8rem);
  letter-spacing:20px;text-indent:20px;
  color:rgba(255,255,255,0);position:relative;overflow:hidden;
}
.ld-logo.in{animation:ldReveal 1s .3s cubic-bezier(.16,1,.3,1) forwards}
@keyframes ldReveal{
  0%{opacity:0;letter-spacing:40px;filter:blur(12px);color:rgba(255,255,255,0)}
  60%{filter:blur(0);color:rgba(255,255,255,.9)}
  100%{opacity:1;letter-spacing:14px;color:rgba(255,255,255,.88)}
}
.ld-logo .ld-shine{
  position:absolute;top:0;left:-100%;width:60%;height:100%;
  background:linear-gradient(90deg,transparent,rgba(255,255,255,.15),transparent);
  animation:ldShine 2s 1.2s ease-in-out forwards;
}
@keyframes ldShine{to{left:200%}}
.ld-rule{
  width:0;height:1px;
  background:linear-gradient(90deg,transparent,rgba(255,255,255,.3),transparent);
  margin:20px 0 24px;
  animation:ldRule .8s 1s cubic-bezier(.16,1,.3,1) forwards;
}
@keyframes ldRule{to{width:260px}}
.ld-status{display:flex;align-items:center;gap:14px;opacity:0;animation:ldFade .3s .1s forwards}
@keyframes ldFade{to{opacity:1}}
.ld-pct{
  font-family:'JetBrains Mono',monospace;
  font-size:.65rem;letter-spacing:2px;
  color:rgba(255,255,255,.35);min-width:38px;text-align:right;
}
.ld-bar-wrap{width:180px;height:1px;background:rgba(255,255,255,.06);position:relative;overflow:hidden;border-radius:1px}
.ld-bar{
  height:100%;width:0%;
  background:linear-gradient(90deg,var(--accent),#fff,var(--accent));
  transition:width .08s linear;
  box-shadow:0 0 12px var(--accent);
}
.ld-msg{
  font-family:'JetBrains Mono',monospace;
  font-size:.48rem;letter-spacing:3px;text-transform:uppercase;
  color:rgba(255,255,255,.18);min-width:140px;
}
.ld-tag{
  position:absolute;bottom:28px;
  font-family:'JetBrains Mono',monospace;
  font-size:.5rem;letter-spacing:5px;text-transform:uppercase;
  color:rgba(255,255,255,.08);
  opacity:0;animation:ldFade .4s 1.4s forwards;
}
#loader::after{
  content:'';position:absolute;inset:0;pointer-events:none;
  background:repeating-linear-gradient(0deg,transparent,transparent 2px,rgba(0,0,0,.06) 2px,rgba(0,0,0,.06) 3px);
  z-index:2;
}

@keyframes sktFlash{0%,100%{opacity:0}50%{opacity:1}}
@keyframes sktShake{0%,100%{transform:translateX(0)}15%{transform:translateX(-8px)}30%{transform:translateX(8px)}45%{transform:translateX(-6px)}60%{transform:translateX(6px)}75%{transform:translateX(-3px)}90%{transform:translateX(3px)}}

.hdr{
  position:fixed;top:0;left:0;right:0;z-index:1000;
  background:rgba(6,6,6,.92);
  backdrop-filter:blur(24px) saturate(1.2);
  border-bottom:1px solid rgba(255,255,255,.04);
  padding:0 40px;
  display:flex;align-items:center;justify-content:space-between;
  height:64px;
  transition:background .4s,border .4s;
  animation:hdrIn .8s cubic-bezier(.16,1,.3,1) both;
}
@keyframes hdrIn{from{opacity:0;transform:translateY(-100%)}to{opacity:1;transform:none}}
.hdr::after{content:'';position:absolute;bottom:-1px;left:0;width:100%;height:1px;
  background:linear-gradient(90deg,transparent,rgba(255,255,255,.25),var(--accent),rgba(255,255,255,.25),transparent);
  background-size:200% 100%;animation:hdrLine 6s linear infinite}
@keyframes hdrLine{0%{background-position:200% 0}100%{background-position:-200% 0}}

.logo-link{text-decoration:none}
.logo{
  font-family:'Bebas Neue',sans-serif;
  font-size:1.8rem;letter-spacing:4px;color:var(--text);
  transition:letter-spacing .3s,opacity .3s,text-shadow .3s;
  position:relative;
}
.logo:hover{opacity:.9;letter-spacing:6px;text-shadow:0 0 20px rgba(255,255,255,.2)}
.logo span{color:var(--g);font-style:normal}
.logo::after{
  content:'';position:absolute;top:0;left:-100%;width:60%;height:100%;
  background:linear-gradient(90deg,transparent,rgba(255,255,255,.08),transparent);
  animation:logoSweep 4s ease-in-out infinite;
}
@keyframes logoSweep{0%,80%,100%{left:-100%}40%{left:200%}}

.nav{display:flex;align-items:center;gap:1px;position:fixed;top:0;right:16px;z-index:1001;padding:0;height:64px;overflow-x:auto;scrollbar-width:none;max-width:calc(100vw - 220px)}
.nav::-webkit-scrollbar{display:none}
.nav a{
  color:var(--muted);text-decoration:none;
  font-size:.64rem;font-weight:700;letter-spacing:1px;text-transform:uppercase;
  padding:0 9px;height:64px;display:flex;align-items:center;
  transition:color .25s;position:relative;
  overflow:hidden;white-space:nowrap;
}
.nav a::after{content:'';position:absolute;bottom:0;left:9px;right:9px;height:1px;
  background:linear-gradient(90deg,transparent,var(--g),transparent);transform:scaleX(0);transition:transform .35s cubic-bezier(.16,1,.3,1)}
.nav a::before{content:'';position:absolute;inset:0;background:rgba(255,255,255,.03);opacity:0;transition:opacity .25s}
.nav a:hover{color:var(--text)}
.nav a:hover::before{opacity:1}
.nav a:hover::after,.nav a.active::after{transform:scaleX(1)}
.nav a.active{color:var(--g)}
.nav-divider{width:1px;height:20px;background:var(--border);margin:0 10px}
.rol-badge{
  font-family:'JetBrains Mono',monospace;
  font-size:.56rem;font-weight:600;letter-spacing:1px;text-transform:uppercase;
  padding:3px 7px;border:1px solid var(--border);color:var(--g);margin:0 4px;
  transition:all .3s cubic-bezier(.16,1,.3,1);
  position:relative;overflow:hidden;white-space:nowrap;
}
.rol-badge:hover{border-color:rgba(255,255,255,.3);box-shadow:0 0 12px rgba(255,255,255,.05)}
.rol-badge::after{content:'';position:absolute;inset:0;background:linear-gradient(110deg,transparent 30%,rgba(255,255,255,.06) 50%,transparent 70%);animation:badgeSweep 3s ease-in-out infinite}
@keyframes badgeSweep{0%,100%{transform:translateX(-100%)}50%{transform:translateX(100%)}}
.nav-user{font-size:.7rem;color:var(--sub);margin-right:4px;white-space:nowrap;max-width:90px;overflow:hidden;text-overflow:ellipsis}
.btn-login{
  background:var(--g)!important;color:#060606!important;font-weight:800!important;
  padding:5px 12px!important;letter-spacing:.8px;font-size:.62rem!important;
  clip-path:polygon(0 0,calc(100% - 6px) 0,100% 6px,100% 100%,6px 100%,0 calc(100% - 6px));
  transition:all .3s cubic-bezier(.16,1,.3,1)!important;
  position:relative;overflow:hidden;white-space:nowrap;
}
.btn-login::before{content:'';position:absolute;inset:0;background:linear-gradient(110deg,transparent 20%,rgba(255,255,255,.2) 50%,transparent 80%);transform:translateX(-100%);transition:transform .5s}
.btn-login:hover{background:var(--g2)!important;box-shadow:0 0 16px rgba(255,255,255,.15)}
.btn-login:hover::before{transform:translateX(100%)}
.btn-login::after{display:none!important}
.btn-logout{color:#e05252!important;transition:all .25s!important}
.btn-logout:hover{color:#ff7070!important}
.btn-logout::after{background:#e05252!important}

.main{padding:84px 40px 32px;max-width:1400px;margin:0 auto}

.page-title{
  font-family:'Bebas Neue',sans-serif;
  font-size:2.8rem;letter-spacing:2px;
  color:var(--text);margin-bottom:28px;
  display:flex;align-items:center;gap:16px;line-height:1;
  animation:titleSlide .7s cubic-bezier(.16,1,.3,1) both;
}
@keyframes titleSlide{from{opacity:0;transform:translateX(-20px)}to{opacity:1;transform:none}}
.page-title::before{content:'';display:block;width:4px;height:36px;background:var(--g);animation:barGrow .5s .2s cubic-bezier(.16,1,.3,1) both}
@keyframes barGrow{from{height:0}to{height:36px}}

.stat-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:1px;margin-bottom:32px;background:var(--border)}
.stat-card{
  background:var(--card);padding:24px 20px;text-align:center;
  position:relative;overflow:hidden;transition:all .35s cubic-bezier(.16,1,.3,1);
}
.stat-card:hover{background:#1a1a1a;transform:translateY(-3px);box-shadow:0 8px 24px rgba(0,0,0,.4)}
.stat-card::before{content:'';position:absolute;top:0;left:0;right:0;height:2px;
  background:linear-gradient(90deg,transparent,var(--g),var(--accent),var(--g),transparent);
  background-size:200% 100%;opacity:0;transition:opacity .3s}
.stat-card:hover::before{opacity:1;animation:hdrLine 3s linear infinite}
.stat-card .val{font-family:'Bebas Neue',sans-serif;font-size:2.8rem;line-height:1;margin-bottom:6px;transition:transform .3s cubic-bezier(.16,1,.3,1)}
.stat-card:hover .val{transform:scale(1.08)}
.stat-card .lbl{font-family:'JetBrains Mono',monospace;font-size:.68rem;color:var(--muted);letter-spacing:1px;text-transform:uppercase}

.tbl-wrap{background:var(--card);border:1px solid var(--border);overflow:hidden;margin-bottom:20px;transition:border-color .3s}
.tbl-wrap:hover{border-color:rgba(255,255,255,.1)}
table{width:100%;border-collapse:collapse}
th{
  background:#0a0a0a;padding:12px 16px;text-align:left;
  font-family:'JetBrains Mono',monospace;font-size:.72rem;color:var(--g);
  font-weight:600;letter-spacing:1.5px;text-transform:uppercase;white-space:nowrap;
  border-bottom:1px solid var(--border);
}
td{padding:11px 16px;border-bottom:1px solid rgba(255,255,255,.04);font-size:.85rem;transition:all .25s cubic-bezier(.16,1,.3,1)}
tr:last-child td{border-bottom:none}
tr:hover td{background:rgba(255,255,255,.04)}
tr{transition:transform .2s}
tr:hover{transform:translateX(3px)}

.panel{background:var(--card);border:1px solid var(--border);padding:24px;margin-bottom:20px;position:relative;overflow:hidden;transition:border-color .3s}
.panel:hover{border-color:rgba(255,255,255,.1)}
.panel::before{content:'';position:absolute;top:0;left:0;width:3px;height:100%;background:var(--g);opacity:.4;transition:opacity .3s}
.panel:hover::before{opacity:.8}
.panel h2{
  font-family:'Bebas Neue',sans-serif;font-size:1.3rem;letter-spacing:2px;
  color:var(--text);margin-bottom:16px;display:flex;align-items:center;gap:8px;
}
.panel h2::after{content:'';flex:1;height:1px;background:var(--border)}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:20px}

input,select,textarea{
  background:rgba(255,255,255,.03);color:var(--text);
  border:1px solid var(--border);
  padding:11px 14px;width:100%;margin-bottom:10px;
  font-size:.9rem;font-family:'Syne',sans-serif;
  transition:all .25s cubic-bezier(.16,1,.3,1);
  outline:none;border-radius:0;
}
input:focus,select:focus{border-color:var(--g);box-shadow:0 0 0 1px var(--g),0 0 20px rgba(255,255,255,.05)}
label{font-family:'JetBrains Mono',monospace;font-size:.72rem;color:var(--muted);letter-spacing:1px;text-transform:uppercase;display:block;margin-bottom:5px}

.btn{
  display:inline-flex;align-items:center;justify-content:center;gap:6px;
  padding:10px 22px;border:none;cursor:pointer;
  font-size:.82rem;font-weight:700;letter-spacing:1px;text-transform:uppercase;
  text-decoration:none;font-family:'Syne',sans-serif;
  transition:all .3s cubic-bezier(.16,1,.3,1);
  clip-path:polygon(0 0,calc(100% - 8px) 0,100% 8px,100% 100%,8px 100%,0 calc(100% - 8px));
  position:relative;overflow:hidden;
}
.btn::before{content:'';position:absolute;inset:0;background:linear-gradient(110deg,transparent 20%,rgba(255,255,255,.12) 50%,transparent 80%);transform:translateX(-100%);transition:transform .5s}
.btn:hover::before{transform:translateX(100%)}
.btn-green{background:var(--g);color:#060606}
.btn-green:hover{background:var(--g2);transform:translateY(-2px);box-shadow:0 8px 24px rgba(255,255,255,.15)}
.btn-red{background:#3d0f0f;color:#e05252;clip-path:none;border:1px solid #5a1515}
.btn-red:hover{background:#5a1515;transform:translateY(-2px)}
.btn-muted{background:rgba(255,255,255,.04);color:var(--sub);clip-path:none;border:1px solid var(--border)}
.btn-muted:hover{border-color:var(--g);color:var(--g);transform:translateY(-2px);box-shadow:0 6px 20px rgba(255,255,255,.06)}

.scan-wrap{max-width:600px;margin:0 auto;padding-top:20px}
.scan-title{font-family:'Bebas Neue',sans-serif;font-size:2.5rem;letter-spacing:2px;margin-bottom:24px;display:flex;align-items:center;gap:12px}
.scan-title::before{content:'';display:block;width:4px;height:32px;background:var(--g)}
.scan-input-row{display:flex;gap:8px;margin-bottom:8px}
.scan-input-row input{
  margin:0;font-family:'JetBrains Mono',monospace;font-size:1.1rem;
  letter-spacing:3px;text-align:center;
  background:rgba(255,255,255,.03);
}

.scan-result{
  margin-top:24px;overflow:hidden;position:relative;
  border:1px solid var(--border);
  animation:resultIn .6s cubic-bezier(.16,1,.3,1) both;
  transition:border-color .3s,box-shadow .3s;
}
.scan-result:hover{border-color:rgba(255,255,255,.1);box-shadow:0 12px 40px rgba(0,0,0,.3)}
@keyframes resultIn{from{opacity:0;transform:translateY(24px) scale(.97)}to{opacity:1;transform:none}}
.scan-result::before{
  content:'';position:absolute;top:0;left:0;right:0;height:2px;
  background:linear-gradient(90deg,transparent,var(--g),var(--accent),var(--g),transparent);
  background-size:200% 100%;
  animation:gradientSlide 3s ease-in-out infinite;z-index:2;
}
.scan-result::after{
  content:'';position:absolute;top:2px;left:0;right:0;height:40px;
  background:linear-gradient(180deg,rgba(165,216,255,.04),transparent);
  pointer-events:none;z-index:1;
}
@keyframes gradientSlide{0%{background-position:200% 0}100%{background-position:-200% 0}}
.scan-header{padding:20px 24px;display:flex;justify-content:space-between;align-items:flex-start;position:relative}
.scan-header::after{content:'';position:absolute;bottom:0;left:24px;right:24px;height:1px;background:var(--border)}
.scan-body{padding:16px 24px 20px;background:var(--card)}
.scan-urun-adi{
  font-family:'Bebas Neue',sans-serif;font-size:1.8rem;letter-spacing:1px;line-height:1;
  animation:nameIn .4s .1s cubic-bezier(.16,1,.3,1) both;
}
@keyframes nameIn{from{opacity:0;transform:translateX(-10px)}to{opacity:1;transform:none}}
.scan-meta{font-family:'JetBrains Mono',monospace;font-size:.72rem;color:var(--sub);margin-top:6px;letter-spacing:.5px}
.scan-skt{
  font-family:'JetBrains Mono',monospace;font-size:.85rem;font-weight:600;
  margin-top:12px;padding:8px 14px;display:inline-block;letter-spacing:.5px;
  animation:sktPop .3s .2s cubic-bezier(.16,1,.3,1) both;
}
@keyframes sktPop{from{opacity:0;transform:scale(.9)}to{opacity:1;transform:none}}

.kamera-box{
  border:1px solid rgba(255,255,255,.2);overflow:hidden;position:relative;
  margin-bottom:12px;background:#000;border-radius:2px;
}
#interactive{width:100%;height:300px;position:relative}
#interactive video{width:100%;height:100%;object-fit:cover}
#interactive canvas{display:none!important}
.drawingBuffer{display:none!important}
.kamera-overlay{
  position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);
  width:260px;height:130px;
  border:2px solid var(--g);
  box-shadow:0 0 0 9999px rgba(0,0,0,.55),0 0 24px rgba(165,216,255,.15) inset;
  pointer-events:none;
}
.kamera-overlay::after{
  content:'';position:absolute;left:4px;right:4px;height:2px;
  background:linear-gradient(90deg,transparent,var(--accent),transparent);
  box-shadow:0 0 12px var(--accent);
  animation:scanLaser 2s ease-in-out infinite;
  opacity:.8;
}
@keyframes scanLaser{0%{top:4px}50%{top:calc(100% - 6px)}100%{top:4px}}

.alert{
  padding:12px 16px;margin-bottom:16px;font-size:.88rem;font-weight:500;
  border-left:3px solid;font-family:'Syne',sans-serif;
  animation:alertIn .4s cubic-bezier(.16,1,.3,1) both;
}
@keyframes alertIn{from{opacity:0;transform:translateX(-10px)}to{opacity:1;transform:none}}
.alert-red{background:rgba(224,82,82,.06);color:#e05252;border-color:#e05252}
.alert-green{background:rgba(255,255,255,.03);color:var(--g);border-color:var(--g)}
.alert-yellow{background:rgba(240,180,41,.05);color:#f0b429;border-color:#f0b429}

.green{color:var(--g)}.red{color:#e05252}.yellow{color:#f0b429}.orange{color:#fb923c}.muted{color:var(--muted)}

.login-wrap{max-width:400px;margin:80px auto;animation:loginIn .7s cubic-bezier(.16,1,.3,1) both}
@keyframes loginIn{from{opacity:0;transform:translateY(24px) scale(.97)}to{opacity:1;transform:none}}
.login-wrap .panel{padding:40px}
.login-logo{font-family:'Bebas Neue',sans-serif;font-size:2.5rem;letter-spacing:4px;text-align:center;margin-bottom:6px}
.login-sub{font-family:'JetBrains Mono',monospace;font-size:.72rem;color:var(--muted);text-align:center;letter-spacing:2px;text-transform:uppercase;margin-bottom:32px}

/* ── MOBİL ── */
html,body{overflow-x:hidden}
.hamburger{
  display:none;background:none;border:none;
  color:var(--text);font-size:1.4rem;cursor:pointer;
  padding:8px;line-height:1;
}
@media(max-width:900px){
  html,body{overflow-x:hidden}
  .hdr{padding:0 16px}
  .main{padding:76px 12px 16px;max-width:100%}
  .grid2{grid-template-columns:1fr}
  .stat-grid{grid-template-columns:repeat(2,1fr)}
  .nav-user,.rol-badge{display:none}
  .tbl-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch}

  /* Nav hamburger */
  .hamburger{display:flex;align-items:center;justify-content:center}
  .nav{
    display:none;
    height:auto;
    position:fixed;top:64px;left:0;right:0;bottom:0;
    background:rgba(6,6,6,.97);
    backdrop-filter:blur(24px);
    flex-direction:column;
    padding:16px;gap:4px;
    z-index:200;overflow-y:auto;
  }
  .nav.mob-open{display:flex!important;flex-direction:column!important}
  .nav a{
    padding:14px 16px;font-size:.85rem;
    border-bottom:1px solid var(--border);
    width:100%;
  }
  .nav a::after{display:none}
  .nav-divider{width:100%;height:1px;margin:8px 0}
  .btn-login,.btn-logout{
    width:100%;text-align:center;
    padding:14px 16px!important;
    border-bottom:1px solid var(--border);
  }

  /* Sayfa başlığı taşmasın */
  .page-title{font-size:2rem}
  .scan-wrap{padding:0}
}
</style>
<script>
document.addEventListener('DOMContentLoaded',function(){
  var navType=(performance.getEntriesByType('navigation')[0]||{}).type;
  var isFirst=!sessionStorage.getItem('nx_v');
  var isReload=navType==='reload';
  if(!isFirst && !isReload){
    var lHide=document.getElementById('loader');
    if(lHide){lHide.style.display='none';}
    return;
  }
  sessionStorage.setItem('nx_v','1');
  var lc=document.getElementById('loader-canvas');
  if(!lc) return;
  var lctx=lc.getContext('2d');
  var W,H,CX,CY;
  function resize(){W=lc.width=window.innerWidth;H=lc.height=window.innerHeight;CX=W/2;CY=H/2;}
  resize();
  window.addEventListener('resize',resize);
  var particles=[];
  for(var i=0;i<120;i++){
    var angle=Math.random()*Math.PI*2;
    var dist=60+Math.random()*Math.min(W,H)*0.4;
    particles.push({x:CX+Math.cos(angle)*dist,y:CY+Math.sin(angle)*dist*0.5,r:1+Math.random()*2.5,delay:Math.random()});
  }
  var startT=null,running=true;
  function draw(ts){
    if(!startT) startT=ts;
    var prog=Math.min((ts-startT)/2400,1);
    var eased=1-Math.pow(1-prog,3);
    lctx.clearRect(0,0,W,H);
    var bg=lctx.createRadialGradient(CX,CY,0,CX,CY,Math.max(W,H)*.65);
    bg.addColorStop(0,'#0a0a14'); bg.addColorStop(1,'#030308');
    lctx.fillStyle=bg; lctx.fillRect(0,0,W,H);
    var g=lctx.createRadialGradient(CX,CY,0,CX,CY,160+eased*100);
    g.addColorStop(0,'rgba(165,216,255,'+(eased*.1)+')');
    g.addColorStop(1,'rgba(165,216,255,0)');
    lctx.fillStyle=g; lctx.beginPath(); lctx.arc(CX,CY,300,0,Math.PI*2); lctx.fill();
    for(var i=0;i<particles.length;i++){
      var p=particles[i];
      var pp=Math.max(0,Math.min((prog-p.delay*.4)/.6,1));
      if(pp<=0) continue;
      lctx.beginPath(); lctx.arc(p.x,p.y,p.r*pp,0,Math.PI*2);
      lctx.fillStyle='rgba(165,216,255,'+(pp*.6)+')'; lctx.fill();
    }
    lctx.strokeStyle='rgba(165,216,255,'+(eased*.06)+')'; lctx.lineWidth=.4;
    for(var a=0;a<particles.length;a+=2){
      for(var b=a+1;b<particles.length;b+=3){
        var dx=particles[a].x-particles[b].x, dy=particles[a].y-particles[b].y;
        if(dx*dx+dy*dy<8100){
          lctx.beginPath(); lctx.moveTo(particles[a].x,particles[a].y);
          lctx.lineTo(particles[b].x,particles[b].y); lctx.stroke();
        }
      }
    }
    if(running) requestAnimationFrame(draw);
  }
  requestAnimationFrame(draw);
  window._stopLoaderCanvas=function(){running=false;};
  setTimeout(function(){
    var el=document.getElementById('ld-logo');
    if(el) el.classList.add('in');
  },100);
  var ldBar=document.getElementById('ld-bar');
  var ldPct=document.getElementById('ld-pct');
  var ldMsg=document.getElementById('ld-msg');
  var loader=document.getElementById('loader');
  var msgs=['INITIALIZING','LOADING ASSETS','CONNECTING DB','CALIBRATING','SYSTEM READY'];
  var pct=0;
  var iv=setInterval(function(){
    pct+=Math.random()*5+4;
    if(pct>100) pct=100;
    if(ldBar) ldBar.style.width=pct+'%';
    if(ldPct) ldPct.textContent=Math.floor(pct)+'%';
    if(ldMsg) ldMsg.textContent=msgs[Math.min(Math.floor(pct/22),4)];
    if(pct>=100){
      clearInterval(iv);
      if(ldMsg) ldMsg.textContent='SYSTEM READY';
      if(ldPct) ldPct.textContent='100%';
      setTimeout(function(){
        if(loader) loader.classList.add('phase-out');
        window._stopLoaderCanvas&&window._stopLoaderCanvas();
        setTimeout(function(){ if(loader) loader.style.display='none'; },800);
      },150);
    }
  },30);
});
</script>
</head>
<body>
<div id="loader">
  <canvas id="loader-canvas"></canvas>
  <div class="ld-wrap">
    <div class="ld-logo" id="ld-logo">NEXSTOCK<span class="ld-shine"></span></div>
    <div class="ld-rule"></div>
    <div class="ld-status">
      <div class="ld-pct" id="ld-pct">0%</div>
      <div class="ld-bar-wrap"><div class="ld-bar" id="ld-bar"></div></div>
      <div class="ld-msg" id="ld-msg">INITIALIZING</div>
    </div>
  </div>
  <div class="ld-tag">DFC T&Uuml;RK&Iacute;YE 2026 &mdash; HORTOR</div>
</div>
<div class="hdr">
  <a href="/" class="logo-link">
    <div class="logo">Nex<span>Stock</span></div>
  </a>
  <button class="hamburger" id="mob-btn" onclick="mobMenu()" aria-label="Menu">☰</button>
</div>
<div class="nav" id="mob-nav">
    <a href="/tarama" class="{{ 'active' if page=='tarama' }}">Tarama</a>
    {% if session.get('rol') not in ['misafir','goruntuleyici'] %}
    <a href="/" class="{{ 'active' if page=='dashboard' }}">Dashboard</a>
    <a href="/urunler" class="{{ 'active' if page=='urunler' }}">Urunler</a>
    <a href="/partiler" class="{{ 'active' if page=='partiler' }}">Partiler</a>
    <a href="/hareketler" class="{{ 'active' if page=='hareketler' }}">Hareketler</a>
    {% if session.get('rol') in ['admin','mudur'] %}
    <a href="/raporlar" class="{{ 'active' if page=='raporlar' }}">Raporlar</a>
    {% endif %}
    {% if session.get('rol') == 'admin' %}
    <a href="/kullanicilar" class="{{ 'active' if page=='kullanicilar' }}">Kullanicilar</a>
    <a href="/ai-okuyucu" class="{{ 'active' if page=='ai-okuyucu' }}">AI Okuyucu</a>
    {% endif %}
    <div class="nav-divider"></div>
    <span class="rol-badge">{{ session.get('rol','') }}</span>
    <span class="nav-user">{{ session.get('tam_ad') or session.get('user') }}</span>
    <a href="/cikis" class="btn-logout">Cikis</a>
    {% else %}
    <div class="nav-divider"></div>
    <a href="/giris" class="btn-login">Giris Yap</a>
    {% endif %}
</div>
<div class="main">
CONTENT_BLOCK
</div>
<script>
var _mobOpen=false;
function mobMenu(){
  var nav=document.getElementById('mob-nav');
  var btn=document.getElementById('mob-btn');
  _mobOpen=!_mobOpen;
  if(_mobOpen){
    nav.classList.add('mob-open');
    nav.style.removeProperty('display');
  } else {
    nav.classList.remove('mob-open');
  }
  btn.textContent=_mobOpen?'✕':'☰';
}
document.addEventListener('DOMContentLoaded',function(){
  document.querySelectorAll('#mob-nav a').forEach(function(a){
    a.addEventListener('click',function(){
      document.getElementById('mob-nav').style.display='none';_mobOpen=false;
      document.getElementById('mob-btn').textContent='☰';
    });
  });
});
document.querySelectorAll('.btn-green,.btn-login').forEach(function(btn){
  btn.addEventListener('mousemove',function(e){
    var r=btn.getBoundingClientRect();
    var x=(e.clientX-r.left-r.width/2)*.15;
    var y=(e.clientY-r.top-r.height/2)*.15;
    btn.style.transform='translate('+x+'px,'+y+'px)';
  });
  btn.addEventListener('mouseleave',function(){btn.style.transform='';});
});
document.querySelectorAll('.stat-card .val').forEach(function(el){
  var target=parseInt(el.textContent);
  if(isNaN(target)||target===0) return;
  el.textContent='0';
  var io=new IntersectionObserver(function(entries){
    entries.forEach(function(en){
      if(en.isIntersecting){
        io.unobserve(el);
        var dur=1200,t0=performance.now();
        (function tick(now){
          var p=Math.min((now-t0)/dur,1);
          el.textContent=Math.floor((1-Math.pow(1-p,4))*target);
          if(p<1) requestAnimationFrame(tick); else el.textContent=target;
        })(t0);
      }
    });
  },{threshold:.3});
  io.observe(el);
});
</script>

<!-- ═══════════════ CHATBOT WIDGET ═══════════════ -->
{% if session.get('rol') in ['kullanici', 'admin'] %}
<style>
#cb-btn{position:fixed;bottom:24px;right:24px;width:52px;height:52px;border-radius:50%;background:var(--g);border:none;cursor:pointer;display:flex;align-items:center;justify-content:center;font-size:1.4rem;box-shadow:0 4px 24px rgba(16,185,129,.4);z-index:9000;transition:transform .2s}
#cb-btn:hover{transform:scale(1.1)}
#cb-panel{position:fixed;bottom:88px;right:24px;width:340px;max-width:calc(100vw - 32px);height:480px;max-height:calc(100vh - 120px);background:#0d0d0d;border:1px solid #1e1e1e;display:flex;flex-direction:column;z-index:9000;box-shadow:0 8px 40px rgba(0,0,0,.6);display:none}
#cb-header{padding:14px 16px;border-bottom:1px solid #1a1a1a;font-family:JetBrains Mono,monospace;font-size:.65rem;letter-spacing:2px;color:var(--g);text-transform:uppercase;display:flex;justify-content:space-between;align-items:center;flex-shrink:0}
#cb-messages{flex:1;overflow-y:auto;padding:12px;display:flex;flex-direction:column;gap:8px}
#cb-messages::-webkit-scrollbar{width:3px}
#cb-messages::-webkit-scrollbar-thumb{background:#1e1e1e}
.cb-msg{max-width:88%;padding:9px 13px;font-size:.82rem;line-height:1.5;border-radius:2px}
.cb-msg.user{align-self:flex-end;background:#10b98122;color:#f5f5f5;border:1px solid #10b98133}
.cb-msg.bot{align-self:flex-start;background:#111;color:#d4d4d4;border:1px solid #1e1e1e}
.cb-msg.bot.loading{color:#525252;font-style:italic}
#cb-input-row{padding:10px;border-top:1px solid #1a1a1a;display:flex;gap:6px;flex-shrink:0}
#cb-input{flex:1;background:#080808;border:1px solid #1a1a1a;color:#f5f5f5;padding:9px 12px;font-size:.82rem;font-family:inherit;outline:none}
#cb-input:focus{border-color:var(--g)}
#cb-send{background:var(--g);border:none;color:#000;font-weight:700;padding:9px 14px;cursor:pointer;font-size:.82rem;font-family:JetBrains Mono,monospace;letter-spacing:1px}
#cb-send:hover{opacity:.85}
</style>
<button id="cb-btn" onclick="cbToggle()" title="Besin Asistanı"><svg xmlns="http://www.w3.org/2000/svg" width="26" height="26" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><circle cx="12" cy="8" r="4"/><path stroke-linecap="round" d="M4 20c0-4 3.6-7 8-7s8 3 8 7"/></svg></button>
<div id="cb-panel">
  <div id="cb-header">
    <span style="display:flex;align-items:center;gap:8px"><svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><circle cx="12" cy="8" r="4"/><path stroke-linecap="round" d="M4 20c0-4 3.6-7 8-7s8 3 8 7"/></svg> Besin Asistanı</span>
    <button onclick="cbToggle()" style="background:none;border:none;color:#525252;cursor:pointer;font-size:1rem">✕</button>
  </div>
  <div id="cb-messages">
    <div class="cb-msg bot">Merhaba! Ben NexStock besin asistanıyım. Alerjin veya sağlık durumun varsa söyle, hangi ürünlerden uzak durman gerektiğini söylerim.</div>
  </div>
  <div id="cb-input-row">
    <input id="cb-input" placeholder="Mesajınızı yazın..." onkeydown="if(event.key==='Enter')cbSend()">
    <button id="cb-send" onclick="cbSend()">→</button>
  </div>
</div>
<script>
var _cbOpen=false;
var _cbHistory=[];
var _cbLoading=false;

function cbToggle(){
  _cbOpen=!_cbOpen;
  document.getElementById('cb-panel').style.display=_cbOpen?'flex':'none';
  document.getElementById('cb-btn').innerHTML=_cbOpen?'<span style="font-size:1.2rem">✕</span>':'<svg xmlns="http://www.w3.org/2000/svg" width="26" height="26" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><circle cx="12" cy="8" r="4"/><path stroke-linecap="round" d="M4 20c0-4 3.6-7 8-7s8 3 8 7"/></svg>';
  if(_cbOpen) setTimeout(function(){document.getElementById('cb-input').focus();},100);
}
function cbAppend(text,cls){
  var el=document.createElement('div');
  el.className='cb-msg '+cls;
  el.textContent=text;
  var msgs=document.getElementById('cb-messages');
  msgs.appendChild(el);
  msgs.scrollTop=msgs.scrollHeight;
  return el;
}
function cbSend(){
  if(_cbLoading) return;
  var inp=document.getElementById('cb-input');
  var msg=inp.value.trim();
  if(!msg) return;
  inp.value='';
  cbAppend(msg,'user');
  _cbHistory.push({role:'user',content:msg});
  _cbLoading=true;
  var loadEl=cbAppend('Düşünüyor…','bot loading');
  fetch('/api/chat',{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({messages:_cbHistory})
  }).then(function(r){return r.json();}).then(function(d){
    _cbLoading=false;
    loadEl.remove();
    var reply=d.reply||d.error||'Bir hata oluştu.';
    cbAppend(reply,'bot');
    _cbHistory.push({role:'assistant',content:reply});
  }).catch(function(e){
    _cbLoading=false;
    loadEl.remove();
    cbAppend('Bağlantı hatası: '+e.message,'bot');
  });
}
</script>
{% endif %}
</body></html>"""

def render(content, page="", title="NexStock", **kw):
    html = BASE.replace("CONTENT_BLOCK", content)
    return render_template_string(html, session=session, page=page, title=title, **kw)

# ═══════════════════════════════════════════════════
#  GİRİŞ / ÇIKIŞ
# ═══════════════════════════════════════════════════
@app.route("/giris", methods=["GET", "POST"])
def giris():
    hata = ""
    if request.method == "POST":
        k = request.form.get("k", "").strip()
        s = request.form.get("s", "").strip()
        c = get_db()
        row = c.execute(
            "SELECT * FROM kullanicilar WHERE kullanici_adi=%s AND sifre_hash=%s AND aktif=1",
            (k, sh(s))
        ).fetchone()
        c.close()
        if row:
            session["user"]   = row["kullanici_adi"]
            session["rol"]    = row["rol"]
            session["tam_ad"] = row["tam_ad"] or ""
            c2 = get_db()
            c2.execute("UPDATE kullanicilar SET son_giris=NOW() WHERE kullanici_adi=%s", (k,))
            c2.commit()
            c2.close()
            return redirect("/")
        hata = "Hatali kullanici adi veya sifre!"

    content = f"""
<div class="login-wrap">
  <div class="panel">
    <div class="login-logo">Nex<span style="color:var(--g)">Stock</span></div>
    <div class="login-sub">Envanter Yonetim Sistemi</div>
    {'<div class="alert alert-red">'+hata+'</div>' if hata else ''}
    <form method="POST">
      <label>KULLANICI ADI</label>
      <input name="k" placeholder="kullanici_adi" autofocus autocomplete="username">
      <label>SIFRE</label>
      <input name="s" type="password" placeholder="••••••••" autocomplete="current-password">
      <button type="submit" class="btn btn-green" style="width:100%;margin-top:4px;padding:12px">GIRIS YAP</button>
    </form>
    <div style="text-align:center;margin-top:16px">
      
    <div style="margin:16px 0;display:flex;align-items:center;gap:12px">
      <div style="flex:1;height:1px;background:#1a1a1a"></div>
      <span style="color:#525252;font-size:.75rem;font-family:JetBrains Mono,monospace">VEYA</span>
      <div style="flex:1;height:1px;background:#1a1a1a"></div>
    </div>
    <button type="button" onclick="googleGiris()" style="width:100%;background:#fff;color:#000;border:none;padding:11px;font-family:inherit;font-size:.85rem;font-weight:700;cursor:pointer;display:flex;align-items:center;justify-content:center;gap:10px;letter-spacing:.5px">
      <svg width="18" height="18" viewBox="0 0 48 48"><path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/><path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/><path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/><path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.18 1.48-4.97 2.36-8.16 2.36-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/></svg>
      Google ile Giriş Yap
    </button>
    <script src="https://www.gstatic.com/firebasejs/10.12.0/firebase-app-compat.js"></script>
    <script src="https://www.gstatic.com/firebasejs/10.12.0/firebase-auth-compat.js"></script>
    <script>
    var _fbConfig = {{
      apiKey:            "AIzaSyDQVp3H0DKjnfcl9-1fe51KBHV43K2TAmA",
      authDomain:        "nexstock-8c7ed.firebaseapp.com",
      projectId:         "nexstock-8c7ed",
      storageBucket:     "nexstock-8c7ed.firebasestorage.app",
      messagingSenderId: "960691238543",
      appId:             "1:960691238543:web:82c3dc0eef17a3eb30a2fa"
    }};
    if (!firebase.apps.length) firebase.initializeApp(_fbConfig);
    function googleGiris(){{
      var provider = new firebase.auth.GoogleAuthProvider();
      firebase.auth().signInWithPopup(provider).then(function(result){{
        return result.user.getIdToken();
      }}).then(function(token){{
        return fetch("/api/firebase-login",{{
          method:"POST",
          headers:{{"Content-Type":"application/json"}},
          body: JSON.stringify({{idToken: token}})
        }});
      }}).then(function(r){{ return r.json(); }}).then(function(data){{
        if(data.ok) window.location.href = data.redirect || "/";
        else alert("Giriş hatası: " + data.error);
      }}).catch(function(err){{
        alert("Google giriş hatası: " + err.message);
      }});
    }}
    </script>
    <a href="/kayit" style="color:var(--g);font-size:.82rem;text-decoration:none;font-family:JetBrains Mono,monospace">Hesap Oluştur →</a>
    </div>
  </div>
</div>"""
    return render(content, page="giris", title="Giris")

@app.route("/kayit", methods=["GET", "POST"])
def kayit():
    hata = ""
    basari = ""
    ROL_SECENEKLER = [
        ("misafir",   "Misafir — sadece tarama görüntüleyebilir"),
        ("kullanici", "Kullanıcı — tarama + besin asistanı"),
        ("kasiyer",   "Kasiyer — stok okutma ve ekleme"),
        ("admin",     "Admin — tam yetki"),
    ]
    if request.method == "POST":
        isim             = request.form.get("isim", "").strip()
        k                = request.form.get("k", "").strip()
        s                = request.form.get("s", "").strip()
        rol              = request.form.get("rol", "misafir").strip()
        hastaliklar      = ",".join(request.form.getlist("hastalik"))
        yeme_aliskanlik  = ",".join(request.form.getlist("yeme"))
        if rol not in [r[0] for r in ROL_SECENEKLER]:
            rol = "misafir"
        import re as _sre
        if not isim or not k or not s:
            hata = "Tüm alanlar zorunlu!"
        elif len(s) < 8:
            hata = "Şifre en az 8 karakter olmalı!"
        elif not _sre.search(r"[A-Z]", s):
            hata = "Şifre en az bir büyük harf içermeli! (A-Z)"
        elif not _sre.search(r"[a-z]", s):
            hata = "Şifre en az bir küçük harf içermeli! (a-z)"
        elif not _sre.search(r"[0-9]", s):
            hata = "Şifre en az bir rakam içermeli! (0-9)"
        elif not _sre.search(r"[!@#$%^&*()_+\-=\[\]{}|;:,.<>?/]", s):
            hata = "Şifre en az bir özel karakter içermeli! (!@#$% vb.)"
        else:
            c = get_db()
            try:
                mevcut = c.execute("SELECT id FROM kullanicilar WHERE kullanici_adi=%s", (k,)).fetchone()
                if mevcut:
                    hata = "Bu kullanıcı adı zaten alınmış!"
                else:
                    c.execute(
                        "INSERT INTO kullanicilar (kullanici_adi,sifre_hash,tam_ad,rol,hastaliklar,yeme_aliskanlik) VALUES (%s,%s,%s,%s,%s,%s)",
                        (k, sh(s), isim, rol, hastaliklar or None, yeme_aliskanlik or None)
                    )
                    c.commit()
                    basari = "Hesap oluşturuldu! Giriş yapabilirsin."
            except Exception as e:
                hata = f"Hata: {str(e)}"
            finally:
                c.close()

    rol_options = "".join(
        f'<option value="{r}">{l}</option>'
        for r, l in [("misafir","Misafir — sadece tarama"),
                     ("kullanici","Kullanıcı — tarama + asistan"),
                     ("kasiyer","Kasiyer — stok yönetimi"),
                     ("admin","Admin — tam yetki")]
    )

    def _acc_block(gid, baslik, inp, secenekler):
        pills = "".join(
            '<label class="tag-pill">'
            f'<input type="checkbox" name="{inp}" value="{v}" onchange="updateBadge(\'{gid}\')">'
            f'<span>{l}</span></label>'
            for v, l in secenekler
        )
        return (
            f'<div class="acc-item">'
            f'<button type="button" class="acc-trigger" onclick="toggleAcc(this,\'{gid}\')">'
            f'<span>{baslik}</span>'
            f'<span class="acc-badge" id="{gid}-badge"></span>'
            f'<span class="acc-arrow">&#8250;</span>'
            f'</button>'
            f'<div class="acc-body" id="{gid}-body" style="display:none">'
            f'<div class="tag-grid">{pills}</div>'
            f'</div></div>'
        )

    profil_acc = (
        '<style>'
        '.acc-item{margin-bottom:6px}'
        '.acc-trigger{width:100%;background:rgba(255,255,255,.03);border:1px solid #1a1a1a;'
        'color:#f5f5f5;padding:11px 14px;cursor:pointer;'
        "font-family:'JetBrains Mono',monospace;font-size:.72rem;"
        'letter-spacing:1px;text-transform:uppercase;'
        'display:flex;align-items:center;justify-content:space-between;'
        'transition:border-color .25s,background .25s}'
        '.acc-trigger:hover{border-color:rgba(255,255,255,.12);background:rgba(255,255,255,.05)}'
        '.acc-trigger.open{border-color:var(--g);background:rgba(16,185,129,.06)}'
        '.acc-arrow{font-size:1.1rem;transition:transform .3s cubic-bezier(.16,1,.3,1);color:var(--muted);line-height:1}'
        '.acc-trigger.open .acc-arrow{transform:rotate(90deg);color:var(--g)}'
        '.acc-badge{margin-left:8px;margin-right:auto;font-size:.62rem;background:var(--g);color:#060606;'
        'padding:1px 7px;font-weight:700;letter-spacing:.5px;display:none}'
        '.acc-badge.visible{display:inline-block}'
        '.acc-body{border:1px solid #1a1a1a;border-top:none;padding:14px;background:rgba(255,255,255,.015)}'
        '.tag-grid{display:flex;flex-wrap:wrap;gap:7px}'
        '.tag-pill{position:relative}'
        '.tag-pill input{position:absolute;opacity:0;width:0;height:0;pointer-events:none}'
        '.tag-pill span{display:inline-flex;align-items:center;padding:6px 13px;cursor:pointer;'
        "font-family:'JetBrains Mono',monospace;font-size:.7rem;letter-spacing:.5px;text-transform:uppercase;"
        'border:1px solid #1a1a1a;color:#686868;background:transparent;'
        'transition:all .2s cubic-bezier(.16,1,.3,1);user-select:none}'
        '.tag-pill span:hover{border-color:rgba(255,255,255,.15);color:#a0a0a0}'
        '.tag-pill input:checked + span{border-color:var(--g);color:#060606;background:var(--g)}'
        '</style>'
        + _acc_block("hastalik", "Hastalık / Alerji", "hastalik", [
            ("colyak","Çölyak"),("seker","Şeker Hastalığı"),("hipertansiyon","Hipertansiyon"),
            ("kolesterol","Yüksek Kolesterol"),("laktoz","Laktoz İnt."),
            ("fruktoz","Fruktoz İnt."),("gluten","Gluten Alerjisi"),("hicbiri","Hiçbiri"),
        ])
        + _acc_block("yeme", "Yeme Alışkanlığı", "yeme", [
            ("vegan","Vegan"),("vejetaryan","Vejetaryan"),("pescatarian","Pescatarian"),
            ("halal","Helal"),("kosher","Koşer"),("glutensiz","Glutensiz"),
            ("dusuk_seker","Düşük Şeker"),("dusuk_tuz","Düşük Tuz"),("hicbiri","Hiçbiri"),
        ])
        + '<script>'
        'function toggleAcc(btn,gid){'
        'var body=document.getElementById(gid+"-body");'
        'var open=body.style.display==="block";'
        'body.style.display=open?"none":"block";'
        'btn.classList.toggle("open",!open)}'
        'function updateBadge(gid){'
        'var n=document.querySelectorAll("input[name=\'"+gid+"\']:checked").length;'
        'var b=document.getElementById(gid+"-badge");'
        'b.textContent=n;b.classList.toggle("visible",n>0)}'
        '</script>'
    )

    content = f"""
<div class="login-wrap">
  <div class="panel">
    <div class="login-logo">Nex<span style="color:var(--g)">Stock</span></div>
    <div class="login-sub" style="margin-bottom:20px">Hesap Oluştur</div>
    {'<div class="alert alert-red">'+hata+'</div>' if hata else ''}
    {'<div class="alert alert-green">'+basari+'</div>' if basari else ''}
    <form method="POST" onsubmit="return checkPw()">
      <label>ROL</label>
      <select name="rol" id="rol-select" onchange="toggleProfilAlanlari(this.value)" style="width:100%;background:#0d0d0d;border:1px solid #1a1a1a;color:#f5f5f5;padding:10px 12px;font-family:inherit;font-size:.85rem;margin-bottom:4px">
        {rol_options}
      </select>
      <label>İSİM</label>
      <input name="isim" placeholder="Adınız Soyadınız" autofocus>
      <label>KULLANICI ADI</label>
      <input name="k" placeholder="kullanici_adi" autocomplete="username">
      <label>ŞİFRE</label>
      <input name="s" id="pw-input" type="password" placeholder="••••••••" autocomplete="new-password" oninput="updateStrength(this.value)">
      <div id="pw-bar" style="height:4px;background:#1a1a1a;border-radius:2px;margin:6px 0 4px">
        <div id="pw-fill" style="height:100%;width:0%;border-radius:2px;transition:width .3s,background .3s"></div>
      </div>
      <ul id="pw-rules" style="list-style:none;padding:0;margin:0 0 12px;font-size:.75rem;font-family:JetBrains Mono,monospace">
        <li id="r-len"  style="color:#525252">✗ En az 8 karakter</li>
        <li id="r-upper" style="color:#525252">✗ En az 1 büyük harf (A-Z)</li>
        <li id="r-lower" style="color:#525252">✗ En az 1 küçük harf (a-z)</li>
        <li id="r-num"  style="color:#525252">✗ En az 1 rakam (0-9)</li>
        <li id="r-spec" style="color:#525252">✗ En az 1 özel karakter (!@#$% vb.)</li>
      </ul>
      <div id="profil-alanlari" style="display:none;margin:16px 0 4px">
        {profil_acc}
      </div>
      <button type="submit" id="pw-submit" class="btn btn-green" style="width:100%;margin-top:8px;padding:12px;opacity:.4;cursor:not-allowed" disabled>KAYIT OL</button>
    </form>
    <script>
    function toggleProfilAlanlari(rol){{
      var el=document.getElementById('profil-alanlari');
      el.style.display=(rol==='misafir'||rol==='kullanici')?'block':'none';
    }}
    toggleProfilAlanlari(document.getElementById('rol-select').value);
    function updateStrength(v){{
      var rules={{
        'r-len':  v.length>=8,
        'r-upper':/[A-Z]/.test(v),
        'r-lower':/[a-z]/.test(v),
        'r-num':  /[0-9]/.test(v),
        'r-spec': /[!@#$%^&*()+\-=\[\]{{}}|;:,.<>?/]/.test(v)
      }};
      var score=0;
      for(var id in rules){{
        var ok=rules[id]; score+=ok?1:0;
        var el=document.getElementById(id);
        el.style.color=ok?'#10b981':'#525252';
        el.textContent=(ok?'\u2713':'\u2717')+' '+el.textContent.slice(2);
      }}
      var pct=score*20;
      var fill=document.getElementById('pw-fill');
      fill.style.width=pct+'%';
      fill.style.background=score<=2?'#ef4444':score<=3?'#f59e0b':score==4?'#3b82f6':'#10b981';
      var btn=document.getElementById('pw-submit');
      var allOk=Object.values(rules).every(Boolean);
      btn.disabled=!allOk;
      btn.style.opacity=allOk?'1':'.4';
      btn.style.cursor=allOk?'pointer':'not-allowed';
    }}
    function checkPw(){{
      var v=document.getElementById('pw-input').value;
      if(v.length<8||!/[A-Z]/.test(v)||!/[a-z]/.test(v)||!/[0-9]/.test(v)||!/[!@#$%^&*()+\-=\[\]{{}}|;:,.<>?/]/.test(v)){{
        return false;
      }}
      return true;
    }}
    </script>
    <div style="text-align:center;margin-top:16px">
      <a href="/giris" style="color:#525252;font-size:.82rem;text-decoration:none;font-family:JetBrains Mono,monospace">← Giriş Yap</a>
    </div>
  </div>
</div>"""
    return render(content, page="kayit", title="Kayit Ol")


@app.route("/admin/temizle-kullanicilar", methods=["POST"])
def admin_temizle_kullanicilar():
    if session.get("rol") != "admin":
        return "Yetkisiz", 403
    c = get_db()
    try:
        c.execute("DELETE FROM kullanicilar WHERE kullanici_adi != 'admin'")
        c.commit()
        silinen = c.execute("SELECT changes()").fetchone()
    except Exception:
        pass
    finally:
        c.close()
    return redirect("/kullanicilar")


@app.route("/api/firebase-login", methods=["POST"])
def firebase_login():
    _get_fb()
    data     = request.get_json(silent=True) or {}
    id_token = data.get("idToken","")
    if not id_token:
        return jsonify({"ok":False,"error":"token yok"}), 400
    try:
        decoded = _fb_auth.verify_id_token(id_token)
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)}), 401
    uid   = decoded["uid"]
    email = decoded.get("email","")
    name  = decoded.get("name", email.split("@")[0] if email else uid)
    c = get_db()
    try:
        row = c.execute("SELECT * FROM kullanicilar WHERE firebase_uid=%s", (uid,)).fetchone()
        if not row and email:
            row = c.execute("SELECT * FROM kullanicilar WHERE email=%s", (email,)).fetchone()
            if row:
                c.execute("UPDATE kullanicilar SET firebase_uid=%s WHERE id=%s", (uid, row["id"]))
                c.commit()
        if not row:
            uname = re.sub(r"[^a-z0-9_]","", (email.split("@")[0] if email else uid).lower())[:24] or "user"
            try:
                c.execute(
                    "INSERT INTO kullanicilar (kullanici_adi,sifre_hash,tam_ad,rol,firebase_uid,email)"
                    " VALUES (%s,%s,%s,%s,%s,%s)",
                    (uname, "", name, "kullanici", uid, email)
                )
                c.commit()
            except Exception:
                uname = uname + uid[-4:]
                c.execute(
                    "INSERT INTO kullanicilar (kullanici_adi,sifre_hash,tam_ad,rol,firebase_uid,email)"
                    " VALUES (%s,%s,%s,%s,%s,%s)",
                    (uname, "", name, "kullanici", uid, email)
                )
                c.commit()
            row = c.execute("SELECT * FROM kullanicilar WHERE firebase_uid=%s", (uid,)).fetchone()
        if not row or not row["aktif"]:
            return jsonify({"ok":False,"error":"Hesap pasif veya bulunamadı"}), 403
        session["user"]   = row["kullanici_adi"]
        session["rol"]    = row["rol"]
        session["tam_ad"] = row.get("tam_ad","")
        c.execute("UPDATE kullanicilar SET son_giris=NOW() WHERE id=%s", (row["id"],))
        c.commit()
        return jsonify({"ok":True, "redirect":"/"})
    finally:
        c.close()

@app.route("/cikis")
def cikis():
    session.clear()
    misafir_yap()
    return redirect("/tarama")

# ═══════════════════════════════════════════════════
#  ANA SAYFA
# ═══════════════════════════════════════════════════
@app.route("/")
@giris_gerekli
def index():
    if session.get("rol") in ("misafir", "goruntuleyici"):
        return redirect("/tarama")

    c = get_db()
    try:
        today = date.today().isoformat()
        s = {
            "toplam_urun":   c.execute("SELECT COUNT(*) FROM urunler").fetchone()[0],
            "toplam_stok":   c.execute("SELECT COALESCE(SUM(miktar),0) FROM partiler").fetchone()[0],
            "tarihi_gecmis": c.execute("SELECT COUNT(DISTINCT barkod) FROM partiler WHERE stt IS NOT NULL AND stt<%s AND miktar>0", (today,)).fetchone()[0],
            "yaklasan":      c.execute("SELECT COUNT(DISTINCT barkod) FROM partiler WHERE stt IS NOT NULL AND stt>=%s AND stt<=%s::date + interval '7 days' AND miktar>0", (today, today)).fetchone()[0],
            "kritik":        c.execute("SELECT COUNT(*) FROM urunler u WHERE (SELECT COALESCE(SUM(miktar),0) FROM partiler WHERE barkod=u.barkod) > 0 AND (SELECT COALESCE(SUM(miktar),0) FROM partiler WHERE barkod=u.barkod) <= u.min_stok").fetchone()[0],
            "stoksuz":       c.execute("SELECT COUNT(*) FROM urunler u WHERE (SELECT COALESCE(SUM(miktar),0) FROM partiler WHERE barkod=u.barkod) <= 0").fetchone()[0],
            "bugun":         c.execute("SELECT COUNT(*) FROM stok_hareketleri WHERE DATE(tarih)=CURRENT_DATE").fetchone()[0],
            "tedarikci":     c.execute("SELECT COUNT(*) FROM tedarikciler WHERE aktif=1").fetchone()[0],
        }
        skt_list = [dict(r) for r in c.execute("""
            SELECT u.barkod, u.urun_adi, u.kategori,
                   MIN(p.stt) as stt,
                   COALESCE(SUM(p.miktar), 0) as stok_adedi
            FROM partiler p JOIN urunler u ON p.barkod = u.barkod
            WHERE p.stt IS NOT NULL AND p.stt <= %s::date + interval '7 days' AND p.miktar > 0
            GROUP BY u.barkod, u.urun_adi, u.kategori ORDER BY MIN(p.stt)
        """, (today,)).fetchall()]
        dusuk = [dict(r) for r in c.execute("""
            SELECT u.*, COALESCE(ps.toplam, 0) as stok_adedi
            FROM urunler u
            LEFT JOIN (SELECT barkod, SUM(miktar) as toplam FROM partiler GROUP BY barkod) ps ON u.barkod = ps.barkod
            WHERE COALESCE(ps.toplam, 0) <= u.min_stok
            ORDER BY COALESCE(ps.toplam, 0) LIMIT 20
        """).fetchall()]
        son_har = [dict(r) for r in c.execute("SELECT * FROM stok_hareketleri ORDER BY tarih DESC LIMIT 10").fetchall()]
    finally:
        c.close()

    kfg = [
        ("toplam_urun",  "Toplam Urun",    "#ffffff"),
        ("toplam_stok",  "Toplam Stok",    "#34d399"),
        ("tarihi_gecmis","Tarihi Gecmis",  "#e05252"),
        ("yaklasan",     "Yaklasan SKT",   "#f0b429"),
        ("kritik",       "Kritik Stok",    "#fb923c"),
        ("stoksuz",      "Stoksuz",        "#a78bfa"),
        ("bugun",        "Bugun Islem",    "#ffffff"),
        ("tedarikci",    "Tedarikci",      "#a3a3a3"),
    ]
    kartlar = "".join(
        f'<div class="stat-card" style="border-color:{col}"><div class="val" style="color:{col}">{s[k]}</div><div class="lbl">{l}</div></div>'
        for k, l, col in kfg
    )

    skt_rows = ""
    for u in skt_list:
        gun = kalan_gun(u.get("stt"))
        rc  = stt_renk(gun)
        skt_rows += f'<tr><td><strong>{u["urun_adi"]}</strong></td><td style="color:#a3a3a3">{u.get("stt","—")}</td><td style="color:{rc};font-weight:600">{stt_etiket(gun)}</td><td>{u["stok_adedi"]}</td></tr>'

    dusuk_rows = ""
    for u in dusuk:
        cls = "red" if u["stok_adedi"] <= 0 else "yellow"
        dusuk_rows += f'<tr><td><strong>{u["urun_adi"]}</strong></td><td class="{cls}" style="font-weight:700">{u["stok_adedi"]}</td><td>{u.get("min_stok",5)}</td></tr>'

    har_rows = ""
    for h in son_har:
        cls = "green" if h["hareket_tipi"] == "Giris" else "red" if h["hareket_tipi"] in ["Cikis","Okutma"] else ""
        har_rows += f'<tr><td class="{cls}" style="font-weight:700">{h["hareket_tipi"]}</td><td>{h.get("urun_adi","—")}</td><td>{h["miktar"]}</td><td style="color:#a3a3a3">{str(h["tarih"])[:16]}</td><td>{h.get("kullanici","—")}</td></tr>'

    content = f"""
<div class="page-title">Dashboard</div>
<div class="stat-grid">{kartlar}</div>
<div class="grid2">
  <div class="panel">
    <h2>SKT Uyarilari (7 gun)</h2>
    <div class="tbl-wrap"><table>
      <tr><th>Urun</th><th>SKT</th><th>Durum</th><th>Stok</th></tr>
      {skt_rows or '<tr><td colspan=4 class="muted" style="text-align:center;padding:16px">Uyari yok ✓</td></tr>'}
    </table></div>
  </div>
  <div class="panel">
    <h2>Kritik Stok</h2>
    <div class="tbl-wrap"><table>
      <tr><th>Urun</th><th>Stok</th><th>Min</th></tr>
      {dusuk_rows or '<tr><td colspan=3 class="muted" style="text-align:center;padding:16px">Kritik stok yok ✓</td></tr>'}
    </table></div>
  </div>
</div>
<div class="panel">
  <h2>Son Islemler</h2>
  <div class="tbl-wrap"><table>
    <tr><th>Tip</th><th>Urun</th><th>Miktar</th><th>Tarih</th><th>Kullanici</th></tr>
    {har_rows or '<tr><td colspan=5 class="muted" style="text-align:center;padding:16px">Islem yok</td></tr>'}
  </table></div>
</div>"""
    return render(content, page="dashboard", title="Dashboard")

# ═══════════════════════════════════════════════════
#  TARAMA  — FIX: All DB connections use try/finally
# ═══════════════════════════════════════════════════
@app.route("/tarama", methods=["GET", "POST"])
@giris_gerekli
def tarama():
    sonuc_html  = ""
    alert_html  = ""

    if request.method == "POST":
        barkod = request.form.get("barkod", "").strip()
    elif request.args.get("barkod"):
        barkod = request.args.get("barkod", "").strip()
        if request.args.get("skt_ok"):
            alert_html = '<div class="alert alert-green">✓ SKT basariyla guncellendi!</div>'
    else:
        barkod = None

    if barkod:
        # ── Step 1: Look up product, auto-add from OFF if missing ──
        c = get_db()
        try:
            urun = c.execute("SELECT * FROM urunler WHERE barkod=%s", (barkod,)).fetchone()
            urun = dict(urun) if urun else None
        finally:
            c.close()

        if not urun:
            # FIX: OFF lookup happens OUTSIDE any open DB connection
            urun_adi, kategori = openfoodfacts(barkod)
            if not urun_adi:                          # Görev-4: go-upc fallback
                urun_adi, kategori = go_upc(barkod)
            if urun_adi:
                c = get_db()
                try:
                    c.execute(
                        "INSERT INTO urunler (barkod,urun_adi,kategori) VALUES (%s,%s,%s) ON CONFLICT (barkod) DO UPDATE SET urun_adi=EXCLUDED.urun_adi, kategori=EXCLUDED.kategori",
                        (barkod, urun_adi, kategori or "Genel")
                    )
                    c.execute(
                        "INSERT INTO partiler (barkod, miktar, ekleyen) VALUES (%s,%s,%s)",
                        (barkod, 30, "sistem")
                    )
                    c.commit()
                    urun = {"barkod": barkod, "urun_adi": urun_adi, "kategori": kategori or "Genel",
                            "min_stok": 5, "fiyat": 0.0, "aciklama": ""}
                    alert_html = f'<div class="alert alert-green">✓ Yeni urun eklendi: <strong>{urun_adi}</strong> (Open Food Facts)</div>'
                finally:
                    c.close()
            else:
                alert_html = f'<div class="alert alert-red">✗ Barkod veritabaninda bulunamadi: <strong>{barkod}</strong></div>'
                sonuc_html = f"""
<div class="scan-result">
  <div class="scan-header" style="background:#1a1000">
    <div>
      <div class="scan-urun-adi" style="font-size:1.2rem;color:#f0b429">Yeni Urun Ekle</div>
      <div class="scan-meta">Barkod: {barkod}</div>
    </div>
  </div>
  <div class="scan-body">
    <form method="POST" action="/urun-hizli-ekle">
      <input type="hidden" name="barkod" value="{barkod}">
      <label>URUN ADI</label>
      <input type="text" name="urun_adi" placeholder="Urun adi girin..." required autofocus>
      <label>KATEGORİ</label>
      <input type="text" name="kategori" placeholder="Genel" value="Genel">
      <label>FIYAT (TL)</label>
      <input type="number" name="fiyat" value="0.00" min="0" step="0.01">
      <label>MIN. STOK</label>
      <input type="number" name="min_stok" value="5" min="0">
      <label>ILK STOK MİKTARI</label>
      <input type="number" name="miktar" value="1" min="0">
      <label>SON TUKETİM TARİHİ (opsiyonel)</label>
      <input type="date" name="stt">
      <button type="submit" class="btn btn-green" style="width:100%;margin-top:4px">KAYDET ve TARA</button>
    </form>
  </div>
</div>"""

        if urun:
            # ── Step 2: Log scan (Okutma) on POST ──
            if request.method == "POST":
                # FEFO deduction for Okutma
                c = get_db()
                try:
                    onceki_stok = get_toplam_stok(c, barkod)  # read BEFORE deduction
                    remaining = 1
                    rows = c.execute(
                        "SELECT parti_id, miktar FROM partiler WHERE barkod=%s AND miktar>0 "
                        "ORDER BY CASE WHEN stt IS NULL THEN 1 ELSE 0 END, stt ASC, eklenme_tarihi ASC",
                        (barkod,)
                    ).fetchall()
                    for p in rows:
                        if remaining <= 0:
                            break
                        azalt = min(remaining, p["miktar"])
                        c.execute("UPDATE partiler SET miktar=miktar-%s WHERE parti_id=%s", (azalt, p["parti_id"]))
                        remaining -= azalt
                    c.commit()
                finally:
                    c.close()
                log_hareket(barkod, urun["urun_adi"], "Okutma", 1,
                            "Web tarama", session.get("user", "misafir"),
                            onceki_override=onceki_stok)

            # ── Step 3: Read current state for display ──
            c = get_db()
            try:
                toplam_stok   = get_toplam_stok(c, barkod)
                en_yakin      = get_en_yakin_stt(c, barkod)
                partiler_list = [dict(r) for r in c.execute(
                    "SELECT * FROM partiler WHERE barkod=%s AND miktar>0 "
                    "ORDER BY CASE WHEN stt IS NULL THEN 1 ELSE 0 END, stt ASC",
                    (barkod,)
                ).fetchall()]
            finally:
                c.close()

            gun = kalan_gun(en_yakin)
            rc  = stt_renk(gun)
            et  = stt_etiket(gun)

            if gun is not None and gun < 0:
                hdr_bg = "background:#3d0f0f"
                uyari  = '<div class="alert alert-red" style="margin-top:12px">⚠ TARIHI GECMIS URUN! RAFA KOYMA!</div>'
            elif gun is not None and gun == 0:
                hdr_bg = "background:#3d0f0f"
                uyari  = '<div class="alert alert-red" style="margin-top:12px">⚠ BUGUN BITIYOR!</div>'
            elif gun is not None and gun <= 3:
                hdr_bg = "background:#3d2800"
                uyari  = f'<div class="alert alert-yellow" style="margin-top:12px">⚠ {gun} gun kaldi — Dikkat!</div>'
            else:
                hdr_bg = "background:#0f0f0f"
                uyari  = ""

            # Parti rows
            parti_rows = ""
            for no, p in enumerate(partiler_list, 1):
                pgun = kalan_gun(p.get("stt"))
                prc  = stt_renk(pgun)
                pet  = stt_etiket(pgun) if p.get("stt") else "SKT Yok"
                parti_rows += f'''<tr>
                  <td style="font-family:JetBrains Mono,monospace;font-size:.8rem;color:#a3a3a3;font-weight:700">P{no}</td>
                  <td>
                    <div style="display:flex;gap:4px;align-items:center">
                      <span id="stt-txt-{p["parti_id"]}">{p.get("stt","—")}</span>
                      <button onclick="document.getElementById('stt-form-{p["parti_id"]}').style.display='block';this.style.display='none';document.getElementById('stt-txt-{p["parti_id"]}').style.display='none'" style="background:none;border:none;color:#a3a3a3;cursor:pointer;font-size:.7rem;padding:2px 4px">✎</button>
                    </div>
                    <div id="stt-form-{p["parti_id"]}" style="display:none;margin-top:4px">
                      <form method="POST" action="/parti-skt-guncelle" style="display:flex;gap:4px;margin:0">
                        <input type="hidden" name="parti_id" value="{p["parti_id"]}">
                        <input type="hidden" name="barkod" value="{barkod}">
                        <input type="date" name="stt" value="{p.get('stt','')}" style="margin:0;padding:3px 6px;font-size:.75rem;width:130px">
                        <button type="submit" class="btn btn-green" style="padding:3px 8px;font-size:.7rem">✓</button>
                      </form>
                    </div>
                  </td>
                  <td style="color:{prc};font-weight:600;font-size:.82rem">{pet}</td>
                  <td style="font-weight:700">{p["miktar"]} adet</td>
                  <td style="display:flex;gap:4px;flex-wrap:wrap">
                    <form method="POST" action="/parti-tukendi" style="margin:0">
                      <input type="hidden" name="parti_id" value="{p["parti_id"]}">
                      <input type="hidden" name="barkod" value="{barkod}">
                      <button type="submit" class="btn btn-muted" style="padding:3px 8px;font-size:.7rem;border-color:#f0b429;color:#f0b429">Tukendi</button>
                    </form>
                    <form method="POST" action="/parti-sil" style="margin:0">
                      <input type="hidden" name="parti_id" value="{p["parti_id"]}">
                      <input type="hidden" name="barkod" value="{barkod}">
                      <button type="submit" class="btn btn-red" style="padding:3px 8px;font-size:.7rem" onclick="return confirm('Parti silinsin mi?')">Sil</button>
                    </form>
                  </td>
                </tr>'''

            parti_opts = '<option value="fefo">Otomatik (FEFO)</option>'
            for no, p in enumerate(partiler_list, 1):
                parti_opts += f'<option value="{p["parti_id"]}">P{no} — {p.get("stt","SKT Yok")} ({p["miktar"]} adet)</option>'

            # ── Allerjen & Besin Bilgileri (tüm kullanıcılar) ──
            allerjen_section = ""
            if True:  # Görev-3: tüm roller için göster
                # Önce lokal DB'den bak (AI Okuyucu ile eklendiyse)
                _local_nut = {
                    "enerji": urun.get("kalori"),
                    "yag": urun.get("yag"),
                    "doymus_yag": None,
                    "karbonhidrat": urun.get("karbonhidrat"),
                    "seker": urun.get("seker"),
                    "protein": urun.get("protein"),
                    "tuz": urun.get("tuz"),
                    "lif": urun.get("lif"),
                }
                _has_local = any(v is not None for v in _local_nut.values())
                _local_ic = urun.get("icindekiler") or ""
                _local_allerjenler = urun.get("allerjenler") or ""
                _local_katki = urun.get("katki_maddeleri") or ""

                _off = off_allerjen(barkod)
                if _off or _has_local:
                    # Besin verisini belirle: lokal varsa onu kullan, yoksa OFF
                    if _has_local:
                        _b_final = _local_nut
                        _ic_final = _local_ic
                        _kaynak = "AI Okuyucu (Lokal DB)"
                    else:
                        _b_final = _off["beslenme"] if _off else {}
                        _ic_final = (_off["icerik"] if _off else "") or ""
                        _kaynak = "OpenFoodFacts.org"
                    _off = _off  # allerjen için hala kullan
                if _off or _has_local:
                    def _badge(nm, col="#e05252", ico="⚠"):
                        return (f'<span style="display:inline-flex;align-items:center;gap:3px;'
                                f'padding:3px 9px;background:{col}18;color:{col};border:1px solid {col}44;'
                                f'border-radius:3px;font-size:.72rem;font-weight:700;margin:2px;'
                                f'font-family:JetBrains Mono,monospace">{ico}&nbsp;{nm}</span>')
                    def _fmt(v, u="g"):
                        return f"{v:.1f}&nbsp;{u}" if v is not None else "—"

                    # Önce lokal DB allerjenlerini kullan
                    if _local_allerjenler:
                        _al_list = [x.strip() for x in _local_allerjenler.split(',') if x.strip()]
                        al_html = ("".join(_badge(a) for a in _al_list)
                                   or '<span style="color:#4ade80;font-size:.78rem">✓ Bilinen alerjen tespit edilmedi</span>')
                    else:
                        al_html = ("".join(_badge(a) for a in (_off["allerjenler"] if _off else []))
                                   or '<span style="color:#4ade80;font-size:.78rem">✓ Bilinen alerjen tespit edilmedi</span>')
                    iz_html = "".join(_badge(z, "#f0b429", "◦") for z in (_off["izler"] if _off else []))
                    et_html = "".join(_badge(e, "#4ade80", "✓") for e in (_off["etiketler"] if _off else []))

                    NS_C = {"A": "#038141", "B": "#85bb2f", "C": "#fecb02", "D": "#ee8100", "E": "#e63312"}
                    ns = _off["nutriscore"] if _off else None
                    ns_badge = (f'<span style="background:{NS_C[ns]};color:#fff;font-size:.88rem;'
                                f'font-weight:900;padding:4px 16px;border-radius:3px;letter-spacing:.5px;'
                                f'font-family:JetBrains Mono,monospace">NUTRI-SCORE&nbsp;{ns}</span>'
                                if ns in NS_C else "")

                    NOVA_L = {1: ("İşlenmemiş", "#4ade80"), 2: ("Mutfak Maddesi", "#86efac"),
                              3: ("İşlenmiş", "#fbbf24"),   4: ("Ultra İşlenmiş", "#e05252")}
                    nv = _off.get("nova") if _off else None
                    nova_badge = ""
                    if isinstance(nv, int) and nv in NOVA_L:
                        nl, nc = NOVA_L[nv]
                        nova_badge = (f'<span style="background:{nc}22;color:{nc};border:1px solid {nc}44;'
                                      f'font-size:.7rem;font-weight:700;padding:4px 12px;border-radius:3px;'
                                      f'font-family:JetBrains Mono,monospace">NOVA&nbsp;{nv}&nbsp;–&nbsp;{nl}</span>')

                    b = _b_final if _has_local else _off["beslenme"]
                    lf_row = (f'<tr><td style="color:#a3a3a3">Lif</td>'
                              f'<td style="text-align:right;color:#f5f5f5">{_fmt(b["lif"])}</td></tr>'
                              if b.get("lif") is not None else "")
                    ic = _ic_final
                    katki_html = ""
                    if _local_katki:
                        katki_html = (f'<div style="margin-top:8px;padding-top:8px;border-top:1px solid #1e1e2e">'
                                     f'<div style="font-family:JetBrains Mono,monospace;font-size:.58rem;'
                                     f'color:#525252;letter-spacing:2px;margin-bottom:4px">KATKI MADDELERİ</div>'
                                     f'<div style="font-size:.72rem;color:#8a8a8a">{_local_katki}</div></div>')
                    ic_html = (f'<div style="margin-top:12px;padding-top:10px;border-top:1px solid #1e1e2e">'
                               f'<div style="font-family:JetBrains Mono,monospace;font-size:.6rem;'
                               f'color:#525252;letter-spacing:2px;margin-bottom:4px">İÇERİK</div>'
                               f'<div style="font-size:.73rem;color:#8a8a8a;line-height:1.6">{ic}</div>'
                               f'{katki_html}</div>'
                               if ic else "")
                    iz_sec = (f'<div style="margin-bottom:10px"><div style="font-family:JetBrains Mono,monospace;'
                              f'font-size:.6rem;color:#a3a3a3;letter-spacing:2px;margin-bottom:6px">'
                              f'İZ MİKTARINDA İÇEREBİLİR</div>{iz_html}</div>'
                              if iz_html else "")
                    et_sec = (f'<div style="margin-bottom:12px"><div style="font-family:JetBrains Mono,monospace;'
                              f'font-size:.6rem;color:#a3a3a3;letter-spacing:2px;margin-bottom:6px">'
                              f'ETİKETLER</div>{et_html}</div>'
                              if et_html else "")

                    allerjen_section = "".join([
                        '<div style="margin-top:16px;padding:14px;background:#0d0d12;'
                        'border:1px solid #1e1e2e;border-radius:4px">',
                        '<div style="font-family:JetBrains Mono,monospace;font-size:.65rem;'
                        'color:#6d7aff;letter-spacing:2px;text-transform:uppercase;margin-bottom:12px">'
                        '🧬 Alerjen &amp; Besin Bilgileri</div>',
                        '<div style="margin-bottom:10px">',
                        '<div style="font-family:JetBrains Mono,monospace;font-size:.6rem;'
                        'color:#a3a3a3;letter-spacing:2px;margin-bottom:6px">ALERJENLER</div>',
                        al_html, '</div>',
                        iz_sec, et_sec,
                        '<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:14px">',
                        ns_badge, ("&nbsp;" if ns_badge and nova_badge else ""), nova_badge, '</div>',
                        '<div style="font-family:JetBrains Mono,monospace;font-size:.6rem;'
                        'color:#525252;letter-spacing:2px;margin-bottom:6px">'
                        'BESİN DEĞERLERİ (100 g/ml başına)</div>',
                        '<div class="tbl-wrap"><table style="font-size:.82rem;width:100%">',
                        '<tr><th style="text-align:left;font-weight:500;color:#a3a3a3">Besin Ögesi</th>',
                        '<th style="text-align:right;font-weight:500;color:#a3a3a3">Miktar</th></tr>',
                        f'<tr><td style="color:#a3a3a3">Enerji</td>'
                        f'<td style="text-align:right;color:#f5f5f5">{_fmt(b.get("enerji"), "kcal")}</td></tr>',
                        f'<tr><td style="color:#a3a3a3">Yağ</td>'
                        f'<td style="text-align:right;color:#f5f5f5">{_fmt(b.get("yag"))}</td></tr>',
                        (f'<tr><td style="color:#6a6a6a;font-size:.78rem;padding-left:12px">— doymuş</td>'
                        f'<td style="text-align:right;color:#6a6a6a;font-size:.78rem">{_fmt(b.get("doymus_yag"))}</td></tr>'
                        if b.get("doymus_yag") is not None else ""),
                        f'<tr><td style="color:#a3a3a3">Karbonhidrat</td>'
                        f'<td style="text-align:right;color:#f5f5f5">{_fmt(b.get("karbonhidrat"))}</td></tr>',
                        (f'<tr><td style="color:#6a6a6a;font-size:.78rem;padding-left:12px">— şeker</td>'
                        f'<td style="text-align:right;color:#6a6a6a;font-size:.78rem">{_fmt(b.get("seker"))}</td></tr>'
                        if b.get("seker") is not None else ""),
                        f'<tr><td style="color:#a3a3a3">Protein</td>'
                        f'<td style="text-align:right;color:#f5f5f5">{_fmt(b.get("protein"))}</td></tr>',
                        f'<tr><td style="color:#a3a3a3">Tuz</td>'
                        f'<td style="text-align:right;color:#f5f5f5">{_fmt(b.get("tuz"))}</td></tr>',
                        lf_row, '</table></div>',
                        ic_html,
                        f'<div style="margin-top:8px;font-size:.6rem;color:#2d2d2d;text-align:right">'
                        f'Kaynak: {_kaynak}</div>',
                        '</div>',
                    ])

            skt_gun_data = f'data-gun="{gun}"' if gun is not None else 'data-gun="null"'
            sonuc_html = f"""
<div id="skt-gun-data" {skt_gun_data} style="display:none"></div>
<div id="alert-type" data-tip="success" style="display:none"></div>
<div class="scan-result">
  <div class="scan-header" style="{hdr_bg}">
    <div>
      <div class="scan-urun-adi">{urun["urun_adi"]}</div>
      <div class="scan-meta">Barkod: {barkod}&nbsp;&nbsp;|&nbsp;&nbsp;Kategori: {urun.get("kategori","—")}</div>
    </div>
    <div style="font-size:1.7rem;font-weight:800;color:#ffffff">{float(urun.get("fiyat") or 0):.2f} TL</div>
  </div>
  <div class="scan-body">
    <span class="scan-skt" style="background:{rc}22;color:{rc};border:1px solid {rc}55">{et}</span>
    <div style="margin-top:10px;color:#a3a3a3;font-size:.9rem">
      Toplam Stok: <strong style="color:#f5f5f5">{toplam_stok} adet</strong>
      &nbsp;&nbsp;|&nbsp;&nbsp;Min: {urun.get("min_stok",5)} adet
      &nbsp;&nbsp;|&nbsp;&nbsp;Parti: <strong style="color:#f5f5f5">{len(partiler_list)}</strong>
    </div>
    {uyari}
    <div style="margin-top:16px">
      <div style="font-family:JetBrains Mono,monospace;font-size:.68rem;color:#a3a3a3;letter-spacing:2px;text-transform:uppercase;margin-bottom:8px">Partiler</div>
      <div class="tbl-wrap"><table style="font-size:.85rem">
        <tr><th>Parti</th><th>SKT</th><th>Durum</th><th>Miktar</th><th></th></tr>
        {parti_rows or '<tr><td colspan=5 style="text-align:center;color:#525252;padding:12px">Aktif parti yok</td></tr>'}
      </table></div>
    </div>
    <div style="margin-top:14px;display:flex;gap:8px">
      <button onclick="partiPanelAc()" class="btn btn-muted" style="flex:1">+ Yeni Parti Ekle</button>
      <button onclick="cikisPanelAc()" class="btn btn-muted" style="flex:1;border-color:#e05252;color:#e05252">- Stok Cikisi</button>
    </div>
    <div id="parti-panel" style="display:none;margin-top:10px;padding:14px;background:#0f0f0f;border:1px solid #1a1a1a">
      <div style="font-family:JetBrains Mono,monospace;font-size:.65rem;color:#a3a3a3;letter-spacing:2px;margin-bottom:10px">YENI PARTI EKLE</div>
      <form method="POST" action="/parti-ekle">
        <input type="hidden" name="barkod" value="{barkod}">
        <label style="font-size:.78rem;color:#a3a3a3">Son Tuketim Tarihi</label>
        <input type="date" name="stt" style="margin-bottom:8px">
        <label style="font-size:.78rem;color:#a3a3a3">Miktar (Adet)</label>
        <input type="number" name="miktar" value="1" min="1" style="margin-bottom:8px">
        <div style="display:flex;gap:8px">
          <button type="submit" class="btn btn-green" style="flex:1">Kaydet</button>
          <button type="button" onclick="partiPanelKapat()" class="btn btn-muted" style="flex:1">Iptal</button>
        </div>
      </form>
    </div>
    <div id="cikis-panel" style="display:none;margin-top:10px;padding:14px;background:#1a0f0f;border:1px solid #3d1a1a">
      <div style="font-family:JetBrains Mono,monospace;font-size:.65rem;color:#e05252;letter-spacing:2px;margin-bottom:10px">STOK CIKISI</div>
      <form method="POST" action="/stok-cikis">
        <input type="hidden" name="barkod" value="{barkod}">
        <label style="font-size:.78rem;color:#a3a3a3">Sebep</label>
        <select name="sebep" style="margin-bottom:8px">
          <option>Raftan Kaldirildi</option>
          <option>Hasarli Urun</option>
          <option>Iade</option>
          <option>SKT Gecmis — Imha</option>
          <option>Diger</option>
        </select>
        <label style="font-size:.78rem;color:#a3a3a3">Parti Secimi</label>
        <select name="parti_id" style="margin-bottom:8px">{parti_opts}</select>
        <label style="font-size:.78rem;color:#a3a3a3">Miktar</label>
        <input type="number" name="miktar" value="1" min="1" style="margin-bottom:8px">
        <label style="font-size:.78rem;color:#a3a3a3">Aciklama (opsiyonel)</label>
        <input type="text" name="aciklama" placeholder="Ek not..." style="margin-bottom:8px">
        <div style="display:flex;gap:8px">
          <button type="submit" class="btn btn-red" style="flex:1">Cikis Kaydet</button>
          <button type="button" onclick="cikisPanelKapat()" class="btn btn-muted" style="flex:1">Iptal</button>
        </div>
      </form>
    </div>
    {allerjen_section}
  </div>
</div>
<script>
function partiPanelAc(){{ document.getElementById('parti-panel').style.display='block'; document.getElementById('cikis-panel').style.display='none'; }}
function partiPanelKapat(){{ document.getElementById('parti-panel').style.display='none'; }}
function cikisPanelAc(){{ document.getElementById('cikis-panel').style.display='block'; document.getElementById('parti-panel').style.display='none'; }}
function cikisPanelKapat(){{ document.getElementById('cikis-panel').style.display='none'; }}
</script>"""

    # ── Son taranan urunler ──
    son_tarananlar_html = ""
    try:
        c = get_db()
        try:
            son_list = c.execute("""
                SELECT sh.barkod, sh.urun_adi, u.kategori,
                       COALESCE((SELECT SUM(miktar) FROM partiler WHERE barkod=sh.barkod), 0) as stok_adedi,
                       (SELECT MIN(stt) FROM partiler WHERE barkod=sh.barkod AND miktar>0 AND stt IS NOT NULL) as stt,
                       MAX(sh.tarih) as son_tarih
                FROM stok_hareketleri sh
                LEFT JOIN urunler u ON sh.barkod = u.barkod
                WHERE sh.hareket_tipi = 'Okutma'
                GROUP BY sh.barkod
                ORDER BY son_tarih DESC
                LIMIT 5
            """).fetchall()
        finally:
            c.close()

        if son_list:
            cards = ""
            for i, s in enumerate(son_list):
                s = dict(s)
                gun = kalan_gun(s.get("stt"))
                rc  = stt_renk(gun)
                et  = stt_etiket(gun)
                skt_badge = f'<span style="font-size:.62rem;padding:3px 8px;background:{rc}18;color:{rc};border:1px solid {rc}44;font-family:JetBrains Mono,monospace;letter-spacing:1px">{et}</span>'
                cards += f'''<a href="/tarama?barkod={s["barkod"]}" class="son-card" style="animation-delay:{i*0.08}s">
                  <div class="son-card-top">
                    <div class="son-card-name">{s["urun_adi"]}</div>
                    {skt_badge}
                  </div>
                  <div class="son-card-bottom">
                    <span class="son-card-barkod">{s["barkod"]}</span>
                    <span class="son-card-stok">{s.get("stok_adedi",0)} adet</span>
                  </div>
                </a>'''
            son_tarananlar_html = f'''
<div class="son-tarananlar">
  <div class="son-baslik">
    <span style="color:var(--muted);font-family:JetBrains Mono,monospace;font-size:.68rem;letter-spacing:2px;text-transform:uppercase">Son Tarananlar</span>
  </div>
  <div class="son-grid">{cards}</div>
</div>'''
    except Exception:
        pass

    # ── Mini istatistik ──
    stats_html = ""
    try:
        c = get_db()
        try:
            toplam     = c.execute("SELECT COUNT(*) FROM urunler").fetchone()[0]
            dusuk_sayisi = c.execute("SELECT COUNT(*) FROM urunler u WHERE COALESCE((SELECT SUM(miktar) FROM partiler WHERE barkod=u.barkod), 0) <= u.min_stok").fetchone()[0]
            bugun_scan = c.execute("SELECT COUNT(*) FROM stok_hareketleri WHERE hareket_tipi='Okutma' AND date(tarih)=CURRENT_DATE").fetchone()[0]
        finally:
            c.close()
        stats_html = f'''
<div class="mini-stats">
  <div class="ms-card" style="animation-delay:.05s"><div class="ms-val">{toplam}</div><div class="ms-lbl">Toplam Urun</div></div>
  <div class="ms-card" style="animation-delay:.1s"><div class="ms-val" style="color:#e05252">{dusuk_sayisi}</div><div class="ms-lbl">Dusuk Stok</div></div>
  <div class="ms-card" style="animation-delay:.15s"><div class="ms-val">{bugun_scan}</div><div class="ms-lbl">Bugun Tarama</div></div>
</div>'''
    except Exception:
        pass

    kamera_js = """
<script src="https://cdnjs.cloudflare.com/ajax/libs/quagga/0.12.1/quagga.min.js" defer></script>
<script>
var _ctx = null;
function _getCtx(){ if(!_ctx) _ctx = new (window.AudioContext||window.webkitAudioContext)(); return _ctx; }
function beep(frekans, sure, tip){
  try {
    var ctx = _getCtx();
    var osc = ctx.createOscillator();
    var gain = ctx.createGain();
    osc.connect(gain); gain.connect(ctx.destination);
    osc.type = tip || 'sine';
    osc.frequency.setValueAtTime(frekans, ctx.currentTime);
    gain.gain.setValueAtTime(0.4, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + sure/1000);
    osc.start(ctx.currentTime);
    osc.stop(ctx.currentTime + sure/1000);
  } catch(e){}
}
function sesOkundu(){ beep(1200, 180, 'square'); setTimeout(function(){ beep(1600, 120, 'square'); }, 200); }
function sesHata(){ beep(400, 200, 'sawtooth'); setTimeout(function(){ beep(300, 300, 'sawtooth'); }, 220); }
function sesSkt(gun){
  if(gun === null) return;
  if(gun < 0){
    var ctx = _getCtx();
    var osc = ctx.createOscillator();
    var gain = ctx.createGain();
    osc.connect(gain); gain.connect(ctx.destination);
    osc.type = 'sawtooth';
    gain.gain.setValueAtTime(0.6, ctx.currentTime);
    for(var i=0; i<12; i++){
      osc.frequency.setValueAtTime(800, ctx.currentTime + i*0.25);
      osc.frequency.linearRampToValueAtTime(1600, ctx.currentTime + i*0.25 + 0.125);
      osc.frequency.linearRampToValueAtTime(800, ctx.currentTime + i*0.25 + 0.25);
    }
    gain.gain.setValueAtTime(0.6, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 3);
    osc.start(ctx.currentTime);
    osc.stop(ctx.currentTime + 3);
    var flash = document.createElement('div');
    flash.style.cssText='position:fixed;inset:0;background:rgba(224,82,82,.25);z-index:9998;pointer-events:none;animation:sktFlash 0.3s ease-in-out 6 both';
    document.body.appendChild(flash);
    setTimeout(function(){ flash.remove(); }, 2000);
    var res = document.querySelector('.scan-result');
    if(res){ res.style.animation='sktShake 0.4s ease-in-out 3'; setTimeout(function(){ res.style.animation=''; },1500); }
  }
  else if(gun === 0){
    var ctx = _getCtx();
    var osc = ctx.createOscillator();
    var gain = ctx.createGain();
    osc.connect(gain); gain.connect(ctx.destination);
    osc.type = 'square';
    gain.gain.setValueAtTime(0.5, ctx.currentTime);
    for(var i=0; i<6; i++){
      osc.frequency.setValueAtTime(600, ctx.currentTime + i*0.3);
      osc.frequency.linearRampToValueAtTime(1200, ctx.currentTime + i*0.3 + 0.15);
    }
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 2);
    osc.start(ctx.currentTime);
    osc.stop(ctx.currentTime + 2);
  }
  else if(gun <= 3){ beep(900, 300, 'sine'); setTimeout(function(){ beep(900, 300, 'sine'); }, 400); }
}
window.addEventListener('DOMContentLoaded', function(){
  var sktEl = document.getElementById('skt-gun-data');
  if(sktEl){
    var gun = parseInt(sktEl.getAttribute('data-gun'));
    if(!isNaN(gun)) setTimeout(function(){ sesSkt(gun); }, 400);
  }
  var alertEl = document.getElementById('alert-type');
  if(alertEl){
    var tip = alertEl.getAttribute('data-tip');
    if(tip==='success') setTimeout(function(){ sesOkundu(); }, 200);
    else if(tip==='error') setTimeout(function(){ sesHata(); }, 200);
  }
});

function gecerliBarkod(kod, format){
  if(format && (format.indexOf('ean')!==-1 || format.indexOf('upc')!==-1)){
    if(!/^\\d+$/.test(kod)) return false;
  }
  if(format==='ean_13' && kod.length!==13) return false;
  if(format==='ean_8' && kod.length!==8) return false;
  if(format==='upc_a' && kod.length!==12) return false;
  if(format==='upc_e' && kod.length!==8) return false;
  if(format==='ean_13' && kod.length===13){
    var t=0;
    for(var i=0;i<12;i++) t+=parseInt(kod[i])*(i%2===0?1:3);
    if((10-t%10)%10!==parseInt(kod[12])) return false;
  }
  if(format==='ean_8' && kod.length===8){
    var t=0;
    for(var i=0;i<7;i++) t+=parseInt(kod[i])*(i%2===0?3:1);
    if((10-t%10)%10!==parseInt(kod[7])) return false;
  }
  return true;
}

var _aktif=false, _sayac={}, _son="", _sonT=0;

function kameraAc(){
  document.getElementById('kam-alan').style.display='block';
  var idleEl = document.getElementById('idle-hero');
  if(idleEl) idleEl.style.display='none';
  _aktif=true; _sayac={}; _son="";

  Quagga.init({
    inputStream:{
      name:"Live", type:"LiveStream",
      target:document.querySelector('#interactive'),
      constraints:{facingMode:"environment",width:{ideal:1280},height:{ideal:720}}
    },
    locator:{patchSize:"medium",halfSample:true},
    numOfWorkers:navigator.hardwareConcurrency||4,
    frequency:15,
    decoder:{
      readers:["ean_reader","ean_8_reader","upc_reader","upc_e_reader","code_128_reader"],
      multiple:false
    },
    locate:true
  }, function(err){
    if(err){
      document.getElementById('kam-durum').innerText='Hata: '+err;
      document.getElementById('kam-durum').style.color='#e05252';
    } else { Quagga.start(); }
  });

  Quagga.onProcessed(function(res){
    var dctx = document.querySelector('#interactive canvas.drawingBuffer');
    if(dctx) dctx.style.display='none';
  });

  Quagga.onDetected(function(res){
    var kod = res.codeResult.code;
    var format = res.codeResult.format;
    var hatalar = res.codeResult.decodedCodes.filter(function(x){return x.error!==undefined;});
    var toplamHata = hatalar.reduce(function(a,b){return a+b.error;},0);
    if(hatalar.length > 0 && toplamHata/hatalar.length > 0.20) return;
    if(!gecerliBarkod(kod, format)) return;
    _sayac[kod] = (_sayac[kod]||0)+1;
    document.getElementById('kam-durum').innerText='Okuma: '+kod+' ('+_sayac[kod]+'/3)';
    document.getElementById('kam-durum').style.color='rgba(165,216,255,.8)';
    if(Object.keys(_sayac).length > 8){ _sayac={}; return; }
    if(_sayac[kod]>=3){
      var simdi=Date.now();
      if(kod===_son && simdi-_sonT<3000) return;
      _son=kod; _sonT=simdi; _sayac={};
      document.getElementById('kam-durum').innerText='OKUNDU: '+kod;
      document.getElementById('kam-durum').style.color='#4ade80';
      sesOkundu();
      Quagga.stop(); _aktif=false;
      document.getElementById('barkod-input').value=kod;
      setTimeout(function(){
        document.getElementById('kam-alan').style.display='none';
        document.getElementById('barkod-form').submit();
      },600);
    }
  });
}

function kameraKapat(){
  if(_aktif){try{Quagga.stop();}catch(e){} _aktif=false;}
  document.getElementById('kam-alan').style.display='none';
  var idleEl = document.getElementById('idle-hero');
  if(idleEl) idleEl.style.display='';
}

function sayiOkuScan(input){
  if(!input.files||!input.files[0]) return;
  var file=input.files[0];
  var loadEl=document.getElementById('sayiOku-loading');
  var errEl=document.getElementById('sayiOku-err');
  loadEl.style.display='block';
  errEl.style.display='none';

  function submitBarkod(num){
    loadEl.style.display='none';
    var inp=document.getElementById('barkod-input');
    inp.value=num;
    inp.style.borderColor='var(--g)';
    inp.style.color='var(--g)';
    setTimeout(function(){ document.getElementById('barkod-form').submit(); }, 350);
  }

  function fallbackAI(dataUrl){
    fetch('/api/ai-barcode',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({image:dataUrl})
    }).then(function(r){return r.json();}).then(function(d){
      loadEl.style.display='none';
      if(d.error){errEl.textContent=d.error;errEl.style.display='block';return;}
      submitBarkod(d.barkod);
    }).catch(function(e){
      loadEl.style.display='none';
      errEl.textContent='Hata: '+e.message;errEl.style.display='block';
    });
  }

  // Once BarcodeDetector API dene (Android Chrome native - %100 dogru)
  if('BarcodeDetector' in window){
    var img=new Image();
    img.onload=function(){
      var bd=new BarcodeDetector({formats:['ean_13','ean_8','upc_a','upc_e','code_128','code_39']});
      bd.detect(img).then(function(codes){
        loadEl.style.display='none';
        if(codes&&codes.length>0){
          submitBarkod(codes[0].rawValue);
        } else {
          errEl.textContent='Barkod bulunamadi — barkodu cerceveleyerek tekrar dene';
          errEl.style.display='block';
        }
      }).catch(function(){
        // BarcodeDetector basarisiz, AI fallback
        var reader=new FileReader();
        reader.onload=function(e){fallbackAI(e.target.result);};
        reader.readAsDataURL(file);
      });
    };
    img.src=URL.createObjectURL(file);
  } else {
    // BarcodeDetector yok, AI kullan
    var reader=new FileReader();
    reader.onload=function(e){fallbackAI(e.target.result);};
    reader.readAsDataURL(file);
  }
}
</script>"""

    idle_hero = "" if sonuc_html else """
<div id="idle-hero" class="idle-hero">
  <canvas id="barcode-canvas" width="400" height="140"></canvas>
  <div class="idle-text">Barkodu okutun veya kamerayi acin</div>
  <div class="idle-sub">EAN-13 &middot; EAN-8 &middot; UPC-A &middot; Code 128</div>
</div>
<script>
(function(){
  var c=document.getElementById('barcode-canvas');
  if(!c) return;
  var ctx=c.getContext('2d');
  var W=c.width, H=c.height;
  var bars=[];
  for(var i=0;i<55;i++){
    bars.push({
      x: 24 + (i/55)*(W-48),
      w: Math.random()>0.5 ? 2 : 3,
      h: 70 + Math.random()*40,
      delay: Math.random()*2,
      speed: .4+Math.random()*.5
    });
  }
  var t=0;
  function draw(){
    t+=0.016;
    ctx.clearRect(0,0,W,H);
    for(var i=0;i<bars.length;i++){
      var b=bars[i];
      var phase=Math.sin(t*b.speed+b.delay)*0.3+0.7;
      ctx.fillStyle='rgba(255,255,255,'+(phase*0.2)+')';
      ctx.fillRect(b.x, (H-b.h)/2, b.w, b.h);
    }
    var scanY = H*0.15 + (H*0.7)*((Math.sin(t*1.2)+1)/2);
    ctx.strokeStyle='rgba(165,216,255,0.45)';
    ctx.lineWidth=1;
    ctx.shadowColor='rgba(165,216,255,0.7)';
    ctx.shadowBlur=10;
    ctx.beginPath();ctx.moveTo(18,scanY);ctx.lineTo(W-18,scanY);ctx.stroke();
    ctx.shadowBlur=0;
    requestAnimationFrame(draw);
  }
  draw();
})();
</script>"""

    content = f"""
{kamera_js}
<style>
.idle-hero{{
  text-align:center;margin:28px 0 24px;padding:24px 0;
  border:1px solid var(--border);background:var(--card);
  position:relative;overflow:hidden;
  animation:resultIn .6s cubic-bezier(.16,1,.3,1) both;
}}
.idle-hero canvas{{display:block;margin:0 auto;opacity:.8}}
.idle-text{{font-family:'Bebas Neue',sans-serif;font-size:1.3rem;letter-spacing:3px;color:var(--sub);margin-top:14px;}}
.idle-sub{{font-family:'JetBrains Mono',monospace;font-size:.58rem;letter-spacing:2px;color:var(--muted);margin-top:5px;text-transform:uppercase;}}
.idle-hero::before{{
  content:'';position:absolute;top:0;left:0;right:0;height:2px;
  background:linear-gradient(90deg,transparent,rgba(165,216,255,.6),transparent);
  animation:gradientSlide 3s ease-in-out infinite;background-size:200% 100%;
}}
.son-tarananlar{{margin-top:24px;animation:resultIn .5s .15s cubic-bezier(.16,1,.3,1) both}}
.son-baslik{{margin-bottom:10px}}
.son-grid{{display:flex;flex-direction:column;gap:5px}}
.son-card{{
  display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;
  padding:13px 16px;background:var(--card);border:1px solid var(--border);
  text-decoration:none;color:var(--text);
  transition:all .3s cubic-bezier(.16,1,.3,1);
  animation:alertIn .4s cubic-bezier(.16,1,.3,1) both;
  position:relative;overflow:hidden;
}}
.son-card::after{{
  content:'';position:absolute;bottom:0;left:0;right:0;height:1px;
  background:linear-gradient(90deg,transparent,var(--g),transparent);
  transform:scaleX(0);transition:transform .4s cubic-bezier(.16,1,.3,1);
}}
.son-card:hover{{background:rgba(255,255,255,.03);border-color:rgba(255,255,255,.1);transform:translateX(4px)}}
.son-card:hover::after{{transform:scaleX(1)}}
.son-card-top{{display:flex;align-items:center;gap:10px;flex:1;min-width:0}}
.son-card-name{{font-family:'Syne',sans-serif;font-size:.85rem;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}}
.son-card-bottom{{display:flex;align-items:center;gap:12px}}
.son-card-barkod{{font-family:'JetBrains Mono',monospace;font-size:.68rem;color:var(--muted);letter-spacing:1px}}
.son-card-stok{{font-family:'JetBrains Mono',monospace;font-size:.68rem;color:var(--sub);letter-spacing:1px}}
.mini-stats{{display:grid;grid-template-columns:repeat(3,1fr);gap:1px;background:var(--border);margin-bottom:20px;}}
.ms-card{{background:var(--card);padding:16px 12px;text-align:center;transition:all .3s cubic-bezier(.16,1,.3,1);animation:resultIn .4s cubic-bezier(.16,1,.3,1) both;}}
.ms-card:hover{{background:rgba(255,255,255,.03)}}
.ms-val{{font-family:'Bebas Neue',sans-serif;font-size:2rem;line-height:1;color:var(--g)}}
.ms-lbl{{font-family:'JetBrains Mono',monospace;font-size:.58rem;color:var(--muted);letter-spacing:1.5px;text-transform:uppercase;margin-top:4px}}
</style>
<div class="scan-wrap">
  <div class="page-title">Barkod Tarama</div>
  {stats_html}
  {alert_html}

  <div id="kam-alan" style="display:none;margin-bottom:12px">
    <div class="kamera-box">
      <div id="interactive"></div>
      <div class="kamera-overlay"></div>
    </div>
    <div id="kam-durum" style="text-align:center;color:#a3a3a3;font-size:.85rem;padding:8px 0;font-family:JetBrains Mono,monospace;letter-spacing:1px">Barkodu cerceve icine getirin...</div>
    <button onclick="kameraKapat()" class="btn btn-red" style="width:100%;margin-top:6px">Kamerayi Kapat</button>
  </div>

  <form method="POST" id="barkod-form">
    <div class="scan-input-row">
      <input name="barkod" id="barkod-input" placeholder="Barkod numarasi..." autofocus autocomplete="off">
      <button type="submit" class="btn btn-green" style="white-space:nowrap;padding:9px 18px">OKUT</button>
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-top:0">
      <button type="button" onclick="kameraAc()" class="btn btn-muted" style="width:100%;font-size:.8rem;padding:11px 6px">
        📷 Şekil Okuma
      </button>
      <button type="button" onclick="document.getElementById('sayiOkuInput').click()" class="btn btn-muted" style="width:100%;font-size:.8rem;padding:11px 6px">
        🔢 Sayı Okuma
      </button>
    </div>
    <input type="file" id="sayiOkuInput" accept="image/*" capture="environment" style="display:none" onchange="sayiOkuScan(this)">
    <div id="sayiOku-loading" style="display:none;text-align:center;padding:10px;font-family:JetBrains Mono,monospace;font-size:.65rem;color:#525252;letter-spacing:2px">BARKOD OKUNUYOR…</div>
    <div id="sayiOku-err" style="display:none;color:#e05252;font-size:.75rem;margin-top:4px;text-align:center"></div>
  </form>

  {idle_hero}
  {sonuc_html}
  {son_tarananlar_html}
</div>"""
    return render(content, page="tarama", title="Tarama")

# ═══════════════════════════════════════════════════
#  HIZLI URUN EKLEME (manual add when barcode not found)
# ═══════════════════════════════════════════════════
@app.route("/urun-hizli-ekle", methods=["POST"])
@giris_gerekli
def urun_hizli_ekle():
    barkod   = request.form.get("barkod", "").strip()
    urun_adi = request.form.get("urun_adi", "").strip()
    kategori = request.form.get("kategori", "Genel").strip() or "Genel"
    fiyat    = float(request.form.get("fiyat", 0) or 0)
    min_stok = int(request.form.get("min_stok", 5) or 5)
    miktar   = int(request.form.get("miktar", 1) or 1)
    stt      = request.form.get("stt", "").strip() or None

    if not barkod or not urun_adi:
        return redirect("/tarama")

    c = get_db()
    try:
        c.execute(
            "INSERT INTO urunler (barkod,urun_adi,kategori,min_stok,fiyat) VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (barkod, urun_adi, kategori, min_stok, fiyat)
        )
        if miktar > 0:
            c.execute(
                "INSERT INTO partiler (barkod, stt, miktar, ekleyen) VALUES (%s,%s,%s,%s)",
                (barkod, stt, miktar, session.get("user", "sistem"))
            )
        c.commit()
    finally:
        c.close()

    if miktar > 0:
        log_hareket(barkod, urun_adi, "Giris", miktar,
                    f"Manuel ekleme (SKT: {stt or 'Belirtilmemis'})",
                    session.get("user", "misafir"))

    return redirect(f"/tarama?barkod={barkod}&skt_ok=1")

# ═══════════════════════════════════════════════════
#  PARTI ISLEMLERI  — FIX: all use try/finally
# ═══════════════════════════════════════════════════
@app.route("/parti-ekle", methods=["POST"])
@giris_gerekli
def parti_ekle():
    barkod = request.form.get("barkod", "").strip()
    stt    = request.form.get("stt", "").strip() or None
    miktar = int(request.form.get("miktar", 1) or 1)
    if barkod and miktar > 0:
        c = get_db()
        try:
            c.execute(
                "INSERT INTO partiler (barkod, stt, miktar, ekleyen) VALUES (%s,%s,%s,%s)",
                (barkod, stt, miktar, session.get("user", "sistem"))
            )
            c.commit()
            urun = c.execute("SELECT urun_adi FROM urunler WHERE barkod=%s", (barkod,)).fetchone()
            urun_adi = urun["urun_adi"] if urun else barkod
        finally:
            c.close()
        log_hareket(barkod, urun_adi, "Giris", miktar,
                    f"Yeni parti eklendi (SKT: {stt or 'Belirtilmemis'})",
                    session.get("user", "misafir"))
    return redirect(f"/tarama?barkod={barkod}&skt_ok=1")

@app.route("/parti-tukendi", methods=["POST"])
@giris_gerekli
def parti_tukendi():
    parti_id = request.form.get("parti_id", "").strip()
    barkod   = request.form.get("barkod", "").strip()
    if parti_id:
        c = get_db()
        try:
            parti = c.execute("SELECT miktar FROM partiler WHERE parti_id=%s", (parti_id,)).fetchone()
            eski_miktar = parti["miktar"] if parti else 0
            c.execute("UPDATE partiler SET miktar=0 WHERE parti_id=%s", (parti_id,))
            c.commit()
            urun = c.execute("SELECT urun_adi FROM urunler WHERE barkod=%s", (barkod,)).fetchone()
            urun_adi = urun["urun_adi"] if urun else barkod
        finally:
            c.close()
        log_hareket(barkod, urun_adi, "Cikis", eski_miktar,
                    f"Parti #{parti_id} tukendi olarak isaretlendi",
                    session.get("user", "misafir"))
    return redirect(f"/tarama?barkod={barkod}")

@app.route("/stok-cikis", methods=["POST"])
@giris_gerekli
def stok_cikis():
    barkod   = request.form.get("barkod", "").strip()
    miktar   = int(request.form.get("miktar", 1) or 1)
    sebep    = request.form.get("sebep", "Raftan Kaldirildi").strip()
    aciklama = request.form.get("aciklama", "").strip()
    parti_id = request.form.get("parti_id", "").strip()
    if barkod and miktar > 0:
        c = get_db()
        try:
            if parti_id and parti_id != "fefo":
                p = c.execute("SELECT miktar FROM partiler WHERE parti_id=%s", (parti_id,)).fetchone()
                if p:
                    azalt = min(miktar, p["miktar"])
                    c.execute("UPDATE partiler SET miktar=GREATEST(0,miktar-%s) WHERE parti_id=%s", (azalt, parti_id))
                    c.commit()
            else:
                remaining = miktar
                rows = c.execute(
                    "SELECT parti_id, miktar FROM partiler WHERE barkod=%s AND miktar>0 "
                    "ORDER BY CASE WHEN stt IS NULL THEN 1 ELSE 0 END, stt ASC",
                    (barkod,)
                ).fetchall()
                for p in rows:
                    if remaining <= 0:
                        break
                    azalt = min(remaining, p["miktar"])
                    c.execute("UPDATE partiler SET miktar=miktar-%s WHERE parti_id=%s", (azalt, p["parti_id"]))
                    remaining -= azalt
                c.commit()
            urun = c.execute("SELECT urun_adi FROM urunler WHERE barkod=%s", (barkod,)).fetchone()
            urun_adi = urun["urun_adi"] if urun else barkod
            onceki   = get_toplam_stok(c, barkod) + miktar
            sonraki  = get_toplam_stok(c, barkod)
            c.execute(
                "INSERT INTO stok_hareketleri "
                "(barkod,urun_adi,hareket_tipi,miktar,onceki_stok,sonraki_stok,kullanici,aciklama) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (barkod, urun_adi, "Cikis", miktar, onceki, sonraki,
                 session.get("user", "misafir"),
                 f"{sebep}: {aciklama}" if aciklama else sebep)
            )
            c.commit()
        finally:
            c.close()
    return redirect(f"/tarama?barkod={barkod}")

@app.route("/urun-sil", methods=["POST"])
@yetkili_giris
def urun_sil():
    if session.get("rol") != "admin":
        return jsonify({"error": "Sadece admin"}), 403
    barkod = (request.form.get("barkod") or "").strip()
    if not barkod:
        return redirect("/urunler")
    c = get_db()
    try:
        c.execute("DELETE FROM partiler WHERE barkod=%s", (barkod,))
        c.execute("DELETE FROM stok_hareketleri WHERE barkod=%s", (barkod,))
        c.execute("DELETE FROM urunler WHERE barkod=%s", (barkod,))
        c.commit()
    finally:
        c.close()
    return redirect("/urunler")


@app.route("/parti-sil", methods=["POST"])
@giris_gerekli
def parti_sil():
    parti_id    = request.form.get("parti_id", "").strip()
    barkod      = request.form.get("barkod", "").strip()
    redirect_to = request.form.get("redirect_to", "tarama")
    if parti_id:
        c = get_db()
        try:
            parti = c.execute("SELECT miktar FROM partiler WHERE parti_id=%s", (parti_id,)).fetchone()
            eski_miktar = parti["miktar"] if parti else 0
            c.execute("DELETE FROM partiler WHERE parti_id=%s", (parti_id,))
            c.commit()
            urun = c.execute("SELECT urun_adi FROM urunler WHERE barkod=%s", (barkod,)).fetchone()
            urun_adi = urun["urun_adi"] if urun else barkod
        finally:
            c.close()
        if eski_miktar > 0:
            log_hareket(barkod, urun_adi, "Cikis", eski_miktar,
                        "Parti silindi", session.get("user", "misafir"))
    if redirect_to == "partiler":
        return redirect(f"/partiler?barkod={barkod}")
    return redirect(f"/tarama?barkod={barkod}")

@app.route("/parti-skt-guncelle", methods=["POST"])
@giris_gerekli
def parti_skt_guncelle():
    parti_id    = request.form.get("parti_id", "").strip()
    barkod      = request.form.get("barkod", "").strip()
    stt         = request.form.get("stt", "").strip() or None
    redirect_to = request.form.get("redirect_to", "tarama")
    if parti_id:
        c = get_db()
        try:
            c.execute("UPDATE partiler SET stt=%s WHERE parti_id=%s", (stt, parti_id))
            c.commit()
        finally:
            c.close()
    if redirect_to == "partiler":
        return redirect(f"/partiler?barkod={barkod}")
    return redirect(f"/tarama?barkod={barkod}")

@app.route("/partiler")
@yetkili_giris
def partiler_sayfasi():
    ara_barkod = request.args.get("barkod", "").strip()
    c = get_db()
    try:
        if ara_barkod:
            urunler_list = [dict(r) for r in c.execute(
                "SELECT * FROM urunler WHERE barkod=%s", (ara_barkod,)).fetchall()]
        else:
            urunler_list = [dict(r) for r in c.execute(
                "SELECT DISTINCT u.* FROM urunler u JOIN partiler p ON u.barkod=p.barkod ORDER BY u.urun_adi"
            ).fetchall()]

        content_rows = ""
        for u in urunler_list:
            partiler_list = [dict(r) for r in c.execute(
                "SELECT * FROM partiler WHERE barkod=%s ORDER BY CASE WHEN stt IS NULL THEN 1 ELSE 0 END, stt ASC, eklenme_tarihi ASC",
                (u["barkod"],)
            ).fetchall()]
            toplam = sum(p["miktar"] for p in partiler_list)

            parti_rows = ""
            for no, p in enumerate(partiler_list, 1):
                pgun    = kalan_gun(p.get("stt"))
                prc     = stt_renk(pgun)
                pet     = stt_etiket(pgun) if p.get("stt") else "SKT Yok"
                dur_cls = "red" if (pgun is not None and pgun < 0) else "yellow" if (pgun is not None and pgun <= 7) else ""
                parti_rows += f"""<tr>
                  <td style="font-family:JetBrains Mono,monospace;font-size:.8rem;color:#a3a3a3;font-weight:700">P{no}</td>
                  <td>
                    <form method="POST" action="/parti-skt-guncelle" style="display:flex;gap:6px;margin:0;align-items:center">
                      <input type="hidden" name="parti_id" value="{p["parti_id"]}">
                      <input type="hidden" name="barkod" value="{u["barkod"]}">
                      <input type="hidden" name="redirect_to" value="partiler">
                      <input type="date" name="stt" value="{p.get('stt','')}" style="margin:0;padding:4px 8px;font-size:.78rem;width:140px">
                      <button type="submit" class="btn btn-muted" style="padding:4px 10px;font-size:.72rem">Kaydet</button>
                    </form>
                  </td>
                  <td class="{dur_cls}" style="color:{prc};font-size:.8rem;font-weight:600">{pet}</td>
                  <td style="font-weight:700">{p["miktar"]} adet</td>
                  <td>
                    <form method="POST" action="/parti-tukendi" style="display:inline;margin:0">
                      <input type="hidden" name="parti_id" value="{p["parti_id"]}">
                      <input type="hidden" name="barkod" value="{u["barkod"]}">
                      <button type="submit" class="btn btn-muted" style="padding:4px 8px;font-size:.7rem;border-color:#f0b429;color:#f0b429">Tukendi</button>
                    </form>
                    <form method="POST" action="/parti-sil" style="display:inline;margin:0 0 0 4px">
                      <input type="hidden" name="parti_id" value="{p["parti_id"]}">
                      <input type="hidden" name="barkod" value="{u["barkod"]}">
                      <input type="hidden" name="redirect_to" value="partiler">
                      <button type="submit" class="btn btn-red" style="padding:4px 8px;font-size:.7rem" onclick="return confirm('Parti silinsin mi?')">Sil</button>
                    </form>
                  </td>
                </tr>"""

            content_rows += f"""
<div class="panel" style="margin-bottom:16px">
  <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;margin-bottom:12px">
    <div>
      <div style="font-family:'Bebas Neue',sans-serif;font-size:1.3rem;letter-spacing:2px">{u["urun_adi"]}</div>
      <div style="font-family:JetBrains Mono,monospace;font-size:.68rem;color:#a3a3a3;margin-top:2px">{u["barkod"]} &nbsp;|&nbsp; Toplam: {toplam} adet &nbsp;|&nbsp; {len(partiler_list)} parti</div>
    </div>
    <button onclick="this.closest('.panel').querySelector('.yeni-form').style.display=this.closest('.panel').querySelector('.yeni-form').style.display=='none'?'block':'none'" class="btn btn-muted" style="font-size:.78rem;padding:6px 14px">+ Yeni Parti</button>
  </div>
  <div class="yeni-form" style="display:none;padding:14px;background:#0f0f0f;border:1px solid #1a1a1a;margin-bottom:12px">
    <form method="POST" action="/parti-ekle" style="display:flex;gap:8px;flex-wrap:wrap;align-items:flex-end">
      <input type="hidden" name="barkod" value="{u["barkod"]}">
      <div><label style="font-size:.72rem;color:#a3a3a3;display:block;margin-bottom:4px">SKT</label><input type="date" name="stt" style="margin:0;width:150px"></div>
      <div><label style="font-size:.72rem;color:#a3a3a3;display:block;margin-bottom:4px">Miktar</label><input type="number" name="miktar" value="1" min="1" style="margin:0;width:90px"></div>
      <button type="submit" class="btn btn-green" style="padding:8px 16px">Ekle</button>
    </form>
  </div>
  <div class="tbl-wrap"><table style="font-size:.85rem">
    <tr><th>Parti No</th><th>SKT</th><th>Durum</th><th>Miktar</th><th>Islemler</th></tr>
    {parti_rows or '<tr><td colspan=5 style="text-align:center;color:#525252;padding:12px">Parti yok</td></tr>'}
  </table></div>
</div>"""
    finally:
        c.close()

    content = f"""
<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;flex-wrap:wrap;gap:12px">
  <div class="page-title" style="margin:0">Parti Yonetimi</div>
  <form method="get" style="display:flex;gap:8px">
    <input name="barkod" value="{ara_barkod}" placeholder="Barkod ile filtrele..." style="width:200px;margin:0">
    <button type="submit" class="btn btn-muted">Filtrele</button>
    {'<a href="/partiler" class="btn btn-muted">Temizle</a>' if ara_barkod else ''}
  </form>
</div>
{content_rows or '<div class="panel" style="text-align:center;color:#525252;padding:32px">Hicbir urun icin parti bulunamadi.</div>'}"""
    return render(content, page="partiler", title="Parti Yonetimi")

# ═══════════════════════════════════════════════════
#  URUNLER
# ═══════════════════════════════════════════════════
@app.route("/urunler")
@yetkili_giris
def urunler():
    ara = request.args.get("ara", "")
    c   = get_db()
    try:
        q = """SELECT u.*,
               COALESCE(ps.toplam, 0) as stok_adedi,
               ps.en_yakin_stt as stt,
               COALESCE(ps.parti_sayisi, 0) as parti_sayisi
        FROM urunler u
        LEFT JOIN (
            SELECT barkod,
                   SUM(miktar) as toplam,
                   MIN(CASE WHEN miktar > 0 AND stt IS NOT NULL THEN stt END) as en_yakin_stt,
                   COUNT(CASE WHEN miktar > 0 THEN 1 END) as parti_sayisi
            FROM partiler GROUP BY barkod
        ) ps ON u.barkod = ps.barkod
        WHERE 1=1"""
        p = []
        if ara:
            q += " AND (u.urun_adi LIKE %s OR u.barkod LIKE %s)"
            p += [f"%{ara}%", f"%{ara}%"]
        q += " ORDER BY u.urun_adi"
        liste = [dict(r) for r in c.execute(q, p).fetchall()]
    finally:
        c.close()

    rows = ""
    for u in liste:
        gun = kalan_gun(u.get("stt"))
        rc  = stt_renk(gun)
        et  = stt_etiket(gun) if u.get("stt") else "—"
        sil_btn = (f'<form method="POST" action="/urun-sil" style="display:inline" onsubmit="return confirm(\'{u["urun_adi"]} silinsin mi? Tüm parti ve hareketler de silinir!\')">'
                   f'<input type="hidden" name="barkod" value="{u["barkod"]}">'
                   f'<button type="submit" class="btn btn-muted" style="padding:3px 10px;font-size:.7rem;border-color:#e05252;color:#e05252">SİL</button></form>')
        rows += f'<tr><td style="font-family:monospace;font-size:.82rem;color:#a3a3a3">{u["barkod"]}</td><td><strong>{u["urun_adi"]}</strong></td><td style="color:#a3a3a3">{u.get("kategori","—")}</td><td>{u.get("stt","—")}</td><td style="color:{rc};font-weight:600;font-size:.82rem">{et}</td><td style="font-weight:700">{u["stok_adedi"]}</td><td style="color:#a3a3a3">{u.get("parti_sayisi",0)}</td><td style="color:#525252">{u.get("min_stok",5)}</td><td style="color:#ffffff">{float(u.get("fiyat") or 0):.2f} TL</td><td>{sil_btn}</td></tr>'

    content = f"""
<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;flex-wrap:wrap;gap:12px">
  <div class="page-title" style="margin:0">Urunler <span style="color:#525252;font-size:.9rem">({len(liste)})</span></div>
  <form method="get" style="display:flex;gap:8px">
    <input name="ara" value="{ara}" placeholder="Urun ara..." style="width:200px;margin:0">
    <button type="submit" class="btn btn-muted">Ara</button>
    {'<a href="/urunler" class="btn btn-muted">✕</a>' if ara else ''}
  </form>
</div>
<div class="tbl-wrap"><table>
  <tr><th>Barkod</th><th>Urun Adi</th><th>Kategori</th><th>SKT</th><th>Durum</th><th>Stok</th><th>Parti</th><th>Min</th><th>Fiyat</th></tr>
  {rows or '<tr><td colspan=9 class="muted" style="text-align:center;padding:20px">Urun bulunamadi</td></tr>'}
</table></div>"""
    return render(content, page="urunler", title="Urunler")

# ═══════════════════════════════════════════════════
#  HAREKETLER
# ═══════════════════════════════════════════════════
@app.route("/hareketler")
@yetkili_giris
def hareketler():
    c = get_db()
    try:
        liste = [dict(r) for r in c.execute(
            "SELECT * FROM stok_hareketleri ORDER BY tarih DESC LIMIT 200").fetchall()]
    finally:
        c.close()

    rows = ""
    for h in liste:
        cls = "green" if h["hareket_tipi"] == "Giris" else "red" if h["hareket_tipi"] in ["Cikis","Okutma"] else ""
        rows += f'<tr><td style="color:#525252">{h["hareket_id"]}</td><td class="{cls}" style="font-weight:700">{h["hareket_tipi"]}</td><td>{h.get("urun_adi","—")}</td><td style="font-family:monospace;font-size:.82rem;color:#a3a3a3">{h.get("barkod","—")}</td><td style="font-weight:700">{h["miktar"]}</td><td style="color:#a3a3a3">{str(h["tarih"])[:16]}</td><td>{h.get("kullanici","—")}</td></tr>'

    content = f"""
<div class="page-title">Hareket Gecmisi</div>
<div class="tbl-wrap"><table>
  <tr><th>#</th><th>Tip</th><th>Urun</th><th>Barkod</th><th>Miktar</th><th>Tarih</th><th>Kullanici</th></tr>
  {rows or '<tr><td colspan=7 class="muted" style="text-align:center;padding:20px">Hareket yok</td></tr>'}
</table></div>"""
    return render(content, page="hareketler", title="Hareketler")

# ═══════════════════════════════════════════════════
#  RAPORLAR
# ═══════════════════════════════════════════════════
@app.route("/raporlar")
@yetkili_giris
def raporlar():
    c = get_db()
    try:
        today = date.today().isoformat()
        s = {
            "toplam_urun":   c.execute("SELECT COUNT(*) FROM urunler").fetchone()[0],
            "toplam_stok":   c.execute("SELECT COALESCE(SUM(miktar),0) FROM partiler").fetchone()[0],
            "toplam_deger":  c.execute("SELECT COALESCE(SUM(ps.toplam * u.fiyat), 0) FROM urunler u JOIN (SELECT barkod, SUM(miktar) as toplam FROM partiler GROUP BY barkod) ps ON u.barkod = ps.barkod").fetchone()[0],
            "tarihi_gecmis": c.execute("SELECT COUNT(DISTINCT barkod) FROM partiler WHERE stt IS NOT NULL AND stt<%s AND miktar>0", (today,)).fetchone()[0],
            "stoksuz":       c.execute("SELECT COUNT(*) FROM urunler u WHERE (SELECT COALESCE(SUM(miktar),0) FROM partiler WHERE barkod=u.barkod) <= 0").fetchone()[0],
            "toplam_islem":  c.execute("SELECT COUNT(*) FROM stok_hareketleri").fetchone()[0],
            "bugun_islem":   c.execute("SELECT COUNT(*) FROM stok_hareketleri WHERE DATE(tarih)=CURRENT_DATE").fetchone()[0],
        }
    finally:
        c.close()

    content = f"""
<div class="page-title">Raporlar</div>
<div class="grid2">
  <div class="panel">
    <h2>Envanter Ozeti</h2>
    <div class="tbl-wrap"><table>
      <tr><td>Toplam Urun Cesidi</td><td class="green" style="text-align:right;font-weight:800">{s["toplam_urun"]}</td></tr>
      <tr><td>Toplam Stok Adedi</td><td class="green" style="text-align:right;font-weight:800">{s["toplam_stok"]}</td></tr>
      <tr><td>Toplam Envanter Degeri</td><td class="green" style="text-align:right;font-weight:800">{s["toplam_deger"]:.2f} TL</td></tr>
      <tr><td>Tarihi Gecmis Urunler</td><td class="red" style="text-align:right;font-weight:800">{s["tarihi_gecmis"]}</td></tr>
      <tr><td>Stoksuz Urunler</td><td class="yellow" style="text-align:right;font-weight:800">{s["stoksuz"]}</td></tr>
      <tr><td>Toplam Islem Sayisi</td><td style="text-align:right;font-weight:800">{s["toplam_islem"]}</td></tr>
      <tr><td>Bugun Yapilan Islem</td><td class="green" style="text-align:right;font-weight:800">{s["bugun_islem"]}</td></tr>
    </table></div>
  </div>
  <div class="panel">
    <h2>API Endpointleri</h2>
    <div class="tbl-wrap"><table>
      <tr><td><a href="/api/stats" style="color:#ffffff">/api/stats</a></td><td class="muted">Dashboard istatistikleri</td></tr>
      <tr><td><a href="/api/urunler" style="color:#ffffff">/api/urunler</a></td><td class="muted">Tum urunler JSON</td></tr>
      <tr><td><a href="/api/hareketler" style="color:#ffffff">/api/hareketler</a></td><td class="muted">Son hareketler JSON</td></tr>
    </table></div>
  </div>
</div>"""
    return render(content, page="raporlar", title="Raporlar")

# ═══════════════════════════════════════════════════
#  KULLANICILAR
# ═══════════════════════════════════════════════════
@app.route("/kullanicilar")
@yetkili_giris
def kullanicilar():
    if session.get("rol") != "admin":
        return redirect("/")
    c = get_db()
    try:
        liste = [dict(r) for r in c.execute(
            "SELECT id,kullanici_adi,tam_ad,rol,aktif,son_giris FROM kullanicilar ORDER BY tam_ad"
        ).fetchall()]
    finally:
        c.close()

    RC = {"admin": "#ffffff", "mudur": "#34d399", "kasiyer": "#86efac", "goruntuleyici": "#a3a3a3"}
    rows = ""
    for u in liste:
        rc  = RC.get(u["rol"], "#a3a3a3")
        akt = '<span class="green">✓ Aktif</span>' if u["aktif"] else '<span class="red">✗ Pasif</span>'
        rows += f'<tr><td style="font-weight:600">{u["kullanici_adi"]}</td><td>{u.get("tam_ad","—")}</td><td style="color:{rc};font-weight:700">{u["rol"].upper()}</td><td>{akt}</td><td style="color:#a3a3a3">{str(u.get("son_giris","—"))[:16]}</td></tr>'

    content = f"""
<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:18px">
  <div class="page-title" style="margin:0">Kullanicilar</div>
  <form method="POST" action="/admin/temizle-kullanicilar" onsubmit="return confirm('Admin disindaki TUM kullanicilar silinecek. Emin misiniz?')">
    <button type="submit" style="background:#ef4444;border:none;color:#fff;padding:8px 16px;font-family:JetBrains Mono,monospace;font-size:.75rem;letter-spacing:1px;cursor:pointer;border-radius:2px">HEPSINI SİL</button>
  </form>
</div>
<div class="tbl-wrap"><table>
  <tr><th>Kullanici Adi</th><th>Tam Ad</th><th>Rol</th><th>Durum</th><th>Son Giris</th></tr>
  {{rows}}
</table></div>"""
    return render(content, page="kullanicilar", title="Kullanicilar")

# ═══════════════════════════════════════════════════
#  API
# ═══════════════════════════════════════════════════
@app.route("/api/stats")
@giris_gerekli
def api_stats():
    c = get_db()
    try:
        today = date.today().isoformat()
        data  = {
            "toplam_urun":   c.execute("SELECT COUNT(*) FROM urunler").fetchone()[0],
            "toplam_stok":   c.execute("SELECT COALESCE(SUM(miktar),0) FROM partiler").fetchone()[0],
            "tarihi_gecmis": c.execute("SELECT COUNT(DISTINCT barkod) FROM partiler WHERE stt IS NOT NULL AND stt<%s AND miktar>0", (today,)).fetchone()[0],
            "stoksuz":       c.execute("SELECT COUNT(*) FROM urunler u WHERE (SELECT COALESCE(SUM(miktar),0) FROM partiler WHERE barkod=u.barkod) <= 0").fetchone()[0],
        }
    finally:
        c.close()
    return jsonify(data)

@app.route("/api/urunler")
@giris_gerekli
def api_urunler():
    c = get_db()
    try:
        liste = [dict(r) for r in c.execute("""
            SELECT u.*,
                   COALESCE(ps.toplam, 0) as stok_adedi,
                   ps.en_yakin_stt as stt
            FROM urunler u
            LEFT JOIN (
                SELECT barkod, SUM(miktar) as toplam,
                       MIN(CASE WHEN miktar>0 AND stt IS NOT NULL THEN stt END) as en_yakin_stt
                FROM partiler GROUP BY barkod
            ) ps ON u.barkod = ps.barkod
            ORDER BY u.urun_adi
        """).fetchall()]
    finally:
        c.close()
    return jsonify(liste)

@app.route("/api/hareketler")
@giris_gerekli
def api_hareketler():
    c = get_db()
    try:
        liste = [dict(r) for r in c.execute(
            "SELECT * FROM stok_hareketleri ORDER BY tarih DESC LIMIT 100").fetchall()
        ]
    finally:
        c.close()
    return jsonify(liste)


@app.route("/api/chat", methods=["POST"])
@yetkili_giris
def api_chat():
    payload = request.get_json(force=True) or {}
    messages = payload.get("messages") or []
    if not messages:
        return jsonify({"error": "Mesaj eksik"}), 400

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        return jsonify({"error": "GROQ_API_KEY ayarlanmamis"}), 500

    # DB'den tüm ürünlerin alerjen + besin bilgilerini çek
    c = get_db()
    try:
        urunler_rows = c.execute(
            """SELECT urun_adi, barkod, allerjenler, katki_maddeleri, icindekiler,
                      kalori, protein, yag, karbonhidrat, seker, tuz, lif
               FROM urunler ORDER BY urun_adi"""
        ).fetchall()
        urunler_rows = [dict(r) for r in urunler_rows]
    finally:
        c.close()

    # Ürün listesini sistem promptuna ekle
    urun_bilgi = []
    for u in urunler_rows:
        satir = f"- {u['urun_adi']} (barkod: {u['barkod']})"
        if u.get('allerjenler'):
            satir += f" | Alerjenler: {u['allerjenler']}"
        if u.get('katki_maddeleri'):
            satir += f" | Katkı: {u['katki_maddeleri']}"
        if u.get('icindekiler'):
            satir += f" | İçindekiler: {u['icindekiler'][:120]}..."
        if u.get('kalori'):
            satir += f" | Kalori: {u['kalori']} kcal"
        urun_bilgi.append(satir)

    urun_listesi = "\n".join(urun_bilgi) if urun_bilgi else "Henüz urun eklenmemis."

    system_prompt = f"""Sen NexStock sisteminin besin ve alerjen asistanısın.
Kullanıcıların sağlık durumlarına göre hangi ürünleri yiyip yiyemeyeceklerini söylüyorsun.

Sistemdeki ürünler ve alerjen bilgileri:
{urun_listesi}

Davranış kuralları:
- Kısa, net ve pratik yanıtlar ver
- Türkçe yaz
- Çölyak/gluten intoleransı: gluten, buğday, arpa, çavdar içeren ürünleri say
- Laktoz intoleransı: süt, peynir, yoğurt içeren ürünleri say
- Fıstık alerjisi: yer fıstığı ve eser miktarlarını say
- Kullanıcı bir ürün sorarsa direkt DB'deki bilgiyi kullan
- DB'de alerjen bilgisi olmayan ürünler için "bu ürünün alerjen bilgisi sisteme eklenmemiş" de
- Asla tıbbi tavsiye verme, doktora yönlendir
- Emoji kullanabilirsin"""

    groq_messages = [{"role": "system", "content": system_prompt}] + messages[-10:]  # son 10 mesaj

    try:
        client = _Groq(api_key=api_key)
        response = client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=groq_messages,
            max_tokens=400,
        )
        reply = response.choices[0].message.content.strip()
        return jsonify({"reply": reply})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ═══════════════════════════════════════════════════
#  AI OKUYUCU — Groq Llama 4 Vision
# ═══════════════════════════════════════════════════
import json as _json
import base64 as _b64
import io as _io
from groq import Groq as _Groq

@app.route("/api/ai-scan", methods=["POST"])
@yetkili_giris
def api_ai_scan():
    if session.get("rol") != "admin":
        return jsonify({"error": "Sadece admin kullanabilir"}), 403

    payload = request.get_json(force=True) or {}
    img_data = payload.get("image", "")
    if not img_data:
        return jsonify({"error": "Goruntu eksik"}), 400

    raw_b64 = img_data.split(",", 1)[1] if "," in img_data else img_data
    data_url = "data:image/jpeg;base64," + raw_b64

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        return jsonify({"error": "GROQ_API_KEY Render'da ayarlanmamis."}), 500

    ocr_prompt = (
        "Bu fotograftaki beslenme tablosunun her satirini oku. "
        "Her satir icin tam olarak su formati kullan: ETIKET: DEGER\n"
        "Ornek: Enerji (kJ/kcal): 2613/565\nYag (g): 51.8\nProtein (g): 27.2\n"
        "Tablodaki TUM satirlari yaz, hicbirini atlama. Baska hicbir sey yazma."
    )

    import re as _re

    def _parse_ocr(ocr_text):
        result = {"kalori": None, "protein": None, "yag": None,
                  "karbonhidrat": None, "seker": None, "tuz": None, "lif": None}
        for line in ocr_text.splitlines():
            if ":" not in line:
                continue
            parts = line.split(":", 1)
            lbl = parts[0].strip().lower()
            val_str = parts[1].strip()
            if "/" in val_str:
                val_str = val_str.split("/")[-1].strip()
            val_str = _re.sub(r"[^0-9,.]", "", val_str).replace(",", ".")
            try:
                val = float(val_str)
            except ValueError:
                continue
            if any(x in lbl for x in ["enerji", "energy", "kalori", "calori"]):
                result["kalori"] = val
            elif any(x in lbl for x in ["protein"]):
                result["protein"] = val
            elif any(x in lbl for x in ["doymu", "tekli", "coklu", "polyun", "monoun"]):
                pass
            elif any(x in lbl for x in ["yag", "yağ", "fat", "lipid"]):
                result["yag"] = val
            elif any(x in lbl for x in ["seker", "şeker", "sugar"]):
                result["seker"] = val
            elif any(x in lbl for x in ["lif", "posa", "fiber", "fibre"]):
                result["lif"] = val
            elif any(x in lbl for x in ["karbonhidrat", "carbohydr"]):
                result["karbonhidrat"] = val
            elif any(x in lbl for x in ["tuz", "salt", "sodyum", "sodium"]):
                result["tuz"] = val
        return result

    try:
        client = _Groq(api_key=api_key)
        response = client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_url}},
                    {"type": "text", "text": ocr_prompt}
                ]
            }],
            max_tokens=512,
        )
        ocr_text = response.choices[0].message.content.strip()
        data = _parse_ocr(ocr_text)
        return jsonify({"success": True, "data": data, "ocr_raw": ocr_text})
    except Exception as e:
        err = str(e)
        if "429" in err or "rate" in err.lower():
            return jsonify({"error": "Groq rate limit asildi. 10 saniye bekleyip tekrar dene."}), 429
        return jsonify({"error": err}), 500



@app.route("/api/ai-ingredients", methods=["POST"])
@yetkili_giris
def api_ai_ingredients():
    if session.get("rol") != "admin":
        return jsonify({"error": "Sadece admin kullanabilir"}), 403
    payload = request.get_json(force=True) or {}
    img_data = payload.get("image", "")
    if not img_data:
        return jsonify({"error": "Goruntu eksik"}), 400
    raw_b64 = img_data.split(",", 1)[1] if "," in img_data else img_data
    data_url = "data:image/jpeg;base64," + raw_b64
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        return jsonify({"error": "GROQ_API_KEY ayarlanmamis"}), 500
    prompt = (
        "Bu fotograftaki icindekiler listesini oku. "
        "Sadece icindekiler satirini/satirlarini bul ve ham metin olarak ver. "
        "Baska hicbir sey yazma, aciklama yapma, sadece icindekiler metnini yaz."
    )
    try:
        client = _Groq(api_key=api_key)
        response = client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": data_url}},
                {"type": "text", "text": prompt}
            ]}],
            max_tokens=512,
        )
        text = response.choices[0].message.content.strip()
        return jsonify({"success": True, "icindekiler": text})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/urun-ai-ekle", methods=["POST"])
@yetkili_giris
def api_urun_ai_ekle():
    if session.get("rol") != "admin":
        return jsonify({"error": "Sadece admin"}), 403
    data = request.get_json(force=True) or {}
    barkod          = (data.get("barkod") or "").strip()
    urun_adi        = (data.get("urun_adi") or "").strip()
    kategori        = (data.get("kategori") or "Genel").strip()
    nutrition       = data.get("nutrition") or {}
    icindekiler     = (data.get("icindekiler") or "").strip()
    allerjenler     = (data.get("allerjenler") or "").strip()
    katki_maddeleri = (data.get("katki_maddeleri") or "").strip()
    if not barkod or not urun_adi:
        return jsonify({"error": "Barkod ve urun adi zorunlu"}), 400
    c = get_db()
    try:
        c.execute(
            """INSERT INTO urunler
               (barkod, urun_adi, kategori, kalori, protein, yag, karbonhidrat,
                seker, tuz, lif, icindekiler, allerjenler, katki_maddeleri)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (barkod) DO UPDATE SET
                 urun_adi=EXCLUDED.urun_adi, kategori=EXCLUDED.kategori,
                 kalori=EXCLUDED.kalori, protein=EXCLUDED.protein,
                 yag=EXCLUDED.yag, karbonhidrat=EXCLUDED.karbonhidrat,
                 seker=EXCLUDED.seker, tuz=EXCLUDED.tuz,
                 lif=EXCLUDED.lif, icindekiler=EXCLUDED.icindekiler,
                 allerjenler=EXCLUDED.allerjenler,
                 katki_maddeleri=EXCLUDED.katki_maddeleri,
                 son_guncelleme=NOW()""",
            (barkod, urun_adi, kategori,
             nutrition.get("kalori"), nutrition.get("protein"), nutrition.get("yag"),
             nutrition.get("karbonhidrat"), nutrition.get("seker"),
             nutrition.get("tuz"), nutrition.get("lif"), icindekiler or None,
             allerjenler or None, katki_maddeleri or None)
        )
        c.commit()
        return jsonify({"success": True, "barkod": barkod})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        c.close()

@app.route("/ai-okuyucu")
@yetkili_giris
def ai_okuyucu():
    if session.get("rol") != "admin":
        return redirect("/")
    content = r"""
<div class="page-title">AI Okuyucu</div>
<div style="max-width:640px;margin:0 auto">

<!-- STEP INDICATOR -->
<div id="step-bar" style="display:flex;gap:0;margin-bottom:24px;border:1px solid #1a1a1a;overflow:hidden">
  <div class="step-tab active" id="tab1" style="flex:1;text-align:center;padding:10px 4px;font-size:.65rem;font-family:JetBrains Mono,monospace;letter-spacing:1px;color:#525252;text-transform:uppercase;transition:all .2s">1 · BARKOD</div>
  <div class="step-tab" id="tab2" style="flex:1;text-align:center;padding:10px 4px;font-size:.65rem;font-family:JetBrains Mono,monospace;letter-spacing:1px;color:#525252;text-transform:uppercase;border-left:1px solid #1a1a1a;transition:all .2s">2 · BESİN</div>
  <div class="step-tab" id="tab3" style="flex:1;text-align:center;padding:10px 4px;font-size:.65rem;font-family:JetBrains Mono,monospace;letter-spacing:1px;color:#525252;text-transform:uppercase;border-left:1px solid #1a1a1a;transition:all .2s">3 · İÇİNDEKİLER</div>
  <div class="step-tab" id="tab4" style="flex:1;text-align:center;padding:10px 4px;font-size:.65rem;font-family:JetBrains Mono,monospace;letter-spacing:1px;color:#525252;text-transform:uppercase;border-left:1px solid #1a1a1a;transition:all .2s">4 · KAYDET</div>
</div>

<!-- STEP 1: BARKOD -->
<div id="step1" class="panel">
  <div style="font-family:JetBrains Mono,monospace;font-size:.6rem;color:#525252;letter-spacing:2px;text-transform:uppercase;margin-bottom:16px">BARKOD NUMARASI</div>
  <input type="text" id="barkod-input" placeholder="Barkod numarasini girin..." inputmode="numeric"
         style="width:100%;box-sizing:border-box;background:#0d0d0d;border:1px solid #1a1a1a;color:#f5f5f5;padding:14px;font-family:JetBrains Mono,monospace;font-size:1rem;margin-bottom:8px">
  <input type="file" id="barkod-file" accept="image/*" capture="environment" style="display:none" onchange="barkodFotoScan(this)">
  <button onclick="document.getElementById('barkod-file').click()" class="btn btn-muted" style="width:100%;padding:11px;font-size:.82rem;margin-bottom:12px">
    📷 Barkodu Fotoğrafla (opsiyonel)
  </button>
  <div id="barkod-loading" style="display:none;text-align:center;padding:12px;font-family:JetBrains Mono,monospace;font-size:.65rem;color:#525252;letter-spacing:2px">BARKOD OKUNUYOR…</div>
  <div id="barkod-err" style="display:none;color:#e05252;font-size:.75rem;margin-bottom:8px"></div>
  <button onclick="step1Next()" class="btn btn-green" style="width:100%;padding:14px">DEVAM ET →</button>
</div>

<!-- STEP 2: BESİN ÖGELERİ -->
<div id="step2" style="display:none">
  <div class="panel">
    <div style="font-family:JetBrains Mono,monospace;font-size:.6rem;color:#525252;letter-spacing:2px;text-transform:uppercase;margin-bottom:16px">BESIN ÖGELERİ ETİKETİ</div>
    <input type="file" id="besin-file" accept="image/*" capture="environment" style="display:none" onchange="besинScan(this)">
    <button onclick="document.getElementById('besin-file').click()" class="btn btn-green" style="width:100%;padding:14px;font-size:1rem">📷 Besin Tablosunu Fotoğrafla</button>
  </div>
  <div id="besin-preview-wrap" style="display:none;margin-top:0">
    <img id="besin-preview" style="width:100%;border:1px solid #1a1a1a;display:block">
    <button onclick="document.getElementById('besin-file').click()" class="btn btn-muted" style="width:100%;margin-top:0;border-top:none">🔄 Tekrar Çek</button>
  </div>
  <div id="besin-loading" style="display:none;text-align:center;padding:24px;font-family:JetBrains Mono,monospace;font-size:.7rem;color:#525252;letter-spacing:2px">ANALİZ EDİLİYOR…</div>
  <div id="besin-sonuc" style="display:none" class="panel" style="margin-top:0">
    <div style="font-family:JetBrains Mono,monospace;font-size:.6rem;color:#525252;letter-spacing:2px;text-transform:uppercase;margin-bottom:12px">ÇIKARILAN DEĞERLER (düzenleyebilirsin)</div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px" id="besin-fields"></div>
    <button onclick="step2Next()" class="btn btn-green" style="width:100%;padding:14px;margin-top:16px">DEVAM ET →</button>
  </div>
</div>

<!-- STEP 3: İÇİNDEKİLER -->
<div id="step3" style="display:none">
  <div class="panel">
    <div style="font-family:JetBrains Mono,monospace;font-size:.6rem;color:#525252;letter-spacing:2px;text-transform:uppercase;margin-bottom:16px">İÇİNDEKİLER</div>
    <input type="file" id="ic-file" accept="image/*" capture="environment" style="display:none" onchange="icindekilerScan(this)">
    <button onclick="document.getElementById('ic-file').click()" class="btn btn-green" style="width:100%;padding:14px;font-size:1rem">📷 İçindekileri Fotoğrafla</button>
    <button onclick="step3Skip()" class="btn btn-muted" style="width:100%;padding:10px;margin-top:4px;font-size:.8rem">Atla →</button>
  </div>
  <div id="ic-preview-wrap" style="display:none;margin-top:0">
    <img id="ic-preview" style="width:100%;border:1px solid #1a1a1a;display:block">
    <button onclick="document.getElementById('ic-file').click()" class="btn btn-muted" style="width:100%;margin-top:0;border-top:none">🔄 Tekrar Çek</button>
  </div>
  <div id="ic-loading" style="display:none;text-align:center;padding:24px;font-family:JetBrains Mono,monospace;font-size:.7rem;color:#525252;letter-spacing:2px">İÇİNDEKİLER OKUNUYOR…</div>
  <div id="ic-sonuc" style="display:none" class="panel" style="margin-top:0">
    <div style="font-family:JetBrains Mono,monospace;font-size:.6rem;color:#525252;letter-spacing:2px;text-transform:uppercase;margin-bottom:8px">İÇİNDEKİLER (düzenleyebilirsin)</div>
    <textarea id="ic-text" rows="4" style="width:100%;box-sizing:border-box;background:#0d0d0d;border:1px solid #1a1a1a;color:#f5f5f5;padding:12px;font-size:.82rem;resize:vertical"></textarea>
    <div id="analiz-loading" style="display:none;text-align:center;padding:12px;font-family:JetBrains Mono,monospace;font-size:.65rem;color:#525252;letter-spacing:2px">ALERJENLER ANALİZ EDİLİYOR…</div>
    <div id="analiz-sonuc" style="display:none;margin-top:12px">
      <div style="font-family:JetBrains Mono,monospace;font-size:.6rem;color:#e05252;letter-spacing:2px;text-transform:uppercase;margin-bottom:6px">⚠ ALERJENLER</div>
      <div id="analiz-allerjenler" style="margin-bottom:10px;font-size:.8rem"></div>
      <div style="font-family:JetBrains Mono,monospace;font-size:.6rem;color:#f0b429;letter-spacing:2px;text-transform:uppercase;margin-bottom:6px">◦ İZ MİKTARINDA İÇEREBİLİR</div>
      <div id="analiz-izler" style="margin-bottom:10px;font-size:.8rem"></div>
      <div style="font-family:JetBrains Mono,monospace;font-size:.6rem;color:#525252;letter-spacing:2px;text-transform:uppercase;margin-bottom:6px">KATKI MADDELERİ</div>
      <div id="analiz-katki" style="font-size:.78rem;color:#a3a3a3"></div>
    </div>
    <button onclick="step3Next()" class="btn btn-green" style="width:100%;padding:14px;margin-top:12px">DEVAM ET →</button>
  </div>
</div>

<!-- STEP 4: KAYDET -->
<div id="step4" style="display:none">
  <div class="panel">
    <div style="font-family:JetBrains Mono,monospace;font-size:.6rem;color:#525252;letter-spacing:2px;text-transform:uppercase;margin-bottom:16px">ÜRÜN BİLGİLERİ</div>
    <label style="font-family:JetBrains Mono,monospace;font-size:.6rem;color:#525252;letter-spacing:1px;display:block;margin-bottom:4px">ÜRÜN ADI</label>
    <input type="text" id="urun-adi-input" placeholder="Ürün adını girin..."
           style="width:100%;box-sizing:border-box;background:#0d0d0d;border:1px solid #1a1a1a;color:#f5f5f5;padding:12px;font-size:.9rem;margin-bottom:12px">
    <label style="font-family:JetBrains Mono,monospace;font-size:.6rem;color:#525252;letter-spacing:1px;display:block;margin-bottom:4px">KATEGORİ</label>
    <input type="text" id="kategori-input" value="Genel"
           style="width:100%;box-sizing:border-box;background:#0d0d0d;border:1px solid #1a1a1a;color:#f5f5f5;padding:12px;font-size:.9rem;margin-bottom:16px">
    <div id="ozet-besin" style="margin-bottom:8px"></div>
    <div id="ozet-ic" style="display:none;margin-bottom:16px">
      <div style="font-family:JetBrains Mono,monospace;font-size:.58rem;color:#525252;letter-spacing:1px;margin-bottom:4px">İÇİNDEKİLER</div>
      <div id="ozet-ic-text" style="font-size:.78rem;color:#a3a3a3;line-height:1.5"></div>
    </div>
    <div id="kaydet-err" style="display:none;color:#e05252;font-size:.75rem;margin-bottom:8px"></div>
    <button onclick="kaydet()" class="btn btn-green" style="width:100%;padding:16px;font-size:1rem" id="kaydet-btn">💾 VERİTABANINA EKLE</button>
  </div>
</div>

<style>
.step-tab.active{color:var(--g)!important;background:rgba(16,185,129,.06)}
</style>
<script>
var _state = {barkod:'', nutrition:{}, icindekiler:'', allerjenler:'', katki_maddeleri:''};
var NUT_KEYS = ['kalori','protein','yag','karbonhidrat','seker','tuz','lif'];
var NUT_LABELS = {kalori:'Kalori (kcal)',protein:'Protein (g)',yag:'Yağ (g)',karbonhidrat:'Karbonhidrat (g)',seker:'Şeker (g)',tuz:'Tuz (g)',lif:'Lif (g)'};

function showStep(n){
  [1,2,3,4].forEach(function(i){
    document.getElementById('step'+i).style.display = i===n?'block':'none';
    var t=document.getElementById('tab'+i);
    t.classList.toggle('active',i===n);
    t.style.color = i===n ? 'var(--g)' : (i<n ? '#f5f5f5' : '#525252');
  });
}

// STEP 1
function barkodFotoScan(input){
  if(!input.files||!input.files[0]) return;
  var file=input.files[0];
  var loadEl=document.getElementById('barkod-loading');
  var errEl=document.getElementById('barkod-err');
  loadEl.style.display='block';
  errEl.style.display='none';

  function setBarkod(num){
    loadEl.style.display='none';
    var inp=document.getElementById('barkod-input');
    inp.value=num;
    inp.style.borderColor='var(--g)';
  }
  function fallbackAI(dataUrl){
    fetch('/api/ai-barcode',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({image:dataUrl})
    }).then(function(r){return r.json();}).then(function(d){
      loadEl.style.display='none';
      if(d.error){errEl.textContent=d.error;errEl.style.display='block';return;}
      setBarkod(d.barkod);
    }).catch(function(e){
      loadEl.style.display='none';
      errEl.textContent='Hata: '+e.message;errEl.style.display='block';
    });
  }

  if('BarcodeDetector' in window){
    var img=new Image();
    img.onload=function(){
      var bd=new BarcodeDetector({formats:['ean_13','ean_8','upc_a','upc_e','code_128','code_39']});
      bd.detect(img).then(function(codes){
        loadEl.style.display='none';
        if(codes&&codes.length>0){
          setBarkod(codes[0].rawValue);
        } else {
          errEl.textContent='Barkod bulunamadi — net fotograf dene';
          errEl.style.display='block';
        }
      }).catch(function(){
        var reader=new FileReader();
        reader.onload=function(e){fallbackAI(e.target.result);};
        reader.readAsDataURL(file);
      });
    };
    img.src=URL.createObjectURL(file);
  } else {
    var reader=new FileReader();
    reader.onload=function(e){fallbackAI(e.target.result);};
    reader.readAsDataURL(file);
  }
}
function step1Next(){
  var b = document.getElementById('barkod-input').value.trim();
  var err = document.getElementById('barkod-err');
  if(!b){err.textContent='Barkod zorunlu';err.style.display='block';return;}
  err.style.display='none';
  _state.barkod = b;
  showStep(2);
}
document.getElementById('barkod-input').addEventListener('keydown',function(e){if(e.key==='Enter')step1Next();});

// STEP 2
function besинScan(input){
  if(!input.files||!input.files[0]) return;
  var reader=new FileReader();
  reader.onload=function(e){
    var url=e.target.result;
    document.getElementById('besin-preview').src=url;
    document.getElementById('besin-preview-wrap').style.display='block';
    document.getElementById('besin-loading').style.display='block';
    document.getElementById('besin-sonuc').style.display='none';
    fetch('/api/ai-scan',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({image:url})
    }).then(function(r){return r.json();}).then(function(d){
      document.getElementById('besin-loading').style.display='none';
      if(d.error){alert('Hata: '+d.error);return;}
      _state.nutrition = d.data || {};
      renderBesinFields(_state.nutrition);
      document.getElementById('besin-sonuc').style.display='block';
    }).catch(function(e){
      document.getElementById('besin-loading').style.display='none';
      alert('Bağlantı hatası: '+e.message);
    });
  };
  reader.readAsDataURL(input.files[0]);
}
function renderBesinFields(data){
  var html='';
  NUT_KEYS.forEach(function(k){
    var v = data[k]!=null ? data[k] : '';
    html += '<div><div style="font-family:JetBrains Mono,monospace;font-size:.55rem;color:#525252;letter-spacing:1px;margin-bottom:4px">'+NUT_LABELS[k]+'</div>';
    html += '<input type="number" step="0.1" id="nut_'+k+'" value="'+v+'" style="width:100%;box-sizing:border-box;background:#0d0d0d;border:1px solid #1a1a1a;color:#f5f5f5;padding:8px;font-size:.9rem"></div>';
  });
  document.getElementById('besin-fields').innerHTML = html;
}
function step2Next(){
  NUT_KEYS.forEach(function(k){
    var el=document.getElementById('nut_'+k);
    _state.nutrition[k] = el&&el.value!=='' ? parseFloat(el.value) : null;
  });
  showStep(3);
}

// STEP 3
function icindekilerScan(input){
  if(!input.files||!input.files[0]) return;
  var reader=new FileReader();
  reader.onload=function(e){
    var url=e.target.result;
    document.getElementById('ic-preview').src=url;
    document.getElementById('ic-preview-wrap').style.display='block';
    document.getElementById('ic-loading').style.display='block';
    document.getElementById('ic-sonuc').style.display='none';
    fetch('/api/ai-ingredients',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({image:url})
    }).then(function(r){return r.json();}).then(function(d){
      document.getElementById('ic-loading').style.display='none';
      if(d.error){alert('Hata: '+d.error);return;}
      var metin = d.icindekiler || '';
      document.getElementById('ic-text').value = metin;
      document.getElementById('ic-sonuc').style.display='block';
      // Otomatik alerjen analizi
      if(metin){
        document.getElementById('analiz-loading').style.display='block';
        fetch('/api/ai-analyze-ingredients',{method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({metin:metin})
        }).then(function(r){return r.json();}).then(function(a){
          document.getElementById('analiz-loading').style.display='none';
          if(a.error||!a.data) return;
          var data=a.data;
          _state._analiz=data;
          // Alerjenler
          var al=data.allerjenler||[];
          document.getElementById('analiz-allerjenler').innerHTML = al.length
            ? al.map(function(x){return '<span style="display:inline-block;background:#e0525222;color:#e05252;border:1px solid #e0525244;padding:2px 10px;border-radius:3px;font-size:.75rem;margin:2px;font-family:JetBrains Mono,monospace">⚠ '+x+'</span>';}).join('')
            : '<span style="color:#4ade80;font-size:.78rem">✓ Tespit edilmedi</span>';
          // İzler
          var iz=data.izler||[];
          document.getElementById('analiz-izler').innerHTML = iz.length
            ? iz.map(function(x){return '<span style="display:inline-block;background:#f0b42922;color:#f0b429;border:1px solid #f0b42944;padding:2px 10px;border-radius:3px;font-size:.75rem;margin:2px;font-family:JetBrains Mono,monospace">◦ '+x+'</span>';}).join('')
            : '<span style="color:#525252;font-size:.78rem">—</span>';
          // Katkı maddeleri
          var kor=(data.koruyucular||[]).concat(data.tatlandiricilar||[]).concat(data.diger_katkilar||[]);
          document.getElementById('analiz-katki').textContent = kor.length ? kor.join(', ') : '—';
          document.getElementById('analiz-sonuc').style.display='block';
        }).catch(function(){
          document.getElementById('analiz-loading').style.display='none';
        });
      }
    }).catch(function(e){
      document.getElementById('ic-loading').style.display='none';
      alert('Bağlantı hatası: '+e.message);
    });
  };
  reader.readAsDataURL(input.files[0]);
}
function step3Next(){
  _state.icindekiler = document.getElementById('ic-text').value.trim();
  if(_state._analiz){
    var a=_state._analiz;
    var al=(a.allerjenler||[]).concat(a.izler&&a.izler.length?['İz: '+a.izler.join(', ')]:[]);
    _state.allerjenler = al.join(', ');
    var kor=(a.koruyucular||[]).concat(a.tatlandiricilar||[]).concat(a.diger_katkilar||[]);
    _state.katki_maddeleri = kor.join(', ');
  }
  showStep4();
}
function step3Skip(){
  _state.icindekiler = '';
  _state.allerjenler = '';
  _state.katki_maddeleri = '';
  showStep4();
}

// STEP 4
function showStep4(){
  showStep(4);
  // Özet besin
  var html='<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:1px;background:var(--border);margin-bottom:12px">';
  NUT_KEYS.forEach(function(k){
    var v=_state.nutrition[k];
    if(v==null) return;
    html+='<div style="background:var(--card);padding:10px;text-align:center">';
    html+='<div style="font-family:Bebas Neue,sans-serif;font-size:1.3rem;color:#f5f5f5">'+v+'</div>';
    html+='<div style="font-family:JetBrains Mono,monospace;font-size:.52rem;color:#525252;letter-spacing:1px">'+NUT_LABELS[k]+'</div>';
    html+='</div>';
  });
  html+='</div>';
  document.getElementById('ozet-besin').innerHTML=html;
  if(_state.icindekiler){
    document.getElementById('ozet-ic-text').textContent=_state.icindekiler;
    document.getElementById('ozet-ic').style.display='block';
  }
}

// KAYDET
function kaydet(){
  var urun_adi = document.getElementById('urun-adi-input').value.trim();
  var kategori = document.getElementById('kategori-input').value.trim() || 'Genel';
  var err = document.getElementById('kaydet-err');
  if(!urun_adi){err.textContent='Ürün adı zorunlu';err.style.display='block';return;}
  err.style.display='none';
  var btn = document.getElementById('kaydet-btn');
  btn.textContent='Kaydediliyor…'; btn.disabled=true;
  fetch('/api/urun-ai-ekle',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({
      barkod: _state.barkod,
      urun_adi: urun_adi,
      kategori: kategori,
      nutrition: _state.nutrition,
      icindekiler: _state.icindekiler,
      allerjenler: _state.allerjenler,
      katki_maddeleri: _state.katki_maddeleri
    })
  }).then(function(r){return r.json();}).then(function(d){
    if(d.error){err.textContent=d.error;err.style.display='block';btn.textContent='💾 VERİTABANINA EKLE';btn.disabled=false;return;}
    window.location.href='/tarama?barkod='+encodeURIComponent(_state.barkod);
  }).catch(function(e){
    err.textContent='Bağlantı hatası: '+e.message;err.style.display='block';
    btn.textContent='💾 VERİTABANINA EKLE';btn.disabled=false;
  });
}
</script>
"""
    return render(content, page="ai-okuyucu", title="AI Okuyucu")

@app.route("/health")
def health():
    return jsonify({"status": "ok"}), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port, threaded=True)
@app.route("/api/ai-barcode", methods=["POST"])
@yetkili_giris
def api_ai_barcode():
    if session.get("rol") != "admin":
        return jsonify({"error": "Sadece admin"}), 403
    payload = request.get_json(force=True) or {}
    img_data = payload.get("image", "")
    if not img_data:
        return jsonify({"error": "Goruntu eksik"}), 400
    raw_b64 = img_data.split(",", 1)[1] if "," in img_data else img_data
    data_url = "data:image/jpeg;base64," + raw_b64
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        return jsonify({"error": "GROQ_API_KEY ayarlanmamis"}), 500
    import re as _re

    def _pick_barcode(raw):
        """AI ciktisindaki ham metinden en mantikli barkod numarasini sec."""
        # Once tam olarak 8, 12 veya 13 haneli gruplari bul (standart EAN/UPC)
        for length in [13, 12, 8]:
            hits = _re.findall(r"(?<![0-9])[0-9]{" + str(length) + r"}(?![0-9])", raw)
            if hits:
                return hits[0]
        # Bulamazsa 7-14 arasi en uzun grubu al
        hits = _re.findall(r"[0-9]{7,14}", raw)
        if hits:
            return max(hits, key=len)
        return None

    ocr_prompt = (
        "Fotograftaki barkodun ALTINDA yazili insan okunakli rakamlari bul ve yaz. "
        "Bu rakamlar genellikle 8, 12 veya 13 haneli olur. "
        "Sadece o rakam grubunu yaz, baska hicbir sey yazma, bosluk koyma. "
        "Ornek: 8690526430124"
    )
    try:
        client = _Groq(api_key=api_key)
        response = client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": data_url}},
                {"type": "text", "text": ocr_prompt}
            ]}],
            max_tokens=32,
        )
        raw = response.choices[0].message.content.strip()
        barkod = _pick_barcode(raw)
        if not barkod:
            return jsonify({"error": "Barkod okunamadi — net bir fotograf dene"}), 400
        return jsonify({"success": True, "barkod": barkod})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/ai-analyze-ingredients", methods=["POST"])
@yetkili_giris
def api_ai_analyze_ingredients():
    if session.get("rol") != "admin":
        return jsonify({"error": "Sadece admin"}), 403
    payload = request.get_json(force=True) or {}
    metin = (payload.get("metin") or "").strip()
    if not metin:
        return jsonify({"error": "Metin eksik"}), 400
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        return jsonify({"error": "GROQ_API_KEY ayarlanmamis"}), 500

    prompt = f"""Asagidaki urun icindekiler listesini analiz et.
SADECE asagidaki JSON formatinda yanit ver, baska hicbir sey yazma:
{{
  "allerjenler": ["madde1", "madde2"],
  "izler": ["madde1"],
  "koruyucular": ["E200", "benzoat..."],
  "tatlandiricilar": ["sukroz", "E951..."],
  "diger_katkilar": ["E471", "lesitin..."]
}}

Kurallar:
- allerjenler: gluten, sut, yumurta, fistik, kuruyemis, soya, balik, kabuklu deniz urunleri, susam — icindekiler listesinde DIREKT gecentler
- izler: "icerebilir", "iz miktarda" ifadesinden sonra gelenler
- koruyucular: E2xx kodlari veya bilinen koruyucular (sorbat, benzoat, nitrit vb.)
- tatlandiricilar: seker disindaki tatlandiricilar (E9xx, stevia, aspartam vb.)
- diger_katkilar: emulgatörler, stabilizerler, aroma maddesi, renk maddesi vb.
- Liste bossa bos array yaz: []
- Turkce yaz

Icindekiler listesi:
{metin}"""

    try:
        client = _Groq(api_key=api_key)
        response = client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=512,
        )
        text = response.choices[0].message.content.strip()
        if "```" in text:
            text = text.split("```")[1].replace("json","").strip()
        data = _json.loads(text)
        return jsonify({"success": True, "data": data})
    except _json.JSONDecodeError:
        return jsonify({"error": "AI yaniti parse edilemedi", "raw": text}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

