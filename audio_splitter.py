#!/usr/bin/env python3
"""
Audio Splitter — uses ffmpeg directly (no pydub).
Works with MP3, M4A, M4B, WAV, OGG, FLAC and more.
Reads only metadata on open — never loads the full file into RAM.
"""

import os
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from pathlib import Path


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


def export_segment(src: str, out: str, start_ms: int, end_ms: int, fmt: str):
    """Cut with ffmpeg. Uses stream copy when possible (instant), else re-encodes."""
    start_s = start_ms / 1000.0
    dur_s   = (end_ms - start_ms) / 1000.0
    src_ext = Path(src).suffix.lower()

    copy_ok = (src_ext in (".m4b", ".m4a") and fmt in ("m4a",)) or \
              (src_ext == f".{fmt}")

    if copy_ok:
        codec_args = ["-c", "copy"]
    elif fmt == "mp3":
        codec_args = ["-c:a", "libmp3lame", "-q:a", "2"]
    elif fmt == "m4a":
        codec_args = ["-c:a", "aac", "-b:a", "192k"]
    elif fmt == "ogg":
        codec_args = ["-c:a", "libvorbis", "-q:a", "6"]
    elif fmt == "flac":
        codec_args = ["-c:a", "flac"]
    elif fmt == "wav":
        codec_args = ["-c:a", "pcm_s16le"]
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
    FG     = "#e8e4d9"
    FG2    = "#888"
    ENTRY  = "#22222c"
    BORDER = "#2d2d3a"

    def __init__(self):
        super().__init__()
        self.title("Audio Splitter")
        self.configure(bg=self.BG)
        self.resizable(True, True)
        self.minsize(700, 530)

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
        self.status_msg  = tk.StringVar(value="Open an audio file to begin.")
        self.dragging    = None

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

    # ── UI ────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        root = tk.Frame(self, bg=self.BG)
        root.pack(fill="both", expand=True, padx=28, pady=24)

        tk.Label(root, text="✦ Audio Splitter", font=self.font_title,
                 bg=self.BG, fg=self.ACCENT).pack(anchor="w")
        tk.Label(root, text="MP3 · M4A · M4B · WAV · OGG · FLAC  —  powered by ffmpeg",
                 font=self.font_label, bg=self.BG, fg=self.FG2).pack(anchor="w")

        self._sep(root)

        frow = tk.Frame(root, bg=self.BG)
        frow.pack(fill="x", pady=(0, 4))
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
                                 bg=self.BG, fg=self.FG2, anchor="w")
        self.lbl_info.pack(fill="x", pady=(2, 8))

        self._sep(root)

        tk.Label(root, text="SELECT RANGE  — drag handles or type times below",
                 font=self.font_label, bg=self.BG, fg=self.FG2
                 ).pack(anchor="w", pady=(4, 4))

        self.canvas = tk.Canvas(root, bg=self.PANEL, bd=0,
                                highlightthickness=1,
                                highlightbackground=self.BORDER,
                                height=84, cursor="crosshair")
        self.canvas.pack(fill="x", pady=(0, 8))
        self.canvas.bind("<ButtonPress-1>",   self._canvas_press)
        self.canvas.bind("<B1-Motion>",       self._canvas_drag)
        self.canvas.bind("<ButtonRelease-1>",
                         lambda e: setattr(self, "dragging", None))
        self.canvas.bind("<Configure>",       self._draw_timeline)
        self._draw_timeline()

        trow = tk.Frame(root, bg=self.BG)
        trow.pack(fill="x", pady=(0, 10))
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
                                bg=self.BG, fg=self.ACC2)
        self.lbl_seg.pack(pady=(0, 8))

        self._sep(root)

        brow = tk.Frame(root, bg=self.BG)
        brow.pack(fill="x", pady=(8, 0))

        tk.Label(brow, text="OUTPUT FORMAT", font=self.font_label,
                 bg=self.BG, fg=self.FG2).pack(side="left", padx=(0, 10))
        for fmt in ("mp3", "m4a", "wav", "ogg", "flac"):
            tk.Radiobutton(brow, text=fmt.upper(), variable=self.out_format,
                           value=fmt, bg=self.BG, fg=self.FG,
                           selectcolor=self.PANEL, activebackground=self.BG,
                           activeforeground=self.ACCENT,
                           font=self.font_label, relief="flat",
                           highlightthickness=0, bd=0
                           ).pack(side="left", padx=3)

        self._btn(brow, "✦  Export Segment", self._export, accent=True
                  ).pack(side="right")

        self.progress = ttk.Progressbar(root, mode="indeterminate")
        self.progress.pack(fill="x", pady=(10, 0))
        self.progress.pack_forget()

        tk.Label(root, textvariable=self.status_msg, font=self.font_status,
                 bg=self.BG, fg=self.FG2, anchor="w"
                 ).pack(fill="x", pady=(8, 0))

    def _sep(self, p):
        tk.Frame(p, bg=self.BORDER, height=1).pack(fill="x", pady=8)

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
                info = get_audio_info(path)
                self.after(0, lambda: self._probe_done(path, info))
            except Exception as ex:
                err = str(ex)
                self.after(0, lambda: self._probe_error(err))

        threading.Thread(target=do_probe, daemon=True).start()

    def _probe_done(self, path, info):
        self.progress.stop()
        self.progress.pack_forget()
        self.duration_ms = info["duration_ms"]
        self.start_ms    = 0
        self.end_ms      = self.duration_ms
        self.start_str.set(ms_to_hms(0))
        self.end_str.set(ms_to_hms(self.duration_ms))
        self.lbl_info.config(
            text=f"Duration: {ms_to_hms(self.duration_ms)}   |   "
                 f"{info['sample_rate']} Hz   |   {info['channels']} ch"
        )
        self._draw_timeline()
        self._update_seg_label()
        self.status_msg.set(f"Ready: {Path(path).name}")

    def _probe_error(self, msg):
        self.progress.stop()
        self.progress.pack_forget()
        messagebox.showerror("Error reading file", msg)
        self.status_msg.set("Failed to read file.")

    # ── timeline ──────────────────────────────────────────────────────────────
    def _draw_timeline(self, *_):
        c = self.canvas
        c.delete("all")
        W = c.winfo_width()  or 640
        H = c.winfo_height() or 84

        if not self.duration_ms:
            c.create_text(W // 2, H // 2,
                          text="Open an audio file to see the timeline",
                          fill=self.FG2, font=self.font_label)
            return

        c.create_rectangle(0, 0, W, H, fill=self.PANEL, outline="")

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
        W  = self.canvas.winfo_width()
        xs = self._ms2x(self.start_ms, W)
        xe = self._ms2x(self.end_ms,   W)
        self.dragging = "start" if abs(e.x - xs) <= abs(e.x - xe) else "end"

    def _canvas_drag(self, e):
        if not self.dragging or not self.duration_ms:
            return
        W  = self.canvas.winfo_width()
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

    # ── export ────────────────────────────────────────────────────────────────
    def _export(self):
        if not self.duration_ms:
            messagebox.showwarning("No file", "Please open an audio file first.")
            return
        if self.start_ms >= self.end_ms:
            messagebox.showwarning("Invalid range", "Start must be before End.")
            return

        fmt  = self.out_format.get()
        src  = self.src_path.get()
        stem = Path(src).stem
        s_tag = ms_to_hms(self.start_ms).replace(":", "-")
        e_tag = ms_to_hms(self.end_ms).replace(":", "-")
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
                export_segment(src, out_path, s_ms, e_ms, fmt)
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


# ── entry ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = AudioSplitterApp()
    app.mainloop()