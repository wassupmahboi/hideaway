"""
app.py - desktop interface for Project Stowaway.

Covers the same two jobs as options 1 and 2 in main.py:
  Hide     lock a message with a password, add repair bytes, hide it in a picture
           When it finishes, a larger window shows the original and edited
           pictures side by side (reopen it with "Compare pictures").
  Recover  read the hidden message from a picture, repair damage, unlock it

The page scrolls with the mouse wheel when it is taller than the window.

Run it from the folder that holds the other Stowaway modules:
    python app.py

It uses encrypt_text / decrypt_text (cryptocumrepair.py) and reveal_bytes
(reveal.py), so nothing in those files needs to change. The header picture is
cut from test.png if that file sits next to app.py; without it the header is
a plain dark bar.
"""

import os
import queue
import sys
import tempfile
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageChops, ImageEnhance, ImageTk
from stegano import lsb
from stegano.lsb import generators

from cryptocumrepair import decrypt_text, encrypt_text
from reveal import reveal_bytes

if getattr(sys, "frozen", False):
    # Running as a PyInstaller .exe: save next to the .exe, and read bundled
    # files (the header picture) from PyInstaller's unpack folder.
    APP_DIR = Path(sys.executable).resolve().parent
    RES_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))
else:
    APP_DIR = RES_DIR = Path(__file__).resolve().parent

DEFAULT_OUT = APP_DIR / "Edited_Images" / "edited1.png"
BANNER_SOURCE = RES_DIR / "test.png"

# Palette taken from the nebula photo: night sky, cloud blue, star amber.
INK = "#0a0f1e"
PANEL = "#111a31"
FIELD = "#0d1428"
LINE = "#26335a"
TEXT = "#e8ecfb"
SOFT = "#a9b4d8"
MUTED = "#8f9bc4"
BLUE = "#6f9df0"
AMBER = "#f2a04a"
AMBER_HI = "#ffb767"
OK = "#5fd6a4"
ERR = "#ff7a7a"

TITLE = ("Georgia", 28, "bold")
BODY = ("Segoe UI", 10)
BODY_BOLD = ("Segoe UI", 10, "bold")

TAGLINE = "Locked text, hidden in a picture, repairable after damage."


class UserError(Exception):
    """A problem the person can fix; the message is shown as is."""


def explain(exc):
    """Turn an exception from the Stowaway modules into a plain sentence."""
    if isinstance(exc, UserError):
        return str(exc)
    text = str(exc)
    if text.startswith(("Header damaged", "No header found")):
        return ("No hidden message found. Either this picture has none, or the "
                "start of the message is too damaged to read.")
    if "exceeds error correction" in text:
        return "The picture is too damaged to repair. Part of the message is lost."
    if "Tampered or wrong password" in text:
        return "Wrong password, or the message was altered. Check the password and try again."
    if isinstance(exc, FileNotFoundError):
        return "That file doesn't exist."
    if isinstance(exc, OSError):
        return f"Couldn't open that picture: {exc}"
    return f"Something went wrong: {exc}"


def make_banner(path, height=112, bg=(10, 15, 30)):
    """Cut a dark, text-friendly strip out of a picture. Returns None on any problem."""
    try:
        src = Image.open(path).convert("RGB")
        w, h = src.size
        band_h = max(1, int(w * 0.075))
        left = int(w * 0.2)
        top = min(max(0, int(h * 0.28)), max(0, h - band_h))
        band = src.crop((left, top, w, min(h, top + band_h)))
        scale = height / band.size[1]
        band = band.resize((max(1, int(band.size[0] * scale)), height), Image.LANCZOS)
        band = ImageEnhance.Brightness(band).enhance(0.62)
        bw = band.size[0]

        # darker on the left where the title sits
        scrim = Image.new("L", band.size)
        scrim.putdata([int(255 * max(0.0, 1 - x / 640) ** 1.3 * 0.92)
                       for _ in range(height) for x in range(bw)])
        band = Image.composite(Image.new("RGB", band.size, bg), band, scrim)

        # fade the bottom edge into the page background
        fade = Image.new("L", band.size)
        fade.putdata([int(255 * max(0.0, (y - height * 0.6) / (height * 0.4)))
                      for y in range(height) for _ in range(bw)])
        return Image.composite(Image.new("RGB", band.size, bg), band, fade)
    except Exception:
        return None


