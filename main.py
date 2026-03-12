# -*- coding: utf-8 -*-
import sys, io, os
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    os.environ["PYTHONIOENCODING"] = "utf-8"

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import threading, csv
from datetime import datetime, date

from database import DatabaseManager, ROL_ADMIN, ROL_MUDUR, ROL_KASIYER, ROL_GORUNTULEYICI, yetkisi_var
from utils     import (C, FONT_MAIN, FONT_BOLD, FONT_LG, FONT_XL, FONT_MONO, FONT_SMALL,
                        beep, sesli_alarm, get_from_openfoodfacts, kalan_gun, stt_renk, stt_etiket,
                        ROL_RENK, ROL_ETIKET)
from widgets   import StatCard, ScrollTree, LabeledEntry, ToolBar, RolBadge

db = DatabaseManager()

# ─── KASA RENKLERI (siyah + yesil) ───────
K = {
    "bg":       "#080d0a",
    "panel":    "#0d1410",
    "card":     "#111c15",
    "border":   "#1a3d24",
    "gold":     "#22c55e",   # ana vurgu = yesil
    "gold2":    "#4ade80",   # acik yesil
    "gold_dim": "#052e16",
    "text":     "#d1fae5",
    "text2":    "#6ee7b7",
    "muted":    "#2d5a3d",
    "green":    "#22c55e",
    "green_bg": "#052e16",
    "yellow":   "#f0b429",
    "red":      "#e05252",
    "red_bg":   "#3d0f0f",
    "border2":  "#1a3d24",
}

def mkbtn(parent, text, cmd, bg, fg="#080d16", fs=10, px=16, py=8, bold=True):
    f = "bold" if bold else "normal"
    return tk.Button(parent, text=text, command=cmd,
                     bg=bg, fg=fg, relief="flat",
                     font=("Segoe UI", fs, f),
                     padx=px, pady=py, cursor="hand2",
                     activebackground=bg, activeforeground=fg, bd=0)

# ════════════════════════════════════════════════════════
#  GİRİŞ POPUP
# ════════════════════════════════════════════════════════
def login_popup(parent, on_success):
    dlg = tk.Toplevel(parent)
    dlg.title("Giris Yap")
    dlg.geometry("360x300")
    dlg.configure(bg=C["bg"])
    dlg.resizable(False, False)
    dlg.grab_set()

    tk.Frame(dlg, bg=C["accent"], height=3).pack(fill="x")
    tk.Label(dlg, text="GIRIS YAP", font=("Segoe UI", 14, "bold"),
             bg=C["bg"], fg=C["accent"]).pack(pady=(22, 16))

    form = tk.Frame(dlg, bg=C["bg"])
    form.pack(padx=40, fill="x")
    tk.Label(form, text="Kullanici Adi", font=FONT_MAIN,
             bg=C["bg"], fg=C["muted"]).pack(anchor="w")
    e_user = ttk.Entry(form, font=FONT_MAIN, width=26)
    e_user.pack(fill="x", pady=(0, 10))
    tk.Label(form, text="Sifre", font=FONT_MAIN,
             bg=C["bg"], fg=C["muted"]).pack(anchor="w")
    e_pass = ttk.Entry(form, font=FONT_MAIN, show="*", width=26)
    e_pass.pack(fill="x", pady=(0, 14))

    hata_lbl = tk.Label(dlg, text="", font=FONT_SMALL, bg=C["bg"], fg=C["red"])
    hata_lbl.pack()

    def _giris():
        k = e_user.get().strip()
        s = e_pass.get().strip()
        if not k or not s:
            hata_lbl.config(text="Tum alanlari doldurun.")
            return
        kullanici = db.giris_yap(k, s)
        if kullanici:
            dlg.destroy()
            on_success(kullanici)
        else:
            hata_lbl.config(text="Hatali kullanici adi veya sifre!")
            e_pass.delete(0, tk.END)
            beep(400, 300)

    e_pass.bind("<Return>", lambda e: _giris())
    tk.Button(form, text="GIRIS", font=FONT_BOLD, bg=C["accent"], fg=C["bg"],
              relief="flat", pady=9, cursor="hand2",
              command=_giris).pack(fill="x")
    tk.Label(dlg, text="Varsayilan: admin / admin123",
             font=FONT_SMALL, bg=C["bg"], fg=C["muted"]).pack(pady=(12, 0))
    e_user.focus()


