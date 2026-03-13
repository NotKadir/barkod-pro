# -*- coding: utf-8 -*-
import os, hashlib, sqlite3, functools, requests
from datetime import datetime, date
from flask import Flask, render_template_string, request, redirect, session, jsonify

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "nexstock_secret_2024")
DB_NAME = os.environ.get("DB_PATH", "envanter_pro.db")

# ═══════════════════════════════════════════════════
#  VERİTABANI
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
        stt DATE,
        stok_adedi INTEGER DEFAULT 0,
        min_stok INTEGER DEFAULT 5,
        fiyat REAL DEFAULT 0.0,
        aciklama TEXT,
        eklenme_tarihi DATETIME DEFAULT CURRENT_TIMESTAMP,
        son_guncelleme DATETIME DEFAULT CURRENT_TIMESTAMP
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

init_db()

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

def log_hareket(barkod, urun_adi, tip, miktar, aciklama, kullanici):
    c = get_db()
    urun = c.execute("SELECT stok_adedi FROM urunler WHERE barkod=?", (barkod,)).fetchone()
    onceki = urun["stok_adedi"] if urun else 0
    if tip in ("Cikis","Okutma"):
        sonraki = max(0, onceki - miktar)
        c.execute("UPDATE urunler SET stok_adedi=MAX(0,stok_adedi-?) WHERE barkod=?", (miktar, barkod))
    elif tip == "Giris":
        sonraki = onceki + miktar
        c.execute("UPDATE urunler SET stok_adedi=stok_adedi+? WHERE barkod=?", (miktar, barkod))
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
<link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=DM+Sans:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
<style>
:root{
  --g:#ffffff;--g2:#e5e5e5;--bg:#080808;--panel:#111111;
  --card:#161616;--border:#2a2a2a;--text:#f5f5f5;--sub:#d4d4d4;--muted:#525252;
}
*{box-sizing:border-box;margin:0;padding:0}
html{scroll-behavior:smooth}
body{background:var(--bg);color:var(--text);font-family:'DM Sans',sans-serif;min-height:100vh;overflow-x:hidden}

/* NOISE */
body::after{content:'';position:fixed;inset:0;pointer-events:none;z-index:999;
  background-image:url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.035'/%3E%3C/svg%3E");
  opacity:.5}

/* HEADER */
.hdr{
  position:sticky;top:0;z-index:100;
  background:rgba(5,8,5,.92);
  backdrop-filter:blur(20px);
  border-bottom:1px solid var(--border);
  padding:0 40px;
  display:flex;align-items:center;justify-content:space-between;
  height:60px;
}
.hdr::after{content:'';position:absolute;bottom:-1px;left:0;width:100%;height:1px;
  background:linear-gradient(90deg,transparent,var(--g),transparent);opacity:.4}

/* LOGO */
.logo-link{text-decoration:none}
.logo{
  font-family:'Bebas Neue',sans-serif;
  font-size:1.6rem;letter-spacing:3px;color:var(--text);
  transition:opacity .2s;
}
.logo:hover{opacity:.7}
.logo span{color:var(--g)}

/* NAV */
.nav{display:flex;align-items:center;gap:2px}
.nav a{
  color:var(--muted);text-decoration:none;
  font-size:.8rem;font-weight:600;letter-spacing:.5px;text-transform:uppercase;
  padding:7px 14px;transition:all .2s;position:relative;
}
.nav a::after{content:'';position:absolute;bottom:0;left:14px;right:14px;height:1px;
  background:var(--g);transform:scaleX(0);transition:transform .2s}
