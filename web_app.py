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
    if gun is None: return "#2d5a3d"
    if gun < 0:  return "#e05252"
    if gun <= 3: return "#f0b429"
    if gun <= 7: return "#fb923c"
    return "#22c55e"

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
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#080d0a;color:#d1fae5;font-family:'Segoe UI',system-ui,sans-serif;min-height:100vh}
.hdr{background:#0d1410;border-bottom:2px solid #22c55e;padding:0 24px;display:flex;align-items:center;justify-content:space-between;height:58px;position:sticky;top:0;z-index:100}
.logo{font-size:1.15rem;font-weight:800;letter-spacing:1px;color:#d1fae5}
.logo span{color:#22c55e}
.nav{display:flex;align-items:center;gap:4px;flex-wrap:wrap}
.nav a{color:#6ee7b7;text-decoration:none;font-size:.88rem;padding:6px 13px;border-radius:6px;transition:.15s}
.nav a:hover,.nav a.active{background:#1a3d24;color:#22c55e}
.nav .btn-login{background:#22c55e;color:#080d0a;font-weight:700}
.nav .btn-login:hover{background:#4ade80}
.nav .btn-logout{color:#e05252}
.nav .btn-logout:hover{background:#3d0f0f}
.rol-badge{font-size:.75rem;font-weight:700;padding:3px 10px;border-radius:999px;background:#1a3d24;color:#22c55e;margin-right:4px}
.main{padding:24px;max-width:1320px;margin:0 auto}
.stat-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin-bottom:24px}
.stat-card{background:#0d1410;border:1px solid #1a3d24;border-radius:10px;padding:18px 12px;text-align:center;border-top:3px solid}
.stat-card .val{font-size:2rem;font-weight:800;margin-bottom:4px}
.stat-card .lbl{font-size:.76rem;color:#6ee7b7;font-weight:500}
.tbl-wrap{background:#0d1410;border:1px solid #1a3d24;border-radius:10px;overflow:hidden;margin-bottom:20px}
table{width:100%;border-collapse:collapse}
th{background:#111c15;padding:10px 14px;text-align:left;font-size:.82rem;color:#22c55e;font-weight:600;white-space:nowrap}
td{padding:9px 14px;border-bottom:1px solid #1a3d24;font-size:.84rem}
tr:last-child td{border-bottom:none}
tr:hover td{background:#111c15}
.panel{background:#0d1410;border:1px solid #1a3d24;border-radius:10px;padding:20px;margin-bottom:20px}
.panel h2{font-size:.95rem;color:#22c55e;margin-bottom:14px;font-weight:700;text-transform:uppercase;letter-spacing:.5px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:20px}
input,select,textarea{background:#111c15;color:#d1fae5;border:1px solid #1a3d24;border-radius:7px;padding:9px 13px;width:100%;margin-bottom:10px;font-size:.9rem;font-family:inherit;transition:.15s}
input:focus,select:focus{outline:none;border-color:#22c55e;box-shadow:0 0 0 2px rgba(34,197,94,.15)}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:6px;padding:9px 20px;border-radius:7px;border:none;cursor:pointer;font-size:.88rem;font-weight:700;text-decoration:none;transition:.15s;font-family:inherit}
.btn-green{background:#22c55e;color:#080d0a}.btn-green:hover{background:#4ade80}
.btn-red{background:#3d0f0f;color:#e05252;border:1px solid #5a1515}.btn-red:hover{background:#5a1515}
.btn-muted{background:#111c15;color:#6ee7b7;border:1px solid #1a3d24}.btn-muted:hover{background:#1a3d24}
.scan-wrap{max-width:580px;margin:32px auto}
.scan-input-row{display:flex;gap:8px;margin-bottom:8px}
.scan-input-row input{margin:0;font-size:1.2rem;text-align:center;letter-spacing:2px}
.scan-result{margin-top:20px;border-radius:10px;overflow:hidden;border:1px solid #1a3d24}
.scan-header{padding:16px 20px;display:flex;justify-content:space-between;align-items:center}
.scan-body{padding:14px 20px;background:#0d1410}
.scan-urun-adi{font-size:1.3rem;font-weight:800}
.scan-meta{font-size:.85rem;color:#6ee7b7;margin-top:4px}
.scan-skt{font-size:1rem;font-weight:700;margin-top:10px;padding:8px 12px;border-radius:6px;display:inline-block}
.kamera-box{background:#111c15;border:2px solid #22c55e;border-radius:10px;overflow:hidden;position:relative;margin-bottom:12px}
#interactive{width:100%;height:280px;position:relative}
#interactive video{width:100%;height:100%;object-fit:cover}
#interactive canvas{display:none!important}
.drawingBuffer{display:none!important}
.kamera-overlay{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);width:240px;height:120px;border:3px solid #22c55e;border-radius:6px;box-shadow:0 0 0 9999px rgba(0,0,0,.55);pointer-events:none}
.alert{padding:12px 16px;border-radius:8px;margin-bottom:16px;font-size:.9rem;font-weight:500}
.alert-red{background:#3d0f0f;color:#e05252;border:1px solid #5a1515}
.alert-green{background:#052e16;color:#22c55e;border:1px solid #1a3d24}
.alert-yellow{background:#3d2800;color:#f0b429;border:1px solid #5a3800}
.page-title{font-size:1.2rem;font-weight:800;color:#d1fae5;margin-bottom:20px}
.green{color:#22c55e}.red{color:#e05252}.yellow{color:#f0b429}.muted{color:#2d5a3d}
.login-wrap{max-width:380px;margin:80px auto}
@media(max-width:700px){
  .grid2{grid-template-columns:1fr}
  .stat-grid{grid-template-columns:repeat(2,1fr)}
  .hdr{padding:0 12px}
  .main{padding:12px}
}
</style>
</head>
<body>
<div class="hdr">
  <a href="https://nextstock-tan-t-m.vercel.app/" style="text-decoration:none" target="_blank"><div class="logo">Nex<span>Stock</span></div></a>
  <div class="nav">
    <a href="/tarama" class="{{ 'active' if page=='tarama' }}">📷 Tarama</a>
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
    <span class="rol-badge">{{ session.get('rol','').upper() }}</span>
    <span style="color:#6ee7b7;font-size:.85rem;margin-right:4px">{{ session.get('tam_ad') or session.get('user') }}</span>
    <a href="/cikis" class="nav btn-logout">Cikis</a>
    {% else %}
    <a href="/giris" class="nav btn-login">Giris Yap</a>
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
    <div style="text-align:center;margin-bottom:28px">
      <div style="font-size:2rem;font-weight:800">Scan<span style="color:#22c55e">Core</span></div>
      <div style="color:#6ee7b7;font-size:.9rem;margin-top:6px">Envanter Yonetim Sistemi</div>
    </div>
    {'<div class="alert alert-red">'+hata+'</div>' if hata else ''}
    <form method="POST">
      <label style="color:#6ee7b7;font-size:.82rem;font-weight:600;display:block;margin-bottom:4px">KULLANICI ADI</label>
      <input name="k" placeholder="kullanici_adi" autofocus autocomplete="username">
      <label style="color:#6ee7b7;font-size:.82rem;font-weight:600;display:block;margin-bottom:4px">SIFRE</label>
      <input name="s" type="password" placeholder="••••••••" autocomplete="current-password">
      <button type="submit" class="btn btn-green" style="width:100%;margin-top:4px;padding:12px">GIRIS YAP</button>
    </form>
    <div style="text-align:center;margin-top:16px;color:#2d5a3d;font-size:.8rem">Varsayilan: admin / admin123</div>
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
        ("toplam_urun","Toplam Urun","#22c55e"),
        ("toplam_stok","Toplam Stok","#34d399"),
        ("tarihi_gecmis","Tarihi Gecmis","#e05252"),
        ("yaklasan","Yaklasan SKT","#f0b429"),
        ("kritik","Kritik Stok","#fb923c"),
        ("stoksuz","Stoksuz","#a78bfa"),
        ("bugun","Bugun Islem","#22c55e"),
        ("tedarikci","Tedarikci","#6ee7b7"),
    ]
    kartlar = "".join(f'<div class="stat-card" style="border-color:{col}"><div class="val" style="color:{col}">{s[k]}</div><div class="lbl">{l}</div></div>' for k,l,col in kfg)

    skt_rows = ""
    for u in skt_list:
        gun = kalan_gun(u.get("stt"))
        rc  = stt_renk(gun)
        skt_rows += f'<tr><td><strong>{u["urun_adi"]}</strong></td><td style="color:#6ee7b7">{u.get("stt","—")}</td><td style="color:{rc};font-weight:600">{stt_etiket(gun)}</td><td>{u["stok_adedi"]}</td></tr>'

    dusuk_rows = ""
    for u in dusuk:
        cls = "red" if u["stok_adedi"] <= 0 else "yellow"
        dusuk_rows += f'<tr><td><strong>{u["urun_adi"]}</strong></td><td class="{cls}" style="font-weight:700">{u["stok_adedi"]}</td><td>{u.get("min_stok",5)}</td></tr>'

    har_rows = ""
    for h in son_har:
        cls = "green" if h["hareket_tipi"]=="Giris" else "red" if h["hareket_tipi"] in ["Cikis","Okutma"] else ""
        har_rows += f'<tr><td class="{cls}" style="font-weight:700">{h["hareket_tipi"]}</td><td>{h.get("urun_adi","—")}</td><td>{h["miktar"]}</td><td style="color:#6ee7b7">{str(h["tarih"])[:16]}</td><td>{h.get("kullanici","—")}</td></tr>'

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
                hdr_bg = "background:#052e16"
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
    <div style="font-size:1.7rem;font-weight:800;color:#22c55e">{float(urun.get("fiyat",0)):.2f} TL</div>
  </div>
  <div class="scan-body">
    <span class="scan-skt" style="background:{rc}22;color:{rc};border:1px solid {rc}55">{et}</span>
    <div style="margin-top:10px;color:#6ee7b7;font-size:.9rem">
      Stok: <strong style="color:#d1fae5">{urun["stok_adedi"]} adet</strong>
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
      document.getElementById('kam-durum').style.color='#22c55e';
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
    <div id="kam-durum" style="text-align:center;color:#6ee7b7;font-size:.88rem;padding:6px 0">Barkodu cerceve icine getirin...</div>
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
        rows += f'<tr><td style="font-family:monospace;font-size:.82rem;color:#6ee7b7">{u["barkod"]}</td><td><strong>{u["urun_adi"]}</strong></td><td style="color:#6ee7b7">{u.get("kategori","—")}</td><td>{u.get("stt","—")}</td><td style="color:{rc};font-weight:600;font-size:.82rem">{et}</td><td style="font-weight:700">{u["stok_adedi"]}</td><td style="color:#2d5a3d">{u.get("min_stok",5)}</td><td style="color:#22c55e">{float(u.get("fiyat",0)):.2f} TL</td></tr>'

    content = f"""
<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;flex-wrap:wrap;gap:12px">
  <div class="page-title" style="margin:0">Urunler <span style="color:#2d5a3d;font-size:.9rem">({len(liste)})</span></div>
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
        rows += f'<tr><td style="color:#2d5a3d">{h["hareket_id"]}</td><td class="{cls}" style="font-weight:700">{h["hareket_tipi"]}</td><td>{h.get("urun_adi","—")}</td><td style="font-family:monospace;font-size:.82rem;color:#6ee7b7">{h.get("barkod","—")}</td><td style="font-weight:700">{h["miktar"]}</td><td style="color:#6ee7b7">{str(h["tarih"])[:16]}</td><td>{h.get("kullanici","—")}</td></tr>'

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
      <tr><td><a href="/api/stats" style="color:#22c55e">/api/stats</a></td><td class="muted">Dashboard istatistikleri</td></tr>
      <tr><td><a href="/api/urunler" style="color:#22c55e">/api/urunler</a></td><td class="muted">Tum urunler JSON</td></tr>
      <tr><td><a href="/api/hareketler" style="color:#22c55e">/api/hareketler</a></td><td class="muted">Son hareketler JSON</td></tr>
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
    RC = {"admin":"#22c55e","mudur":"#34d399","kasiyer":"#86efac","goruntuleyici":"#6ee7b7"}
    rows = ""
    for u in liste:
        rc  = RC.get(u["rol"],"#6ee7b7")
        akt = '<span class="green">✓ Aktif</span>' if u["aktif"] else '<span class="red">✗ Pasif</span>'
        rows += f'<tr><td style="font-weight:600">{u["kullanici_adi"]}</td><td>{u.get("tam_ad","—")}</td><td style="color:{rc};font-weight:700">{u["rol"].upper()}</td><td>{akt}</td><td style="color:#6ee7b7">{str(u.get("son_giris","—"))[:16]}</td></tr>'

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
