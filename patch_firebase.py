"""
NexStock — Firebase Google Auth patch
Reponda çalıştır: python patch_firebase.py
"""
import re, sys, os

TARGET = "web_app.py"
if not os.path.exists(TARGET):
    print("HATA: web_app.py bulunamadı. Reponun içinde çalıştır.")
    sys.exit(1)

with open(TARGET, "r", encoding="utf-8") as f:
    src = f.read()

ok = []

# ─────────────────────────────────────────────
# 1. Firebase admin import + init (imports bölümünün sonuna)
# ─────────────────────────────────────────────
FB_INIT = '''
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
                "private_key":  os.environ["FIREBASE_PRIVATE_KEY"].replace("\\\\n","\\n"),
                "client_email": os.environ["FIREBASE_CLIENT_EMAIL"],
                "token_uri":    "https://oauth2.googleapis.com/token",
            }))
        except ValueError:
            pass  # already initialized
    return _fb_app
# ────────────────────────────────────────────
'''

ANCHOR_IMPORT = "app = Flask(__name__)"
if FB_INIT.strip() not in src:
    src = src.replace(ANCHOR_IMPORT, FB_INIT + "\n" + ANCHOR_IMPORT, 1)
    ok.append("✓ Firebase admin init eklendi")
else:
    ok.append("- Firebase admin init zaten var")

# ─────────────────────────────────────────────
# 2. kullanicilar tablosuna firebase_uid + email kolonu
# ─────────────────────────────────────────────
OLD_TABLE = "kullanici_adi TEXT UNIQUE NOT NULL,"
NEW_TABLE = """kullanici_adi TEXT UNIQUE NOT NULL,
            firebase_uid  TEXT UNIQUE,
            email         TEXT,"""
if "firebase_uid" not in src:
    src = src.replace(OLD_TABLE, NEW_TABLE, 1)
    ok.append("✓ firebase_uid + email kolonları eklendi")
else:
    ok.append("- firebase_uid kolonu zaten var")

# ─────────────────────────────────────────────
# 3. init_db migration: ALTER TABLE ekle
# ─────────────────────────────────────────────
ALTER_BLOCK = """        # Firebase kolonları migration
        for _col, _typ in [("firebase_uid","TEXT"), ("email","TEXT")]:
            try:
                c.execute(f"ALTER TABLE kullanicilar ADD COLUMN IF NOT EXISTS {_col} {_typ}")
                c.commit()
            except Exception:
                pass
"""
ANCHOR_ALTER = '("allerjenler","TEXT"), ("katki_maddeleri","TEXT")'
if "Firebase kolonları migration" not in src:
    src = src.replace(ANCHOR_ALTER, ANCHOR_ALTER + "\n" + ALTER_BLOCK, 1)
    ok.append("✓ Migration ALTER TABLE eklendi")
else:
    ok.append("- Migration zaten var")

# ─────────────────────────────────────────────
# 4. /api/firebase-login endpoint
# ─────────────────────────────────────────────
FB_ROUTE = '''
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

'''

if "/api/firebase-login" not in src:
    src = src.replace('@app.route("/cikis")', FB_ROUTE + '@app.route("/cikis")', 1)
    ok.append("✓ /api/firebase-login route eklendi")
else:
    ok.append("- /api/firebase-login zaten var")

# ─────────────────────────────────────────────
# 5. /giris sayfasına Google Sign-In butonu
# ─────────────────────────────────────────────
GOOGLE_BTN = """
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
    </script>"""

# Inject before closing </div> of the login panel
ANCHOR_GIRIS = '<a href="/kayit" style="color:var(--g);font-size:.82rem;text-decoration:none;font-family:JetBrains Mono,monospace">Hesap Oluştur →</a>'
if "googleGiris" not in src:
    src = src.replace(ANCHOR_GIRIS, GOOGLE_BTN + "\n    " + ANCHOR_GIRIS, 1)
    ok.append("✓ Google Sign-In butonu /giris sayfasına eklendi")
else:
    ok.append("- Google butonu zaten var")

# ─────────────────────────────────────────────
# Write
# ─────────────────────────────────────────────
with open(TARGET, "w", encoding="utf-8") as f:
    f.write(src)

print("\n".join(ok))
print("\n✅ web_app.py güncellendi.")
print("Şimdi: pip install firebase-admin (requirements.txt'e de ekle)")
print("Sonra: git add web_app.py requirements.txt && git commit -m 'feat: Firebase Google Auth' && git push")
