# -*- coding: utf-8 -*-
import sqlite3
import threading
import hashlib
import os
from datetime import datetime, date

# ─── ROL SABİTLERİ ───────────────────────
ROL_ADMIN      = "admin"
ROL_MUDUR      = "mudur"
ROL_KASIYER    = "kasiyer"
ROL_GORUNTULEYICI = "goruntuleyici"

ROL_YETKILERI = {
    ROL_ADMIN:         {"tara", "urun_ekle", "urun_duzenle", "urun_sil", "stok_guncelle",
                         "rapor", "kullanici_yonet", "tedarikci_yonet", "toplu_import", "log_goruntule"},
    ROL_MUDUR:         {"tara", "urun_ekle", "urun_duzenle", "stok_guncelle",
                         "rapor", "tedarikci_yonet", "toplu_import", "log_goruntule"},
    ROL_KASIYER:       {"tara", "stok_guncelle"},
    ROL_GORUNTULEYICI: {"rapor", "log_goruntule"},
}

def yetkisi_var(rol, yetki):
    return yetki in ROL_YETKILERI.get(rol, set())

def sifre_hash(sifre):
    return hashlib.sha256(sifre.encode("utf-8")).hexdigest()


class DatabaseManager:
    def __init__(self, db_name="envanter_pro.db"):
        self.db_name = db_name
        self._local  = threading.local()
        self.lock    = threading.Lock()
        self._init_db()

    # ── BAĞLANTI ────────────────────────────
    def _conn(self):
        if not hasattr(self._local, "conn") or self._local.conn is None:
            c = sqlite3.connect(self.db_name)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA foreign_keys=ON")
            self._local.conn = c
        return self._local.conn

    # ── ŞEMA OLUŞTUR ────────────────────────
    def _init_db(self):
        c = sqlite3.connect(self.db_name)
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        c.executescript("""
        CREATE TABLE IF NOT EXISTS kullanicilar (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            kullanici_adi TEXT UNIQUE NOT NULL,
            sifre_hash    TEXT NOT NULL,
            tam_ad        TEXT,
            rol           TEXT NOT NULL DEFAULT 'kasiyer',
            aktif         INTEGER DEFAULT 1,
            olusturma     DATETIME DEFAULT CURRENT_TIMESTAMP,
            son_giris     DATETIME
        );

        CREATE TABLE IF NOT EXISTS tedarikciler (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            ad        TEXT NOT NULL,
            telefon   TEXT,
            email     TEXT,
            adres     TEXT,
            not_      TEXT,
            aktif     INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS urunler (
            barkod          TEXT PRIMARY KEY,
            urun_adi        TEXT NOT NULL,
            kategori        TEXT DEFAULT 'Genel',
            stt             DATE,
            stok_adedi      INTEGER DEFAULT 0,
            min_stok        INTEGER DEFAULT 5,
            fiyat           REAL DEFAULT 0.0,
            tedarikci_id    INTEGER REFERENCES tedarikciler(id),
            aciklama        TEXT,
            eklenme_tarihi  DATETIME DEFAULT CURRENT_TIMESTAMP,
            son_guncelleme  DATETIME DEFAULT CURRENT_TIMESTAMP
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

        CREATE TABLE IF NOT EXISTS kamera_log (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            barkod   TEXT,
            tarih    DATETIME DEFAULT CURRENT_TIMESTAMP,
            sonuc    TEXT
        );
        """)
        c.commit()

        # Varsayılan admin hesabı
        row = c.execute("SELECT id FROM kullanicilar WHERE kullanici_adi='admin'").fetchone()
        if not row:
            c.execute(
                "INSERT INTO kullanicilar (kullanici_adi, sifre_hash, tam_ad, rol) VALUES (?,?,?,?)",
                ("admin", sifre_hash("admin123"), "Sistem Yöneticisi", ROL_ADMIN)
            )
            c.commit()
        c.close()

    # ══════════════════════════════════════
    #  KULLANICI YÖNETİMİ
    # ══════════════════════════════════════
    def giris_yap(self, kullanici_adi, sifre):
        h = sifre_hash(sifre)
        row = self._conn().execute(
            "SELECT * FROM kullanicilar WHERE kullanici_adi=? AND sifre_hash=? AND aktif=1",
            (kullanici_adi, h)
        ).fetchone()
        if row:
            self._conn().execute(
                "UPDATE kullanicilar SET son_giris=CURRENT_TIMESTAMP WHERE id=?", (row["id"],)
            )
            self._conn().commit()
            return dict(row)
        return None

    def kullanici_ekle(self, kullanici_adi, sifre, tam_ad, rol):
        with self.lock:
            try:
                self._conn().execute(
                    "INSERT INTO kullanicilar (kullanici_adi, sifre_hash, tam_ad, rol) VALUES (?,?,?,?)",
                    (kullanici_adi, sifre_hash(sifre), tam_ad, rol)
                )
                self._conn().commit()
                return True
            except sqlite3.IntegrityError:
                return False

    def kullanici_guncelle(self, uid, tam_ad, rol, aktif, yeni_sifre=None):
        with self.lock:
            if yeni_sifre:
                self._conn().execute(
                    "UPDATE kullanicilar SET tam_ad=?, rol=?, aktif=?, sifre_hash=? WHERE id=?",
                    (tam_ad, rol, aktif, sifre_hash(yeni_sifre), uid)
                )
            else:
                self._conn().execute(
                    "UPDATE kullanicilar SET tam_ad=?, rol=?, aktif=? WHERE id=?",
                    (tam_ad, rol, aktif, uid)
                )
            self._conn().commit()

    def kullanici_sil(self, uid):
        with self.lock:
            self._conn().execute("UPDATE kullanicilar SET aktif=0 WHERE id=?", (uid,))
            self._conn().commit()

    def tum_kullanicilar(self):
        rows = self._conn().execute(
            "SELECT id, kullanici_adi, tam_ad, rol, aktif, son_giris FROM kullanicilar ORDER BY tam_ad"
        ).fetchall()
        return [dict(r) for r in rows]

    # ══════════════════════════════════════
    #  TEDARİKÇİ YÖNETİMİ
    # ══════════════════════════════════════
    def tedarikci_ekle(self, ad, telefon="", email="", adres="", not_=""):
        with self.lock:
            self._conn().execute(
                "INSERT INTO tedarikciler (ad, telefon, email, adres, not_) VALUES (?,?,?,?,?)",
                (ad, telefon, email, adres, not_)
            )
            self._conn().commit()

    def tedarikci_guncelle(self, tid, ad, telefon, email, adres, not_):
        with self.lock:
            self._conn().execute(
                "UPDATE tedarikciler SET ad=?, telefon=?, email=?, adres=?, not_=? WHERE id=?",
                (ad, telefon, email, adres, not_, tid)
            )
            self._conn().commit()

    def tedarikci_sil(self, tid):
        with self.lock:
            self._conn().execute("UPDATE tedarikciler SET aktif=0 WHERE id=?", (tid,))
            self._conn().commit()

    def tum_tedarikciler(self):
        rows = self._conn().execute(
            "SELECT * FROM tedarikciler WHERE aktif=1 ORDER BY ad"
        ).fetchall()
        return [dict(r) for r in rows]

    # ══════════════════════════════════════
    #  ÜRÜN YÖNETİMİ
    # ══════════════════════════════════════
    def get_product(self, barkod):
        row = self._conn().execute("SELECT * FROM urunler WHERE barkod=?", (barkod,)).fetchone()
        return dict(row) if row else None

    def add_product(self, barkod, urun_adi, stt=None, stok=0, kategori="Genel",
                    min_stok=5, fiyat=0.0, tedarikci_id=None, aciklama=""):
        with self.lock:
            self._conn().execute(
                "INSERT OR REPLACE INTO urunler (barkod,urun_adi,kategori,stt,stok_adedi,min_stok,fiyat,tedarikci_id,aciklama) VALUES (?,?,?,?,?,?,?,?,?)",
                (barkod, urun_adi, kategori, stt, stok, min_stok, fiyat, tedarikci_id, aciklama)
            )
            self._conn().commit()

    def update_product(self, barkod, urun_adi, stt, stok, kategori, min_stok, fiyat, tedarikci_id=None, aciklama=""):
        with self.lock:
            self._conn().execute(
                "UPDATE urunler SET urun_adi=?,stt=?,stok_adedi=?,kategori=?,min_stok=?,fiyat=?,tedarikci_id=?,aciklama=?,son_guncelleme=CURRENT_TIMESTAMP WHERE barkod=?",
                (urun_adi, stt, stok, kategori, min_stok, fiyat, tedarikci_id, aciklama, barkod)
            )
            self._conn().commit()

    def delete_product(self, barkod):
        with self.lock:
            self._conn().execute("DELETE FROM urunler WHERE barkod=?", (barkod,))
            self._conn().commit()

    def get_all_products(self, search="", kategori="Tumü"):
        q = "SELECT u.*, t.ad as tedarikci_adi FROM urunler u LEFT JOIN tedarikciler t ON u.tedarikci_id=t.id WHERE 1=1"
        p = []
        if search:
            q += " AND (u.urun_adi LIKE ? OR u.barkod LIKE ?)"
            p += [f"%{search}%", f"%{search}%"]
        if kategori and kategori not in ("Tümü", "Tumü"):
            q += " AND u.kategori=?"
            p.append(kategori)
        q += " ORDER BY u.urun_adi"
        return [dict(r) for r in self._conn().execute(q, p).fetchall()]

    def toplu_import(self, satirlar, kullanici="sistem"):
        """satirlar: list of dicts with keys: barkod, urun_adi, stt, stok, kategori, min_stok, fiyat"""
        eklendi = guncellendi = hata = 0
        with self.lock:
            for s in satirlar:
                try:
                    barkod = str(s.get("barkod","")).strip()
                    if not barkod:
                        hata += 1
                        continue
                    mevcut = self.get_product(barkod)
                    self._conn().execute(
                        "INSERT OR REPLACE INTO urunler (barkod,urun_adi,kategori,stt,stok_adedi,min_stok,fiyat) VALUES (?,?,?,?,?,?,?)",
                        (barkod,
                         str(s.get("urun_adi","")).strip() or f"Urun-{barkod[-6:]}",
                         str(s.get("kategori","Genel")).strip(),
                         str(s.get("stt","")).strip() or None,
                         int(s.get("stok", 0)),
                         int(s.get("min_stok", 5)),
                         float(s.get("fiyat", 0)))
                    )
                    if mevcut:
                        guncellendi += 1
                    else:
                        eklendi += 1
                except Exception:
                    hata += 1
            self._conn().commit()
        return eklendi, guncellendi, hata

    # ══════════════════════════════════════
    #  STOK HAREKETLERİ
    # ══════════════════════════════════════
    def log_movement(self, barkod, urun_adi, hareket_tipi="Okutma", miktar=1,
                     aciklama="Kasa okutma", kullanici="sistem"):
        with self.lock:
            urun = self.get_product(barkod)
            onceki = urun["stok_adedi"] if urun else 0
            if hareket_tipi in ["Cikis", "Okutma"]:
                sonraki = onceki - miktar
                self._conn().execute(
                    "UPDATE urunler SET stok_adedi=stok_adedi-? WHERE barkod=?", (miktar, barkod))
            elif hareket_tipi == "Giris":
                sonraki = onceki + miktar
                self._conn().execute(
                    "UPDATE urunler SET stok_adedi=stok_adedi+? WHERE barkod=?", (miktar, barkod))
            else:
                sonraki = onceki
            self._conn().execute(
                "INSERT INTO stok_hareketleri (barkod,urun_adi,hareket_tipi,miktar,onceki_stok,sonraki_stok,kullanici,aciklama) VALUES (?,?,?,?,?,?,?,?)",
                (barkod, urun_adi, hareket_tipi, miktar, onceki, sonraki, kullanici, aciklama)
            )
            self._conn().commit()

    def get_recent_movements(self, limit=100, barkod=None):
        q = "SELECT * FROM stok_hareketleri WHERE 1=1"
        p = []
        if barkod:
            q += " AND barkod=?"
            p.append(barkod)
        q += " ORDER BY tarih DESC LIMIT ?"
        p.append(limit)
        return [dict(r) for r in self._conn().execute(q, p).fetchall()]

    # ══════════════════════════════════════
    #  DASHBOARD & RAPORLAR
    # ══════════════════════════════════════
    def get_dashboard_stats(self):
        c = self._conn()
        today = date.today().isoformat()
        s = {}
        s["toplam_urun"]    = c.execute("SELECT COUNT(*) FROM urunler").fetchone()[0]
        s["toplam_stok"]    = c.execute("SELECT COALESCE(SUM(stok_adedi),0) FROM urunler").fetchone()[0]
        s["kritik_stok"]    = c.execute("SELECT COUNT(*) FROM urunler WHERE stok_adedi>0 AND stok_adedi<=min_stok").fetchone()[0]
        s["stoksuz"]        = c.execute("SELECT COUNT(*) FROM urunler WHERE stok_adedi<=0").fetchone()[0]
        s["tarihi_gecmis"]  = c.execute("SELECT COUNT(*) FROM urunler WHERE stt IS NOT NULL AND stt<?", (today,)).fetchone()[0]
        s["yaklasan_stt"]   = c.execute("SELECT COUNT(*) FROM urunler WHERE stt IS NOT NULL AND stt>=? AND stt<=date(?,'+'||7||' days')", (today, today)).fetchone()[0]
        s["bugun_hareket"]  = c.execute("SELECT COUNT(*) FROM stok_hareketleri WHERE DATE(tarih)=DATE('now')").fetchone()[0]
        s["toplam_tedarikci"] = c.execute("SELECT COUNT(*) FROM tedarikciler WHERE aktif=1").fetchone()[0]
        return s

    def get_expiry_alerts(self, gun=7):
        today = date.today().isoformat()
        rows = self._conn().execute(
            "SELECT * FROM urunler WHERE stt IS NOT NULL AND stt<=date(?,'+'||?||' days') ORDER BY stt ASC",
            (today, gun)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_low_stock_alerts(self):
        rows = self._conn().execute(
            "SELECT * FROM urunler WHERE stok_adedi<=min_stok ORDER BY stok_adedi ASC"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_categories(self):
        rows = self._conn().execute(
            "SELECT DISTINCT kategori FROM urunler ORDER BY kategori"
        ).fetchall()
        return ["Tümü"] + [r[0] for r in rows if r[0]]

    def get_movement_summary(self, gun=30):
        rows = self._conn().execute("""
            SELECT DATE(tarih) as gun, hareket_tipi, SUM(miktar) as toplam
            FROM stok_hareketleri
            WHERE tarih >= date('now', ?||' days')
            GROUP BY DATE(tarih), hareket_tipi
            ORDER BY gun DESC
        """, (f"-{gun}",)).fetchall()
        return [dict(r) for r in rows]