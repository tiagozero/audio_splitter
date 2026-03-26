#!/usr/bin/env python3
"""
Audio Splitter — uses ffmpeg directly (no pydub).
Works with MP3, M4A, M4B, WAV, OGG, FLAC and more.
Reads only metadata on open — never loads the full file into RAM.

New features:
  • Split by chapters
  • Quality selection: 96 / 128 / 320 kbps
  • Split into equal-duration parts (e.g. 1 hour each)
  • Split into N equal parts
"""

import os
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from pathlib import Path
import json


# ── ffmpeg helpers ────────────────────────────────────────────────────────────

def check_ffmpeg():
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return True
    except FileNotFoundError:
        return False


def get_audio_info(path: str) -> dict:
    """Return duration_ms, sample_rate, channels via ffprobe. Fast — no decoding."""
    result = subprocess.run(
        ["ffprobe", "-v", "error",
         "-select_streams", "a:0",
         "-show_entries", "stream=sample_rate,channels",
         "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1",
         path],
        capture_output=True, text=True
    )
    info = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            info[k.strip()] = v.strip()
    duration_ms = int(float(info.get("duration", 0)) * 1000)
    sample_rate = info.get("sample_rate", "?")
    channels    = info.get("channels", "?")
    return {"duration_ms": duration_ms,
            "sample_rate": sample_rate,
            "channels": channels}


def get_chapters(path: str) -> list:
    """Return list of chapter dicts: {title, start_ms, end_ms}."""
    result = subprocess.run(
        ["ffprobe", "-v", "error",
         "-print_format", "json",
         "-show_chapters",
         path],
        capture_output=True, text=True
    )
    try:
        data = json.loads(result.stdout)
        chapters = []
        for ch in data.get("chapters", []):
            start_ms = int(float(ch.get("start_time", 0)) * 1000)
            end_ms   = int(float(ch.get("end_time",   0)) * 1000)
            title    = ch.get("tags", {}).get("title", f"Chapter {ch.get('id', '?') + 1}")
            chapters.append({"title": title, "start_ms": start_ms, "end_ms": end_ms})
        return chapters
    except Exception:
        return []


def export_segment(src: str, out: str, start_ms: int, end_ms: int,
                   fmt: str, quality_kbps: int = None):
    """
    Cut with ffmpeg.
    quality_kbps: None = use smart default / stream-copy;
                  96 / 128 / 320 = force re-encode at that bitrate.
    """
    start_s = start_ms / 1000.0
    dur_s   = (end_ms - start_ms) / 1000.0
    src_ext = Path(src).suffix.lower()

    # Stream-copy is only valid when no quality override is requested
    copy_ok = quality_kbps is None and (
        (src_ext in (".m4b", ".m4a") and fmt in ("m4a",)) or
        (src_ext == f".{fmt}")
    )

    if copy_ok:
        codec_args = ["-c", "copy"]
    elif fmt == "mp3":
        if quality_kbps:
            codec_args = ["-c:a", "libmp3lame", "-b:a", f"{quality_kbps}k"]
        else:
            codec_args = ["-c:a", "libmp3lame", "-q:a", "2"]
    elif fmt == "m4a":
        if quality_kbps:
            codec_args = ["-c:a", "aac", "-b:a", f"{quality_kbps}k"]
        else:
            codec_args = ["-c:a", "aac", "-b:a", "192k"]
    elif fmt == "ogg":
        if quality_kbps:
            codec_args = ["-c:a", "libvorbis", "-b:a", f"{quality_kbps}k"]
        else:
            codec_args = ["-c:a", "libvorbis", "-q:a", "6"]
    elif fmt == "flac":
        codec_args = ["-c:a", "flac"]          # lossless — bitrate ignored
    elif fmt == "wav":
        codec_args = ["-c:a", "pcm_s16le"]     # lossless — bitrate ignored
    else:
        codec_args = []

    cmd = ["ffmpeg", "-y",
           "-ss", str(start_s),
           "-i", src,
           "-t",  str(dur_s),
           ] + codec_args + [out]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-800:])


# ── time helpers ──────────────────────────────────────────────────────────────

def parse_time(s: str) -> int:
    s = s.strip()
    parts = s.split(":")
    try:
        if len(parts) == 3:
            h, m, sec = int(parts[0]), int(parts[1]), float(parts[2])
            return int((h * 3600 + m * 60 + sec) * 1000)
        elif len(parts) == 2:
            m, sec = int(parts[0]), float(parts[1])
            return int((m * 60 + sec) * 1000)
        else:
            return int(float(s) * 1000)
    except ValueError:
        raise ValueError(f"Cannot parse time: '{s}'")