# ════════════════════════════════════════════════════════
#  ANA UYGULAMA
# ════════════════════════════════════════════════════════
class BarkodApp:
    def __init__(self, kullanici=None):
        if kullanici is None:
            kullanici = {"id": 0, "kullanici_adi": "misafir",
                         "tam_ad": "Misafir", "rol": ROL_GORUNTULEYICI}
        self.kullanici  = kullanici
        self.rol        = kullanici["rol"]
        self.sepet      = []
        self._last_scanned = None

        self.root = tk.Tk()
        self.root.title("Akilli Barkod Pro v3.0")
        self.root.geometry("1280x820")
        self.root.configure(bg=C["bg"])
        self.root.minsize(1100, 700)

        self._build_styles()
        self._build_header()
        self._build_content()
        self._tick()
        self.root.mainloop()

    # ── STYLES ──────────────────────────────
    def _build_styles(self):
        s = ttk.Style()
        s.theme_use("clam")
        s.configure("TNotebook",
            background=C["panel"], borderwidth=0, tabmargins=[0,0,0,0])
        s.configure("TNotebook.Tab",
            background=C["panel"], foreground=C["muted"],
            padding=[22, 11], font=("Segoe UI", 10, "bold"), borderwidth=0)
        s.map("TNotebook.Tab",
            background=[("selected", C["bg"])],
            foreground=[("selected", C["accent"])])
        s.configure("Treeview",
            background=C["card"], foreground=C["text"],
            fieldbackground=C["card"], rowheight=30,
            font=FONT_MAIN, borderwidth=0)
        s.configure("Treeview.Heading",
            background=C["panel"], foreground=C["accent"],
            font=("Segoe UI", 10, "bold"), borderwidth=0, relief="flat")
        s.map("Treeview",
            background=[("selected", C["accent"])],
            foreground=[("selected", C["bg"])])
        s.configure("TEntry",
            fieldbackground=C["card"], foreground=C["text"],
            insertcolor=C["accent"], borderwidth=1, relief="solid")
        s.configure("TCombobox",
            fieldbackground=C["card"], foreground=C["text"],
            selectbackground=C["accent"], borderwidth=1)
        s.configure("Vertical.TScrollbar",
            background=C["panel"], troughcolor=C["bg"],
            arrowcolor=C["muted"], borderwidth=0)

    # ── HEADER ──────────────────────────────
    def _build_header(self):
        hdr = tk.Frame(self.root, bg=C["panel"], height=60)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)

        left = tk.Frame(hdr, bg=C["panel"])
        left.pack(side="left", fill="y")
        tk.Frame(left, bg=C["accent"], width=4).pack(side="left", fill="y")
        tk.Label(left, text="  AKILLI BARKOD",
                 font=("Segoe UI", 15, "bold"),
                 bg=C["panel"], fg=C["text"]).pack(side="left", pady=16)
        tk.Label(left, text=" PRO v3.0",
                 font=("Segoe UI", 9),
                 bg=C["panel"], fg=C["accent"]).pack(side="left", pady=22)

        tk.Button(hdr, text="Cikis", font=FONT_SMALL,
                  bg=C["error_bg"], fg=C["red"],
                  relief="flat", padx=10, pady=4, cursor="hand2",
                  command=lambda: messagebox.askyesno("Cikis","Cikmak istiyor musunuz?") and self.root.destroy()
                  ).pack(side="right", pady=18, padx=8)

        self._hdr_info = tk.Frame(hdr, bg=C["panel"])
        self._hdr_info.pack(side="right", padx=4)
        self._refresh_header_info()

        self.clock_lbl = tk.Label(hdr, text="", font=("Consolas", 10),
                                   bg=C["panel"], fg=C["accent"])
        self.clock_lbl.pack(side="right", padx=20)

        tk.Frame(self.root, bg=C["accent"], height=1).pack(fill="x")

    def _refresh_header_info(self):
        for w in self._hdr_info.winfo_children():
            w.destroy()
        if self.kullanici["kullanici_adi"] == "misafir":
            tk.Button(self._hdr_info, text="  Giris Yap  ", font=FONT_BOLD,
                      bg=C["accent"], fg=C["bg"], relief="flat",
                      padx=14, pady=5, cursor="hand2",
                      command=lambda: login_popup(self.root, self._giris_basarili)
                      ).pack(pady=16)
        else:
            rc = ROL_RENK.get(self.rol, C["muted"])
            tk.Label(self._hdr_info, text=self.rol.upper(),
                     font=("Segoe UI", 8, "bold"),
                     bg=C["gold_dim"] if hasattr(C, "gold_dim") else C["card"], fg=rc,
                     padx=8, pady=2).pack(side="right", pady=20, padx=4)
            tk.Label(self._hdr_info,
                     text=self.kullanici.get("tam_ad") or self.kullanici["kullanici_adi"],
                     font=FONT_BOLD, bg=C["panel"], fg=C["text"]
                     ).pack(side="right", padx=4, pady=20)
            tk.Button(self._hdr_info, text="Oturumu Kapat", font=FONT_SMALL,
                      bg=C["warn_bg"], fg="white", relief="flat",
                      padx=8, pady=4, cursor="hand2",
                      command=self._oturumu_kapat).pack(side="right", pady=18, padx=6)

    def _giris_basarili(self, kullanici):
        self.kullanici = kullanici
        self.rol       = kullanici["rol"]
        self._rebuild_content()
        self._refresh_header_info()
        self.root.title(f"Akilli Barkod Pro v3.0  —  {kullanici.get('tam_ad') or kullanici['kullanici_adi']}")

    def _oturumu_kapat(self):
        self.kullanici = {"id":0,"kullanici_adi":"misafir","tam_ad":"Misafir","rol":ROL_GORUNTULEYICI}
        self.rol = ROL_GORUNTULEYICI
        self.sepet = []
        self._rebuild_content()
        self._refresh_header_info()
        self.root.title("Akilli Barkod Pro v3.0")

    def _tick(self):
        self.clock_lbl.config(text=datetime.now().strftime("%d.%m.%Y  %H:%M:%S"))
        self.root.after(1000, self._tick)

    # ── İÇERİK ──────────────────────────────
    def _build_content(self):
        self._content_frame = tk.Frame(self.root, bg=C["bg"])
        self._content_frame.pack(fill="both", expand=True)

        misafir = (self.rol in (ROL_GORUNTULEYICI,) and self.kullanici["kullanici_adi"] == "misafir")

        if misafir:
            # Sadece kasa arayüzü
            self._build_kasa_ui(self._content_frame)
        else:
            # Tam notebook
            self.nb = ttk.Notebook(self._content_frame)
            self.nb.pack(fill="both", expand=True, padx=10, pady=(6,10))
            self._build_kasa_tab()
            if yetkisi_var(self.rol, "rapor") or yetkisi_var(self.rol, "log_goruntule"):
                self._build_dashboard_tab()
            if yetkisi_var(self.rol, "urun_ekle") or yetkisi_var(self.rol, "urun_duzenle") or yetkisi_var(self.rol, "urun_sil") or yetkisi_var(self.rol, "rapor"):
                self._build_products_tab()
            if yetkisi_var(self.rol, "log_goruntule"):
                self._build_history_tab()
            if yetkisi_var(self.rol, "rapor"):
                self._build_reports_tab()
            if yetkisi_var(self.rol, "tedarikci_yonet"):
                self._build_suppliers_tab()
            if yetkisi_var(self.rol, "kullanici_yonet"):
                self._build_users_tab()
            self.refresh_dashboard()
            self.refresh_products()
            self.refresh_history()

    def _rebuild_content(self):
        self._content_frame.destroy()
        self._build_content()
        if hasattr(self, "barkod_entry"):
            self.barkod_entry.focus()

    def _tab(self, label):
        f = tk.Frame(self.nb, bg=C["bg"])
        self.nb.add(f, text=label)
        return f

    # ════════════════════════════════════════
    #  KASA SEKMESİ (notebook içi)
    # ════════════════════════════════════════
    def _build_kasa_tab(self):
        tab = self._tab("  Kasa  ")
        self._build_kasa_ui(tab)

    # ════════════════════════════════════════
    #  KASA UI (hem tab hem standalone)
    # ════════════════════════════════════════
    def _build_kasa_ui(self, parent):
        parent.configure(bg=K["bg"])
        main = tk.Frame(parent, bg=K["bg"])
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=1)
        main.columnconfigure(1, weight=0)
        main.rowconfigure(0, weight=1)

        left = tk.Frame(main, bg=K["bg"])
        left.grid(row=0, column=0, sticky="nsew", padx=(12,6), pady=12)
        left.rowconfigure(1, weight=1)
        left.columnconfigure(0, weight=1)

        right = tk.Frame(main, bg=K["panel"],
                          highlightbackground=K["border"], highlightthickness=1,
                          width=280)
        right.grid(row=0, column=1, sticky="nsew", padx=(6,12), pady=12)
        right.pack_propagate(False)

        self._build_kasa_scan(left)
        self._build_kasa_sepet(left)
        self._build_kasa_right(right)

    # ── TARAMA ───────────────────────────
    def _build_kasa_scan(self, parent):
        frame = tk.Frame(parent, bg=K["panel"],
                          highlightbackground=K["border"], highlightthickness=1)
        frame.grid(row=0, column=0, sticky="ew", pady=(0,10))

        tk.Frame(frame, bg=K["gold"], height=2).pack(fill="x")
        tk.Label(frame, text="BARKOD OKUT",
                 font=("Segoe UI", 8, "bold"),
                 bg=K["panel"], fg=K["gold"]).pack(anchor="w", padx=14, pady=(10,6))

        row = tk.Frame(frame, bg=K["panel"])
        row.pack(fill="x", padx=12, pady=(0,10))

        self.barkod_var = tk.StringVar()
        self.barkod_entry = ttk.Entry(row, textvariable=self.barkod_var,
                                       font=("Consolas", 20), width=20, justify="center")
        self.barkod_entry.pack(side="left", fill="x", expand=True, padx=(0,8))
        self.barkod_entry.bind("<Return>", lambda e: self._kasa_okut())

        mkbtn(row, "OKUT", self._kasa_okut,
              K["gold"], K["bg"], 11, 20, 10).pack(side="left", padx=2)
        mkbtn(row, "Temizle", self._kasa_sifirla,
              K["card"], K["muted"], 9, 10, 10, bold=False).pack(side="left", padx=2)
        mkbtn(row, "Kamera", self._kamera_ac,
              K["card"], K["text2"], 9, 10, 10, bold=False).pack(side="left", padx=2)

        # Son okutulan kart
        self.son_kart = tk.Frame(frame, bg=K["panel"])
        self.son_kart.pack(fill="x", padx=12, pady=(0,10))

    def _goster_son_kart(self, urun, gun):
        for w in self.son_kart.winfo_children():
            w.destroy()
        gecmis = gun is not None and gun < 0
        bg = K["red_bg"] if gecmis else K["green_bg"]
        fg_skt = (K["red"] if gecmis else
                  K["yellow"] if (gun is not None and gun <= 3) else K["green"])

        if gun is None:     skt_txt = "SKT belirtilmemis"
        elif gun < 0:       skt_txt = f"TARIHI GECMIS ({abs(gun)} gun once!)"
        elif gun == 0:      skt_txt = "BUGUN bitiyor!"
        elif gun <= 3:      skt_txt = f"SKT: {urun.get('stt')}  —  {gun} gun kaldi"
        else:               skt_txt = f"SKT: {urun.get('stt') or 'Belirtilmemis'}"

        top = tk.Frame(self.son_kart, bg=bg)
        top.pack(fill="x")
        tk.Label(top, text=f"  {urun['urun_adi']}",
                 font=("Segoe UI", 13, "bold"),
                 bg=bg, fg=K["text"], anchor="w").pack(side="left", fill="x", expand=True, pady=10)
        tk.Label(top, text=f"{float(urun.get('fiyat',0)):.2f} TL  ",
                 font=("Segoe UI", 17, "bold"),
                 bg=bg, fg=K["gold"]).pack(side="right", pady=10)
        tk.Label(self.son_kart, text=f"  {skt_txt}",
                 font=("Segoe UI", 9), bg=bg, fg=fg_skt, anchor="w"
                 ).pack(fill="x", pady=(0,6))

    # ── SEPET ────────────────────────────
    def _build_kasa_sepet(self, parent):
        frame = tk.Frame(parent, bg=K["panel"],
                          highlightbackground=K["border"], highlightthickness=1)
        frame.grid(row=1, column=0, sticky="nsew")
        frame.rowconfigure(1, weight=1)
        frame.columnconfigure(0, weight=1)

        tk.Frame(frame, bg=K["border"], height=1).pack(fill="x")
        hdr = tk.Frame(frame, bg=K["panel"])
        hdr.pack(fill="x", padx=12, pady=8)
        self.sepet_baslik = tk.Label(hdr, text="SEPET — 0 URUN",
                                      font=("Segoe UI", 8, "bold"),
                                      bg=K["panel"], fg=K["gold"])
        self.sepet_baslik.pack(side="left")

        tree_f = tk.Frame(frame, bg=K["panel"])
        tree_f.pack(fill="both", expand=True, padx=8, pady=(0,8))
        tree_f.rowconfigure(0, weight=1)
        tree_f.columnconfigure(0, weight=1)

        cols   = ["Urun Adi","SKT Durumu","Adet","Birim Fiyat","Toplam"]
        widths = [230,120,55,95,95]
        self.sepet_tree = ttk.Treeview(tree_f, columns=cols,
                                        show="headings", selectmode="browse")
        vsb = ttk.Scrollbar(tree_f, orient="vertical", command=self.sepet_tree.yview)
        self.sepet_tree.configure(yscrollcommand=vsb.set)
        vsb.grid(row=0, column=1, sticky="ns")
        self.sepet_tree.grid(row=0, column=0, sticky="nsew")
        for col, w in zip(cols, widths):
            self.sepet_tree.heading(col, text=col)
            self.sepet_tree.column(col, width=w, anchor="w" if col=="Urun Adi" else "center")
        self.sepet_tree.tag_configure("gecmis", foreground=K["red"],    background="#180606")
        self.sepet_tree.tag_configure("yakin",  foreground=K["yellow"], background="#181000")
        self.sepet_tree.tag_configure("normal", foreground=K["text"])

        alt = tk.Frame(frame, bg=K["panel"])
        alt.pack(fill="x", padx=8, pady=(0,8))
        mkbtn(alt, "Secileni Kaldir", self._kasa_kaldir,
              K["card"], K["red"], 9, 10, 5).pack(side="left", padx=2)
        mkbtn(alt, "Sepeti Temizle", self._kasa_temizle,
              K["red_bg"], K["red"], 9, 10, 5).pack(side="left", padx=2)

        # Hızlı stok (kasiyer+)
        if yetkisi_var(self.rol, "stok_guncelle"):
            stok_f = tk.Frame(frame, bg=K["panel"])
            stok_f.pack(fill="x", padx=8, pady=(0,6))
            tk.Label(stok_f, text="Hizli Stok:", font=("Segoe UI", 9),
                     bg=K["panel"], fg=K["muted"]).pack(side="left", padx=(0,8))
            for txt, dlt, clr, fg_c in [
                ("-10",-10,K["red_bg"],K["red"]),("-1",-1,K["red_bg"],K["red"]),
                ("+1",1,"#071a10",K["green"]),("+10",10,"#071a10",K["green"])]:
                tk.Button(stok_f, text=txt, font=("Segoe UI",10,"bold"),
                          bg=clr, fg=fg_c, relief="flat",
                          padx=12, pady=4, cursor="hand2",
                          command=lambda d=dlt: self._hizli_stok(d)).pack(side="left", padx=2)

    # ── SAĞ PANEL ────────────────────────
    def _build_kasa_right(self, parent):
        tk.Frame(parent, bg=K["gold"], height=3).pack(fill="x")
        tk.Label(parent, text="ODEME OZETI",
                 font=("Segoe UI", 8, "bold"),
                 bg=K["panel"], fg=K["gold"]).pack(anchor="w", padx=16, pady=(14,8))

        ozet = tk.Frame(parent, bg=K["panel"])
        ozet.pack(fill="x", padx=14)
        self.k_adet  = self._k_ozet(ozet, "Urun Sayisi",  "0")
        self.k_ara   = self._k_ozet(ozet, "Ara Toplam",   "0.00 TL")
        self.k_kdv   = self._k_ozet(ozet, "KDV (%18)",    "0.00 TL")

        tk.Frame(parent, bg=K["border"], height=1).pack(fill="x", padx=12, pady=10)

        toplam_f = tk.Frame(parent, bg=K["panel"])
        toplam_f.pack(fill="x", padx=16)
        tk.Label(toplam_f, text="TOPLAM", font=("Segoe UI",10),
                 bg=K["panel"], fg=K["text2"]).pack(side="left")
        self.k_toplam = tk.Label(toplam_f, text="0.00 TL",
                                  font=("Segoe UI",26,"bold"),
                                  bg=K["panel"], fg=K["gold"])
        self.k_toplam.pack(side="right")

        self.k_uyari_f = tk.Frame(parent, bg=K["panel"])
        self.k_uyari_f.pack(fill="x", padx=12, pady=(10,0))

        self.k_skt_f = tk.Frame(parent, bg=K["panel"])
        self.k_skt_f.pack(fill="x", padx=12, pady=(8,0))

        btn_f = tk.Frame(parent, bg=K["panel"])
        btn_f.pack(fill="x", padx=12, pady=(16,8), side="bottom")
        mkbtn(btn_f, "SEPETI TEMIZLE", self._kasa_temizle,
              K["red_bg"], K["red"], 9, 10, 7).pack(fill="x", pady=3)
        mkbtn(btn_f, "  KART ILE ODE  ",
              lambda: messagebox.showinfo("Odeme","Kart odeme baslatildi."),
              K["card"], K["text2"], 12, 10, 12).pack(fill="x", pady=3)
        mkbtn(btn_f, "  NAKIT ODEME  ", self._nakit_odeme,
              K["gold"], K["bg"], 13, 10, 14).pack(fill="x", pady=3)

    def _k_ozet(self, parent, label, value):
        f = tk.Frame(parent, bg=K["panel"])
        f.pack(fill="x", pady=4)
        tk.Label(f, text=label, font=("Segoe UI",10),
                 bg=K["panel"], fg=K["text2"]).pack(side="left")
        lbl = tk.Label(f, text=value, font=("Segoe UI",10),
                       bg=K["panel"], fg=K["text"])
        lbl.pack(side="right")
        return lbl

    # ── KASA İŞLEMLER ────────────────────
    def _kasa_okut(self):
        barkod = self.barkod_var.get().strip()
        if not barkod: return

        urun = db.get_product(barkod)
        if not urun:
            urun_adi, kategori = get_from_openfoodfacts(barkod)
            if urun_adi:
                db.add_product(barkod, urun_adi, None, 30, kategori or "Genel")
                urun = db.get_product(barkod)
                messagebox.showinfo("Yeni Urun Eklendi",
                                    f"{urun_adi}\nOtomatik eklendi. Varsayilan stok: 30")
            else:
                messagebox.showerror("Bulunamadi",
                                     f"Barkod: {barkod}\nYerel DB ve Open Food Facts'te yok.")
                self.barkod_var.set("")
                return

        self.barkod_var.set("")
        self._last_scanned = barkod

        for item in self.sepet:
            if item["urun"]["barkod"] == barkod:
                item["adet"] += 1
                break
        else:
            self.sepet.append({"urun": urun, "adet": 1})

        db.log_movement(barkod, urun["urun_adi"], "Okutma", 1,
                        "Kasa okutma", self.kullanici["kullanici_adi"])

        gun = kalan_gun(urun.get("stt"))
        self._goster_son_kart(urun, gun)
        self._skt_buton_goster(urun)

        # Ses
        if gun is not None and gun < 0:
            sesli_alarm(3, 1500, 700)
        elif gun is not None and gun == 0:
            beep(1400, 600)
        elif gun is not None and gun == 1:
            beep(900, 400)

        self._sepet_guncelle()
        if hasattr(self, "refresh_dashboard"):
            self.refresh_dashboard()
        self.barkod_entry.focus()

    def _skt_buton_goster(self, urun):
        for w in self.k_skt_f.winfo_children():
            w.destroy()
        if not urun.get("stt"):
            mkbtn(self.k_skt_f, "+ SKT Tarihi Ekle",
                  lambda: self._skt_popup(urun),
                  K["yellow"], "#080d16", 9, 10, 5).pack(fill="x")
        else:
            mkbtn(self.k_skt_f, "SKT Degistir",
                  lambda: self._skt_popup(urun),
                  K["card"], K["text2"], 9, 10, 5).pack(fill="x")

    def _skt_popup(self, urun):
        dlg = tk.Toplevel(self.root)
        dlg.title("SKT Tarihi")
        dlg.geometry("320x230")
        dlg.configure(bg=K["bg"])
        dlg.resizable(False, False)
        dlg.grab_set()

        tk.Frame(dlg, bg=K["gold"], height=2).pack(fill="x")
        tk.Label(dlg, text=urun["urun_adi"], font=("Segoe UI",12,"bold"),
                 bg=K["bg"], fg=K["text"]).pack(pady=(14,4))
        tk.Label(dlg, text="Son Kullanma Tarihi:", font=("Segoe UI",9),
                 bg=K["bg"], fg=K["text2"]).pack()

        mevcut = urun.get("stt")
        if mevcut:
            try:
                d = datetime.strptime(str(mevcut)[:10],"%Y-%m-%d").date()
                gd, ay, yil = d.day, d.month, d.year
            except: gd, ay, yil = date.today().day, date.today().month, date.today().year
        else:
            gd, ay, yil = date.today().day, date.today().month, date.today().year

        tf = tk.Frame(dlg, bg=K["bg"])
        tf.pack(pady=12)
        for i, lbl in enumerate(["Gun","Ay","Yil"]):
            tk.Label(tf, text=lbl, font=("Segoe UI",8),
                     bg=K["bg"], fg=K["muted"]).grid(row=0, column=i, padx=8)
        e_g = ttk.Entry(tf, width=4, font=("Segoe UI",11), justify="center")
        e_a = ttk.Entry(tf, width=4, font=("Segoe UI",11), justify="center")
        e_y = ttk.Entry(tf, width=6, font=("Segoe UI",11), justify="center")
        e_g.insert(0,str(gd)); e_a.insert(0,str(ay)); e_y.insert(0,str(yil))
        e_g.grid(row=1,column=0,padx=8)
        e_a.grid(row=1,column=1,padx=8)
        e_y.grid(row=1,column=2,padx=8)

        hata = tk.Label(dlg, text="", font=("Segoe UI",8), bg=K["bg"], fg=K["red"])
        hata.pack()

        def kaydet():
            try:
                stt_date = date(int(e_y.get()), int(e_a.get()), int(e_g.get()))
                stt_str  = stt_date.strftime("%Y-%m-%d")
            except ValueError:
                hata.config(text="Gecersiz tarih!")
                return
            db.update_product(urun["barkod"], urun["urun_adi"], stt_str,
                               urun["stok_adedi"], urun.get("kategori","Genel"),
                               urun.get("min_stok",5), urun.get("fiyat",0),
                               urun.get("tedarikci_id"), urun.get("aciklama",""))
            urun["stt"] = stt_str
            gun = kalan_gun(stt_str)
            self._goster_son_kart(urun, gun)
            self._skt_buton_goster(urun)
            self._sepet_guncelle()
            dlg.destroy()

        mkbtn(dlg, "Kaydet", kaydet, K["green"], K["bg"], 10, 20, 7).pack(pady=(0,10))
        e_g.focus()

    def _kasa_sifirla(self):
        for w in self.son_kart.winfo_children():
            w.destroy()
        for w in self.k_skt_f.winfo_children():
            w.destroy()
        self.barkod_entry.focus()

    def _sepet_guncelle(self):
        for row in self.sepet_tree.get_children():
            self.sepet_tree.delete(row)
        toplam = 0.0
        for item in self.sepet:
            u    = item["urun"]
            adet = item["adet"]
            fiyat = float(u.get("fiyat") or 0)
            sat   = fiyat * adet
            toplam += sat
            gun = kalan_gun(u.get("stt"))
            if gun is None:     skt_s, tag = "Belirtilmemis", "normal"
            elif gun < 0:       skt_s, tag = f"GECMIS ({abs(gun)}g)", "gecmis"
            elif gun == 0:      skt_s, tag = "BUGUN bitiyor!", "gecmis"
            elif gun <= 3:      skt_s, tag = f"{gun} gun kaldi", "yakin"
            else:               skt_s, tag = str(u.get("stt","")), "normal"
            self.sepet_tree.insert("", "end",
                values=[u["urun_adi"], skt_s, adet,
                        f"{fiyat:.2f} TL", f"{sat:.2f} TL"],
                tags=(tag,))

        kdv = toplam * 0.18
        self.k_adet.config(text=str(len(self.sepet)))
        self.k_ara.config(text=f"{toplam:.2f} TL")
        self.k_kdv.config(text=f"{kdv:.2f} TL")
        self.k_toplam.config(text=f"{toplam:.2f} TL")
        self.sepet_baslik.config(text=f"SEPET — {len(self.sepet)} URUN")

        for w in self.k_uyari_f.winfo_children():
            w.destroy()
        if any((kalan_gun(i["urun"].get("stt")) or 1) < 0 for i in self.sepet):
            tk.Label(self.k_uyari_f,
                     text="  Sepette tarihi gecmis urun var!",
                     font=("Segoe UI",9,"bold"),
                     bg=K["red_bg"], fg=K["red"],
                     anchor="w", pady=6).pack(fill="x")

    def _kasa_kaldir(self):
        sel = self.sepet_tree.selection()
        if not sel: return
        idx = self.sepet_tree.index(sel[0])
        if 0 <= idx < len(self.sepet):
            self.sepet.pop(idx)
        self._sepet_guncelle()

    def _kasa_temizle(self):
        self.sepet = []
        self._sepet_guncelle()
        self._kasa_sifirla()

    def _nakit_odeme(self):
        if not self.sepet:
            messagebox.showwarning("Bos Sepet","Sepette urun yok.")
            return
        toplam = sum(float(i["urun"].get("fiyat") or 0)*i["adet"] for i in self.sepet)
        if messagebox.askyesno("Nakit Odeme",
                                f"Toplam: {toplam:.2f} TL\n\nOdeme tamamlandi mi?"):
            self._kasa_temizle()
            messagebox.showinfo("Tamamlandi","Odeme alindi!")

    def _hizli_stok(self, delta):
        if not self._last_scanned:
            messagebox.showwarning("Uyari","Once bir urun okutun.")
            return
        urun = db.get_product(self._last_scanned)
        if not urun: return
        tip = "Giris" if delta > 0 else "Cikis"
        db.log_movement(self._last_scanned, urun["urun_adi"], tip, abs(delta),
                        f"Manuel ({delta:+d})", self.kullanici["kullanici_adi"])
        if hasattr(self, "refresh_dashboard"):
            self.refresh_dashboard()
        if hasattr(self, "refresh_products"):
            self.refresh_products()

    def _kamera_ac(self):
        import cv2
        import zxingcpp
        from PIL import Image, ImageTk

        win = tk.Toplevel(self.root)
        win.title("Kamera — Barkod Okuyucu")
        win.configure(bg=K["bg"])
        win.resizable(False, False)

        tk.Frame(win, bg=K["gold"], height=2).pack(fill="x")
        tk.Label(win, text="KAMERA BARKOD OKUYUCU",
                 font=("Segoe UI", 10, "bold"),
                 bg=K["bg"], fg=K["gold"]).pack(pady=(10, 4))

        lbl_img = tk.Label(win, bg=K["bg"])
        lbl_img.pack(padx=12, pady=4)

        lbl_s = tk.Label(win, text="Barkodu kameraya tutun...",
                         font=("Segoe UI", 10), bg=K["bg"], fg=K["text2"])
        lbl_s.pack(pady=4)

        lbl_sonuc = tk.Label(win, text="", font=("Consolas", 13, "bold"),
                             bg=K["bg"], fg=K["gold"])
        lbl_sonuc.pack()

        mkbtn(win, "Kapat", win.destroy, K["red_bg"], K["red"], 9, 14, 6).pack(pady=10)

        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            messagebox.showerror("Hata", "Kamera acilamadi!")
            win.destroy()
            return

        self._kamera_aktif = True

        def guncelle():
            if not self._kamera_aktif or not win.winfo_exists():
                return

            ret, frame = cap.read()
            if not ret:
                win.after(30, guncelle)
                return

            # zxing-cpp ile oku — EAN-13 dahil tüm formatlar
            veri = None
            try:
                import numpy as np
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                results = zxingcpp.read_barcodes(gray)
                for r in results:
                    if r.valid and r.text:
                        veri = r.text
                        break
            except Exception:
                pass

            # Görüntüyü göster
            try:
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                img = Image.fromarray(rgb).resize((480, 360))
                photo = ImageTk.PhotoImage(img)
                lbl_img.config(image=photo)
                lbl_img.image = photo
            except Exception:
                pass

            if veri:
                lbl_s.config(text="OKUNDU!", fg=K["green"])
                lbl_sonuc.config(text=veri)
                beep(1200, 300)
                self._kamera_aktif = False
                cap.release()
                win.after(500, lambda: (win.destroy(), self.barkod_var.set(veri), self._kasa_okut()))
                return

            win.after(30, guncelle)

        def kapan():
            self._kamera_aktif = False
            try: cap.release()
            except: pass
        win.protocol("WM_DELETE_WINDOW", kapan)
        guncelle()

    # ════════════════════════════════════════
    #  TAB — DASHBOARD
    # ════════════════════════════════════════
    def _build_dashboard_tab(self):
        tab = self._tab("  Dashboard  ")
        self.dash_tab = tab

        cards_f = tk.Frame(tab, bg=C["bg"])
        cards_f.pack(fill="x", padx=16, pady=(14,6))
        self.stat_cards = {}
        cfgs = [
            ("toplam_urun",      "Toplam Urun",      C["accent"],  ""),
            ("toplam_stok",      "Toplam Stok",      C["cyan"],    ""),
            ("tarihi_gecmis",    "Tarihi Gecmis",    C["red"],     ""),
            ("yaklasan_stt",     "Yaklasan SKT(7g)", C["yellow"],  ""),
            ("kritik_stok",      "Kritik Stok",      C["orange"],  ""),
            ("stoksuz",          "Stoksuz",          C["purple"],  ""),
            ("bugun_hareket",    "Bugun Islem",      C["green"],   ""),
            ("toplam_tedarikci", "Tedarikci",        C["muted"],   ""),
        ]
        for i, (key, lbl, clr, icon) in enumerate(cfgs):
            card = StatCard(cards_f, lbl, "...", clr, icon)
            card.grid(row=0, column=i, padx=4, sticky="nsew")
            cards_f.columnconfigure(i, weight=1)
            self.stat_cards[key] = card

        split = tk.Frame(tab, bg=C["bg"])
        split.pack(fill="both", expand=True, padx=16, pady=6)
        split.columnconfigure(0, weight=1)
        split.columnconfigure(1, weight=1)

        left_f = tk.Frame(split, bg=C["panel"],
                          highlightbackground=C["border"], highlightthickness=1)
        left_f.grid(row=0, column=0, sticky="nsew", padx=(0,6))
        tk.Label(left_f, text="SKT Uyarilari (7 gun)", font=FONT_LG,
                 bg=C["panel"], fg=C["yellow"]).pack(pady=(8,4), padx=10, anchor="w")
        self.skt_tree = ScrollTree(left_f, ["Urun","SKT","Kalan","Stok"],[210,100,90,60])
        self.skt_tree.pack(fill="both", expand=True, padx=6, pady=(0,8))

        right_f = tk.Frame(split, bg=C["panel"],
                           highlightbackground=C["border"], highlightthickness=1)
        right_f.grid(row=0, column=1, sticky="nsew", padx=(6,0))
        tk.Label(right_f, text="Kritik Stok Uyarilari", font=FONT_LG,
                 bg=C["panel"], fg=C["orange"]).pack(pady=(8,4), padx=10, anchor="w")
        self.low_tree = ScrollTree(right_f, ["Urun","Stok","Min","Durum"],[210,65,55,90])
        self.low_tree.pack(fill="both", expand=True, padx=6, pady=(0,8))

        tk.Button(tab, text="Yenile", font=FONT_BOLD, bg=C["accent"], fg=C["bg"],
                  relief="flat", padx=16, pady=5, cursor="hand2",
                  command=self.refresh_dashboard).pack(pady=(0,10))

    def refresh_dashboard(self):
        if not hasattr(self, "stat_cards"): return
        stats = db.get_dashboard_stats()
        for k, card in self.stat_cards.items():
            card.update_val(stats.get(k, 0))
        self.skt_tree.clear()
        for u in db.get_expiry_alerts(7):
            gun = kalan_gun(u.get("stt"))
            tag = "expired" if (gun is not None and gun < 0) else \
                  "warn"    if (gun is not None and gun <= 2) else "ok"
            self.skt_tree.insert(
                [u["urun_adi"], u.get("stt","—"), stt_etiket(gun), u["stok_adedi"]], (tag,))
        self.skt_tree.tag_color("expired", C["red"])
        self.skt_tree.tag_color("warn",    C["yellow"])
        self.skt_tree.tag_color("ok",      C["orange"])
        self.low_tree.clear()
        for u in db.get_low_stock_alerts():
            durum = "STOKSUZ" if u["stok_adedi"] <= 0 else "KRITIK"
            tag   = "stoksuz" if u["stok_adedi"] <= 0 else "kritik"
            self.low_tree.insert(
                [u["urun_adi"], u["stok_adedi"], u.get("min_stok",5), durum], (tag,))
        self.low_tree.tag_color("stoksuz", C["red"])
        self.low_tree.tag_color("kritik",  C["orange"])

    # ════════════════════════════════════════
    #  TAB — URUNLER
    # ════════════════════════════════════════
    def _build_products_tab(self):
        tab = self._tab("  Urunler  ")
        tb = ToolBar(tab)
        tb.pack(fill="x", padx=10, pady=(8,0))
        tb.add_label("Ara:", side="left")
        self.search_var = tk.StringVar()
        self.search_var.trace("w", lambda *a: self.refresh_products())
        tb.add_entry(self.search_var, width=22)
        tb.add_label("Kategori:", side="left")
        self.kat_var   = tk.StringVar(value="Tuumu")
        self.kat_combo = tb.add_combo(self.kat_var, ["Tuumu"])
        self.kat_combo.bind("<<ComboboxSelected>>", lambda e: self.refresh_products())
        if yetkisi_var(self.rol, "urun_sil"):
            tb.add_button("Sil",        self._urun_sil,      C["red"])
        if yetkisi_var(self.rol, "urun_duzenle"):
            tb.add_button("Duzenle",    self._dlg_edit,      C["accent"])
        if yetkisi_var(self.rol, "urun_ekle"):
            tb.add_button("Yeni Urun",  self._dlg_add,       C["green"])
        if yetkisi_var(self.rol, "toplu_import"):
            tb.add_button("CSV Import", self._toplu_import,  C["purple"])

        cols   = ["Barkod","Urun Adi","Kategori","SKT","Kalan","Stok","Min","Fiyat","Tedarikci"]
        widths = [130,200,110,100,90,55,45,75,120]
        self.prod_tree = ScrollTree(tab, cols, widths)
        self.prod_tree.pack(fill="both", expand=True, padx=10, pady=6)
        self.prod_tree.bind("<Double-1>", lambda e: self._dlg_edit()
                            if yetkisi_var(self.rol, "urun_duzenle") else None)
        self.prod_count = tk.Label(tab, text="", font=FONT_SMALL,
                                    bg=C["bg"], fg=C["muted"])
        self.prod_count.pack(pady=(0,6))

    def refresh_products(self):
        if not hasattr(self, "prod_tree"): return
        self.kat_combo.config(values=db.get_categories())
        self.prod_tree.clear()
        kat = self.kat_var.get()
        if kat in ("Tuumu",""):
            kat = "Tümü"
        urunler = db.get_all_products(search=self.search_var.get(), kategori=kat)
        for u in urunler:
            gun   = kalan_gun(u.get("stt"))
            kalan = stt_etiket(gun) if u.get("stt") else "—"
            fiyat = f"{u.get('fiyat',0):.2f}TL" if u.get("fiyat") else "—"
            ted   = u.get("tedarikci_adi") or "—"
            tag   = ("expired" if (gun is not None and gun < 0)
                     else "warn"    if (gun is not None and gun <= 7)
                     else "stoksuz" if u["stok_adedi"] <= 0
                     else "normal")
            self.prod_tree.insert(
                [u["barkod"],u["urun_adi"],u.get("kategori","—"),
                 u.get("stt","—"),kalan,u["stok_adedi"],
                 u.get("min_stok",5),fiyat,ted], (tag,))
        self.prod_tree.tag_color("expired", C["red"])
        self.prod_tree.tag_color("warn",    C["yellow"])
        self.prod_tree.tag_color("stoksuz", C["purple"])
        self.prod_tree.tag_color("normal",  C["text"])
        self.prod_count.config(text=f"{len(urunler)} urun listeleniyor")

    def _selected_barkod(self):
        vals = self.prod_tree.selected_values()
        if not vals:
            messagebox.showwarning("Secim Yok","Lutfen bir urun secin.")
            return None
        return str(vals[0])

    def _urun_sil(self):
        barkod = self._selected_barkod()
        if not barkod: return
        urun = db.get_product(barkod)
        if urun and messagebox.askyesno("Sil", f"{urun['urun_adi']} silinsin mi?"):
            db.delete_product(barkod)
            self.refresh_products()
            self.refresh_dashboard()

    def _dlg_add(self):   self._urun_formu(None)
    def _dlg_edit(self):
        barkod = self._selected_barkod()
        if barkod: self._urun_formu(barkod)

    def _urun_formu(self, barkod):
        urun = db.get_product(barkod) if barkod else None
        dlg  = tk.Toplevel(self.root)
        dlg.title("Urun Ekle" if not urun else "Urun Duzenle")
        dlg.geometry("440x580")
        dlg.configure(bg=C["bg"])
        dlg.grab_set()

        sf = tk.Frame(dlg, bg=C["bg"])
        sf.pack(padx=24, pady=12, fill="both", expand=True)

        tedarikciler = db.tum_tedarikciler()
        ted_list     = ["—"] + [f"{t['id']}|{t['ad']}" for t in tedarikciler]
        ted_current  = "—"
        if urun and urun.get("tedarikci_id"):
            for t in tedarikciler:
                if t["id"] == urun["tedarikci_id"]:
                    ted_current = f"{t['id']}|{t['ad']}"

        fields = {}
        defs = [
            ("Barkod",          "barkod",   urun["barkod"]           if urun else "", False),
            ("Urun Adi",        "urun_adi", urun["urun_adi"]         if urun else "", False),
            ("Kategori",        "kategori", urun.get("kategori","Genel") if urun else "Genel", False),
            ("SKT (YYYY-MM-DD)","stt",      urun.get("stt","")       if urun else "", False),
            ("Stok",            "stok",     str(urun["stok_adedi"])  if urun else "0", False),
            ("Min Stok",        "min_stok", str(urun.get("min_stok",5)) if urun else "5", False),
            ("Fiyat (TL)",      "fiyat",    str(urun.get("fiyat",0)) if urun else "0", False),
            ("Aciklama",        "aciklama", urun.get("aciklama","")  if urun else "", False),
        ]
        for label, key, default, dis in defs:
            f = LabeledEntry(sf, label, default,
                             disabled=(key=="barkod" and bool(urun)), width=34)
            f.pack(fill="x", pady=3)
            fields[key] = f

        tk.Label(sf, text="Tedarikci", font=FONT_MAIN,
                 bg=C["bg"], fg=C["muted"]).pack(anchor="w", pady=(6,2))
        ted_var = tk.StringVar(value=ted_current)
        ttk.Combobox(sf, textvariable=ted_var, values=ted_list,
                     width=32, state="readonly").pack(fill="x")

        def kaydet():
            try:
                b = fields["barkod"].get()
                n = fields["urun_adi"].get()
                if not b or not n:
                    messagebox.showerror("Hata","Barkod ve Urun Adi zorunludur.",parent=dlg)
                    return
                stt_val  = fields["stt"].get() or None
                stok     = int(fields["stok"].get()     or 0)
                min_s    = int(fields["min_stok"].get() or 5)
                fiyat    = float(fields["fiyat"].get()  or 0)
                kat      = fields["kategori"].get()     or "Genel"
                aciklama = fields["aciklama"].get()
                ted_sel  = ted_var.get()
                ted_id   = int(ted_sel.split("|")[0]) if ted_sel != "—" else None
                if urun:
                    db.update_product(b,n,stt_val,stok,kat,min_s,fiyat,ted_id,aciklama)
                else:
                    db.add_product(b,n,stt_val,stok,kat,min_s,fiyat,ted_id,aciklama)
                dlg.destroy()
                self.refresh_products()
                self.refresh_dashboard()
            except ValueError as ex:
                messagebox.showerror("Hata",f"Gecersiz deger: {ex}",parent=dlg)

        tk.Button(dlg, text="Kaydet", font=FONT_BOLD, bg=C["green"], fg="white",
                  relief="flat", padx=20, pady=8, cursor="hand2",
                  command=kaydet).pack(pady=12)

    def _toplu_import(self):
        path = filedialog.askopenfilename(
            title="CSV Dosyasi Sec",
            filetypes=[("CSV","*.csv"),("Tumu","*.*")])
        if not path: return
        try:
            with open(path, newline="", encoding="utf-8-sig") as f:
                satirlar = list(csv.DictReader(f))
            if not satirlar:
                messagebox.showwarning("Bos Dosya","CSV dosyasinda veri bulunamadi.")
                return
            eklendi, guncellendi, hata = db.toplu_import(satirlar, self.kullanici["kullanici_adi"])
            messagebox.showinfo("Import Tamamlandi",
                                f"Eklendi: {eklendi}\nGuncellendi: {guncellendi}\nHata: {hata}")
            self.refresh_products()
            self.refresh_dashboard()
        except Exception as ex:
            messagebox.showerror("Import Hatasi", str(ex))

    # ════════════════════════════════════════
    #  TAB — HAREKETLER
    # ════════════════════════════════════════
    def _build_history_tab(self):
        tab = self._tab("  Hareketler  ")
        tb = ToolBar(tab)
        tb.pack(fill="x", padx=10, pady=(8,0))
        tb.add_label("Son hareket gecmisi", side="left")
        tb.add_button("Yenile", self.refresh_history)

        cols   = ["#","Barkod","Urun Adi","Islem","Miktar","Onceki","Sonraki","Tarih","Kullanici","Aciklama"]
        widths = [40,120,180,70,60,60,60,140,90,160]
        self.hist_tree = ScrollTree(tab, cols, widths)
        self.hist_tree.pack(fill="both", expand=True, padx=10, pady=6)

    def refresh_history(self):
        if not hasattr(self, "hist_tree"): return
        self.hist_tree.clear()
        for h in db.get_recent_movements(limit=150):
            tag = ("giris" if h["hareket_tipi"]=="Giris"
                   else "cikis" if h["hareket_tipi"] in ["Cikis","Okutma"]
                   else "normal")
            self.hist_tree.insert(
                [h["hareket_id"], h["barkod"], h.get("urun_adi","—"),
                 h["hareket_tipi"], h["miktar"],
                 h.get("onceki_stok","—"), h.get("sonraki_stok","—"),
                 str(h["tarih"])[:16], h.get("kullanici","—"), h.get("aciklama","")],
                (tag,))
        self.hist_tree.tag_color("giris",  C["green"])
        self.hist_tree.tag_color("cikis",  C["red"])
        self.hist_tree.tag_color("normal", C["text"])

    # ════════════════════════════════════════
    #  TAB — RAPORLAR
    # ════════════════════════════════════════
    def _build_reports_tab(self):
        if not yetkisi_var(self.rol, "rapor"): return
        tab = self._tab("  Raporlar  ")
        center = tk.Frame(tab, bg=C["bg"])
        center.pack(expand=True)

        tk.Label(center, text="Rapor & Disa Aktarim",
                 font=FONT_XL, bg=C["bg"], fg=C["white"]).pack(pady=(30,8))
        tk.Label(center, text="Envanter verilerinizi CSV olarak disari aktarin.",
                 font=FONT_MAIN, bg=C["bg"], fg=C["muted"]).pack(pady=(0,28))

        for lbl, cmd in [
            ("Tum Urunler (CSV)",    self._exp_all),
            ("Tarihi Gecmis Urunler",self._exp_expired),
            ("Yaklasan SKT Urunleri",self._exp_expiring),
            ("Dusuk Stok Urunleri",  self._exp_lowstock),
            ("Tum Hareket Gecmisi",  self._exp_movements),
            ("Tedarikci Listesi",    self._exp_suppliers),
        ]:
            tk.Button(center, text=lbl, font=FONT_LG, bg=C["card"], fg=C["white"],
                      relief="flat", padx=28, pady=12, cursor="hand2", width=34,
                      activebackground=C["hover"], activeforeground=C["white"],
                      command=cmd).pack(pady=5)

    def _exp_csv(self, fname, headers, rows):
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", initialfile=fname,
            filetypes=[("CSV","*.csv"),("Tumu","*.*")])
        if not path: return
        with open(path,"w",newline="",encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(headers)
            w.writerows(rows)
        messagebox.showinfo("Basarili", f"Kaydedildi:\n{path}")

    def _exp_all(self):
        self._exp_csv("urunler.csv",
            ["Barkod","Urun Adi","Kategori","SKT","Stok","Min Stok","Fiyat","Tedarikci"],
            [[u["barkod"],u["urun_adi"],u.get("kategori",""),u.get("stt",""),
              u["stok_adedi"],u.get("min_stok",5),u.get("fiyat",0),
              u.get("tedarikci_adi","")]
             for u in db.get_all_products()])

    def _exp_expired(self):
        today = date.today().isoformat()
        import sqlite3
        rows = db._conn().execute(
            "SELECT * FROM urunler WHERE stt IS NOT NULL AND stt<?", (today,)).fetchall()
        self._exp_csv("tarihi_gecmis.csv",
            ["Barkod","Urun Adi","SKT","Stok"],
            [[r["barkod"],r["urun_adi"],r["stt"],r["stok_adedi"]] for r in rows])

    def _exp_expiring(self):
        self._exp_csv("yaklasan_skt.csv",
            ["Barkod","Urun Adi","SKT","Kalan","Stok"],
            [[u["barkod"],u["urun_adi"],u.get("stt",""),
              stt_etiket(kalan_gun(u.get("stt"))),u["stok_adedi"]]
             for u in db.get_expiry_alerts(7)])

    def _exp_lowstock(self):
        self._exp_csv("dusuk_stok.csv",
            ["Barkod","Urun Adi","Stok","Min Stok"],
            [[u["barkod"],u["urun_adi"],u["stok_adedi"],u.get("min_stok",5)]
             for u in db.get_low_stock_alerts()])

    def _exp_movements(self):
        self._exp_csv("hareketler.csv",
            ["#","Barkod","Urun Adi","Islem","Miktar","Onceki","Sonraki","Tarih","Kullanici"],
            [[h["hareket_id"],h["barkod"],h.get("urun_adi",""),
              h["hareket_tipi"],h["miktar"],h.get("onceki_stok",""),
              h.get("sonraki_stok",""),str(h["tarih"])[:16],h.get("kullanici","")]
             for h in db.get_recent_movements(9999)])

    def _exp_suppliers(self):
        self._exp_csv("tedarikciler.csv",
            ["ID","Ad","Telefon","Email","Adres"],
            [[t["id"],t["ad"],t.get("telefon",""),t.get("email",""),t.get("adres","")]
             for t in db.tum_tedarikciler()])

    # ════════════════════════════════════════
    #  TAB — TEDARİKÇİLER
    # ════════════════════════════════════════
    def _build_suppliers_tab(self):
        tab = self._tab("  Tedarikciler  ")
        tb = ToolBar(tab)
        tb.pack(fill="x", padx=10, pady=(8,0))
        tb.add_button("Sil",           self._ted_sil,   C["red"])
        tb.add_button("Duzenle",       self._ted_edit,  C["accent"])
        tb.add_button("Yeni Tedarikci",self._ted_add,   C["green"])

        self.ted_tree = ScrollTree(tab,
            ["ID","Ad","Telefon","Email","Adres"],[50,200,120,180,200])
        self.ted_tree.pack(fill="both", expand=True, padx=10, pady=6)
        self._refresh_suppliers()

    def _refresh_suppliers(self):
        self.ted_tree.clear()
        for t in db.tum_tedarikciler():
            self.ted_tree.insert([t["id"],t["ad"],t.get("telefon",""),
                                   t.get("email",""),t.get("adres","")],())

    def _ted_add(self):  self._ted_formu(None)
    def _ted_edit(self):
        vals = self.ted_tree.selected_values()
        if vals: self._ted_formu(int(vals[0]))
        else: messagebox.showwarning("Secim Yok","Bir tedarikci secin.")

    def _ted_sil(self):
        vals = self.ted_tree.selected_values()
        if not vals: return
        if messagebox.askyesno("Sil","Tedarikci silinsin mi?"):
            db.tedarikci_sil(int(vals[0]))
            self._refresh_suppliers()

    def _ted_formu(self, tid):
        ted = None
        if tid:
            for t in db.tum_tedarikciler():
                if t["id"] == tid:
                    ted = t; break
        dlg = tk.Toplevel(self.root)
        dlg.title("Tedarikci")
        dlg.geometry("380x340")
        dlg.configure(bg=C["bg"])
        dlg.grab_set()
        sf = tk.Frame(dlg, bg=C["bg"])
        sf.pack(padx=24, pady=12, fill="both", expand=True)
        fields = {}
        for label, key in [("Ad","ad"),("Telefon","telefon"),("Email","email"),("Adres","adres"),("Not","not_")]:
            f = LabeledEntry(sf, label, ted.get(key,"") if ted else "", width=32)
            f.pack(fill="x", pady=3)
            fields[key] = f

        def kaydet():
            ad = fields["ad"].get()
            if not ad:
                messagebox.showerror("Hata","Ad zorunludur.",parent=dlg)
                return
            if ted:
                db.tedarikci_guncelle(tid, ad, fields["telefon"].get(),
                                      fields["email"].get(), fields["adres"].get(),
                                      fields["not_"].get())
            else:
                db.tedarikci_ekle(ad, fields["telefon"].get(),
                                   fields["email"].get(), fields["adres"].get(),
                                   fields["not_"].get())
            dlg.destroy()
            self._refresh_suppliers()

        tk.Button(dlg, text="Kaydet", font=FONT_BOLD, bg=C["green"], fg="white",
                  relief="flat", padx=20, pady=8, cursor="hand2",
                  command=kaydet).pack(pady=12)

    # ════════════════════════════════════════
    #  TAB — KULLANICILAR
    # ════════════════════════════════════════
    def _build_users_tab(self):
        tab = self._tab("  Kullanicilar  ")
        tb = ToolBar(tab)
        tb.pack(fill="x", padx=10, pady=(8,0))
        tb.add_button("Sil",           self._usr_sil,  C["red"])
        tb.add_button("Duzenle",       self._usr_edit, C["accent"])
        tb.add_button("Yeni Kullanici",self._usr_add,  C["green"])

        self.usr_tree = ScrollTree(tab,
            ["ID","Kullanici Adi","Tam Ad","Rol","Aktif","Son Giris"],
            [40,130,150,100,60,150])
        self.usr_tree.pack(fill="both", expand=True, padx=10, pady=6)
        self._refresh_users()

    def _refresh_users(self):
        self.usr_tree.clear()
        for u in db.tum_kullanicilar():
            self.usr_tree.insert([
                u["id"], u["kullanici_adi"], u.get("tam_ad",""),
                u["rol"], "Evet" if u["aktif"] else "Hayir",
                str(u.get("son_giris","—"))[:16]
            ], ())

    def _usr_add(self):  self._usr_formu(None)
    def _usr_edit(self):
        vals = self.usr_tree.selected_values()
        if vals: self._usr_formu(int(vals[0]))
        else: messagebox.showwarning("Secim Yok","Bir kullanici secin.")

    def _usr_sil(self):
        vals = self.usr_tree.selected_values()
        if not vals: return
        if int(vals[0]) == self.kullanici.get("id"):
            messagebox.showwarning("Uyari","Kendinizi silemezsiniz.")
            return
        if messagebox.askyesno("Sil","Kullanici silinsin mi?"):
            db.kullanici_sil(int(vals[0]))
            self._refresh_users()

    def _usr_formu(self, uid):
        usr = None
        if uid:
            for u in db.tum_kullanicilar():
                if u["id"] == uid:
                    usr = u; break
        dlg = tk.Toplevel(self.root)
        dlg.title("Kullanici")
        dlg.geometry("380x380")
        dlg.configure(bg=C["bg"])
        dlg.grab_set()
        sf = tk.Frame(dlg, bg=C["bg"])
        sf.pack(padx=24, pady=12, fill="both", expand=True)

        fields = {}
        defs = [
            ("Kullanici Adi","kullanici_adi", usr["kullanici_adi"] if usr else ""),
            ("Tam Ad",       "tam_ad",        usr.get("tam_ad","") if usr else ""),
            ("Sifre",        "sifre",         ""),
        ]
        for label, key, default in defs:
            f = LabeledEntry(sf, label, default,
                             disabled=(key=="kullanici_adi" and bool(usr)), width=30)
            f.pack(fill="x", pady=3)
            fields[key] = f

        tk.Label(sf, text="Rol", font=FONT_MAIN, bg=C["bg"], fg=C["muted"]).pack(anchor="w",pady=(6,2))
        rol_var = tk.StringVar(value=usr["rol"] if usr else ROL_KASIYER)
        ttk.Combobox(sf, textvariable=rol_var,
                     values=[ROL_ADMIN, ROL_MUDUR, ROL_KASIYER, ROL_GORUNTULEYICI],
                     width=28, state="readonly").pack(fill="x")

        if usr:
            aktif_var = tk.BooleanVar(value=bool(usr["aktif"]))
            tk.Checkbutton(sf, text="Aktif", variable=aktif_var,
                           bg=C["bg"], fg=C["text"], selectcolor=C["card"],
                           font=FONT_MAIN).pack(anchor="w", pady=6)
        else:
            aktif_var = tk.BooleanVar(value=True)

        def kaydet():
            k  = fields["kullanici_adi"].get()
            ta = fields["tam_ad"].get()
            si = fields["sifre"].get()
            if not k:
                messagebox.showerror("Hata","Kullanici adi zorunludur.",parent=dlg)
                return
            if usr:
                db.kullanici_guncelle(uid, ta, rol_var.get(),
                                      int(aktif_var.get()), si or None)
            else:
                if not si:
                    messagebox.showerror("Hata","Sifre zorunludur.",parent=dlg)
                    return
                if not db.kullanici_ekle(k, si, ta, rol_var.get()):
                    messagebox.showerror("Hata","Bu kullanici adi zaten var.",parent=dlg)
                    return
            dlg.destroy()
            self._refresh_users()

        tk.Button(dlg, text="Kaydet", font=FONT_BOLD, bg=C["green"], fg="white",
                  relief="flat", padx=20, pady=8, cursor="hand2",
                  command=kaydet).pack(pady=12)


# ════════════════════════════════════════════════════════
#  WEB SUNUCU
# ════════════════════════════════════════════════════════
def web_sunucu_baslat():
    try:
        from flask import Flask, render_template_string, request, redirect, session, jsonify
        import functools
        app = Flask(__name__)
        app.secret_key = "barkodpro_secret_2024"

        def giris_gerekli(f):
            @functools.wraps(f)
            def decorated(*args, **kwargs):
                if not session.get("user"):
                    return redirect("/giris")
                return f(*args, **kwargs)
            return decorated

        @app.route("/giris", methods=["GET","POST"])
        def giris():
            hata = ""
            if request.method == "POST":
                u = db.giris_yap(request.form.get("kullanici",""),
                                  request.form.get("sifre",""))
                if u:
                    session["user"]   = u["kullanici_adi"]
                    session["rol"]    = u["rol"]
                    session["tam_ad"] = u.get("tam_ad","")
                    return redirect("/")
                hata = "Hatali kullanici adi veya sifre!"
            return f"<h2>Giris</h2>{'<p style=color:red>'+hata+'</p>' if hata else ''}<form method=POST><input name=kullanici placeholder='Kullanici'><input name=sifre type=password placeholder='Sifre'><button>Giris</button></form>"

        @app.route("/cikis")
        def cikis():
            session.clear()
            return redirect("/giris")

        @app.route("/")
        @giris_gerekli
        def dashboard():
            stats = db.get_dashboard_stats()
            return jsonify(stats)

        @app.route("/api/stats")
        @giris_gerekli
        def api_stats():
            return jsonify(db.get_dashboard_stats())

        @app.route("/api/urunler")
        @giris_gerekli
        def api_urunler():
            return jsonify(db.get_all_products())

        app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)
    except ImportError:
        pass
    except Exception:
        pass


# ════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════
if __name__ == "__main__":
    t = threading.Thread(target=web_sunucu_baslat, daemon=True)
    t.start()
    BarkodApp()