.nav a:hover{color:var(--text)}
.nav a:hover::after,.nav a.active::after{transform:scaleX(1)}
.nav a.active{color:var(--g)}
.nav-divider{width:1px;height:20px;background:var(--border);margin:0 8px}
.rol-badge{
  font-family:'JetBrains Mono',monospace;
  font-size:.68rem;font-weight:600;letter-spacing:1px;text-transform:uppercase;
  padding:4px 10px;border:1px solid var(--border);color:var(--g);margin:0 6px;
}
.nav-user{font-size:.82rem;color:var(--sub);margin-right:4px}
.btn-login{
  background:var(--g)!important;color:#050805!important;font-weight:700!important;
  padding:8px 20px!important;letter-spacing:.5px;
  clip-path:polygon(0 0,calc(100% - 8px) 0,100% 8px,100% 100%,8px 100%,0 calc(100% - 8px));
}
.btn-login:hover{background:var(--g2)!important}
.btn-login::after{display:none!important}
.btn-logout{color:#e05252!important}
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
}
.page-title::before{content:'';display:block;width:4px;height:36px;background:var(--g)}

/* STAT CARDS */
.stat-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:1px;margin-bottom:32px;background:var(--border)}
.stat-card{
  background:var(--card);padding:24px 20px;text-align:center;
  position:relative;overflow:hidden;transition:background .2s;
}
.stat-card:hover{background:#1f1f1f}
.stat-card::before{content:'';position:absolute;top:0;left:0;right:0;height:2px}
.stat-card .val{font-family:'Bebas Neue',sans-serif;font-size:2.8rem;line-height:1;margin-bottom:6px}
.stat-card .lbl{font-family:'JetBrains Mono',monospace;font-size:.68rem;color:var(--muted);letter-spacing:1px;text-transform:uppercase}

/* TABLES */
.tbl-wrap{background:var(--card);border:1px solid var(--border);overflow:hidden;margin-bottom:20px}
table{width:100%;border-collapse:collapse}
th{
  background:#0a0f0b;padding:12px 16px;text-align:left;
  font-family:'JetBrains Mono',monospace;font-size:.72rem;color:var(--g);
  font-weight:600;letter-spacing:1.5px;text-transform:uppercase;white-space:nowrap;
  border-bottom:1px solid var(--border);
}
td{padding:11px 16px;border-bottom:1px solid rgba(26,61,36,.5);font-size:.85rem;transition:background .15s}
tr:last-child td{border-bottom:none}
tr:hover td{background:#161616}

/* PANELS */
.panel{background:var(--card);border:1px solid var(--border);padding:24px;margin-bottom:20px;position:relative;overflow:hidden}
.panel::before{content:'';position:absolute;top:0;left:0;width:3px;height:100%;background:var(--g);opacity:.5}
.panel h2{
  font-family:'Bebas Neue',sans-serif;font-size:1.3rem;letter-spacing:2px;
  color:var(--text);margin-bottom:16px;display:flex;align-items:center;gap:8px;
}
.panel h2::after{content:'';flex:1;height:1px;background:var(--border)}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:20px}

/* FORMS */
input,select,textarea{
  background:#161616;color:var(--text);
  border:1px solid var(--border);
  padding:10px 14px;width:100%;margin-bottom:10px;
  font-size:.9rem;font-family:'DM Sans',sans-serif;transition:.15s;
  outline:none;border-radius:0;
}
input:focus,select:focus{border-color:var(--g);box-shadow:inset 0 0 0 1px var(--g)}
label{font-family:'JetBrains Mono',monospace;font-size:.72rem;color:var(--muted);letter-spacing:1px;text-transform:uppercase;display:block;margin-bottom:5px}

/* BUTTONS */
.btn{
  display:inline-flex;align-items:center;justify-content:center;gap:6px;
  padding:10px 22px;border:none;cursor:pointer;
  font-size:.82rem;font-weight:700;letter-spacing:.5px;text-transform:uppercase;
  text-decoration:none;transition:.15s;font-family:'DM Sans',sans-serif;
  clip-path:polygon(0 0,calc(100% - 8px) 0,100% 8px,100% 100%,8px 100%,0 calc(100% - 8px));
}
.btn-green{background:var(--g);color:#050805}.btn-green:hover{background:var(--g2)}
.btn-red{background:#3d0f0f;color:#e05252;clip-path:none;border:1px solid #5a1515}.btn-red:hover{background:#5a1515}
.btn-muted{background:#161616;color:var(--sub);clip-path:none;border:1px solid var(--border)}.btn-muted:hover{border-color:var(--g);color:var(--g)}

/* SCAN PAGE */
.scan-wrap{max-width:600px;margin:0 auto;padding-top:20px}
.scan-title{font-family:'Bebas Neue',sans-serif;font-size:2.5rem;letter-spacing:2px;margin-bottom:24px;display:flex;align-items:center;gap:12px}
.scan-title::before{content:'';display:block;width:4px;height:32px;background:var(--g)}
.scan-input-row{display:flex;gap:8px;margin-bottom:8px}
.scan-input-row input{margin:0;font-family:'JetBrains Mono',monospace;font-size:1.1rem;letter-spacing:3px;text-align:center}
.scan-result{margin-top:24px;border:1px solid var(--border);overflow:hidden;position:relative}
.scan-result::before{content:'';position:absolute;top:0;left:0;right:0;height:2px;background:linear-gradient(90deg,transparent,var(--g),transparent)}
.scan-header{padding:20px 24px;display:flex;justify-content:space-between;align-items:flex-start}
.scan-body{padding:16px 24px 20px;background:var(--card)}
.scan-urun-adi{font-family:'Bebas Neue',sans-serif;font-size:1.8rem;letter-spacing:1px;line-height:1}
.scan-meta{font-family:'JetBrains Mono',monospace;font-size:.72rem;color:var(--sub);margin-top:6px;letter-spacing:.5px}
.scan-skt{font-family:'JetBrains Mono',monospace;font-size:.85rem;font-weight:600;margin-top:12px;padding:8px 14px;display:inline-block;letter-spacing:.5px}

/* CAMERA */
.kamera-box{border:1px solid var(--g);overflow:hidden;position:relative;margin-bottom:12px;background:#000}
#interactive{width:100%;height:300px;position:relative}
#interactive video{width:100%;height:100%;object-fit:cover}
#interactive canvas{display:none!important}
.drawingBuffer{display:none!important}
.kamera-overlay{
  position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);
  width:260px;height:130px;
  border:2px solid var(--g);
  box-shadow:0 0 0 9999px rgba(0,0,0,.6),0 0 20px var(--g) inset;
  pointer-events:none;
}
.kamera-corner{position:absolute;width:16px;height:16px;border-color:var(--g);border-style:solid}
.kamera-corner.tl{top:-1px;left:-1px;border-width:2px 0 0 2px}
.kamera-corner.tr{top:-1px;right:-1px;border-width:2px 2px 0 0}
.kamera-corner.bl{bottom:-1px;left:-1px;border-width:0 0 2px 2px}
.kamera-corner.br{bottom:-1px;right:-1px;border-width:0 2px 2px 0}

/* ALERTS */
.alert{padding:12px 16px;margin-bottom:16px;font-size:.88rem;font-weight:500;border-left:3px solid;font-family:'DM Sans',sans-serif}
.alert-red{background:#1a0505;color:#e05252;border-color:#e05252}
.alert-green{background:#021008;color:var(--g);border-color:var(--g)}
.alert-yellow{background:#1a1000;color:#f0b429;border-color:#f0b429}

/* COLORS */
.green{color:var(--g)}.red{color:#e05252}.yellow{color:#f0b429}.orange{color:#fb923c}.muted{color:var(--muted)}

/* LOGIN */
.login-wrap{max-width:400px;margin:80px auto}
.login-wrap .panel{padding:40px}
.login-logo{font-family:'Bebas Neue',sans-serif;font-size:2.5rem;letter-spacing:4px;text-align:center;margin-bottom:6px}
.login-sub{font-family:'JetBrains Mono',monospace;font-size:.72rem;color:var(--muted);text-align:center;letter-spacing:2px;text-transform:uppercase;margin-bottom:32px}

/* RESPONSIVE */
@media(max-width:900px){
  .hdr{padding:0 16px}
  .main{padding:20px 16px}
  .grid2{grid-template-columns:1fr}
  .stat-grid{grid-template-columns:repeat(2,1fr)}
  .nav a{padding:6px 8px;font-size:.75rem}
}
</style>
</head>
<body>
<div class="hdr">
  <a href="https://nextstock-tan-t-m.vercel.app/" class="logo-link" target="_blank">
    <div class="logo">Nex<span>Stock</span></div>
  </a>
  <div class="nav">
    <a href="/tarama" class="{{ 'active' if page=='tarama' }}">Tarama</a>
    {% if session.get('rol') not in ['misafir','goruntuleyici'] %}
    <a href="/" class="{{ 'active' if page=='dashboard' }}">Dashboard</a>
    <a href="/urunler" class="{{ 'active' if page=='urunler' }}">Urunler</a>
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
        "toplam_stok":   c.execute("SELECT COALESCE(SUM(stok_adedi),0) FROM urunler").fetchone()[0],
        "tarihi_gecmis": c.execute("SELECT COUNT(*) FROM urunler WHERE stt IS NOT NULL AND stt<?", (today,)).fetchone()[0],
        "yaklasan":      c.execute("SELECT COUNT(*) FROM urunler WHERE stt IS NOT NULL AND stt>=? AND stt<=date(?,'+'||7||' days')", (today,today)).fetchone()[0],
        "kritik":        c.execute("SELECT COUNT(*) FROM urunler WHERE stok_adedi>0 AND stok_adedi<=min_stok").fetchone()[0],
        "stoksuz":       c.execute("SELECT COUNT(*) FROM urunler WHERE stok_adedi<=0").fetchone()[0],
        "bugun":         c.execute("SELECT COUNT(*) FROM stok_hareketleri WHERE DATE(tarih)=DATE('now')").fetchone()[0],
        "tedarikci":     c.execute("SELECT COUNT(*) FROM tedarikciler WHERE aktif=1").fetchone()[0],
    }
    skt_list = [dict(r) for r in c.execute("SELECT * FROM urunler WHERE stt IS NOT NULL AND stt<=date(?,'+'||7||' days') ORDER BY stt", (today,)).fetchall()]
    dusuk    = [dict(r) for r in c.execute("SELECT * FROM urunler WHERE stok_adedi<=min_stok ORDER BY stok_adedi LIMIT 20").fetchall()]
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
        c.close()

        if not urun:
            urun_adi, kategori = openfoodfacts(barkod)
            if urun_adi:
                c = get_db()
                c.execute("INSERT OR REPLACE INTO urunler (barkod,urun_adi,kategori,stok_adedi) VALUES (?,?,?,?)",
                          (barkod, urun_adi, kategori or "Genel", 30))
                c.commit(); c.close()
                alert_html = f'<div class="alert alert-green">✓ Yeni urun eklendi: <strong>{urun_adi}</strong> (Open Food Facts)</div>'
                c = get_db()
                urun = c.execute("SELECT * FROM urunler WHERE barkod=?", (barkod,)).fetchone()
                c.close()
            else:
                alert_html = f'<div id="alert-type" data-tip="error" style="display:none"></div><div class="alert alert-red">✗ Barkod bulunamadi: <strong>{barkod}</strong></div>'

        if urun:
            urun = dict(urun)
            gun  = kalan_gun(urun.get("stt"))
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

            skt_gun_data = f'data-gun="{gun}"' if gun is not None else 'data-gun="null"'
            skt_btn_label = "SKT Guncelle" if urun.get("stt") else "SKT Ekle"
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
      Stok: <strong style="color:#f5f5f5">{urun["stok_adedi"]} adet</strong>
      &nbsp;&nbsp;|&nbsp;&nbsp;Min: {urun.get("min_stok",5)} adet
    </div>
    {uyari}
    <div style="margin-top:14px">
      <button onclick="sktPanelAc()" class="btn btn-muted" style="width:100%">📅 {skt_btn_label}</button>
      <div id="skt-panel" style="display:none;margin-top:10px">
        <form method="POST" action="/skt-guncelle">
          <input type="hidden" name="barkod" value="{barkod}">
          <input type="date" name="stt" value="{urun.get('stt','')}" style="margin-bottom:8px">
          <div style="display:flex;gap:8px">
            <button type="submit" class="btn btn-green" style="flex:1">Kaydet</button>
            <button type="button" onclick="sktPanelKapat()" class="btn btn-muted" style="flex:1">Iptal</button>
          </div>
        </form>
      </div>
    </div>
  </div>
</div>
<script>
function sktPanelAc(){{ document.getElementById('skt-panel').style.display='block'; }}
function sktPanelKapat(){{ document.getElementById('skt-panel').style.display='none'; }}
</script>"""

    # Kamera JS (Quagga - dogruluk icin 3 tutarli okuma)
    kamera_js = """
<script src="https://cdnjs.cloudflare.com/ajax/libs/quagga/0.12.1/quagga.min.js"></script>
<script>
// ── SES SİSTEMİ ──────────────────────────────
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
    // Tarihi gecmis — rahatsiz edici alarm (5 kez yukari-asagi)
    var t = 0;
    for(var i=0; i<5; i++){
      (function(i){
        setTimeout(function(){ beep(1400, 120, 'sawtooth'); }, t);
        t += 130;
        setTimeout(function(){ beep(300, 180, 'sawtooth'); }, t);
        t += 210;
      })(i);
    }
  }
  else if(gun === 0){ beep(1000, 400, 'square'); }
  else if(gun <= 3){ beep(900, 250, 'sine'); }
}
// Sayfa yüklenince SKT sesi çal
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
// ── KAMERA ───────────────────────────────────
var _aktif=false, _sayac={}, _son="", _sonT=0;

function kameraAc(){
  document.getElementById('kam-alan').style.display='block';
  _aktif=true; _sayac={}; _son="";
  Quagga.init({
    inputStream:{
      name:"Live", type:"LiveStream",
      target:document.querySelector('#interactive'),
      constraints:{facingMode:"environment",width:{ideal:1280},height:{ideal:720}}
    },
    locator:{patchSize:"medium",halfSample:true},
    numOfWorkers:2,
    frequency:15,
    decoder:{
      readers:["ean_reader","ean_8_reader","code_128_reader","upc_reader","upc_e_reader"]
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
    // Hata orani filtresi
    var hatalar = res.codeResult.decodedCodes.filter(function(x){return x.error!==undefined;});
    var toplamHata = hatalar.reduce(function(a,b){return a+b.error;},0);
    if(hatalar.length > 0 && toplamHata/hatalar.length > 0.12) return;

    // EAN-13 uzunluk kontrolu
    if(res.codeResult.format==="ean_13" && kod.length!==13) return;

    _sayac[kod] = (_sayac[kod]||0)+1;
    document.getElementById('kam-durum').innerText='Okuma: '+kod+' ('+_sayac[kod]+'/3)';

    if(_sayac[kod]>=3){
      var simdi=Date.now();
      if(kod===_son && simdi-_sonT<2000) return;
      _son=kod; _sonT=simdi; _sayac={};

      document.getElementById('kam-durum').innerText='OKUNDU: '+kod;
      document.getElementById('kam-durum').style.color='#ffffff';
      sesOkundu();
      Quagga.stop(); _aktif=false;

      document.getElementById('barkod-input').value=kod;
      setTimeout(function(){
        document.getElementById('kam-alan').style.display='none';
        document.getElementById('barkod-form').submit();
      },500);
    }
  });
}

function kameraKapat(){
  if(_aktif){try{Quagga.stop();}catch(e){} _aktif=false;}
  document.getElementById('kam-alan').style.display='none';
}
</script>"""

    content = f"""
{kamera_js}
<div class="scan-wrap">
  <div class="page-title">📷 Barkod Tarama</div>
  {alert_html}

  <div id="kam-alan" style="display:none;margin-bottom:12px">
    <div class="kamera-box">
      <div id="interactive"></div>
      <div class="kamera-overlay"></div>
    </div>
    <div id="kam-durum" style="text-align:center;color:#a3a3a3;font-size:.88rem;padding:6px 0">Barkodu cerceve icine getirin...</div>
    <button onclick="kameraKapat()" class="btn btn-red" style="width:100%;margin-top:6px">✕ Kamerayi Kapat</button>
  </div>

  <form method="POST" id="barkod-form">
    <div class="scan-input-row">
      <input name="barkod" id="barkod-input" placeholder="Barkod numarasi..." autofocus autocomplete="off">
      <button type="submit" class="btn btn-green" style="white-space:nowrap;padding:9px 18px">OKUT</button>
    </div>
    <button type="button" onclick="kameraAc()" class="btn btn-muted" style="width:100%">📷 Kamera ile Tara</button>
  </form>

  {sonuc_html}
</div>"""
    return render(content, page="tarama", title="Tarama")

# ═══════════════════════════════════════════════════
#  SKT GUNCELLE
# ═══════════════════════════════════════════════════
@app.route("/skt-guncelle", methods=["POST"])
@giris_gerekli
def skt_guncelle():
    barkod = request.form.get("barkod","").strip()
    stt    = request.form.get("stt","").strip()
    if barkod:
        c = get_db()
        c.execute("UPDATE urunler SET stt=?, son_guncelleme=CURRENT_TIMESTAMP WHERE barkod=?",
                  (stt if stt else None, barkod))
        c.commit(); c.close()
    return redirect(f"/tarama?barkod={barkod}&skt_ok=1")

# ═══════════════════════════════════════════════════
#  URUNLER
# ═══════════════════════════════════════════════════
@app.route("/urunler")
@yetkili_giris
def urunler():
    ara = request.args.get("ara","")
    c = get_db()
    q = "SELECT * FROM urunler WHERE 1=1"
    p = []
    if ara:
        q += " AND (urun_adi LIKE ? OR barkod LIKE ?)"
        p += [f"%{ara}%",f"%{ara}%"]
    q += " ORDER BY urun_adi"
    liste = [dict(r) for r in c.execute(q,p).fetchall()]
    c.close()

    rows = ""
    for u in liste:
        gun = kalan_gun(u.get("stt"))
        rc  = stt_renk(gun)
        et  = stt_etiket(gun) if u.get("stt") else "—"
        rows += f'<tr><td style="font-family:monospace;font-size:.82rem;color:#a3a3a3">{u["barkod"]}</td><td><strong>{u["urun_adi"]}</strong></td><td style="color:#a3a3a3">{u.get("kategori","—")}</td><td>{u.get("stt","—")}</td><td style="color:{rc};font-weight:600;font-size:.82rem">{et}</td><td style="font-weight:700">{u["stok_adedi"]}</td><td style="color:#525252">{u.get("min_stok",5)}</td><td style="color:#ffffff">{float(u.get("fiyat",0)):.2f} TL</td></tr>'

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
  <tr><th>Barkod</th><th>Urun Adi</th><th>Kategori</th><th>SKT</th><th>Durum</th><th>Stok</th><th>Min</th><th>Fiyat</th></tr>
  {rows or '<tr><td colspan=8 class="muted" style="text-align:center;padding:20px">Urun bulunamadi</td></tr>'}
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
        "toplam_stok":   c.execute("SELECT COALESCE(SUM(stok_adedi),0) FROM urunler").fetchone()[0],
        "toplam_deger":  c.execute("SELECT COALESCE(SUM(stok_adedi*fiyat),0) FROM urunler").fetchone()[0],
        "tarihi_gecmis": c.execute("SELECT COUNT(*) FROM urunler WHERE stt IS NOT NULL AND stt<?", (today,)).fetchone()[0],
        "stoksuz":       c.execute("SELECT COUNT(*) FROM urunler WHERE stok_adedi<=0").fetchone()[0],
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
        "toplam_stok":   c.execute("SELECT COALESCE(SUM(stok_adedi),0) FROM urunler").fetchone()[0],
        "tarihi_gecmis": c.execute("SELECT COUNT(*) FROM urunler WHERE stt IS NOT NULL AND stt<?", (today,)).fetchone()[0],
        "stoksuz":       c.execute("SELECT COUNT(*) FROM urunler WHERE stok_adedi<=0").fetchone()[0],
    }
    c.close()
    return jsonify(data)

@app.route("/api/urunler")
@giris_gerekli
def api_urunler():
    c = get_db()
    liste = [dict(r) for r in c.execute("SELECT * FROM urunler ORDER BY urun_adi").fetchall()]
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
