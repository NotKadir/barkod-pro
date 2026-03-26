# -*- coding: utf-8 -*-
import os, hashlib, sqlite3, functools, requests
from datetime import datetime, date
from flask import Flask, render_template_string, request, redirect, session, jsonify

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "nexstock_secret_2024")
DB_NAME = os.environ.get("DB_PATH", "envanter_pro.db")

# ═══════════════════════════════════════════════════
#  VERİTABANIrr
# ═══════════════════════════════════════════════════
def get_db():
    c = sqlite3.connect(DB_NAME)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    return c

def init_db():
    c = get_db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS kullanicilar (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kullanici_adi TEXT UNIQUE NOT NULL,
        sifre_hash TEXT NOT NULL,
        tam_ad TEXT,
        rol TEXT NOT NULL DEFAULT 'kasiyer',
        aktif INTEGER DEFAULT 1,
        son_giris DATETIME
    );
    CREATE TABLE IF NOT EXISTS urunler (
        barkod TEXT PRIMARY KEY,
        urun_adi TEXT NOT NULL,
        kategori TEXT DEFAULT 'Genel',
        min_stok INTEGER DEFAULT 5,
        fiyat REAL DEFAULT 0.0,
        aciklama TEXT,
        eklenme_tarihi DATETIME DEFAULT CURRENT_TIMESTAMP,
        son_guncelleme DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS partiler (
        parti_id INTEGER PRIMARY KEY AUTOINCREMENT,
        barkod TEXT NOT NULL,
        stt DATE,
        miktar INTEGER DEFAULT 0,
        eklenme_tarihi DATETIME DEFAULT CURRENT_TIMESTAMP,
        ekleyen TEXT DEFAULT 'sistem',
        FOREIGN KEY (barkod) REFERENCES urunler(barkod)
    );
    CREATE TABLE IF NOT EXISTS stok_hareketleri (
        hareket_id INTEGER PRIMARY KEY AUTOINCREMENT,
        barkod TEXT,
        urun_adi TEXT,
        hareket_tipi TEXT NOT NULL,
        miktar INTEGER NOT NULL,
        onceki_stok INTEGER,
        sonraki_stok INTEGER,
        tarih DATETIME DEFAULT CURRENT_TIMESTAMP,
        kullanici TEXT DEFAULT 'sistem',
        aciklama TEXT
    );
    CREATE TABLE IF NOT EXISTS tedarikciler (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ad TEXT NOT NULL,
        telefon TEXT, email TEXT, adres TEXT, not_ TEXT,
        aktif INTEGER DEFAULT 1
    );
    """)
    c.commit()
    h = hashlib.sha256("admin123".encode()).hexdigest()
    try:
        c.execute("INSERT INTO kullanicilar (kullanici_adi,sifre_hash,tam_ad,rol) VALUES (?,?,?,?)",
                  ("admin", h, "Sistem Yoneticisi", "admin"))
        c.commit()
    except: pass
    c.close()

def migrate_to_partiler():
    c = get_db()
    cols = [r[1] for r in c.execute("PRAGMA table_info(urunler)").fetchall()]
    if "stt" not in cols:
        c.close()
        return
    # Mevcut verileri partiler tablosuna aktar
    rows = c.execute("SELECT barkod, stt, stok_adedi FROM urunler").fetchall()
    for r in rows:
        if r[1] or r[2]:
            c.execute("INSERT INTO partiler (barkod, stt, miktar, ekleyen) VALUES (?,?,?,?)",
                      (r[0], r[1], r[2] if r[2] else 0, "migrasyon"))
    c.commit()
    # Tabloyu yeniden olustur — FK kontrol kapaliyken
    c.execute("PRAGMA foreign_keys=OFF")
    c.execute("""CREATE TABLE urunler_new (
        barkod TEXT PRIMARY KEY,
        urun_adi TEXT NOT NULL,
        kategori TEXT DEFAULT 'Genel',
        min_stok INTEGER DEFAULT 5,
        fiyat REAL DEFAULT 0.0,
        aciklama TEXT,
        eklenme_tarihi DATETIME DEFAULT CURRENT_TIMESTAMP,
        son_guncelleme DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    c.execute("INSERT INTO urunler_new SELECT barkod, urun_adi, kategori, min_stok, fiyat, aciklama, eklenme_tarihi, son_guncelleme FROM urunler")
    c.execute("DROP TABLE urunler")
    c.execute("ALTER TABLE urunler_new RENAME TO urunler")
    c.commit()
    c.execute("PRAGMA foreign_keys=ON")
    c.close()

init_db()
migrate_to_partiler()

# ═══════════════════════════════════════════════════
#  YARDIMCI
# ═══════════════════════════════════════════════════
def sh(s): return hashlib.sha256(s.encode()).hexdigest()

def kalan_gun(stt):
    if not stt: return None
    try: return (datetime.strptime(str(stt)[:10], "%Y-%m-%d").date() - date.today()).days
    except: return None

def stt_etiket(gun):
    if gun is None: return "SKT Belirtilmemis"
    if gun < 0:  return f"TARIHI GECMIS ({abs(gun)} gun once!)"
    if gun == 0: return "BUGUN bitiyor!"
    if gun <= 3: return f"{gun} gun kaldi — Dikkat!"
    return f"{gun} gun kaldi"

def stt_renk(gun):
    if gun is None: return "#525252"
    if gun < 0:  return "#e05252"
    if gun <= 3: return "#f0b429"
    if gun <= 7: return "#fb923c"
    return "#ffffff"

def openfoodfacts(barkod):
    urls = [
        f"https://world.openfoodfacts.net/api/v2/product/{barkod}?fields=product_name,product_name_tr,generic_name,categories_tags",
        f"https://world.openfoodfacts.org/api/v2/product/{barkod}.json",
    ]
    headers = {"User-Agent": "NexStock/3.0 (github.com/nexstock)"}
    for url in urls:
        try:
            r = requests.get(url, headers=headers, timeout=8)
            if r.status_code != 200: continue
            data = r.json()
            if data.get("status") == 1 and "product" in data:
                p = data["product"]
                name = (p.get("product_name_tr") or p.get("product_name")
                        or p.get("generic_name") or "").strip()
                if not name: name = f"Urun-{barkod[-6:]}"
                tags = p.get("categories_tags", [])
                cat = "Genel"
                if tags:
                    tr = [t for t in tags if t.startswith("tr:")]
                    cat = (tr[0].replace("tr:","") if tr else tags[0].replace("en:","")).replace("-"," ").title()
                return name, cat
        except: continue
    return None, None

def get_toplam_stok(c, barkod):
    row = c.execute("SELECT COALESCE(SUM(miktar),0) FROM partiler WHERE barkod=?", (barkod,)).fetchone()
    return row[0]

def get_en_yakin_stt(c, barkod):
    row = c.execute("SELECT MIN(stt) FROM partiler WHERE barkod=? AND miktar>0 AND stt IS NOT NULL", (barkod,)).fetchone()
    return row[0] if row else None

def log_hareket(barkod, urun_adi, tip, miktar, aciklama, kullanici):
    c = get_db()
    onceki = get_toplam_stok(c, barkod)
    if tip in ("Cikis","Okutma"):
        remaining = miktar
        partiler_rows = c.execute(
            "SELECT parti_id, miktar FROM partiler WHERE barkod=? AND miktar>0 ORDER BY CASE WHEN stt IS NULL THEN 1 ELSE 0 END, stt ASC, eklenme_tarihi ASC",
            (barkod,)
        ).fetchall()
        for p in partiler_rows:
            if remaining <= 0:
                break
            azalt = min(remaining, p["miktar"])
            c.execute("UPDATE partiler SET miktar=miktar-? WHERE parti_id=?", (azalt, p["parti_id"]))
            remaining -= azalt
        sonraki = get_toplam_stok(c, barkod)
    elif tip == "Giris":
        sonraki = onceki + miktar
    else:
        sonraki = onceki
    c.execute("INSERT INTO stok_hareketleri (barkod,urun_adi,hareket_tipi,miktar,onceki_stok,sonraki_stok,kullanici,aciklama) VALUES (?,?,?,?,?,?,?,?)",
              (barkod, urun_adi, tip, miktar, onceki, sonraki, kullanici, aciklama))
    c.commit()
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
        if not session.get("user") or session.get("rol") in ("misafir","goruntuleyici"):
            return redirect("/giris")
        return f(*a, **kw)
    return dec

# ═══════════════════════════════════════════════════
#  HTML ŞABLONU
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
body{background:var(--bg);color:var(--text);font-family:'Syne',sans-serif;min-height:100vh;overflow-x:hidden}

/* NOISE */
body::after{content:'';position:fixed;inset:0;pointer-events:none;z-index:999;
  background-image:url("data:image/svg+xml,%3Csvg viewBox='0 0 512 512' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.75' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='.035'/%3E%3C/svg%3E");
  opacity:.45}

/* PAGE TRANSITION */
.main{animation:pageIn .6s cubic-bezier(.16,1,.3,1) both}
@keyframes pageIn{from{opacity:0;transform:translateY(16px)}to{opacity:1;transform:none}}

/* ═══ LOADER ═══ */
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

/* SKT ALARM ANIMATIONS */
@keyframes sktFlash{0%,100%{opacity:0}50%{opacity:1}}
@keyframes sktShake{0%,100%{transform:translateX(0)}15%{transform:translateX(-8px)}30%{transform:translateX(8px)}45%{transform:translateX(-6px)}60%{transform:translateX(6px)}75%{transform:translateX(-3px)}90%{transform:translateX(3px)}}

/* HEADER */
.hdr{
  position:sticky;top:0;z-index:100;
  background:rgba(6,6,6,.88);
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

/* LOGO */
.logo-link{text-decoration:none}
.logo{
  font-family:'Bebas Neue',sans-serif;
  font-size:1.8rem;letter-spacing:4px;color:var(--text);
  transition:letter-spacing .3s,opacity .3s,text-shadow .3s;
  position:relative;
}
.logo:hover{opacity:.9;letter-spacing:6px;text-shadow:0 0 20px rgba(255,255,255,.2)}
.logo span{color:var(--g);font-style:normal}
/* Logo shine sweep */
.logo::after{
  content:'';position:absolute;top:0;left:-100%;width:60%;height:100%;
  background:linear-gradient(90deg,transparent,rgba(255,255,255,.08),transparent);
  animation:logoSweep 4s ease-in-out infinite;
}
@keyframes logoSweep{0%,80%,100%{left:-100%}40%{left:200%}}

/* NAV */
.nav{display:flex;align-items:center;gap:2px}
.nav a{
  color:var(--muted);text-decoration:none;
  font-size:.72rem;font-weight:700;letter-spacing:1.5px;text-transform:uppercase;
  padding:8px 16px;transition:all .25s cubic-bezier(.16,1,.3,1);position:relative;
  overflow:hidden;
}
.nav a::after{content:'';position:absolute;bottom:0;left:16px;right:16px;height:1px;
  background:linear-gradient(90deg,transparent,var(--g),transparent);transform:scaleX(0);transition:transform .35s cubic-bezier(.16,1,.3,1)}
.nav a::before{content:'';position:absolute;inset:0;background:rgba(255,255,255,.03);opacity:0;transition:opacity .25s}
.nav a:hover{color:var(--text)}
.nav a:hover::before{opacity:1}
.nav a:hover::after,.nav a.active::after{transform:scaleX(1)}
.nav a.active{color:var(--g)}
.nav a:active{transform:scale(.96)}
.nav-divider{width:1px;height:20px;background:var(--border);margin:0 10px}
.rol-badge{
  font-family:'JetBrains Mono',monospace;
  font-size:.65rem;font-weight:600;letter-spacing:1.5px;text-transform:uppercase;
  padding:4px 12px;border:1px solid var(--border);color:var(--g);margin:0 8px;
  transition:all .3s cubic-bezier(.16,1,.3,1);
  position:relative;overflow:hidden;
}
.rol-badge:hover{border-color:rgba(255,255,255,.3);box-shadow:0 0 12px rgba(255,255,255,.05)}
.rol-badge::after{content:'';position:absolute;inset:0;background:linear-gradient(110deg,transparent 30%,rgba(255,255,255,.06) 50%,transparent 70%);animation:badgeSweep 3s ease-in-out infinite}
@keyframes badgeSweep{0%,100%{transform:translateX(-100%)}50%{transform:translateX(100%)}}
.nav-user{font-size:.82rem;color:var(--sub);margin-right:4px}
.btn-login{
  background:var(--g)!important;color:#060606!important;font-weight:800!important;
  padding:8px 22px!important;letter-spacing:1px;
  clip-path:polygon(0 0,calc(100% - 8px) 0,100% 8px,100% 100%,8px 100%,0 calc(100% - 8px));
  transition:all .3s cubic-bezier(.16,1,.3,1)!important;
  position:relative;overflow:hidden;
}
.btn-login::before{content:'';position:absolute;inset:0;background:linear-gradient(110deg,transparent 20%,rgba(255,255,255,.2) 50%,transparent 80%);transform:translateX(-100%);transition:transform .5s}
.btn-login:hover{background:var(--g2)!important;transform:translateY(-2px);box-shadow:0 8px 24px rgba(255,255,255,.15)}
.btn-login:hover::before{transform:translateX(100%)}
.btn-login::after{display:none!important}
.btn-logout{color:#e05252!important;transition:all .25s!important}
.btn-logout:hover{color:#ff7070!important}
.btn-logout::after{background:#e05252!important}

/* MAIN */
.main{padding:32px 40px;max-width:1400px;margin:0 auto}

/* PAGE TITLE */
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

/* STAT CARDS */
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

/* TABLES */
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

/* PANELS */
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

/* FORMS */
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

/* BUTTONS */
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

/* SCAN PAGE */
.scan-wrap{max-width:600px;margin:0 auto;padding-top:20px}
.scan-title{font-family:'Bebas Neue',sans-serif;font-size:2.5rem;letter-spacing:2px;margin-bottom:24px;display:flex;align-items:center;gap:12px}
.scan-title::before{content:'';display:block;width:4px;height:32px;background:var(--g)}
.scan-input-row{display:flex;gap:8px;margin-bottom:8px}
.scan-input-row input{
  margin:0;font-family:'JetBrains Mono',monospace;font-size:1.1rem;
  letter-spacing:3px;text-align:center;
  background:rgba(255,255,255,.03);
}

/* SCAN RESULT — creative addition: animated gradient border + slide-in */
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

/* CAMERA */
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
/* Scanning laser line */
.kamera-overlay::after{
  content:'';position:absolute;left:4px;right:4px;height:2px;
  background:linear-gradient(90deg,transparent,var(--accent),transparent);
  box-shadow:0 0 12px var(--accent);
  animation:scanLaser 2s ease-in-out infinite;
  opacity:.8;
}
@keyframes scanLaser{0%{top:4px}50%{top:calc(100% - 6px)}100%{top:4px}}
/* Corner brackets */
.kamera-overlay::before{
  content:'';position:absolute;inset:-1px;
  border:16px solid transparent;
  border-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='48' height='48'%3E%3Cpath d='M0,16V0H16M32,0H48V16M48,32V48H32M16,48H0V32' fill='none' stroke='white' stroke-width='2'/%3E%3C/svg%3E") 16 fill;
  pointer-events:none;
}

/* ALERTS */
.alert{
  padding:12px 16px;margin-bottom:16px;font-size:.88rem;font-weight:500;
  border-left:3px solid;font-family:'Syne',sans-serif;
  animation:alertIn .4s cubic-bezier(.16,1,.3,1) both;
}
@keyframes alertIn{from{opacity:0;transform:translateX(-10px)}to{opacity:1;transform:none}}
.alert-red{background:rgba(224,82,82,.06);color:#e05252;border-color:#e05252}
.alert-green{background:rgba(255,255,255,.03);color:var(--g);border-color:var(--g)}
.alert-yellow{background:rgba(240,180,41,.05);color:#f0b429;border-color:#f0b429}

/* COLORS */
.green{color:var(--g)}.red{color:#e05252}.yellow{color:#f0b429}.orange{color:#fb923c}.muted{color:var(--muted)}

/* LOGIN */
.login-wrap{max-width:400px;margin:80px auto;animation:loginIn .7s cubic-bezier(.16,1,.3,1) both}
@keyframes loginIn{from{opacity:0;transform:translateY(24px) scale(.97)}to{opacity:1;transform:none}}
.login-wrap .panel{padding:40px}
.login-logo{font-family:'Bebas Neue',sans-serif;font-size:2.5rem;letter-spacing:4px;text-align:center;margin-bottom:6px}
.login-sub{font-family:'JetBrains Mono',monospace;font-size:.72rem;color:var(--muted);text-align:center;letter-spacing:2px;text-transform:uppercase;margin-bottom:32px}

/* RESPONSIVE */
@media(max-width:900px){
  .hdr{padding:0 16px}
  .main{padding:20px 16px}
  .grid2{grid-template-columns:1fr}
  .stat-grid{grid-template-columns:repeat(2,1fr)}
  .nav a{padding:6px 8px;font-size:.7rem}
  .nav-user{display:none}
}
</style>
<script>
/* ═══ LOADER — HEAD (runs before body paints) ═══ */
document.addEventListener('DOMContentLoaded',function(){
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
    pct+=Math.random()*2.5+1.5;
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
        setTimeout(function(){ if(loader) loader.style.display='none'; },1000);
      },200);
    }
  },40);
});
</script>
</head>
<body>
<!-- LOADER -->
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
  <a href="https://nextstock-tan-t-m.vercel.app/" class="logo-link" target="_blank">
    <div class="logo">Nex<span>Stock</span></div>
  </a>
  <div class="nav">
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
</div>
<div class="main">
CONTENT_BLOCK
</div>
<script>
// Magnetic buttons
document.querySelectorAll('.btn-green,.btn-login,.nav-demo').forEach(function(btn){
  btn.addEventListener('mousemove',function(e){
    var r=btn.getBoundingClientRect();
    var x=(e.clientX-r.left-r.width/2)*.15;
    var y=(e.clientY-r.top-r.height/2)*.15;
    btn.style.transform='translate('+x+'px,'+y+'px)';
  });
  btn.addEventListener('mouseleave',function(){btn.style.transform='';});
});
// Stat card counter animation
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
</body></html>"""

def render(content, page="", title="NexStock", **kw):
    html = BASE.replace("CONTENT_BLOCK", content)
    return render_template_string(html, session=session, page=page, title=title, **kw)

# ═══════════════════════════════════════════════════
#  GİRİŞ / ÇIKIŞ
# ═══════════════════════════════════════════════════
@app.route("/giris", methods=["GET","POST"])
def giris():
    hata = ""
    if request.method == "POST":
        k = request.form.get("k","").strip()
        s = request.form.get("s","").strip()
        c = get_db()
        row = c.execute("SELECT * FROM kullanicilar WHERE kullanici_adi=? AND sifre_hash=? AND aktif=1",
                        (k, sh(s))).fetchone()
        c.close()
        if row:
            session["user"]   = row["kullanici_adi"]
            session["rol"]    = row["rol"]
            session["tam_ad"] = row["tam_ad"] or ""
            c2 = get_db()
            c2.execute("UPDATE kullanicilar SET son_giris=CURRENT_TIMESTAMP WHERE kullanici_adi=?", (k,))
            c2.commit(); c2.close()
            return redirect("/")
        hata = "Hatali kullanici adi veya sifre!"

    content = f"""
<div class="login-wrap">
  <div class="panel">
    <div class="login-logo">Nex<span style="color:var(--g)">Stock</span></div>
    <div class="login-sub">Envanter Yonetim Sistemi</div>
    {'<div class="alert alert-red">'+hata+'</div>' if hata else ''}
    <form method="POST">
      <label style="color:#a3a3a3;font-size:.82rem;font-weight:600;display:block;margin-bottom:4px">KULLANICI ADI</label>
      <input name="k" placeholder="kullanici_adi" autofocus autocomplete="username">
      <label style="color:#a3a3a3;font-size:.82rem;font-weight:600;display:block;margin-bottom:4px">SIFRE</label>
      <input name="s" type="password" placeholder="••••••••" autocomplete="current-password">
      <button type="submit" class="btn btn-green" style="width:100%;margin-top:4px;padding:12px">GIRIS YAP</button>
    </form>
    <div style="text-align:center;margin-top:16px;color:#525252;font-size:.8rem">Varsayilan: admin / admin123</div>
  </div>
</div>"""
    return render(content, page="giris", title="Giris")

@app.route("/cikis")
def cikis():
    session.clear()
    misafir_yap()
    return redirect("/tarama")

# ═══════════════════════════════════════════════════
#  ANA SAYFA
# ═══════════════════════════════════════════════════
@app.route("/anasayfa")
def anasayfa():
    return redirect("https://nexstock-landing.vercel.app", code=302)

@app.route("/")
@giris_gerekli
def index():
    if session.get("rol") in ("misafir","goruntuleyici"):
        return redirect("/tarama")

    c = get_db()
    today = date.today().isoformat()
    s = {
        "toplam_urun":   c.execute("SELECT COUNT(*) FROM urunler").fetchone()[0],
        "toplam_stok":   c.execute("SELECT COALESCE(SUM(miktar),0) FROM partiler").fetchone()[0],
        "tarihi_gecmis": c.execute("SELECT COUNT(DISTINCT barkod) FROM partiler WHERE stt IS NOT NULL AND stt<? AND miktar>0", (today,)).fetchone()[0],
        "yaklasan":      c.execute("SELECT COUNT(DISTINCT barkod) FROM partiler WHERE stt IS NOT NULL AND stt>=? AND stt<=date(?,'+'||7||' days') AND miktar>0", (today,today)).fetchone()[0],
        "kritik":        c.execute("SELECT COUNT(*) FROM urunler u WHERE (SELECT COALESCE(SUM(miktar),0) FROM partiler WHERE barkod=u.barkod) > 0 AND (SELECT COALESCE(SUM(miktar),0) FROM partiler WHERE barkod=u.barkod) <= u.min_stok").fetchone()[0],
        "stoksuz":       c.execute("SELECT COUNT(*) FROM urunler u WHERE (SELECT COALESCE(SUM(miktar),0) FROM partiler WHERE barkod=u.barkod) <= 0").fetchone()[0],
        "bugun":         c.execute("SELECT COUNT(*) FROM stok_hareketleri WHERE DATE(tarih)=DATE('now')").fetchone()[0],
        "tedarikci":     c.execute("SELECT COUNT(*) FROM tedarikciler WHERE aktif=1").fetchone()[0],
    }
    skt_list = [dict(r) for r in c.execute("""
        SELECT u.barkod, u.urun_adi, u.kategori, p.stt,
               COALESCE((SELECT SUM(miktar) FROM partiler WHERE barkod=u.barkod), 0) as stok_adedi
        FROM partiler p JOIN urunler u ON p.barkod = u.barkod
        WHERE p.stt IS NOT NULL AND p.stt <= date(?,'+'||7||' days') AND p.miktar > 0
        GROUP BY p.barkod ORDER BY p.stt
    """, (today,)).fetchall()]
    dusuk = [dict(r) for r in c.execute("""
        SELECT u.*, COALESCE(ps.toplam, 0) as stok_adedi
        FROM urunler u
        LEFT JOIN (SELECT barkod, SUM(miktar) as toplam FROM partiler GROUP BY barkod) ps ON u.barkod = ps.barkod
        WHERE COALESCE(ps.toplam, 0) <= u.min_stok
        ORDER BY COALESCE(ps.toplam, 0) LIMIT 20
    """).fetchall()]
    son_har  = [dict(r) for r in c.execute("SELECT * FROM stok_hareketleri ORDER BY tarih DESC LIMIT 10").fetchall()]
    c.close()

    kfg = [
        ("toplam_urun","Toplam Urun","#ffffff"),
        ("toplam_stok","Toplam Stok","#34d399"),
        ("tarihi_gecmis","Tarihi Gecmis","#e05252"),
        ("yaklasan","Yaklasan SKT","#f0b429"),
        ("kritik","Kritik Stok","#fb923c"),
        ("stoksuz","Stoksuz","#a78bfa"),
        ("bugun","Bugun Islem","#ffffff"),
        ("tedarikci","Tedarikci","#a3a3a3"),
    ]
    kartlar = "".join(f'<div class="stat-card" style="border-color:{col}"><div class="val" style="color:{col}">{s[k]}</div><div class="lbl">{l}</div></div>' for k,l,col in kfg)

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
        cls = "green" if h["hareket_tipi"]=="Giris" else "red" if h["hareket_tipi"] in ["Cikis","Okutma"] else ""
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
#  TARAMA
# ═══════════════════════════════════════════════════
@app.route("/tarama", methods=["GET","POST"])
@giris_gerekli
def tarama():
    sonuc_html = ""
    alert_html = ""

    if request.method == "POST":
        barkod = request.form.get("barkod","").strip()
    elif request.args.get("barkod"):
        barkod = request.args.get("barkod","").strip()
        if request.args.get("skt_ok"):
            alert_html = '<div class="alert alert-green">✓ SKT basariyla guncellendi!</div>'
    else:
        barkod = None

    if barkod:
        c = get_db()
        urun = c.execute("SELECT * FROM urunler WHERE barkod=?", (barkod,)).fetchone()

        if not urun:
            c.close()
            urun_adi, kategori = openfoodfacts(barkod)
            if urun_adi:
                c = get_db()
                c.execute("INSERT OR REPLACE INTO urunler (barkod,urun_adi,kategori) VALUES (?,?,?)",
                          (barkod, urun_adi, kategori or "Genel"))
                c.execute("INSERT INTO partiler (barkod, miktar, ekleyen) VALUES (?,?,?)",
                          (barkod, 30, "sistem"))
                c.commit()
                alert_html = f'<div class="alert alert-green">✓ Yeni urun eklendi: <strong>{urun_adi}</strong> (Open Food Facts)</div>'
                urun = c.execute("SELECT * FROM urunler WHERE barkod=?", (barkod,)).fetchone()
            else:
                alert_html = f'<div id="alert-type" data-tip="error" style="display:none"></div><div class="alert alert-red">✗ Barkod bulunamadi: <strong>{barkod}</strong></div>'
                c = get_db()

        if urun:
            urun = dict(urun)
            toplam_stok = get_toplam_stok(c, barkod)
            en_yakin = get_en_yakin_stt(c, barkod)
            partiler_list = [dict(r) for r in c.execute(
                "SELECT * FROM partiler WHERE barkod=? AND miktar>0 ORDER BY CASE WHEN stt IS NULL THEN 1 ELSE 0 END, stt ASC",
                (barkod,)
            ).fetchall()]
            c.close()

            gun  = kalan_gun(en_yakin)
            rc   = stt_renk(gun)
            et   = stt_etiket(gun)

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

            if request.method == "POST":
                log_hareket(barkod, urun["urun_adi"], "Okutma", 1,
                            "Web tarama", session.get("user","misafir"))
                # Stok degismis olabilir, yeniden cek
                c2 = get_db()
                toplam_stok = get_toplam_stok(c2, barkod)
                partiler_list = [dict(r) for r in c2.execute(
                    "SELECT * FROM partiler WHERE barkod=? AND miktar>0 ORDER BY CASE WHEN stt IS NULL THEN 1 ELSE 0 END, stt ASC",
                    (barkod,)
                ).fetchall()]
                c2.close()

            # Parti satirlari — sıra numarası P1, P2...
            parti_rows = ""
            for no, p in enumerate(partiler_list, 1):
                pgun = kalan_gun(p.get("stt"))
                prc = stt_renk(pgun)
                pet = stt_etiket(pgun) if p.get("stt") else "SKT Yok"
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

            # Parti dropdown secenekleri (stok cikisi icin)
            parti_opts = '<option value="fefo">Otomatik (FEFO)</option>'
            for no, p in enumerate(partiler_list, 1):
                parti_opts += f'<option value="{p["parti_id"]}">P{no} — {p.get("stt","SKT Yok")} ({p["miktar"]} adet)</option>'

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
    <div style="font-size:1.7rem;font-weight:800;color:#ffffff">{float(urun.get("fiyat",0)):.2f} TL</div>
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
  </div>
</div>
<script>
function partiPanelAc(){{ document.getElementById('parti-panel').style.display='block'; document.getElementById('cikis-panel').style.display='none'; }}
function partiPanelKapat(){{ document.getElementById('parti-panel').style.display='none'; }}
function cikisPanelAc(){{ document.getElementById('cikis-panel').style.display='block'; document.getElementById('parti-panel').style.display='none'; }}
function cikisPanelKapat(){{ document.getElementById('cikis-panel').style.display='none'; }}
</script>"""
        else:
            c.close()

    # ── Son taranan urunler ──
    son_tarananlar_html = ""
    try:
        c = get_db()
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
        c.close()
        if son_list:
            cards = ""
            for i, s in enumerate(son_list):
                s = dict(s)
                gun = kalan_gun(s.get("stt"))
                rc = stt_renk(gun)
                et = stt_etiket(gun)
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

    # ── Toplam istatistik ──
    stats_html = ""
    try:
        c = get_db()
        toplam = c.execute("SELECT COUNT(*) FROM urunler").fetchone()[0]
        dusuk = c.execute("SELECT COUNT(*) FROM urunler u WHERE COALESCE((SELECT SUM(miktar) FROM partiler WHERE barkod=u.barkod), 0) <= u.min_stok").fetchone()[0]
        bugun_scan = c.execute("SELECT COUNT(*) FROM stok_hareketleri WHERE hareket_tipi='Okutma' AND date(tarih)=date('now')").fetchone()[0]
        c.close()
        stats_html = f'''
<div class="mini-stats">
  <div class="ms-card" style="animation-delay:.05s"><div class="ms-val">{toplam}</div><div class="ms-lbl">Toplam Urun</div></div>
  <div class="ms-card" style="animation-delay:.1s"><div class="ms-val" style="color:#e05252">{dusuk}</div><div class="ms-lbl">Dusuk Stok</div></div>
  <div class="ms-card" style="animation-delay:.15s"><div class="ms-val">{bugun_scan}</div><div class="ms-lbl">Bugun Tarama</div></div>
</div>'''
    except Exception:
        pass

    # Kamera JS — siki dogrulama ile
    kamera_js = """
<script src="https://cdnjs.cloudflare.com/ajax/libs/quagga/0.12.1/quagga.min.js" defer></script>
<script>
// ── SES SISTEMI ──────────────────────────────
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
    // SIREN ALARMI — tarihi gecmis urun, cok agresif
    var ctx = _getCtx();
    var osc = ctx.createOscillator();
    var gain = ctx.createGain();
    osc.connect(gain); gain.connect(ctx.destination);
    osc.type = 'sawtooth';
    gain.gain.setValueAtTime(0.6, ctx.currentTime);
    // Siren: frekans yukari-asagi sallanir 3 saniye boyunca
    for(var i=0; i<12; i++){
      osc.frequency.setValueAtTime(800, ctx.currentTime + i*0.25);
      osc.frequency.linearRampToValueAtTime(1600, ctx.currentTime + i*0.25 + 0.125);
      osc.frequency.linearRampToValueAtTime(800, ctx.currentTime + i*0.25 + 0.25);
    }
    gain.gain.setValueAtTime(0.6, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 3);
    osc.start(ctx.currentTime);
    osc.stop(ctx.currentTime + 3);
    // Ekrani kirmizi flash yap
    var flash = document.createElement('div');
    flash.style.cssText='position:fixed;inset:0;background:rgba(224,82,82,.25);z-index:9998;pointer-events:none;animation:sktFlash 0.3s ease-in-out 6 both';
    document.body.appendChild(flash);
    setTimeout(function(){ flash.remove(); }, 2000);
    // Sonucu titret
    var res = document.querySelector('.scan-result');
    if(res){ res.style.animation='sktShake 0.4s ease-in-out 3'; setTimeout(function(){ res.style.animation=''; },1500); }
  }
  else if(gun === 0){
    // Bugun bitiyor — orta seviye alarm
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

// ── BARKOD DOGRULAMA ─────────────────────────
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

// ── KAMERA ───────────────────────────────────
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

    // 1) Hata orani filtresi
    var hatalar = res.codeResult.decodedCodes.filter(function(x){return x.error!==undefined;});
    var toplamHata = hatalar.reduce(function(a,b){return a+b.error;},0);
    if(hatalar.length > 0 && toplamHata/hatalar.length > 0.20) return;

    // 2) Barkod dogrulama (checksum + uzunluk + rakam)
    if(!gecerliBarkod(kod, format)) return;

    // 3) Tutarlilik — 3 kez ayni kod
    _sayac[kod] = (_sayac[kod]||0)+1;
    document.getElementById('kam-durum').innerText='Okuma: '+kod+' ('+_sayac[kod]+'/3)';
    document.getElementById('kam-durum').style.color='rgba(165,216,255,.8)';

    // Gurultu temizleme
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
</script>"""

    # ── Idle hero (animasyonlu barkod gorseli — bos sayfa icin) ──
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
.idle-text{{
  font-family:'Bebas Neue',sans-serif;font-size:1.3rem;letter-spacing:3px;
  color:var(--sub);margin-top:14px;
}}
.idle-sub{{
  font-family:'JetBrains Mono',monospace;font-size:.58rem;letter-spacing:2px;
  color:var(--muted);margin-top:5px;text-transform:uppercase;
}}
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
.son-card-name{{
  font-family:'Syne',sans-serif;font-size:.85rem;font-weight:700;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
}}
.son-card-bottom{{display:flex;align-items:center;gap:12px}}
.son-card-barkod{{font-family:'JetBrains Mono',monospace;font-size:.68rem;color:var(--muted);letter-spacing:1px}}
.son-card-stok{{font-family:'JetBrains Mono',monospace;font-size:.68rem;color:var(--sub);letter-spacing:1px}}
.mini-stats{{
  display:grid;grid-template-columns:repeat(3,1fr);gap:1px;
  background:var(--border);margin-bottom:20px;
}}
.ms-card{{
  background:var(--card);padding:16px 12px;text-align:center;
  transition:all .3s cubic-bezier(.16,1,.3,1);
  animation:resultIn .4s cubic-bezier(.16,1,.3,1) both;
}}
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
    <button type="button" onclick="kameraAc()" class="btn btn-muted" style="width:100%">Kamera ile Tara</button>
  </form>

  {idle_hero}
  {sonuc_html}
  {son_tarananlar_html}
</div>"""
    return render(content, page="tarama", title="Tarama")

# ═══════════════════════════════════════════════════
#  PARTI ISLEMLERI
# ═══════════════════════════════════════════════════
@app.route("/parti-ekle", methods=["POST"])
@giris_gerekli
def parti_ekle():
    barkod = request.form.get("barkod","").strip()
    stt = request.form.get("stt","").strip() or None
    miktar = int(request.form.get("miktar", 1) or 1)
    if barkod and miktar > 0:
        c = get_db()
        c.execute("INSERT INTO partiler (barkod, stt, miktar, ekleyen) VALUES (?,?,?,?)",
                  (barkod, stt, miktar, session.get("user","sistem")))
        c.commit()
        urun = c.execute("SELECT urun_adi FROM urunler WHERE barkod=?", (barkod,)).fetchone()
        c.close()
        urun_adi = urun["urun_adi"] if urun else barkod
        log_hareket(barkod, urun_adi, "Giris", miktar,
                    f"Yeni parti eklendi (SKT: {stt or 'Belirtilmemis'})",
                    session.get("user","misafir"))
    return redirect(f"/tarama?barkod={barkod}&skt_ok=1")

@app.route("/parti-tukendi", methods=["POST"])
@giris_gerekli
def parti_tukendi():
    parti_id = request.form.get("parti_id","").strip()
    barkod = request.form.get("barkod","").strip()
    if parti_id:
        c = get_db()
        parti = c.execute("SELECT miktar FROM partiler WHERE parti_id=?", (parti_id,)).fetchone()
        eski_miktar = parti["miktar"] if parti else 0
        c.execute("UPDATE partiler SET miktar=0 WHERE parti_id=?", (parti_id,))
        c.commit()
        urun = c.execute("SELECT urun_adi FROM urunler WHERE barkod=?", (barkod,)).fetchone()
        c.close()
        urun_adi = urun["urun_adi"] if urun else barkod
        log_hareket(barkod, urun_adi, "Cikis", eski_miktar,
                    f"Parti #{parti_id} tukendi olarak isaretlendi",
                    session.get("user","misafir"))
    return redirect(f"/tarama?barkod={barkod}")

@app.route("/stok-cikis", methods=["POST"])
@giris_gerekli
def stok_cikis():
    barkod = request.form.get("barkod","").strip()
    miktar = int(request.form.get("miktar", 1) or 1)
    sebep = request.form.get("sebep","Raftan Kaldirildi").strip()
    aciklama = request.form.get("aciklama","").strip()
    parti_id = request.form.get("parti_id","").strip()
    if barkod and miktar > 0:
        c = get_db()
        if parti_id and parti_id != "fefo":
            # Manuel parti secimi
            p = c.execute("SELECT miktar FROM partiler WHERE parti_id=?", (parti_id,)).fetchone()
            if p:
                azalt = min(miktar, p["miktar"])
                c.execute("UPDATE partiler SET miktar=MAX(0,miktar-?) WHERE parti_id=?", (azalt, parti_id))
                c.commit()
        else:
            # FEFO: en yakin SKT'li partiden dus
            remaining = miktar
            partiler_rows = c.execute(
                "SELECT parti_id, miktar FROM partiler WHERE barkod=? AND miktar>0 ORDER BY CASE WHEN stt IS NULL THEN 1 ELSE 0 END, stt ASC",
                (barkod,)
            ).fetchall()
            for p in partiler_rows:
                if remaining <= 0: break
                azalt = min(remaining, p["miktar"])
                c.execute("UPDATE partiler SET miktar=miktar-? WHERE parti_id=?", (azalt, p["parti_id"]))
                remaining -= azalt
            c.commit()
        urun = c.execute("SELECT urun_adi FROM urunler WHERE barkod=?", (barkod,)).fetchone()
        urun_adi = urun["urun_adi"] if urun else barkod
        onceki = get_toplam_stok(c, barkod) + miktar
        sonraki = get_toplam_stok(c, barkod)
        c.execute("INSERT INTO stok_hareketleri (barkod,urun_adi,hareket_tipi,miktar,onceki_stok,sonraki_stok,kullanici,aciklama) VALUES (?,?,?,?,?,?,?,?)",
                  (barkod, urun_adi, "Cikis", miktar, onceki, sonraki, session.get("user","misafir"),
                   f"{sebep}: {aciklama}" if aciklama else sebep))
        c.commit()
        c.close()
    return redirect(f"/tarama?barkod={barkod}")

@app.route("/parti-sil", methods=["POST"])
@giris_gerekli
def parti_sil():
    parti_id = request.form.get("parti_id","").strip()
    barkod   = request.form.get("barkod","").strip()
    redirect_to = request.form.get("redirect_to","tarama")
    if parti_id:
        c = get_db()
        parti = c.execute("SELECT miktar FROM partiler WHERE parti_id=?", (parti_id,)).fetchone()
        eski_miktar = parti["miktar"] if parti else 0
        c.execute("DELETE FROM partiler WHERE parti_id=?", (parti_id,))
        c.commit()
        urun = c.execute("SELECT urun_adi FROM urunler WHERE barkod=?", (barkod,)).fetchone()
        c.close()
        urun_adi = urun["urun_adi"] if urun else barkod
        if eski_miktar > 0:
            log_hareket(barkod, urun_adi, "Cikis", eski_miktar,
                        f"Parti silindi", session.get("user","misafir"))
    if redirect_to == "partiler":
        return redirect(f"/partiler?barkod={barkod}")
    return redirect(f"/tarama?barkod={barkod}")

@app.route("/parti-skt-guncelle", methods=["POST"])
@giris_gerekli
def parti_skt_guncelle():
    parti_id = request.form.get("parti_id","").strip()
    barkod   = request.form.get("barkod","").strip()
    stt      = request.form.get("stt","").strip() or None
    redirect_to = request.form.get("redirect_to","tarama")
    if parti_id:
        c = get_db()
        c.execute("UPDATE partiler SET stt=? WHERE parti_id=?", (stt, parti_id))
        c.commit()
        c.close()
    if redirect_to == "partiler":
        return redirect(f"/partiler?barkod={barkod}")
    return redirect(f"/tarama?barkod={barkod}")

@app.route("/partiler")
@yetkili_giris
def partiler_sayfasi():
    ara_barkod = request.args.get("barkod","").strip()
    c = get_db()
    if ara_barkod:
        urunler_list = [dict(r) for r in c.execute(
            "SELECT * FROM urunler WHERE barkod=?", (ara_barkod,)).fetchall()]
    else:
        urunler_list = [dict(r) for r in c.execute(
            "SELECT DISTINCT u.* FROM urunler u JOIN partiler p ON u.barkod=p.barkod ORDER BY u.urun_adi"
        ).fetchall()]

    content_rows = ""
    for u in urunler_list:
        partiler_list = [dict(r) for r in c.execute(
            "SELECT * FROM partiler WHERE barkod=? ORDER BY CASE WHEN stt IS NULL THEN 1 ELSE 0 END, stt ASC, eklenme_tarihi ASC",
            (u["barkod"],)
        ).fetchall()]
        toplam = sum(p["miktar"] for p in partiler_list)

        parti_rows = ""
        for no, p in enumerate(partiler_list, 1):
            pgun = kalan_gun(p.get("stt"))
            prc  = stt_renk(pgun)
            pet  = stt_etiket(pgun) if p.get("stt") else "SKT Yok"
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
    ara = request.args.get("ara","")
    c = get_db()
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
        q += " AND (u.urun_adi LIKE ? OR u.barkod LIKE ?)"
        p += [f"%{ara}%",f"%{ara}%"]
    q += " ORDER BY u.urun_adi"
    liste = [dict(r) for r in c.execute(q,p).fetchall()]
    c.close()

    rows = ""
    for u in liste:
        gun = kalan_gun(u.get("stt"))
        rc  = stt_renk(gun)
        et  = stt_etiket(gun) if u.get("stt") else "—"
        rows += f'<tr><td style="font-family:monospace;font-size:.82rem;color:#a3a3a3">{u["barkod"]}</td><td><strong>{u["urun_adi"]}</strong></td><td style="color:#a3a3a3">{u.get("kategori","—")}</td><td>{u.get("stt","—")}</td><td style="color:{rc};font-weight:600;font-size:.82rem">{et}</td><td style="font-weight:700">{u["stok_adedi"]}</td><td style="color:#a3a3a3">{u.get("parti_sayisi",0)}</td><td style="color:#525252">{u.get("min_stok",5)}</td><td style="color:#ffffff">{float(u.get("fiyat",0)):.2f} TL</td></tr>'

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
    liste = [dict(r) for r in c.execute(
        "SELECT * FROM stok_hareketleri ORDER BY tarih DESC LIMIT 200").fetchall()]
    c.close()
    rows = ""
    for h in liste:
        cls = "green" if h["hareket_tipi"]=="Giris" else "red" if h["hareket_tipi"] in ["Cikis","Okutma"] else ""
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
    today = date.today().isoformat()
    s = {
        "toplam_urun":   c.execute("SELECT COUNT(*) FROM urunler").fetchone()[0],
        "toplam_stok":   c.execute("SELECT COALESCE(SUM(miktar),0) FROM partiler").fetchone()[0],
        "toplam_deger":  c.execute("SELECT COALESCE(SUM(ps.toplam * u.fiyat), 0) FROM urunler u JOIN (SELECT barkod, SUM(miktar) as toplam FROM partiler GROUP BY barkod) ps ON u.barkod = ps.barkod").fetchone()[0],
        "tarihi_gecmis": c.execute("SELECT COUNT(DISTINCT barkod) FROM partiler WHERE stt IS NOT NULL AND stt<? AND miktar>0", (today,)).fetchone()[0],
        "stoksuz":       c.execute("SELECT COUNT(*) FROM urunler u WHERE (SELECT COALESCE(SUM(miktar),0) FROM partiler WHERE barkod=u.barkod) <= 0").fetchone()[0],
        "toplam_islem":  c.execute("SELECT COUNT(*) FROM stok_hareketleri").fetchone()[0],
        "bugun_islem":   c.execute("SELECT COUNT(*) FROM stok_hareketleri WHERE DATE(tarih)=DATE('now')").fetchone()[0],
    }
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
    liste = [dict(r) for r in c.execute(
        "SELECT id,kullanici_adi,tam_ad,rol,aktif,son_giris FROM kullanicilar ORDER BY tam_ad").fetchall()]
    c.close()
    RC = {"admin":"#ffffff","mudur":"#34d399","kasiyer":"#86efac","goruntuleyici":"#a3a3a3"}
    rows = ""
    for u in liste:
        rc  = RC.get(u["rol"],"#a3a3a3")
        akt = '<span class="green">✓ Aktif</span>' if u["aktif"] else '<span class="red">✗ Pasif</span>'
        rows += f'<tr><td style="font-weight:600">{u["kullanici_adi"]}</td><td>{u.get("tam_ad","—")}</td><td style="color:{rc};font-weight:700">{u["rol"].upper()}</td><td>{akt}</td><td style="color:#a3a3a3">{str(u.get("son_giris","—"))[:16]}</td></tr>'

    content = f"""
<div class="page-title">Kullanicilar</div>
<div class="tbl-wrap"><table>
  <tr><th>Kullanici Adi</th><th>Tam Ad</th><th>Rol</th><th>Durum</th><th>Son Giris</th></tr>
  {rows}
</table></div>"""
    return render(content, page="kullanicilar", title="Kullanicilar")

# ═══════════════════════════════════════════════════
#  API
# ═══════════════════════════════════════════════════
@app.route("/api/stats")
@giris_gerekli
def api_stats():
    c = get_db()
    today = date.today().isoformat()
    data = {
        "toplam_urun":   c.execute("SELECT COUNT(*) FROM urunler").fetchone()[0],
        "toplam_stok":   c.execute("SELECT COALESCE(SUM(miktar),0) FROM partiler").fetchone()[0],
        "tarihi_gecmis": c.execute("SELECT COUNT(DISTINCT barkod) FROM partiler WHERE stt IS NOT NULL AND stt<? AND miktar>0", (today,)).fetchone()[0],
        "stoksuz":       c.execute("SELECT COUNT(*) FROM urunler u WHERE (SELECT COALESCE(SUM(miktar),0) FROM partiler WHERE barkod=u.barkod) <= 0").fetchone()[0],
    }
    c.close()
    return jsonify(data)

@app.route("/api/urunler")
@giris_gerekli
def api_urunler():
    c = get_db()
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
    c.close()
    return jsonify(liste)

@app.route("/api/hareketler")
@giris_gerekli
def api_hareketler():
    c = get_db()
    liste = [dict(r) for r in c.execute(
        "SELECT * FROM stok_hareketleri ORDER BY tarih DESC LIMIT 100").fetchall()]
    c.close()
    return jsonify(liste)

if __name__ == "__main__":
    app.run(debug=False)
