# -*- coding: utf-8 -*-
import os
import hashlib
import sqlite3
import threading
from datetime import datetime, date
from flask import Flask, render_template_string, request, redirect, session, jsonify
import functools

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "barkodpro_secret_2024")

DB_NAME = os.environ.get("DB_PATH", "envanter_pro.db")

# ═══════════════════════════════════════════
#  VERİTABANI
# ═══════════════════════════════════════════
def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def init_db():
    conn = get_db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS kullanicilar (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        kullanici_adi TEXT UNIQUE NOT NULL,
        sifre_hash    TEXT NOT NULL,
        tam_ad        TEXT,
        rol           TEXT NOT NULL DEFAULT 'kasiyer',
        aktif         INTEGER DEFAULT 1,
        son_giris     DATETIME
    );
    CREATE TABLE IF NOT EXISTS urunler (
        barkod         TEXT PRIMARY KEY,
        urun_adi       TEXT NOT NULL,
        kategori       TEXT DEFAULT 'Genel',
        stt            DATE,
        stok_adedi     INTEGER DEFAULT 0,
        min_stok       INTEGER DEFAULT 5,
        fiyat          REAL DEFAULT 0.0,
        aciklama       TEXT,
        eklenme_tarihi DATETIME DEFAULT CURRENT_TIMESTAMP,
        son_guncelleme DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS stok_hareketleri (
        hareket_id   INTEGER PRIMARY KEY AUTOINCREMENT,
        barkod       TEXT,
        urun_adi     TEXT,
        hareket_tipi TEXT NOT NULL,
        miktar       INTEGER NOT NULL,
        onceki_stok  INTEGER,
        sonraki_stok INTEGER,
        tarih        DATETIME DEFAULT CURRENT_TIMESTAMP,
        kullanici    TEXT DEFAULT 'sistem',
        aciklama     TEXT
    );
    CREATE TABLE IF NOT EXISTS tedarikciler (
        id      INTEGER PRIMARY KEY AUTOINCREMENT,
        ad      TEXT NOT NULL,
        telefon TEXT,
        email   TEXT,
        adres   TEXT,
        not_    TEXT,
        aktif   INTEGER DEFAULT 1
    );
    """)
    conn.commit()
    # Admin hesabı
    h = hashlib.sha256("admin123".encode()).hexdigest()
    try:
        conn.execute(
            "INSERT INTO kullanicilar (kullanici_adi,sifre_hash,tam_ad,rol) VALUES (?,?,?,?)",
            ("admin", h, "Sistem Yoneticisi", "admin"))
        conn.commit()
    except: pass
    conn.close()

init_db()

def sifre_hash(s):
    return hashlib.sha256(s.encode()).hexdigest()

def kalan_gun(stt):
    if not stt: return None
    try: return (datetime.strptime(str(stt)[:10], "%Y-%m-%d").date() - date.today()).days
    except: return None

def stt_etiket(gun):
    if gun is None: return "—"
    if gun < 0: return f"{abs(gun)}g once sona erdi"
    if gun == 0: return "BUGUN bitiyor!"
    return f"{gun} gun kaldi"

# ═══════════════════════════════════════════
#  AUTH
# ═══════════════════════════════════════════
def giris_gerekli(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user"):
            # Misafir olarak otomatik gir
            session["user"]   = "misafir"
            session["rol"]    = "goruntuleyici"
            session["tam_ad"] = "Misafir"
        return f(*args, **kwargs)
    return decorated

def admin_gerekli(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user") or session.get("rol") == "goruntuleyici":
            return redirect("/giris")
        return f(*args, **kwargs)
    return decorated

def yetkisi_var(yetki):
    rol = session.get("rol","")
    yetkiler = {
        "admin":         {"tara","urun_ekle","urun_duzenle","urun_sil","stok","rapor","kullanici","tedarikci"},
        "mudur":         {"tara","urun_ekle","urun_duzenle","stok","rapor","tedarikci"},
        "kasiyer":       {"tara","stok"},
        "goruntuleyici": {"rapor"},
    }
    return yetki in yetkiler.get(rol, set())

# ═══════════════════════════════════════════
#  HTML ŞABLONU
# ═══════════════════════════════════════════
BASE = """<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ScanCore — {{ title }}</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:#080d0a;color:#d1fae5;font-family:'Segoe UI',sans-serif;min-height:100vh}
  .header{background:#0d1410;border-bottom:1px solid #1a3d24;padding:0 24px;display:flex;align-items:center;justify-content:space-between;height:56px}
  .logo{font-size:1.1rem;font-weight:700;color:#d1fae5}
  .logo span{color:#22c55e}
  .nav a{color:#6ee7b7;text-decoration:none;margin-left:20px;font-size:.9rem;padding:6px 12px;border-radius:6px;transition:.2s}
  .nav a:hover,.nav a.active{background:#1a3d24;color:#22c55e}
  .nav .logout{color:#e05252}
  .nav .logout:hover{background:#3d0f0f}
  .main{padding:24px;max-width:1300px;margin:0 auto}
  .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:24px}
  .card{background:#0d1410;border:1px solid #1a3d24;border-radius:10px;padding:18px 14px;text-align:center}
  .card .val{font-size:2rem;font-weight:700;margin-bottom:4px}
  .card .lbl{font-size:.78rem;color:#6ee7b7}
  .card-top{border-top:3px solid}
  table{width:100%;border-collapse:collapse;background:#0d1410;border-radius:10px;overflow:hidden}
  th{background:#111c15;padding:10px 14px;text-align:left;font-size:.82rem;color:#22c55e;font-weight:600}
  td{padding:10px 14px;border-bottom:1px solid #1a3d24;font-size:.85rem}
  tr:hover td{background:#111c15}
  tr:last-child td{border-bottom:none}
  .badge{padding:3px 10px;border-radius:999px;font-size:.75rem;font-weight:600}
  .green{color:#22c55e} .red{color:#e05252} .yellow{color:#f0b429} .muted{color:#2d5a3d}
  .panel{background:#0d1410;border:1px solid #1a3d24;border-radius:10px;padding:20px;margin-bottom:20px}
  .panel h2{font-size:1rem;color:#22c55e;margin-bottom:16px;font-weight:600}
  .grid2{display:grid;grid-template-columns:1fr 1fr;gap:20px}
  input,select,textarea{background:#111c15;color:#d1fae5;border:1px solid #1a3d24;border-radius:6px;padding:8px 12px;width:100%;margin-bottom:10px;font-size:.9rem;font-family:inherit}
  input:focus,select:focus{outline:none;border-color:#22c55e}
  .btn{display:inline-block;padding:9px 20px;border-radius:7px;border:none;cursor:pointer;font-size:.88rem;font-weight:600;text-decoration:none;transition:.2s}
  .btn-green{background:#22c55e;color:#080d0a}
  .btn-green:hover{background:#4ade80}
  .btn-red{background:#3d0f0f;color:#e05252}
  .btn-red:hover{background:#5a1515}
  .btn-muted{background:#111c15;color:#6ee7b7;border:1px solid #1a3d24}
  .scan-box{max-width:500px;margin:40px auto;text-align:center}
  .scan-box input{font-size:1.4rem;text-align:center;padding:14px;letter-spacing:2px}
  .scan-result{margin-top:20px;padding:20px;border-radius:10px;border:1px solid #1a3d24;background:#111c15}
  .page-title{font-size:1.2rem;font-weight:700;color:#d1fae5;margin-bottom:20px}
  .tag{display:inline-block;padding:2px 8px;border-radius:4px;font-size:.75rem;margin:1px}
  @media(max-width:700px){.grid2{grid-template-columns:1fr}.cards{grid-template-columns:repeat(2,1fr)}}
</style>
</head>
<body>
<div class="header">
  <div class="logo">Scan<span>Core</span></div>
  <div class="nav">
    <a href="/tarama" class="{{ 'active' if page=='tarama' }}">Tarama</a>
    {% if session.get('rol') not in ['goruntuleyici', None] %}
    <a href="/" class="{{ 'active' if page=='dashboard' }}">Dashboard</a>
    <a href="/urunler" class="{{ 'active' if page=='urunler' }}">Urunler</a>
    <a href="/hareketler" class="{{ 'active' if page=='hareketler' }}">Hareketler</a>
    {% endif %}
    {% if session.get('rol') in ['admin','mudur'] %}
    <a href="/raporlar" class="{{ 'active' if page=='raporlar' }}">Raporlar</a>
    {% endif %}
    {% if session.get('rol') == 'admin' %}
    <a href="/kullanicilar" class="{{ 'active' if page=='kullanicilar' }}">Kullanicilar</a>
    {% endif %}
    {% if session.get('user') and session.get('user') != 'misafir' %}
    <span style="color:#2d5a3d;margin-left:12px;font-size:.85rem">{{ session.get('tam_ad') or session.get('user') }}</span>
    <a href="/cikis" class="nav logout">Cikis</a>
    {% else %}
    <a href="/giris" class="nav" style="background:#22c55e;color:#080d0a;font-weight:700">Giris Yap</a>
    {% endif %}
  </div>
</div>
<div class="main">{% block content %}{% endblock %}</div>
</body></html>"""

def render(template, **kwargs):
    full = BASE.replace("{% block content %}{% endblock %}", template)
    return render_template_string(full, session=session, **kwargs)

# ═══════════════════════════════════════════
#  ROUTES
# ═══════════════════════════════════════════
@app.route("/giris", methods=["GET","POST"])
def giris():
    hata = ""
    if request.method == "POST":
        k = request.form.get("kullanici","")
        s = request.form.get("sifre","")
        conn = get_db()
        row = conn.execute(
            "SELECT * FROM kullanicilar WHERE kullanici_adi=? AND sifre_hash=? AND aktif=1",
            (k, sifre_hash(s))).fetchone()
        conn.close()
        if row:
            session["user"]   = row["kullanici_adi"]
            session["rol"]    = row["rol"]
            session["tam_ad"] = row["tam_ad"] or ""
            return redirect("/")
        hata = "Hatali kullanici adi veya sifre!"

    tmpl = """
<div style="max-width:360px;margin:80px auto">
  <div class="panel">
    <div style="text-align:center;margin-bottom:24px">
      <div style="font-size:1.8rem;font-weight:700;color:#d1fae5">Scan<span style="color:#22c55e">Core</span></div>
      <div style="color:#6ee7b7;font-size:.9rem;margin-top:4px">Envanter Yonetim Sistemi</div>
    </div>
    {% if hata %}<div style="background:#3d0f0f;color:#e05252;padding:10px;border-radius:6px;margin-bottom:12px;font-size:.88rem">{{ hata }}</div>{% endif %}
    <form method="POST">
      <label style="color:#6ee7b7;font-size:.85rem">Kullanici Adi</label>
      <input name="kullanici" placeholder="kullanici_adi" autofocus>
      <label style="color:#6ee7b7;font-size:.85rem">Sifre</label>
      <input name="sifre" type="password" placeholder="••••••••">
      <button type="submit" class="btn btn-green" style="width:100%;margin-top:4px">GIRIS YAP</button>
    </form>
    <div style="text-align:center;margin-top:14px;color:#2d5a3d;font-size:.8rem">admin / admin123</div>
  </div>
</div>"""
    return render_template_string(BASE.replace("{% block content %}{% endblock %}", tmpl),
                                  session=session, page="giris", title="Giris", hata=hata)

@app.route("/cikis")
def cikis():
    session.clear()
    return redirect("/giris")

@app.route("/")
@giris_gerekli
def dashboard():
    if session.get("rol") == "goruntuleyici":
        return redirect("/tarama")
    conn = get_db()
    today = date.today().isoformat()
    stats = {
        "toplam_urun":    conn.execute("SELECT COUNT(*) FROM urunler").fetchone()[0],
        "toplam_stok":    conn.execute("SELECT COALESCE(SUM(stok_adedi),0) FROM urunler").fetchone()[0],
        "tarihi_gecmis":  conn.execute("SELECT COUNT(*) FROM urunler WHERE stt IS NOT NULL AND stt<?", (today,)).fetchone()[0],
        "yaklasan_stt":   conn.execute("SELECT COUNT(*) FROM urunler WHERE stt IS NOT NULL AND stt>=? AND stt<=date(?,'+'||7||' days')", (today,today)).fetchone()[0],
        "kritik_stok":    conn.execute("SELECT COUNT(*) FROM urunler WHERE stok_adedi>0 AND stok_adedi<=min_stok").fetchone()[0],
        "stoksuz":        conn.execute("SELECT COUNT(*) FROM urunler WHERE stok_adedi<=0").fetchone()[0],
        "bugun_hareket":  conn.execute("SELECT COUNT(*) FROM stok_hareketleri WHERE DATE(tarih)=DATE('now')").fetchone()[0],
    }
    skt_list  = [dict(r) for r in conn.execute("SELECT * FROM urunler WHERE stt IS NOT NULL AND stt<=date(?,'+'||7||' days') ORDER BY stt", (today,)).fetchall()]
    dusuk     = [dict(r) for r in conn.execute("SELECT * FROM urunler WHERE stok_adedi<=min_stok ORDER BY stok_adedi").fetchall()]
    conn.close()

    kart_cfg = [
        ("toplam_urun","Toplam Urun","#22c55e"),
        ("toplam_stok","Toplam Stok","#34d399"),
        ("tarihi_gecmis","Tarihi Gecmis","#e05252"),
        ("yaklasan_stt","Yaklasan SKT","#f0b429"),
        ("kritik_stok","Kritik Stok","#fb923c"),
        ("stoksuz","Stoksuz","#a78bfa"),
        ("bugun_hareket","Bugun Islem","#22c55e"),
    ]
    kartlar = "".join(f'<div class="card card-top" style="border-color:{c}"><div class="val" style="color:{c}">{stats[k]}</div><div class="lbl">{l}</div></div>' for k,l,c in kart_cfg)

    skt_satirlar = ""
    for u in skt_list:
        gun = kalan_gun(u.get("stt"))
        cls = "red" if (gun is not None and gun < 0) else "yellow"
        skt_satirlar += f"<tr><td>{u['urun_adi']}</td><td>{u.get('stt','—')}</td><td class='{cls}'>{stt_etiket(gun)}</td><td>{u['stok_adedi']}</td></tr>"

    dusuk_satirlar = ""
    for u in dusuk:
        cls = "red" if u["stok_adedi"] <= 0 else "yellow"
        dusuk_satirlar += f"<tr><td>{u['urun_adi']}</td><td class='{cls}'>{u['stok_adedi']}</td><td>{u.get('min_stok',5)}</td></tr>"

    tmpl = f"""
<div class="page-title">Dashboard</div>
<div class="cards">{kartlar}</div>
<div class="grid2">
  <div class="panel">
    <h2>SKT Uyarilari (7 gun)</h2>
    <table><tr><th>Urun</th><th>SKT</th><th>Kalan</th><th>Stok</th></tr>{skt_satirlar or '<tr><td colspan=4 class="muted">Uyari yok</td></tr>'}</table>
  </div>
  <div class="panel">
    <h2>Kritik Stok</h2>
    <table><tr><th>Urun</th><th>Stok</th><th>Min</th></tr>{dusuk_satirlar or '<tr><td colspan=3 class="muted">Uyari yok</td></tr>'}</table>
  </div>
</div>"""
    return render(tmpl, page="dashboard", title="Dashboard")

@app.route("/tarama", methods=["GET","POST"])
@giris_gerekli
def tarama():
    sonuc = None
    hata  = None
    if request.method == "POST":
        barkod = request.form.get("barkod","").strip()
        if barkod:
            conn = get_db()
            urun = conn.execute("SELECT * FROM urunler WHERE barkod=?", (barkod,)).fetchone()
            if urun:
                urun = dict(urun)
                gun  = kalan_gun(urun.get("stt"))
                conn.execute(
                    "INSERT INTO stok_hareketleri (barkod,urun_adi,hareket_tipi,miktar,kullanici,aciklama) VALUES (?,?,?,?,?,?)",
                    (barkod, urun["urun_adi"], "Okutma", 1, session["user"], "Web tarama"))
                conn.execute("UPDATE urunler SET stok_adedi=stok_adedi-1 WHERE barkod=?", (barkod,))
                conn.commit()
                sonuc = {"urun": urun, "gun": gun}
            else:
                hata = f"Barkod bulunamadi: {barkod}"
            conn.close()

    if sonuc:
        gun = sonuc["gun"]
        u   = sonuc["urun"]
        if gun is None:     skt_cls, skt_txt = "muted", "SKT belirtilmemis"
        elif gun < 0:       skt_cls, skt_txt = "red",   f"TARIHI GECMIS ({abs(gun)} gun once!)"
        elif gun == 0:      skt_cls, skt_txt = "red",   "BUGUN bitiyor!"
        elif gun <= 3:      skt_cls, skt_txt = "yellow", f"{gun} gun kaldi"
        else:               skt_cls, skt_txt = "green",  f"Taze — {gun} gun kaldi"
        sonuc_html = f"""
<div class="scan-result">
  <div style="font-size:1.3rem;font-weight:700;margin-bottom:8px">{u['urun_adi']}</div>
  <div style="color:#6ee7b7;font-size:.88rem;margin-bottom:6px">Barkod: {u['barkod']} | Kategori: {u.get('kategori','—')}</div>
  <div class="{skt_cls}" style="font-size:1rem;font-weight:600;margin-bottom:6px">{skt_txt}</div>
  <div style="color:#6ee7b7">Stok: {u['stok_adedi']} adet | Fiyat: {float(u.get('fiyat',0)):.2f} TL</div>
</div>"""
    elif hata:
        sonuc_html = f'<div class="scan-result"><div class="red">{hata}</div></div>'
    else:
        sonuc_html = ""

    tmpl = f"""
<script src="https://cdnjs.cloudflare.com/ajax/libs/quagga/0.12.1/quagga.min.js"></script>
<div class="scan-box" style="max-width:600px">
  <div class="page-title">Barkod Tarama</div>

  <!-- Kamera -->
  <div id="kamera-alan" style="display:none;margin-bottom:16px">
    <div id="interactive" style="width:100%;height:280px;background:#111c15;border-radius:10px;overflow:hidden;position:relative;border:2px solid #22c55e"></div>
    <div id="kamera-sonuc" style="text-align:center;color:#22c55e;font-weight:700;margin-top:8px;font-size:1.1rem"></div>
    <button onclick="kameraKapat()" class="btn btn-red" style="width:100%;margin-top:8px">Kamerayi Kapat</button>
  </div>

  <!-- Manuel giriş -->
  <form method="POST" id="barkod-form">
    <input name="barkod" id="barkod-input" placeholder="Barkod numarasini girin..." autofocus value="">
    <div style="display:flex;gap:8px;margin-top:8px">
      <button type="submit" class="btn btn-green" style="flex:1">OKUT</button>
      <button type="button" onclick="kameraAc()" class="btn btn-muted" style="flex:1">📷 Kamera</button>
    </div>
  </form>

  {sonuc_html}
</div>

<style>
#interactive video {{ width:100%;height:100%;object-fit:cover }}
#interactive canvas {{ display:none }}
.drawingBuffer {{ display:none }}
</style>

<script>
var kameraAktif = false;

function kameraAc() {{
  document.getElementById('kamera-alan').style.display = 'block';
  kameraAktif = true;
  Quagga.init({{
    inputStream: {{
      name: "Live",
      type: "LiveStream",
      target: document.querySelector('#interactive'),
      constraints: {{ facingMode: "environment" }}
    }},
    decoder: {{
      readers: ["ean_reader","ean_8_reader","code_128_reader","code_39_reader","upc_reader"]
    }},
    locate: true
  }}, function(err) {{
    if (err) {{
      document.getElementById('kamera-sonuc').textContent = 'Kamera acilamadi: ' + err;
      document.getElementById('kamera-sonuc').style.color = '#e05252';
      return;
    }}
    Quagga.start();
  }});

  var sonOkunan = "";
  var sonZaman  = 0;
  Quagga.onDetected(function(result) {{
    var kod = result.codeResult.code;
    var simdi = Date.now();
    // Ayni barkodu 2 saniye icinde tekrar okuma
    if (kod === sonOkunan && simdi - sonZaman < 2000) return;
    sonOkunan = kod;
    sonZaman  = simdi;

    document.getElementById('kamera-sonuc').textContent = 'OKUNDU: ' + kod;
    Quagga.stop();
    kameraAktif = false;
    document.getElementById('kamera-alan').style.display = 'none';

    // Forma yaz ve gonder
    document.getElementById('barkod-input').value = kod;
    setTimeout(function() {{
      document.getElementById('barkod-form').submit();
    }}, 300);
  }});
}}

function kameraKapat() {{
  if (kameraAktif) {{ Quagga.stop(); kameraAktif = false; }}
  document.getElementById('kamera-alan').style.display = 'none';
}}
</script>"""
    return render(tmpl, page="tarama", title="Tarama")

@app.route("/urunler")
@giris_gerekli
def urunler():
    if session.get("rol") == "goruntuleyici":
        return redirect("/tarama")
    ara = request.args.get("ara","")
    conn = get_db()
    q = "SELECT * FROM urunler WHERE 1=1"
    p = []
    if ara:
        q += " AND (urun_adi LIKE ? OR barkod LIKE ?)"
        p += [f"%{ara}%", f"%{ara}%"]
    q += " ORDER BY urun_adi"
    liste = [dict(r) for r in conn.execute(q,p).fetchall()]
    conn.close()

    satirlar = ""
    for u in liste:
        gun = kalan_gun(u.get("stt"))
        cls = "red" if (gun is not None and gun < 0) else "yellow" if (gun is not None and gun <= 7) else ""
        satirlar += f"<tr><td>{u['barkod']}</td><td>{u['urun_adi']}</td><td>{u.get('kategori','—')}</td><td class='{cls}'>{u.get('stt','—')}</td><td class='{cls}'>{stt_etiket(gun) if u.get('stt') else '—'}</td><td>{u['stok_adedi']}</td><td>{float(u.get('fiyat',0)):.2f} TL</td></tr>"

    tmpl = f"""
<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px">
  <div class="page-title" style="margin:0">Urunler ({len(liste)})</div>
  <form method="get" style="display:flex;gap:8px">
    <input name="ara" value="{ara}" placeholder="Ara..." style="width:200px;margin:0">
    <button type="submit" class="btn btn-muted">Ara</button>
  </form>
</div>
<div class="panel" style="padding:0;overflow:hidden">
<table><tr><th>Barkod</th><th>Urun Adi</th><th>Kategori</th><th>SKT</th><th>Kalan</th><th>Stok</th><th>Fiyat</th></tr>
{satirlar or '<tr><td colspan=7 class="muted" style="text-align:center;padding:20px">Urun bulunamadi</td></tr>'}
</table></div>"""
    return render(tmpl, page="urunler", title="Urunler")

@app.route("/hareketler")
@giris_gerekli
def hareketler():
    if session.get("rol") == "goruntuleyici":
        return redirect("/tarama")
    conn = get_db()
    liste = [dict(r) for r in conn.execute(
        "SELECT * FROM stok_hareketleri ORDER BY tarih DESC LIMIT 200").fetchall()]
    conn.close()

    satirlar = ""
    for h in liste:
        cls = "green" if h["hareket_tipi"]=="Giris" else "red" if h["hareket_tipi"] in ["Cikis","Okutma"] else ""
        satirlar += f"<tr><td>{h['hareket_id']}</td><td class='{cls}'>{h['hareket_tipi']}</td><td>{h.get('urun_adi','—')}</td><td>{h['miktar']}</td><td>{str(h['tarih'])[:16]}</td><td>{h.get('kullanici','—')}</td></tr>"

    tmpl = f"""
<div class="page-title">Son Hareketler</div>
<div class="panel" style="padding:0;overflow:hidden">
<table><tr><th>#</th><th>Tip</th><th>Urun</th><th>Miktar</th><th>Tarih</th><th>Kullanici</th></tr>
{satirlar or '<tr><td colspan=6 class="muted" style="text-align:center;padding:20px">Hareket yok</td></tr>'}
</table></div>"""
    return render(tmpl, page="hareketler", title="Hareketler")

@app.route("/raporlar")
@giris_gerekli
def raporlar():
    conn = get_db()
    today = date.today().isoformat()
    stats = {
        "toplam_urun":   conn.execute("SELECT COUNT(*) FROM urunler").fetchone()[0],
        "toplam_stok":   conn.execute("SELECT COALESCE(SUM(stok_adedi),0) FROM urunler").fetchone()[0],
        "tarihi_gecmis": conn.execute("SELECT COUNT(*) FROM urunler WHERE stt IS NOT NULL AND stt<?", (today,)).fetchone()[0],
        "stoksuz":       conn.execute("SELECT COUNT(*) FROM urunler WHERE stok_adedi<=0").fetchone()[0],
        "toplam_islem":  conn.execute("SELECT COUNT(*) FROM stok_hareketleri").fetchone()[0],
    }
    conn.close()

    tmpl = f"""
<div class="page-title">Raporlar</div>
<div class="grid2">
  <div class="panel">
    <h2>Genel Istatistikler</h2>
    <table>
      <tr><td>Toplam Urun Cesidi</td><td class="green" style="text-align:right;font-weight:700">{stats['toplam_urun']}</td></tr>
      <tr><td>Toplam Stok Adedi</td><td class="green" style="text-align:right;font-weight:700">{stats['toplam_stok']}</td></tr>
      <tr><td>Tarihi Gecmis Urunler</td><td class="red" style="text-align:right;font-weight:700">{stats['tarihi_gecmis']}</td></tr>
      <tr><td>Stoksuz Urunler</td><td class="yellow" style="text-align:right;font-weight:700">{stats['stoksuz']}</td></tr>
      <tr><td>Toplam Islem Sayisi</td><td class="green" style="text-align:right;font-weight:700">{stats['toplam_islem']}</td></tr>
    </table>
  </div>
  <div class="panel">
    <h2>API Endpointleri</h2>
    <table>
      <tr><td><a href="/api/stats" style="color:#22c55e">/api/stats</a></td><td class="muted">Dashboard istatistikleri</td></tr>
      <tr><td><a href="/api/urunler" style="color:#22c55e">/api/urunler</a></td><td class="muted">Tum urunler JSON</td></tr>
      <tr><td><a href="/api/hareketler" style="color:#22c55e">/api/hareketler</a></td><td class="muted">Son hareketler JSON</td></tr>
    </table>
  </div>
</div>"""
    return render(tmpl, page="raporlar", title="Raporlar")

@app.route("/kullanicilar")
@giris_gerekli
def kullanicilar():
    if session.get("rol") != "admin":
        return redirect("/")
    conn = get_db()
    liste = [dict(r) for r in conn.execute(
        "SELECT id,kullanici_adi,tam_ad,rol,aktif,son_giris FROM kullanicilar ORDER BY tam_ad").fetchall()]
    conn.close()
    ROL_RENK = {"admin":"#22c55e","mudur":"#34d399","kasiyer":"#86efac","goruntuleyici":"#2d5a3d"}
    satirlar = ""
    for u in liste:
        rc = ROL_RENK.get(u["rol"],"#2d5a3d")
        aktif = '<span class="green">Aktif</span>' if u["aktif"] else '<span class="red">Pasif</span>'
        satirlar += f"<tr><td>{u['kullanici_adi']}</td><td>{u.get('tam_ad','—')}</td><td><span style='color:{rc};font-weight:600'>{u['rol'].upper()}</span></td><td>{aktif}</td><td>{str(u.get('son_giris','—'))[:16]}</td></tr>"

    tmpl = f"""
<div class="page-title">Kullanicilar</div>
<div class="panel" style="padding:0;overflow:hidden">
<table><tr><th>Kullanici Adi</th><th>Tam Ad</th><th>Rol</th><th>Durum</th><th>Son Giris</th></tr>
{satirlar}
</table></div>"""
    return render(tmpl, page="kullanicilar", title="Kullanicilar")

# ═══════════════════════════════════════════
#  API
# ═══════════════════════════════════════════
@app.route("/api/stats")
@giris_gerekli
def api_stats():
    conn = get_db()
    today = date.today().isoformat()
    stats = {
        "toplam_urun":   conn.execute("SELECT COUNT(*) FROM urunler").fetchone()[0],
        "toplam_stok":   conn.execute("SELECT COALESCE(SUM(stok_adedi),0) FROM urunler").fetchone()[0],
        "tarihi_gecmis": conn.execute("SELECT COUNT(*) FROM urunler WHERE stt IS NOT NULL AND stt<?", (today,)).fetchone()[0],
        "stoksuz":       conn.execute("SELECT COUNT(*) FROM urunler WHERE stok_adedi<=0").fetchone()[0],
    }
    conn.close()
    return jsonify(stats)

@app.route("/api/urunler")
@giris_gerekli
def api_urunler():
    conn = get_db()
    liste = [dict(r) for r in conn.execute("SELECT * FROM urunler ORDER BY urun_adi").fetchall()]
    conn.close()
    return jsonify(liste)

@app.route("/api/hareketler")
@giris_gerekli
def api_hareketler():
    conn = get_db()
    liste = [dict(r) for r in conn.execute(
        "SELECT * FROM stok_hareketleri ORDER BY tarih DESC LIMIT 100").fetchall()]
    conn.close()
    return jsonify(liste)

if __name__ == "__main__":
    app.run(debug=False)