def ms_to_hms(ms: int) -> str:
    ms = max(0, int(ms))
    s  = ms // 1000
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{sec:02d}"


def sanitize_filename(name: str) -> str:
    """Remove / replace characters that are illegal in file names."""
    bad = r'\/:*?"<>|'
    for ch in bad:
        name = name.replace(ch, "_")
    return name.strip()


# ── App ───────────────────────────────────────────────────────────────────────

class AudioSplitterApp(tk.Tk):
    SUPPORTED = (
        ("Audio files", "*.mp3 *.m4a *.m4b *.wav *.ogg *.flac *.aac *.wma *.opus"),
        ("MP3",         "*.mp3"),
        ("M4A / M4B",   "*.m4a *.m4b"),
        ("WAV",         "*.wav"),
        ("All files",   "*.*"),
    )

    BG     = "#0f0f13"
    PANEL  = "#18181f"
    ACCENT = "#c8a96e"
    ACC2   = "#7e6bcc"
    ACC3   = "#6bcc9e"   # green for new feature buttons
    FG     = "#e8e4d9"
    FG2    = "#888"
    ENTRY  = "#22222c"
    BORDER = "#2d2d3a"

    def __init__(self):
        super().__init__()
        self.title("Audio Splitter")
        self.configure(bg=self.BG)
        self.resizable(True, True)
        self.minsize(720, 660)

        if not check_ffmpeg():
            messagebox.showerror(
                "ffmpeg not found",
                "ffmpeg is required.\n\nInstall it with:\n  sudo apt install ffmpeg"
            )
            self.destroy()
            return

        self.src_path    = tk.StringVar()
        self.start_str   = tk.StringVar(value="00:00:00")
        self.end_str     = tk.StringVar(value="00:00:00")
        self.start_ms    = 0
        self.end_ms      = 0
        self.duration_ms = 0
        self.out_format  = tk.StringVar(value="mp3")
        self.quality_var = tk.StringVar(value="128")   # NEW: bitrate selection
        self.status_msg  = tk.StringVar(value="Open an audio file to begin.")
        self.dragging    = None
        self.chapters    = []                           # NEW: chapter list

        # equal-parts & N-parts split vars
        self.eq_hours    = tk.StringVar(value="1")
        self.eq_mins     = tk.StringVar(value="0")
        self.n_parts_var = tk.StringVar(value="5")

        self._build_fonts()
        self._build_ui()

    # ── fonts ─────────────────────────────────────────────────────────────────
    def _build_fonts(self):
        from tkinter import font as tkfont
        self.font_title  = tkfont.Font(family="Georgia",     size=20, weight="bold")
        self.font_label  = tkfont.Font(family="Courier New", size=10)
        self.font_time   = tkfont.Font(family="Courier New", size=13, weight="bold")
        self.font_btn    = tkfont.Font(family="Georgia",     size=11, weight="bold")
        self.font_status = tkfont.Font(family="Courier New", size=9)
        self.font_small  = tkfont.Font(family="Courier New", size=9)

    # ── UI ────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        # Scrollable outer frame
        outer = tk.Frame(self, bg=self.BG)
        outer.pack(fill="both", expand=True)

        canvas_scroll = tk.Canvas(outer, bg=self.BG, bd=0, highlightthickness=0)
        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas_scroll.yview)
        canvas_scroll.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas_scroll.pack(side="left", fill="both", expand=True)

        root = tk.Frame(canvas_scroll, bg=self.BG)
        win_id = canvas_scroll.create_window((0, 0), window=root, anchor="nw")

        def _on_frame_configure(e):
            canvas_scroll.configure(scrollregion=canvas_scroll.bbox("all"))
        def _on_canvas_configure(e):
            canvas_scroll.itemconfig(win_id, width=e.width)
        root.bind("<Configure>", _on_frame_configure)
        canvas_scroll.bind("<Configure>", _on_canvas_configure)

        # mouse-wheel scrolling
        def _on_mousewheel(e):
            canvas_scroll.yview_scroll(int(-1 * (e.delta / 120)), "units")
        canvas_scroll.bind_all("<MouseWheel>", _on_mousewheel)

        pad = dict(padx=28)

        # ── Title ─────────────────────────────────────────────────────────────
        tk.Label(root, text="✦ Audio Splitter", font=self.font_title,
                 bg=self.BG, fg=self.ACCENT, **pad).pack(anchor="w", pady=(24, 0))
        tk.Label(root, text="MP3 · M4A · M4B · WAV · OGG · FLAC  —  powered by ffmpeg",
                 font=self.font_label, bg=self.BG, fg=self.FG2, **pad).pack(anchor="w")

        self._sep(root)

        # ── File row ──────────────────────────────────────────────────────────
        frow = tk.Frame(root, bg=self.BG)
        frow.pack(fill="x", pady=(0, 4), **pad)
        tk.Label(frow, text="FILE", font=self.font_label,
                 bg=self.BG, fg=self.FG2, width=6, anchor="w").pack(side="left")
        tk.Entry(frow, textvariable=self.src_path, state="readonly",
                 bg=self.ENTRY, fg=self.FG, relief="flat",
                 readonlybackground=self.ENTRY, font=self.font_label,
                 bd=0, highlightthickness=1,
                 highlightcolor=self.ACCENT,
                 highlightbackground=self.BORDER
                 ).pack(side="left", fill="x", expand=True, ipady=6, padx=(0, 8))
        self._btn(frow, "Browse…", self._open_file, small=True).pack(side="left")

        self.lbl_info = tk.Label(root, text="", font=self.font_label,
                                 bg=self.BG, fg=self.FG2, anchor="w", **pad)
        self.lbl_info.pack(fill="x", pady=(2, 8))

        self._sep(root)

        # ── Timeline ──────────────────────────────────────────────────────────
        tk.Label(root, text="SELECT RANGE  — drag handles or type times below",
                 font=self.font_label, bg=self.BG, fg=self.FG2, **pad
                 ).pack(anchor="w", pady=(4, 4))

        self.timeline = tk.Canvas(root, bg=self.PANEL, bd=0,
                                  highlightthickness=1,
                                  highlightbackground=self.BORDER,
                                  height=84, cursor="crosshair")
        self.timeline.pack(fill="x", pady=(0, 8), **pad)
        self.timeline.bind("<ButtonPress-1>",   self._canvas_press)
        self.timeline.bind("<B1-Motion>",       self._canvas_drag)
        self.timeline.bind("<ButtonRelease-1>",
                           lambda e: setattr(self, "dragging", None))
        self.timeline.bind("<Configure>",       self._draw_timeline)
        self._draw_timeline()

        trow = tk.Frame(root, bg=self.BG)
        trow.pack(fill="x", pady=(0, 10), **pad)
        for label, var, side in [("START  HH:MM:SS", self.start_str, "left"),
                                  ("END    HH:MM:SS", self.end_str,   "right")]:
            col = tk.Frame(trow, bg=self.BG)
            col.pack(side=side, fill="x", expand=True,
                     padx=(0, 14) if side == "left" else 0)
            tk.Label(col, text=label, font=self.font_label,
                     bg=self.BG, fg=self.FG2).pack(anchor="w")
            e = tk.Entry(col, textvariable=var,
                         bg=self.ENTRY, fg=self.ACCENT,
                         relief="flat", insertbackground=self.ACCENT,
                         font=self.font_time, bd=0,
                         highlightthickness=1,
                         highlightcolor=self.ACCENT,
                         highlightbackground=self.BORDER,
                         justify="center")
            e.pack(fill="x", ipady=8)
            e.bind("<FocusOut>", lambda *_: self._entries_to_handles())
            e.bind("<Return>",   lambda *_: self._entries_to_handles())

        self.lbl_seg = tk.Label(root, text="", font=self.font_label,
                                bg=self.BG, fg=self.ACC2, **pad)
        self.lbl_seg.pack(pady=(0, 8), anchor="w")

        self._sep(root)

        # ── Output format + Quality + Export single segment ────────────────
        tk.Label(root, text="OUTPUT FORMAT  &  QUALITY",
                 font=self.font_label, bg=self.BG, fg=self.FG2, **pad
                 ).pack(anchor="w", pady=(4, 4))

        fmt_row = tk.Frame(root, bg=self.BG)
        fmt_row.pack(fill="x", **pad)

        # Format radios
        fmt_col = tk.Frame(fmt_row, bg=self.BG)
        fmt_col.pack(side="left")
        tk.Label(fmt_col, text="Format:", font=self.font_small,
                 bg=self.BG, fg=self.FG2).pack(anchor="w")
        fmts_row = tk.Frame(fmt_col, bg=self.BG)
        fmts_row.pack(anchor="w")
        for fmt in ("mp3", "m4a", "wav", "ogg", "flac"):
            tk.Radiobutton(fmts_row, text=fmt.upper(), variable=self.out_format,
                           value=fmt, bg=self.BG, fg=self.FG,
                           selectcolor=self.PANEL, activebackground=self.BG,
                           activeforeground=self.ACCENT,
                           font=self.font_label, relief="flat",
                           highlightthickness=0, bd=0,
                           command=self._on_format_change
                           ).pack(side="left", padx=3)

        # Spacer
        tk.Frame(fmt_row, bg=self.BG, width=24).pack(side="left")

        # Quality radios (new)
        self.qual_col = tk.Frame(fmt_row, bg=self.BG)
        self.qual_col.pack(side="left")
        tk.Label(self.qual_col, text="Quality (kbps):", font=self.font_small,
                 bg=self.BG, fg=self.FG2).pack(anchor="w")
        qual_row = tk.Frame(self.qual_col, bg=self.BG)
        qual_row.pack(anchor="w")
        for kbps in ("96", "128", "320"):
            tk.Radiobutton(qual_row, text=kbps, variable=self.quality_var,
                           value=kbps, bg=self.BG, fg=self.FG,
                           selectcolor=self.PANEL, activebackground=self.BG,
                           activeforeground=self.ACCENT,
                           font=self.font_label, relief="flat",
                           highlightthickness=0, bd=0
                           ).pack(side="left", padx=3)
        self.lbl_lossless = tk.Label(qual_row, text="(lossless — bitrate N/A)",
                                     font=self.font_small, bg=self.BG, fg=self.FG2)

        # Export segment button
        self._btn(fmt_row, "✦  Export Segment", self._export, accent=True
                  ).pack(side="right")

        self._on_format_change()   # set initial quality-widget visibility

        self._sep(root)

        # ══════════════════════════════════════════════════════════════════════
        # NEW SECTION: Advanced splitting modes
        # ══════════════════════════════════════════════════════════════════════
        tk.Label(root, text="ADVANCED SPLITTING",
                 font=self.font_label, bg=self.BG, fg=self.FG2, **pad
                 ).pack(anchor="w", pady=(4, 6))

        adv = tk.Frame(root, bg=self.PANEL,
                       highlightthickness=1, highlightbackground=self.BORDER)
        adv.pack(fill="x", **pad, pady=(0, 6))
        adv.columnconfigure(0, weight=1)

        # ── (A) Split by chapters ──────────────────────────────────────────
        row_ch = tk.Frame(adv, bg=self.PANEL)
        row_ch.pack(fill="x", padx=14, pady=10)

        ch_left = tk.Frame(row_ch, bg=self.PANEL)
        ch_left.pack(side="left", fill="x", expand=True)
        tk.Label(ch_left, text="① Split by Chapters",
                 font=self.font_btn, bg=self.PANEL, fg=self.ACCENT).pack(anchor="w")
        self.lbl_chapters = tk.Label(ch_left,
                                     text="(load a file with chapters to enable)",
                                     font=self.font_small, bg=self.PANEL, fg=self.FG2)
        self.lbl_chapters.pack(anchor="w")

        self.btn_chapters = self._btn(row_ch, "Split All Chapters",
                                      self._split_chapters, small=True)
        self.btn_chapters.pack(side="right", padx=(8, 0))
        self.btn_chapters.config(state="disabled")

        self._hsep(adv)

        # ── (B) Equal parts (by duration) ─────────────────────────────────
        row_eq = tk.Frame(adv, bg=self.PANEL)
        row_eq.pack(fill="x", padx=14, pady=10)

        eq_left = tk.Frame(row_eq, bg=self.PANEL)
        eq_left.pack(side="left", fill="x", expand=True)
        tk.Label(eq_left, text="② Split into Equal Duration Parts",
                 font=self.font_btn, bg=self.PANEL, fg=self.ACCENT).pack(anchor="w")

        eq_ctrl = tk.Frame(eq_left, bg=self.PANEL)
        eq_ctrl.pack(anchor="w", pady=(4, 0))
        tk.Label(eq_ctrl, text="Part length:", font=self.font_small,
                 bg=self.PANEL, fg=self.FG2).pack(side="left")
        tk.Entry(eq_ctrl, textvariable=self.eq_hours, width=4,
                 bg=self.ENTRY, fg=self.ACCENT, relief="flat",
                 insertbackground=self.ACCENT, font=self.font_label,
                 bd=0, highlightthickness=1,
                 highlightcolor=self.ACCENT, highlightbackground=self.BORDER,
                 justify="center").pack(side="left", ipady=4, padx=(6, 2))
        tk.Label(eq_ctrl, text="h", font=self.font_small,
                 bg=self.PANEL, fg=self.FG2).pack(side="left")
        tk.Entry(eq_ctrl, textvariable=self.eq_mins, width=4,
                 bg=self.ENTRY, fg=self.ACCENT, relief="flat",
                 insertbackground=self.ACCENT, font=self.font_label,
                 bd=0, highlightthickness=1,
                 highlightcolor=self.ACCENT, highlightbackground=self.BORDER,
                 justify="center").pack(side="left", ipady=4, padx=(6, 2))
        tk.Label(eq_ctrl, text="min", font=self.font_small,
                 bg=self.PANEL, fg=self.FG2).pack(side="left")

        self.lbl_eq_preview = tk.Label(eq_left, text="",
                                       font=self.font_small, bg=self.PANEL, fg=self.FG2)
        self.lbl_eq_preview.pack(anchor="w", pady=(2, 0))

        eq_btn_col = tk.Frame(row_eq, bg=self.PANEL)
        eq_btn_col.pack(side="right", padx=(8, 0))
        self._btn(eq_btn_col, "Preview", self._preview_equal_parts, small=True
                  ).pack(pady=(0, 4))
        self._btn(eq_btn_col, "Split Now", self._split_equal_parts, small=True
                  ).pack()

        self._hsep(adv)

        # ── (C) Split into N files ─────────────────────────────────────────
        row_n = tk.Frame(adv, bg=self.PANEL)
        row_n.pack(fill="x", padx=14, pady=10)

        n_left = tk.Frame(row_n, bg=self.PANEL)
        n_left.pack(side="left", fill="x", expand=True)
        tk.Label(n_left, text="③ Split into N Equal Files",
                 font=self.font_btn, bg=self.PANEL, fg=self.ACCENT).pack(anchor="w")

        n_ctrl = tk.Frame(n_left, bg=self.PANEL)
        n_ctrl.pack(anchor="w", pady=(4, 0))
        tk.Label(n_ctrl, text="Number of parts:", font=self.font_small,
                 bg=self.PANEL, fg=self.FG2).pack(side="left")
        tk.Entry(n_ctrl, textvariable=self.n_parts_var, width=5,
                 bg=self.ENTRY, fg=self.ACCENT, relief="flat",
                 insertbackground=self.ACCENT, font=self.font_label,
                 bd=0, highlightthickness=1,
                 highlightcolor=self.ACCENT, highlightbackground=self.BORDER,
                 justify="center").pack(side="left", ipady=4, padx=(6, 0))

        self.lbl_n_preview = tk.Label(n_left, text="",
                                      font=self.font_small, bg=self.PANEL, fg=self.FG2)
        self.lbl_n_preview.pack(anchor="w", pady=(2, 0))

        n_btn_col = tk.Frame(row_n, bg=self.PANEL)
        n_btn_col.pack(side="right", padx=(8, 0))
        self._btn(n_btn_col, "Preview", self._preview_n_parts, small=True
                  ).pack(pady=(0, 4))
        self._btn(n_btn_col, "Split Now", self._split_n_parts, small=True
                  ).pack()

        self._sep(root)

        # ── Progress & status ─────────────────────────────────────────────
        self.progress = ttk.Progressbar(root, mode="indeterminate")
        self.progress.pack(fill="x", pady=(10, 0), **pad)
        self.progress.pack_forget()

        self.det_progress = ttk.Progressbar(root, mode="determinate")
        self.det_progress.pack(fill="x", pady=(4, 0), **pad)
        self.det_progress.pack_forget()

        tk.Label(root, textvariable=self.status_msg, font=self.font_status,
                 bg=self.BG, fg=self.FG2, anchor="w", **pad
                 ).pack(fill="x", pady=(8, 20))

    # ── widget helpers ────────────────────────────────────────────────────────
    def _sep(self, p):
        tk.Frame(p, bg=self.BORDER, height=1).pack(fill="x", pady=8)

    def _hsep(self, p):
        tk.Frame(p, bg=self.BORDER, height=1).pack(fill="x", padx=14)

    def _btn(self, parent, text, cmd, small=False, accent=False):
        bg  = self.ACCENT if accent else self.ENTRY
        fg  = self.BG     if accent else self.FG
        abg = "#dbbe88"   if accent else self.BORDER
        return tk.Button(parent, text=text, command=cmd,
                         bg=bg, fg=fg, activebackground=abg,
                         activeforeground=self.BG if accent else self.FG,
                         relief="flat", font=self.font_btn,
                         padx=6 if small else 10, pady=4,
                         cursor="hand2", bd=0)

    def _on_format_change(self):
        """Show/hide quality selector based on whether format is lossy."""
        fmt = self.out_format.get()
        lossless = fmt in ("wav", "flac")
        # Hide quality radios for lossless, show note instead
        for w in self.qual_col.winfo_children():
            for child in w.winfo_children() if hasattr(w, 'winfo_children') else []:
                pass
        if lossless:
            self.lbl_lossless.pack(side="left", padx=6)
        else:
            self.lbl_lossless.pack_forget()

    def _get_quality_kbps(self) -> int | None:
        """Return selected kbps as int, or None if format is lossless."""
        if self.out_format.get() in ("wav", "flac"):
            return None
        try:
            return int(self.quality_var.get())
        except ValueError:
            return 128

    # ── file open ─────────────────────────────────────────────────────────────
    def _open_file(self):
        path = filedialog.askopenfilename(filetypes=self.SUPPORTED)
        if not path:
            return
        self.src_path.set(path)
        self.status_msg.set("Reading file info…")
        self.progress.pack(fill="x", pady=(10, 0))
        self.progress.start(12)
        self.update()

        def do_probe():
            try:
                info     = get_audio_info(path)
                chapters = get_chapters(path)
                self.after(0, lambda: self._probe_done(path, info, chapters))
            except Exception as ex:
                err = str(ex)
                self.after(0, lambda: self._probe_error(err))

        threading.Thread(target=do_probe, daemon=True).start()

    def _probe_done(self, path, info, chapters):
        self.progress.stop()
        self.progress.pack_forget()
        self.duration_ms = info["duration_ms"]
        self.start_ms    = 0
        self.end_ms      = self.duration_ms
        self.chapters    = chapters
        self.start_str.set(ms_to_hms(0))
        self.end_str.set(ms_to_hms(self.duration_ms))
        self.lbl_info.config(
            text=f"Duration: {ms_to_hms(self.duration_ms)}   |   "
                 f"{info['sample_rate']} Hz   |   {info['channels']} ch"
        )
        self._draw_timeline()
        self._update_seg_label()

        # Chapter button
        if chapters:
            self.lbl_chapters.config(
                text=f"{len(chapters)} chapter(s) found — will export to a folder"
            )
            self.btn_chapters.config(state="normal")
        else:
            self.lbl_chapters.config(text="No chapters found in this file.")
            self.btn_chapters.config(state="disabled")

        self.status_msg.set(f"Ready: {Path(path).name}")

    def _probe_error(self, msg):
        self.progress.stop()
        self.progress.pack_forget()
        messagebox.showerror("Error reading file", msg)
        self.status_msg.set("Failed to read file.")

    # ── timeline ──────────────────────────────────────────────────────────────
    def _draw_timeline(self, *_):
        c = self.timeline
        c.delete("all")
        W = c.winfo_width()  or 640
        H = c.winfo_height() or 84

        if not self.duration_ms:
            c.create_text(W // 2, H // 2,
                          text="Open an audio file to see the timeline",
                          fill=self.FG2, font=self.font_label)
            return

        c.create_rectangle(0, 0, W, H, fill=self.PANEL, outline="")

        # Chapter markers
        for ch in self.chapters:
            x = self._ms2x(ch["start_ms"], W)
            c.create_line(x, 0, x, H, fill="#444466", width=1, dash=(3, 3))

        for i in range(1, 10):
            x = int(W * i / 10)
            c.create_line(x, 0, x, H, fill=self.BORDER, width=1)
            c.create_text(x, H - 3,
                          text=ms_to_hms(int(self.duration_ms * i / 10)),
                          fill=self.FG2, font=self.font_status, anchor="s")

        xs = self._ms2x(self.start_ms, W)
        xe = self._ms2x(self.end_ms,   W)

        c.create_rectangle(xs, 0, xe, H, fill="#25203a", outline="")

        for x, col in [(xs, self.ACCENT), (xe, self.ACC2)]:
            c.create_line(x, 0, x, H, fill=col, width=2)
            c.create_rectangle(x - 7, H // 2 - 11,
                                x + 7, H // 2 + 11,
                                fill=col, outline="")

    def _ms2x(self, ms, W):
        if not self.duration_ms:
            return 0
        return int(W * ms / self.duration_ms)

    def _x2ms(self, x, W):
        if not self.duration_ms:
            return 0
        return int(self.duration_ms * max(0, min(x, W)) / W)

    def _canvas_press(self, e):
        if not self.duration_ms:
            return
        W  = self.timeline.winfo_width()
        xs = self._ms2x(self.start_ms, W)
        xe = self._ms2x(self.end_ms,   W)
        self.dragging = "start" if abs(e.x - xs) <= abs(e.x - xe) else "end"

    def _canvas_drag(self, e):
        if not self.dragging or not self.duration_ms:
            return
        W  = self.timeline.winfo_width()
        ms = self._x2ms(e.x, W)
        if self.dragging == "start":
            self.start_ms = max(0, min(ms, self.end_ms - 500))
            self.start_str.set(ms_to_hms(self.start_ms))
        else:
            self.end_ms = min(self.duration_ms, max(ms, self.start_ms + 500))
            self.end_str.set(ms_to_hms(self.end_ms))
        self._draw_timeline()
        self._update_seg_label()

    def _entries_to_handles(self):
        if not self.duration_ms:
            return
        try:
            s = parse_time(self.start_str.get())
            e = parse_time(self.end_str.get())
        except ValueError as ex:
            self.status_msg.set(str(ex))
            return
        s = max(0, min(s, self.duration_ms))
        e = max(0, min(e, self.duration_ms))
        if s >= e:
            self.status_msg.set("Start must be before End.")
            return
        self.start_ms = s
        self.end_ms   = e
        self.start_str.set(ms_to_hms(s))
        self.end_str.set(ms_to_hms(e))
        self._draw_timeline()
        self._update_seg_label()

    def _update_seg_label(self):
        if self.duration_ms:
            self.lbl_seg.config(
                text=f"Segment length: {ms_to_hms(self.end_ms - self.start_ms)}"
            )

    # ── export single segment ─────────────────────────────────────────────────
    def _export(self):
        if not self.duration_ms:
            messagebox.showwarning("No file", "Please open an audio file first.")
            return
        if self.start_ms >= self.end_ms:
            messagebox.showwarning("Invalid range", "Start must be before End.")
            return

        fmt     = self.out_format.get()
        quality = self._get_quality_kbps()
        src     = self.src_path.get()
        stem    = Path(src).stem
        s_tag   = ms_to_hms(self.start_ms).replace(":", "-")
        e_tag   = ms_to_hms(self.end_ms).replace(":", "-")
        default_name = f"{stem}_{s_tag}_{e_tag}.{fmt}"

        out_path = filedialog.asksaveasfilename(
            defaultextension=f".{fmt}",
            initialfile=default_name,
            filetypes=[(fmt.upper(), f"*.{fmt}"), ("All files", "*.*")]
        )
        if not out_path:
            return

        self.progress.pack(fill="x", pady=(10, 0))
        self.progress.start(12)
        self.status_msg.set("Exporting with ffmpeg…")
        self.update()

        s_ms, e_ms = self.start_ms, self.end_ms

        def do_export():
            try:
                export_segment(src, out_path, s_ms, e_ms, fmt, quality)
                size_kb = os.path.getsize(out_path) / 1024
                self.after(0, lambda: self._export_done(out_path, size_kb))
            except Exception as ex:
                err = str(ex)
                self.after(0, lambda: self._export_error(err))

        threading.Thread(target=do_export, daemon=True).start()

    def _export_done(self, path, size_kb):
        self.progress.stop()
        self.progress.pack_forget()
        self.status_msg.set(f"✓ Saved: {Path(path).name}  ({size_kb:.0f} KB)")
        messagebox.showinfo("Export complete",
                            f"Segment saved!\n\n{path}\n({size_kb:.0f} KB)")

    def _export_error(self, msg):
        self.progress.stop()
        self.progress.pack_forget()
        self.status_msg.set("Export failed.")
        messagebox.showerror("Export error", msg)

    # ═══════════════════════════════════════════════════════════════════════════
    # NEW — Batch split helpers
    # ═══════════════════════════════════════════════════════════════════════════

    def _check_file_loaded(self) -> bool:
        if not self.duration_ms:
            messagebox.showwarning("No file", "Please open an audio file first.")
            return False
        return True

    def _ask_output_folder(self) -> str | None:
        folder = filedialog.askdirectory(title="Choose output folder")
        return folder or None

    def _run_batch(self, segments: list, folder: str, stem: str):
        """
        segments: list of (filename_no_ext, start_ms, end_ms)
        Runs in a worker thread; updates determinate progress bar.
        """
        fmt     = self.out_format.get()
        quality = self._get_quality_kbps()
        src     = self.src_path.get()
        total   = len(segments)

        self.det_progress["maximum"] = total
        self.det_progress["value"]   = 0
        self.det_progress.pack(fill="x", pady=(4, 0), padx=28)
        self.progress.pack(fill="x", pady=(10, 0), padx=28)
        self.progress.start(12)
        self.status_msg.set(f"Exporting {total} segment(s)…")
        self.update()

        errors = []

        def do_batch():
            for i, (name, s_ms, e_ms) in enumerate(segments, 1):
                out = os.path.join(folder, f"{name}.{fmt}")
                try:
                    export_segment(src, out, s_ms, e_ms, fmt, quality)
                except Exception as ex:
                    errors.append(f"{name}: {ex}")
                self.after(0, lambda v=i: self._batch_tick(v, total))

            self.after(0, lambda: self._batch_done(total, errors, folder))

        threading.Thread(target=do_batch, daemon=True).start()

    def _batch_tick(self, done, total):
        self.det_progress["value"] = done
        self.status_msg.set(f"Exported {done} / {total}…")

    def _batch_done(self, total, errors, folder):
        self.progress.stop()
        self.progress.pack_forget()
        self.det_progress.pack_forget()
        if errors:
            messagebox.showwarning(
                "Partial export",
                f"{total - len(errors)} of {total} exported.\n\nErrors:\n" +
                "\n".join(errors[:5])
            )
        else:
            self.status_msg.set(f"✓ All {total} segments exported to {folder}")
            messagebox.showinfo("Done",
                                f"All {total} segment(s) exported!\n\n{folder}")

    # ── ① Split by chapters ──────────────────────────────────────────────────
    def _split_chapters(self):
        if not self._check_file_loaded():
            return
        if not self.chapters:
            messagebox.showinfo("No chapters", "This file has no embedded chapters.")
            return

        folder = self._ask_output_folder()
        if not folder:
            return

        stem     = Path(self.src_path.get()).stem
        segments = []
        for i, ch in enumerate(self.chapters, 1):
            safe_title = sanitize_filename(ch["title"])
            name       = f"{i:02d} - {safe_title}"
            segments.append((name, ch["start_ms"], ch["end_ms"]))

        self._run_batch(segments, folder, stem)

    # ── ② Split into equal-duration parts ────────────────────────────────────
    def _calc_equal_parts(self):
        """Return (part_ms, segments_list) or raise ValueError."""
        try:
            h = int(self.eq_hours.get() or 0)
            m = int(self.eq_mins.get()  or 0)
        except ValueError:
            raise ValueError("Hours and minutes must be whole numbers.")
        part_ms = (h * 3600 + m * 60) * 1000
        if part_ms <= 0:
            raise ValueError("Part length must be greater than 0.")
        if not self.duration_ms:
            raise ValueError("No file loaded.")

        total    = self.duration_ms
        segments = []
        start    = 0
        idx      = 1
        stem     = Path(self.src_path.get()).stem
        while start < total:
            end  = min(start + part_ms, total)
            name = f"{stem}_part{idx:03d}_{ms_to_hms(start).replace(':','-')}_{ms_to_hms(end).replace(':','-')}"
            segments.append((name, start, end))
            start += part_ms
            idx   += 1
        return part_ms, segments

    def _preview_equal_parts(self):
        if not self._check_file_loaded():
            return
        try:
            part_ms, segs = self._calc_equal_parts()
        except ValueError as ex:
            self.lbl_eq_preview.config(text=str(ex), fg="#cc6666")
            return
        last_dur = (self.duration_ms % part_ms) or part_ms
        self.lbl_eq_preview.config(
            text=f"→ {len(segs)} file(s)  "
                 f"(last part: {ms_to_hms(last_dur)})",
            fg=self.FG2
        )

    def _split_equal_parts(self):
        if not self._check_file_loaded():
            return
        try:
            _, segs = self._calc_equal_parts()
        except ValueError as ex:
            messagebox.showwarning("Invalid input", str(ex))
            return

        folder = self._ask_output_folder()
        if not folder:
            return
        stem = Path(self.src_path.get()).stem
        self._run_batch(segs, folder, stem)

    # ── ③ Split into N equal files ────────────────────────────────────────────
    def _calc_n_parts(self):
        """Return segments list or raise ValueError."""
        try:
            n = int(self.n_parts_var.get())
        except ValueError:
            raise ValueError("Number of parts must be a whole number.")
        if n < 2:
            raise ValueError("Number of parts must be at least 2.")
        if not self.duration_ms:
            raise ValueError("No file loaded.")

        part_ms  = self.duration_ms // n
        segments = []
        stem     = Path(self.src_path.get()).stem
        for i in range(n):
            start = i * part_ms
            end   = self.duration_ms if i == n - 1 else (i + 1) * part_ms
            name  = f"{stem}_part{i+1:03d}_of{n:03d}"
            segments.append((name, start, end))
        return segments

    def _preview_n_parts(self):
        if not self._check_file_loaded():
            return
        try:
            segs = self._calc_n_parts()
        except ValueError as ex:
            self.lbl_n_preview.config(text=str(ex), fg="#cc6666")
            return
        part_ms = self.duration_ms // len(segs)
        self.lbl_n_preview.config(
            text=f"→ {len(segs)} file(s),  each ~{ms_to_hms(part_ms)}",
            fg=self.FG2
        )

    def _split_n_parts(self):
        if not self._check_file_loaded():
            return
        try:
            segs = self._calc_n_parts()
        except ValueError as ex:
            messagebox.showwarning("Invalid input", str(ex))
            return

        folder = self._ask_output_folder()
        if not folder:
            return
        stem = Path(self.src_path.get()).stem
        self._run_batch(segs, folder, stem)


# ── entry ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = AudioSplitterApp()
    app.mainloop()