def make_preview(img, limit=1600):
    """A copy small enough to redraw quickly when the compare window is resized."""
    small = img.copy()
    small.thumbnail((limit, limit), Image.LANCZOS)
    return small


def count_changed_pixels(before, after):
    """How many pixels differ in any colour channel, and how many there are in total."""
    diff = ImageChops.difference(before, after)
    r, g, b = diff.split()
    any_channel = ImageChops.lighter(ImageChops.lighter(r, g), b)
    mask = any_channel.point([0] + [255] * 255)
    return mask.histogram()[255], before.size[0] * before.size[1]


class CompareDialog(tk.Toplevel):
    """Original and edited picture side by side, scaled to fill the window."""

    def __init__(self, parent, images, captions, stats):
        super().__init__(parent)
        self.title("Compare pictures")
        self.configure(bg=INK)
        self.geometry("1240x760")
        self.minsize(760, 500)
        self.transient(parent)
        self.bind("<Escape>", lambda e: self.destroy())

        self.images = images
        self.photos = []
        self._job = None

        head = ttk.Frame(self)
        head.pack(fill="x", padx=24, pady=(20, 0))
        if stats and stats[1]:
            changed, total = stats
            note = (f"The hidden message changed {changed:,} of {total:,} pixels "
                    f"({changed / total:.3%}), and each colour value moved by at most "
                    "1 out of 255.")
        else:
            note = "The hidden message only nudges the lowest bit of a few colour values."
        blurb = tk.Label(head, text=note, bg=INK, fg=SOFT, font=BODY,
                         justify="left", anchor="w", wraplength=1100)
        blurb.pack(fill="x", pady=(4, 0))
        head.bind("<Configure>", lambda e: blurb.configure(wraplength=max(200, e.width - 4)))

        panels = ttk.Frame(self)
        panels.pack(fill="both", expand=True, padx=24, pady=(10, 6))
        panels.columnconfigure(0, weight=1, uniform="half")
        panels.columnconfigure(1, weight=1, uniform="half")
        panels.rowconfigure(0, weight=1)

        self.canvases = []
        for col, (title, caption) in enumerate(captions):
            cell = ttk.Frame(panels)
            cell.grid(row=0, column=col, sticky="nsew",
                      padx=(0, 8) if col == 0 else (8, 0))
            ttk.Label(cell, text=title, font=BODY_BOLD).pack(anchor="w")
            ttk.Label(cell, text=caption, style="Muted.TLabel").pack(anchor="w", pady=(0, 6))
            canvas = tk.Canvas(cell, bg=FIELD, highlightthickness=1,
                               highlightbackground=LINE, width=10, height=10)
            canvas.pack(fill="both", expand=True)
            self.canvases.append(canvas)
        panels.bind("<Configure>", self._schedule)

        foot = ttk.Frame(self)
        foot.pack(fill="x", padx=24, pady=(6, 18))
        ttk.Button(foot, text="Close", command=self.destroy).pack(side="right")

        self.after(120, self._render)

    def _schedule(self, _event=None):
        if self._job:
            self.after_cancel(self._job)
        self._job = self.after(70, self._render)

    def _render(self):
        self._job = None
        self.photos = []
        for canvas, img in zip(self.canvases, self.images):
            w, h = max(1, canvas.winfo_width() - 2), max(1, canvas.winfo_height() - 2)
            scale = min(w / img.size[0], h / img.size[1])
            size = (max(1, int(img.size[0] * scale)), max(1, int(img.size[1] * scale)))
            photo = ImageTk.PhotoImage(img.resize(size, Image.LANCZOS))
            self.photos.append(photo)          # keep a reference or Tk drops the picture
            canvas.delete("all")
            canvas.create_image(w // 2 + 1, h // 2 + 1, image=photo)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Stowaway")
        self.geometry("780x760")
        self.minsize(700, 700)
        self.configure(bg=INK)

        self.busy = False
        self.inbox = queue.Queue()   # worker threads hand results back through this
        self.last_compare = None     # data for the compare window, set after a successful hide
        self.compare_win = None

        self._styles()
        self._header()
        self._tabs()
        self.show("hide")
        self.msg.focus_set()

        # mouse wheel: Windows and macOS send MouseWheel, Linux sends Button-4 and Button-5
        self.bind_all("<MouseWheel>", self._on_wheel)
        self.bind_all("<Button-4>", self._on_wheel)
        self.bind_all("<Button-5>", self._on_wheel)
        self.after(80, self._drain)

    # ---------------------------------------------------------------- look

    def _styles(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(".", background=INK, foreground=TEXT, font=BODY,
                    bordercolor=LINE, lightcolor=LINE, darkcolor=LINE,
                    troughcolor=FIELD, focuscolor=BLUE)
        s.configure("TFrame", background=INK)
        s.configure("TLabel", background=INK, foreground=TEXT)
        s.configure("Muted.TLabel", background=INK, foreground=MUTED)

        s.configure("TEntry", fieldbackground=FIELD, foreground=TEXT,
                    insertcolor=TEXT, padding=7)
        s.map("TEntry", bordercolor=[("focus", BLUE)],
              lightcolor=[("focus", BLUE)], darkcolor=[("focus", BLUE)])

        s.configure("TButton", background=PANEL, foreground=TEXT, padding=(14, 7))
        s.map("TButton", background=[("active", LINE), ("disabled", INK)],
              foreground=[("disabled", MUTED)])

        s.configure("Accent.TButton", background=AMBER, foreground="#1a1206",
                    font=BODY_BOLD, padding=(20, 9), bordercolor=AMBER,
                    lightcolor=AMBER, darkcolor=AMBER)
        s.map("Accent.TButton",
              background=[("active", AMBER_HI), ("disabled", LINE)],
              foreground=[("disabled", MUTED)],
              bordercolor=[("active", AMBER_HI), ("disabled", LINE)],
              lightcolor=[("active", AMBER_HI), ("disabled", LINE)],
              darkcolor=[("active", AMBER_HI), ("disabled", LINE)])

        for name, colour in (("Tab.TButton", MUTED), ("TabOn.TButton", TEXT)):
            s.configure(name, background=INK, foreground=colour, font=BODY_BOLD,
                        padding=(2, 8), bordercolor=INK, lightcolor=INK, darkcolor=INK)
            s.map(name, background=[("active", INK)],
                  foreground=[("active", TEXT)],
                  bordercolor=[("active", INK)],
                  lightcolor=[("active", INK)], darkcolor=[("active", INK)])

        s.configure("Vertical.TScrollbar", background=LINE, troughcolor=INK,
                    bordercolor=INK, lightcolor=LINE, darkcolor=LINE,
                    arrowcolor=MUTED, gripcount=0)
        s.map("Vertical.TScrollbar", background=[("active", MUTED)])

        s.configure("TCheckbutton", background=INK, foreground=MUTED)
        s.map("TCheckbutton", background=[("active", INK)],
              foreground=[("active", TEXT)],
              indicatorbackground=[("selected", AMBER), ("!selected", FIELD)])

    def _header(self):
        self.header = tk.Canvas(self, height=112, bg=INK, highlightthickness=0)
        self.header.pack(fill="x")
        strip = make_banner(BANNER_SOURCE, 112)
        self._banner = ImageTk.PhotoImage(strip) if strip else None
        if self._banner:
            self.header.create_image(0, 0, image=self._banner, anchor="nw")
        self.header.create_text(28, 44, text="Stowaway", anchor="w",
                                fill=TEXT, font=TITLE)
        self.header.create_text(28, 82, text=TAGLINE, anchor="w",
                                fill=SOFT, font=BODY)

    def _tabs(self):
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=28, pady=(12, 0))

        # The pages live inside a canvas so the window can scroll when the
        # content is taller than the window.
        holder = ttk.Frame(self)
        holder.pack(fill="both", expand=True)
        self.scroll = tk.Canvas(holder, bg=INK, highlightthickness=0,
                                yscrollincrement=24)
        scrollbar = ttk.Scrollbar(holder, orient="vertical", command=self.scroll.yview)
        self.scroll.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.scroll.pack(side="left", fill="both", expand=True)

        body = ttk.Frame(self.scroll)
        body_id = self.scroll.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>",
                  lambda e: self.scroll.configure(scrollregion=self.scroll.bbox("all")))
        self.scroll.bind("<Configure>",
                         lambda e: self.scroll.itemconfigure(body_id, width=e.width))

        self.pages, self.tab_btns, self.tab_lines = {}, {}, {}
        for i, (key, label) in enumerate((("hide", "Hide"), ("recover", "Recover"))):
            cell = ttk.Frame(bar)
            cell.grid(row=0, column=i, padx=(0, 20))
            self.tab_btns[key] = ttk.Button(cell, text=label, style="Tab.TButton",
                                            command=lambda k=key: self.show(k))
            self.tab_btns[key].pack()
            self.tab_lines[key] = tk.Frame(cell, height=2, bg=INK)
            self.tab_lines[key].pack(fill="x")
            self.pages[key] = ttk.Frame(body)

        self._build_hide(self.pages["hide"])
        self._build_recover(self.pages["recover"])

    def show(self, key):
        for k, page in self.pages.items():
            if k == key:
                page.pack(fill="both", expand=True, padx=28, pady=(8, 22))
            else:
                page.pack_forget()
            self.tab_btns[k].configure(style="TabOn.TButton" if k == key else "Tab.TButton")
            self.tab_lines[k].configure(bg=AMBER if k == key else INK)
        self.scroll.yview_moveto(0)

    def _on_wheel(self, event):
        widget = event.widget
        if not isinstance(widget, tk.Misc) or widget.winfo_toplevel() is not self:
            return                                   # wheel over the compare window
        if isinstance(widget, tk.Text) and widget.yview() != (0.0, 1.0):
            return                                   # a long text box scrolls itself first
        first, last = self.scroll.yview()
        if first <= 0.0 and last >= 1.0:
            return                                   # everything already fits
        if event.num == 4:
            step = -1
        elif event.num == 5:
            step = 1
        else:
            step = -1 if event.delta > 0 else 1
        self.scroll.yview_scroll(step * 3, "units")

    # ------------------------------------------------------------- widgets

    def _label(self, parent, text, top=14):
        ttk.Label(parent, text=text, font=BODY_BOLD).pack(anchor="w", pady=(top, 4))

    def _hint(self, parent, text):
        ttk.Label(parent, text=text, style="Muted.TLabel").pack(anchor="w", pady=(4, 0))

    def _path_row(self, parent, var, command):
        row = ttk.Frame(parent)
        row.pack(fill="x")
        ttk.Entry(row, textvariable=var).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Browse…", command=command).pack(side="left", padx=(8, 0))

    def _textbox(self, parent, height, readonly=False):
        box = tk.Text(parent, height=height, wrap="word", font=BODY,
                      bg=FIELD, fg=TEXT, insertbackground=TEXT, relief="flat",
                      padx=10, pady=8, highlightthickness=1,
                      highlightbackground=LINE, highlightcolor=BLUE,
                      selectbackground=LINE, selectforeground=TEXT)
        box.pack(fill="x")
        if readonly:
            box.configure(state="disabled")
        return box

    def _password_pair(self, parent, labels):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(14, 0))
        entries = []
        for i, text in enumerate(labels):
            col = ttk.Frame(row)
            col.pack(side="left", fill="x", expand=True, padx=(0 if i == 0 else 12, 0))
            ttk.Label(col, text=text, font=BODY_BOLD).pack(anchor="w", pady=(0, 4))
            entry = ttk.Entry(col, show="•")
            entry.pack(fill="x")
            entries.append(entry)

        reveal_pw = tk.BooleanVar(value=False)

        def toggle():
            for entry in entries:
                entry.configure(show="" if reveal_pw.get() else "•")

        ttk.Checkbutton(parent, text="Show password", variable=reveal_pw,
                        command=toggle).pack(anchor="w", pady=(8, 0))
        return entries

    def _status(self, parent):
        label = tk.Label(parent, text="", bg=INK, fg=MUTED, font=BODY,
                         justify="left", anchor="w", wraplength=700)
        label.pack(fill="x", pady=(14, 0))
        parent.bind("<Configure>",
                    lambda e: label.configure(wraplength=max(200, e.width - 4)))
        return label

    @staticmethod
    def _say(label, text, colour=MUTED):
        label.configure(text=text, fg=colour)

    # ---------------------------------------------------------------- pages

    def _build_hide(self, page):
        self._label(page, "Message", top=0)
        self.msg = self._textbox(page, 6)

        self._label(page, "Cover picture")
        self.cover = tk.StringVar()
        self._path_row(page, self.cover, self._pick_cover)
        self._hint(page, "PNG or JPG. The message goes into the pixels, so the picture looks the same.")

        self.pw1, self.pw2 = self._password_pair(page, ("Password", "Confirm password"))

        self._label(page, "Save as")
        self.out = tk.StringVar(value=str(DEFAULT_OUT))
        self._path_row(page, self.out, self._pick_out)
        self._hint(page, "Always saved as PNG. A JPG would scramble the hidden message.")

        actions = ttk.Frame(page)
        actions.pack(fill="x", pady=(20, 0))
        self.hide_btn = ttk.Button(actions, text="Lock and hide",
                                   style="Accent.TButton", command=self.on_hide)
        self.hide_btn.pack(side="left")
        self.cmp_btn = ttk.Button(actions, text="Compare pictures",
                                  command=self.open_compare, state="disabled")
        self.cmp_btn.pack(side="left", padx=(10, 0))
        self.hide_status = self._status(page)
        self.pw2.bind("<Return>", lambda e: self.on_hide())

    def _build_recover(self, page):
        self._label(page, "Picture with the hidden message", top=0)
        self.source = tk.StringVar()
        self._path_row(page, self.source, self._pick_source)
        self._hint(page, "A damaged picture is fine. Repair happens before unlocking.")

        (self.pw_r,) = self._password_pair(page, ("Password",))

        actions = ttk.Frame(page)
        actions.pack(fill="x", pady=(20, 0))
        self.rec_btn = ttk.Button(actions, text="Extract and unlock",
                                  style="Accent.TButton", command=self.on_recover)
        self.rec_btn.pack(side="left")
        self.rec_status = self._status(page)
        self.pw_r.bind("<Return>", lambda e: self.on_recover())

        head = ttk.Frame(page)
        head.pack(fill="x", pady=(18, 4))
        ttk.Label(head, text="Recovered message", font=BODY_BOLD).pack(side="left")
        self.copy_btn = ttk.Button(head, text="Copy", command=self.on_copy,
                                   state="disabled")
        self.copy_btn.pack(side="right")
        self.result = self._textbox(page, 8, readonly=True)

    # -------------------------------------------------------- file pickers

    def _pick_cover(self):
        path = filedialog.askopenfilename(
            title="Choose a cover picture",
            filetypes=[("Pictures", "*.png *.jpg *.jpeg *.bmp"), ("All files", "*.*")])
        if path:
            self.cover.set(path)

    def _pick_out(self):
        current = Path(self.out.get() or DEFAULT_OUT)
        path = filedialog.asksaveasfilename(
            title="Save the picture as",
            defaultextension=".png",
            initialdir=str(current.parent) if current.parent.exists() else str(APP_DIR),
            initialfile=current.name,
            filetypes=[("PNG picture", "*.png")])
        if path:
            self.out.set(path)

    def _pick_source(self):
        path = filedialog.askopenfilename(
            title="Choose the picture to read",
            filetypes=[("Pictures", "*.png *.jpg *.jpeg *.bmp"), ("All files", "*.*")])
        if path:
            self.source.set(path)

    # --------------------------------------------------------------- tasks

    def _drain(self):
        try:
            while True:
                self.inbox.get_nowait()()
        except queue.Empty:
            pass
        self.after(80, self._drain)

    def _run(self, button, busy_text, work, done, status):
        """Run work() off the UI thread, then call done(result) back on it."""
        self.busy = True
        idle_text = button.cget("text")
        button.configure(text=busy_text, state="disabled")
        self.configure(cursor="watch")
        self._say(status, "")

        def finish(action):
            self.busy = False
            button.configure(text=idle_text, state="normal")
            self.configure(cursor="")
            action()

        def target():
            try:
                result = work()
            except Exception as exc:
                message = explain(exc)
                self.inbox.put(lambda: finish(lambda: self._say(status, message, ERR)))
            else:
                self.inbox.put(lambda: finish(lambda: done(result)))

        threading.Thread(target=target, daemon=True).start()

    # ---------------------------------------------------------------- Hide

    def on_hide(self):
        if self.busy:
            return
        message = self.msg.get("1.0", "end-1c").strip()
        cover = self.cover.get().strip()
        pw, pw2 = self.pw1.get(), self.pw2.get()
        out = Path(self.out.get().strip() or DEFAULT_OUT)
        if out.suffix.lower() != ".png":
            out = out.with_suffix(".png")

        problem = None
        if not message:
            problem = "Type the message you want to hide."
        elif not cover:
            problem = "Choose a cover picture."
        elif not Path(cover).is_file():
            problem = "That cover picture doesn't exist."
        elif not pw:
            problem = "Enter a password."
        elif pw != pw2:
            problem = "The two passwords don't match."
        elif out.resolve() == Path(cover).resolve():
            problem = "Save under a different name so the cover picture isn't overwritten."
        if problem:
            self._say(self.hide_status, problem, ERR)
            return
        if out.exists() and not messagebox.askyesno(
                "Replace file?", f"{out.name} already exists. Replace it?"):
            return

        def work():
            source, temp = cover, None
            with Image.open(cover) as img:
                if img.mode not in ("RGB", "RGBA"):
                    fd, temp = tempfile.mkstemp(suffix=".png")
                    os.close(fd)
                    img.convert("RGB").save(temp)
                    source = temp
            try:
                packed = encrypt_text(message, pw)
                try:
                    secret = lsb.hide(source, packed, generators.eratosthenes())
                except (ValueError, IndexError):
                    raise UserError("This picture is too small for the message. "
                                    "Choose a larger picture.")
                out.parent.mkdir(parents=True, exist_ok=True)
                secret.save(out)
            finally:
                if temp:
                    try:
                        os.remove(temp)
                    except OSError:
                        pass

            # material for the compare window
            with Image.open(cover) as img:
                before = img.convert("RGB")
            after = secret.convert("RGB")
            try:
                stats = count_changed_pixels(before, after)
            except Exception:
                stats = None
            return {
                "out": out,
                "cover": Path(cover),
                "size": before.size,
                "before": make_preview(before),
                "after": make_preview(after),
                "stats": stats,
            }

        def done(result):
            self.pw1.delete(0, "end")
            self.pw2.delete(0, "end")
            self._say(self.hide_status,
                      f"Locked and hidden. Saved to {result['out']}\n"
                      "Keep the password safe: without it the message can't be unlocked.",
                      OK)
            self.last_compare = result
            self.cmp_btn.configure(state="normal")
            self.open_compare()

        self._run(self.hide_btn, "Working…", work, done, self.hide_status)

    def open_compare(self):
        data = self.last_compare
        if not data:
            return
        if self.compare_win is not None and self.compare_win.winfo_exists():
            self.compare_win.destroy()
        w, h = data["size"]
        self.compare_win = CompareDialog(
            self,
            (data["before"], data["after"]),
            (("Original", f"{data['cover'].name}, {w} × {h}"),
             ("With hidden message", f"{data['out'].name}, {w} × {h}")),
            data["stats"],
        )

    # ------------------------------------------------------------- Recover

    def _set_result(self, text):
        self.result.configure(state="normal")
        self.result.delete("1.0", "end")
        self.result.insert("1.0", text)
        self.result.configure(state="disabled")
        self.copy_btn.configure(state="normal" if text else "disabled")

    def on_recover(self):
        if self.busy:
            return
        path = self.source.get().strip()
        pw = self.pw_r.get()

        problem = None
        if not path:
            problem = "Choose the picture to read."
        elif not Path(path).is_file():
            problem = "That picture doesn't exist."
        elif not pw:
            problem = "Enter the password."
        if problem:
            self._say(self.rec_status, problem, ERR)
            return

        self._set_result("")

        def work():
            raw = reveal_bytes(Path(path), generators.eratosthenes())
            return decrypt_text(raw.decode("latin-1"), pw)

        def done(text):
            self.pw_r.delete(0, "end")
            self._set_result(text)
            self._say(self.rec_status, "Message unlocked.", OK)

        self._run(self.rec_btn, "Working…", work, done, self.rec_status)

    def on_copy(self):
        text = self.result.get("1.0", "end-1c")
        if text:
            self.clipboard_clear()
            self.clipboard_append(text)
            self._say(self.rec_status, "Copied to the clipboard.", OK)


if __name__ == "__main__":
    try:  # sharper text on high-DPI Windows screens
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    App().mainloop()
