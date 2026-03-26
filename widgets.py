# -*- coding: utf-8 -*-
import tkinter as tk
from tkinter import ttk
from utils import C, FONT_MAIN, FONT_BOLD, FONT_SMALL, FONT_LG


class StatCard(tk.Frame):
    def __init__(self, parent, label, value, color, icon="", **kwargs):
        super().__init__(parent, bg=C["card"], relief="flat",
                         highlightbackground=color, highlightthickness=1, **kwargs)
        # Ust altin cizgi
        tk.Frame(self, bg=color, height=3).pack(fill="x")
        tk.Label(self, text=icon, font=("Segoe UI Emoji", 16),
                 bg=C["card"], fg=color).pack(pady=(10, 1))
        self.val_lbl = tk.Label(self, text=str(value),
                                font=("Segoe UI Light", 22, "bold"),
                                bg=C["card"], fg=color)
        self.val_lbl.pack()
        tk.Label(self, text=label, font=("Segoe UI", 8),
                 bg=C["card"], fg=C["muted"]).pack(pady=(1, 10))

    def update_val(self, v):
        self.val_lbl.config(text=str(v))


class ScrollTree(tk.Frame):
    """Treeview + dikey + yatay scrollbar sarmalayıcı."""
    def __init__(self, parent, cols, widths, **kwargs):
        super().__init__(parent, bg=C["bg"])
        self.tree = ttk.Treeview(self, columns=cols, show="headings",
                                  selectmode="browse", **kwargs)
        vsb = ttk.Scrollbar(self, orient="vertical",   command=self.tree.yview)
        hsb = ttk.Scrollbar(self, orient="horizontal",  command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.pack(side="right",  fill="y")
        hsb.pack(side="bottom", fill="x")
        self.tree.pack(fill="both", expand=True)
        for col, w in zip(cols, widths):
            self.tree.heading(col, text=col)
            self.tree.column(col, width=w, minwidth=40)

    def clear(self):
        for i in self.tree.get_children():
            self.tree.delete(i)

    def insert(self, values, tags=()):
        self.tree.insert("", "end", values=values, tags=tags)

    def tag_color(self, tag, fg):
        self.tree.tag_configure(tag, foreground=fg)

    def selected_values(self):
        sel = self.tree.selection()
        if not sel:
            return None
        return self.tree.item(sel[0])["values"]

    def bind(self, event, func):
        self.tree.bind(event, func)


class LabeledEntry(tk.Frame):
    """Etiket + ttk.Entry sarmalayıcı."""
    def __init__(self, parent, label, default="", disabled=False, width=30, **kwargs):
        super().__init__(parent, bg=C["bg"], **kwargs)
        tk.Label(self, text=label, font=FONT_MAIN,
                 bg=C["bg"], fg=C["muted"]).pack(anchor="w")
        self.var = tk.StringVar(value=str(default))
        self.entry = ttk.Entry(self, textvariable=self.var, width=width, font=FONT_MAIN)
        if disabled:
            self.entry.config(state="disabled")
        self.entry.pack(fill="x")

    def get(self):
        return self.var.get().strip()

    def set(self, v):
        self.var.set(str(v))


class ToolBar(tk.Frame):
    """Üst araç çubuğu."""
    def __init__(self, parent, **kwargs):
        super().__init__(parent, bg=C["panel"], height=50, **kwargs)
        self.pack_propagate(False)

    def add_button(self, text, cmd, color=None, side="right"):
        color = color or C["accent"]
        tk.Button(self, text=text, font=FONT_BOLD, bg=color, fg="white",
                  relief="flat", padx=10, pady=6, cursor="hand2",
                  activebackground=color, activeforeground="white",
                  command=cmd).pack(side=side, padx=6, pady=8)

    def add_label(self, text, side="left"):
        tk.Label(self, text=text, font=FONT_MAIN,
                 bg=C["panel"], fg=C["muted"]).pack(side=side, padx=12, pady=14)

    def add_entry(self, var, width=20, side="left"):
        e = ttk.Entry(self, textvariable=var, width=width)
        e.pack(side=side, pady=10, padx=4)
        return e

    def add_combo(self, var, values, width=16, side="left"):
        cb = ttk.Combobox(self, textvariable=var, values=values,
                          width=width, state="readonly")
        cb.pack(side=side, pady=10, padx=4)
        return cb


class RolBadge(tk.Label):
    def __init__(self, parent, rol, **kwargs):
        from utils import ROL_RENK, ROL_ETIKET
        color = ROL_RENK.get(rol, C["muted"])
        text  = ROL_ETIKET.get(rol, rol.upper())
        super().__init__(parent, text=text, font=("Segoe UI", 8, "bold"),
                         bg=color, fg="white", padx=6, pady=2, **kwargs)