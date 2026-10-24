#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""校园网自动登录 · 图形界面（带动效版）。

只用 Python 标准库的 tkinter，不引入任何第三方依赖，和命令行版共用同一份配置
（``%APPDATA%\\campusnet\\config.json``）与同一份加密密码，两边互不冲突。

动效部分全部用 Canvas 自绘 + ``after()`` 逐帧插值实现：
* 窗口启动淡入
* 状态圆点：呼吸脉冲、颜色渐变、成功涟漪
* 圆角按钮：悬停/按下颜色过渡
* 开机自启：滑动开关
* 标签页：下划线平滑滑动
* 连接中：动态省略号
"""

from __future__ import annotations

import math
import os
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tkinter as tk  # noqa: E402
from tkinter import messagebox, ttk  # noqa: E402

import autologin as core  # noqa: E402

APP_TITLE = "szu-netlogin"
SUBTITLE = "Shenzhen University · 校园网自动登录"
VERSION = "1.2.2"

BG = "#f3f5f9"
CARD = "#ffffff"
TEXT = "#1f2937"
MUTED = "#6b7280"
LINE = "#e5e7eb"
OK = "#16a34a"
BAD = "#dc2626"
WARN = "#d97706"
ACCENT = "#2563eb"
ACCENT_DARK = "#1d4ed8"
GRAY = "#cbd5e1"

PROVIDER_CHOICES = [
    ("ePortal 新版（推荐）", "eportal_portal"),
    ("自动识别（auto）", "auto"),
    ("ePortal 通用", "eportal"),
    ("Dr.COM 经典", "drcom"),
    ("深澜 SRun", "srun"),
    ("锐捷 Ruijie", "ruijie"),
    ("自定义", "custom"),
]
PROVIDER_LABELS = [label for label, _code in PROVIDER_CHOICES]
PROVIDER_LABEL_TO_CODE = dict(PROVIDER_CHOICES)
PROVIDER_CODE_TO_LABEL = {code: label for label, code in PROVIDER_CHOICES}

FONT = "Microsoft YaHei UI"


# --------------------------------------------------------------------------- 动画工具
def ease_out(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def hex_to_rgb(color: str):
    color = color.lstrip("#")
    return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))


def rgb_to_hex(rgb) -> str:
    return "#{:02x}{:02x}{:02x}".format(*(max(0, min(255, int(round(c)))) for c in rgb))


def mix(c1: str, c2: str, t: float) -> str:
    a, b = hex_to_rgb(c1), hex_to_rgb(c2)
    return rgb_to_hex(tuple(lerp(a[i], b[i], t) for i in range(3)))


def round_points(x1, y1, x2, y2, r):
    return [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]


class Animator:
    """极简补间：按帧回调 0→1 的缓动进度。"""

    def __init__(self, widget: tk.Misc) -> None:
        self.widget = widget
        self._jobs = {}

    def animate(self, key: str, duration: int, on_frame, on_done=None, fps: int = 60) -> None:
        self.cancel(key)
        steps = max(1, int(duration / (1000 / fps)))

        def tick(i=0):
            t = ease_out(i / steps)
            try:
                on_frame(t)
            except tk.TclError:
                return
            if i >= steps:
                if on_done:
                    on_done()
                self._jobs.pop(key, None)
                return
            self._jobs[key] = self.widget.after(int(1000 / fps), lambda: tick(i + 1))

        tick()

    def cancel(self, key: str) -> None:
        job = self._jobs.pop(key, None)
        if job:
            try:
                self.widget.after_cancel(job)
            except tk.TclError:
                pass


class RoundedButton(tk.Canvas):
    """圆角按钮：悬停变亮、按下变深，颜色平滑过渡。"""

    def __init__(self, master, text, command, width=120, height=40,
                 fill=ACCENT, hover=None, press=None, fg="#ffffff",
                 font=(FONT, 10, "bold"), radius=11, bg=BG, outline=""):
        super().__init__(master, width=width, height=height, bg=bg,
                         highlightthickness=0, cursor="hand2")
        self._command = command
        # 注意：不能用 self._w / self._h，那是 tkinter 内部的控件路径名
        self._bw, self._bh, self._br = width, height, radius
        self._fill = fill
        self._hover = hover or mix(fill, "#ffffff", 0.16)
        self._press = press or mix(fill, "#000000", 0.18)
        self._fg = fg
        self._outline = outline
        self._enabled = True
        self._current = fill
        self._anim = Animator(self)

        self._shape = self.create_polygon(
            round_points(1, 1, width - 1, height - 1, radius),
            smooth=True, splinesteps=24, fill=fill, outline=outline, width=1)
        self._label = self.create_text(width / 2, height / 2, text=text,
                                       fill=fg, font=font)

        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)

    def _paint(self, color: str) -> None:
        self._current = color
        self.itemconfigure(self._shape, fill=color)

    def _glide(self, target: str, duration=120) -> None:
        start = self._current
        self._anim.animate("color", duration, lambda t: self._paint(mix(start, target, t)))

    def _on_enter(self, _e):
        if self._enabled:
            self._glide(self._hover)

    def _on_leave(self, _e):
        if self._enabled:
            self._glide(self._fill)

    def _on_press(self, _e):
        if self._enabled:
            self._glide(self._press, 70)
            self.move(self._label, 0, 1)

    def _on_release(self, _e):
        if not self._enabled:
            return
        self.move(self._label, 0, -1)
        self._glide(self._hover, 90)
        self.event_generate("<<Click>>")
        if self._command:
            self.after(10, self._command)

    def set_text(self, text: str) -> None:
        self.itemconfigure(self._label, text=text)

    def set_enabled(self, enabled: bool) -> None:
        if self._enabled == enabled:
            return
        self._enabled = enabled
        self.configure(cursor="hand2" if enabled else "arrow")
        color = self._fill if enabled else mix(GRAY, "#ffffff", 0.35)
        self._glide(color, 140)
        self.itemconfigure(self._label, fill=self._fg if enabled else "#94a3b8")


class ToggleSwitch(tk.Canvas):
    """滑动开关，切换时滑块与底色平滑过渡。"""

    def __init__(self, master, value=False, command=None, bg=CARD, width=50, height=26):
        super().__init__(master, width=width, height=height, bg=bg, highlightthickness=0,
                         cursor="hand2")
        self._sw, self._sh = width, height
        self._value = bool(value)
        self._command = command
        self._anim = Animator(self)
        self._knob_x = self._target_x()

        self._track = self.create_polygon(
            round_points(1, 1, width - 1, height - 1, height / 2 - 1),
            smooth=True, splinesteps=24,
            fill=OK if value else GRAY, outline="")
        r = height / 2 - 4
        self._knob = self.create_oval(0, 0, 0, 0, fill="#ffffff", outline="", width=1)

        self._draw_knob(self._knob_x, r)
        self.bind("<Button-1>", self._toggle)

    def _target_x(self) -> float:
        return self._sw - (self._sh / 2) if self._value else (self._sh / 2)

    def _draw_knob(self, cx: float, r: float) -> None:
        cy = self._sh / 2
        self.coords(self._knob, cx - r, cy - r, cx + r, cy + r)

    def _toggle(self, _e=None):
        self._value = not self._value
        start = self._knob_x
        target = self._target_x()
        r = self._sh / 2 - 4
        start_color = OK if not self._value else GRAY
        end_color = OK if self._value else GRAY

        def frame(t):
            self._knob_x = lerp(start, target, t)
            self._draw_knob(self._knob_x, r)
            self.itemconfigure(self._track, fill=mix(start_color, end_color, t))

        self._anim.animate("knob", 180, frame)
        if self._command:
            self.after(30, self._command)

    @property
    def value(self) -> bool:
        return self._value

    def set_value(self, value: bool) -> None:
        if bool(value) != self._value:
            self._toggle()


class RoundedInput(tk.Canvas):
    """圆角输入框：聚焦时边框渐变 + 外发光，出错时红色闪烁。"""

    def __init__(self, master, width=300, height=40, radius=10, bg=CARD,
                 font=(FONT, 10), show="", on_enter=None):
        super().__init__(master, width=width, height=height, bg=bg, highlightthickness=0)
        self._cw, self._ch, self._r = width, height, radius
        self._bg = bg
        self._anim = Animator(self)
        self._current_border = LINE
        self._glow_color = bg

        self._glow = self.create_polygon(
            round_points(2, 2, width - 2, height - 2, radius),
            smooth=True, splinesteps=20, fill="", outline=bg, width=3)
        self._border = self.create_polygon(
            round_points(1, 1, width - 1, height - 1, radius),
            smooth=True, splinesteps=20, fill="#ffffff", outline=LINE, width=1)

        self.entry = tk.Entry(self, relief="flat", bd=0, bg="#ffffff", fg=TEXT,
                              insertbackground=ACCENT, highlightthickness=0,
                              font=font, show=show)
        self.create_window(13, height // 2, window=self.entry, anchor="w",
                           width=width - 26, height=height - 12)
        self.entry.bind("<FocusIn>", self._focus_in)
        self.entry.bind("<FocusOut>", self._focus_out)
        if on_enter:
            self.entry.bind("<Return>", lambda _e: on_enter())
        self.bind("<Button-1>", lambda _e: self.entry.focus_set())

    def _glide(self, border_target, glow_target, duration=180, done=None):
        b0, g0 = self._current_border, self._glow_color

        def frame(t):
            self._current_border = mix(b0, border_target, t)
            self._glow_color = mix(g0, glow_target, t)
            self.itemconfigure(self._border, outline=self._current_border)
            self.itemconfigure(self._glow, outline=self._glow_color)

        self._anim.animate("border", duration, frame, on_done=done)

    def _focus_in(self, _e=None):
        self._glide(ACCENT, mix(self._bg, ACCENT, 0.30))

    def _focus_out(self, _e=None):
        self._glide(LINE, self._bg)

    def flash_error(self) -> None:
        """校验失败时红一下，比弹窗更轻。"""
        def frame(t):
            k = 1 - abs(2 * t - 1)
            self.itemconfigure(self._border, outline=mix(LINE, BAD, k))

        def restore():
            target = ACCENT if self.entry.focus_get() is self.entry else LINE
            glow = mix(self._bg, ACCENT, 0.30) if self.entry.focus_get() is self.entry else self._bg
            self._glide(target, glow, 160)

        self._anim.animate("flash", 520, frame, on_done=restore)

    def flash_ok(self) -> None:
        def frame(t):
            k = 1 - abs(2 * t - 1)
            self.itemconfigure(self._border, outline=mix(LINE, OK, k))

        def restore():
            self._glide(LINE, self._bg, 220)

        self._anim.animate("ok", 620, frame, on_done=restore)

    # --- 常用代理 ---
    def get(self) -> str:
        return self.entry.get()

    def set(self, text: str) -> None:
        self.entry.delete(0, "end")
        self.entry.insert(0, text or "")

    def clear(self) -> None:
        self.entry.delete(0, "end")

    def set_show(self, show: str) -> None:
        self.entry.configure(show=show)

    def focus_set(self) -> None:
        self.entry.focus_set()


class TabBar(tk.Canvas):
    """标签栏：选中项下方有一条平滑滑动的下划线。"""

    def __init__(self, master, labels, command, bg=CARD, height=46):
        super().__init__(master, height=height, bg=bg, highlightthickness=0)
        self._labels = labels
        self._command = command
        self._index = 0
        self._tab_width = 118
        self._texts = []
        self._underline_x = 0

        for i, label in enumerate(labels):
            x = 26 + i * self._tab_width
            self._texts.append(self.create_text(
                x + self._tab_width / 2, height / 2 - 2, text=label,
                fill=MUTED, font=(FONT, 10)))
            self.tag_bind(self._texts[-1], "<Button-1>", lambda _e, idx=i: self.select(idx))
            self.tag_bind(self._texts[-1], "<Enter>", lambda _e, idx=i: self._hover(idx))
            self.tag_bind(self._texts[-1], "<Leave>", lambda _e, idx=i: self._hover(-1))

        self._underline = self.create_polygon(
            round_points(0, 0, self._tab_width - 36, 3, 1.5), smooth=True, splinesteps=12,
            fill=ACCENT, outline="")
        self._base_y = height - 7
        self._place_underline(0, animate=False)

    def _hover(self, idx: int) -> None:
        for i, item in enumerate(self._texts):
            if i == self._index:
                continue
            self.itemconfigure(item, fill=TEXT if i == idx else MUTED)

    def _place_underline(self, idx: int, animate=True) -> None:
        width = 68
        target_x = 26 + idx * self._tab_width + self._tab_width / 2 - width / 2

        def draw(x):
            self.coords(self._underline, *round_points(
                x, self._base_y, x + width, self._base_y + 3, 1.5))

        if not animate:
            self._underline_x = target_x
            draw(target_x)
            return
        start = self._underline_x
        anim = Animator(self)
        anim.animate("line", 220, lambda t: draw(lerp(start, target_x, t)))
        self._underline_x = target_x

    def select(self, idx: int) -> None:
        if idx == self._index:
            return
        self._index = idx
        for i, item in enumerate(self._texts):
            self.itemconfigure(item, fill=ACCENT if i == idx else MUTED,
                               font=(FONT, 10, "bold") if i == idx else (FONT, 10))
        self._place_underline(idx)
        if self._command:
            self._command(idx)


class StatusDot(tk.Canvas):
    """状态圆点：呼吸脉冲 + 颜色渐变 + 成功涟漪。"""

    def __init__(self, master, size=72, bg=CARD):
        super().__init__(master, width=size, height=size, bg=bg, highlightthickness=0)
        self._size = size
        self._c = size / 2
        self._base_r = size * 0.30
        self._bg = bg
        self._color = GRAY
        self._target = GRAY
        self._pulse = False
        self._phase = 0.0
        self._anim = Animator(self)

        self._ripple = self.create_oval(0, 0, 0, 0, outline="", width=2)
        self._halo = self.create_oval(0, 0, 0, 0, outline="", width=0)
        self._dot = self.create_oval(0, 0, 0, 0, fill=GRAY, outline="")
        self._gloss = self.create_oval(0, 0, 0, 0, fill="#ffffff", outline="", stipple="gray50")

        self.bind("<Configure>", lambda _e: self._draw(self._base_r))
        self._draw(self._base_r)
        self._loop()

    def _draw(self, radius: float, halo_extra: float = 0.0) -> None:
        c = self._c
        self.coords(self._dot, c - radius, c - radius, c + radius, c + radius)
        hr = radius + 7 + halo_extra
        self.coords(self._halo, c - hr, c - hr, c + hr, c + hr)
        self.itemconfigure(self._halo, outline=mix(self._color, self._bg, 0.70), width=2)
        gr = radius * 0.42
        self.coords(self._gloss, c - gr * 0.9, c - radius * 0.62,
                    c + gr * 0.5, c - radius * 0.62 + gr * 1.1)

    def _loop(self) -> None:
        if self._pulse:
            self._phase += 0.16
            scale = 1 + 0.10 * math.sin(self._phase)
            extra = 3.5 * (math.sin(self._phase) + 1) / 2
            self._draw(self._base_r * scale, extra)
            self.itemconfigure(self._halo, outline=mix(self._color, self._bg, 0.55))
        self.after(45, self._loop)

    def set_color(self, color: str, pulse: bool = False, ripple: bool = False) -> None:
        self._pulse = pulse
        start = self._color
        self._target = color
        self._anim.animate("color", 320, lambda t: self._apply_color(mix(start, color, t)))
        if ripple:
            self._start_ripple(color)
        if not pulse:
            self._draw(self._base_r, 0)

    def _apply_color(self, color: str) -> None:
        self._color = color
        self.itemconfigure(self._dot, fill=color)
        if not self._pulse:
            self._draw(self._base_r, 0)

    def _start_ripple(self, color: str) -> None:
        c = self._c

        def frame(t):
            r = lerp(self._base_r, self._base_r * 2.1, t)
            self.coords(self._ripple, c - r, c - r, c + r, c + r)
            self.itemconfigure(self._ripple, outline=mix(color, self._bg, t), width=max(1, 3 * (1 - t)))

        self._anim.animate("ripple", 620, frame,
                           on_done=lambda: self.itemconfigure(self._ripple, outline=""))


def _icon_path() -> Path:
    """图标文件位置：源码运行时在 assets/，打包后在 _MEIPASS/assets/。"""
    base = Path(getattr(sys, "_MEIPASS", ROOT))
    return base / "assets" / "icon.ico"


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self._enable_dpi()
        self.title(APP_TITLE)
        self._apply_icon()
        self.configure(bg=BG)
        self.geometry("880x700")
        self.minsize(800, 640)
        self._center()

        self._busy = False
        self._anim = Animator(self)
        self._dots_job = None
        self._dot_phase = 0

        self._build_style()
        self._build_header()
        self._build_status_card()
        self._build_actions()
        self._build_tabs()
        self._build_footer()

        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self._fade_in()
        self.after(260, self.refresh_status)
        self.after(900, self._auto_fill_from_config)

    # ------------------------------------------------------------------ 基础
    @staticmethod
    def _enable_dpi() -> None:
        if os.name != "nt":
            return
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:  # noqa: BLE001
            pass

    def _apply_icon(self) -> None:
        """设置标题栏 / 任务栏图标（Tk 默认是它的羽毛图标，必须显式覆盖）。"""
        if os.name == "nt":
            try:
                import ctypes

                # 让任务栏把它当成独立应用，而不是"python.exe"或空白图标
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("CampusNetAutoLogin")
            except Exception:  # noqa: BLE001
                pass
        path = _icon_path()
        if not path.exists():
            return
        try:
            self.iconbitmap(default=str(path))
        except tk.TclError:
            try:
                self.iconbitmap(str(path))
            except tk.TclError:
                pass
        try:
            self.iconname(APP_TITLE)
        except tk.TclError:
            pass

    def _center(self) -> None:
        self.update_idletasks()
        w, h = 880, 700
        x = max(0, (self.winfo_screenwidth() - w) // 2)
        y = max(0, (self.winfo_screenheight() - h) // 3)
        self.geometry("{}x{}+{}+{}".format(w, h, x, y))

    def _fade_in(self) -> None:
        try:
            self.attributes("-alpha", 0.0)
        except tk.TclError:
            return

        def frame(t):
            try:
                self.attributes("-alpha", max(0.02, t))
            except tk.TclError:
                pass

        self.attributes("-alpha", 0.0)
        self._anim.animate("fade", 280, frame,
                           on_done=lambda: self.attributes("-alpha", 1.0))

    def _build_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TButton", font=(FONT, 10), padding=(12, 6))
        style.configure("TEntry", padding=5)
        style.configure("TCheckbutton", background=CARD, font=(FONT, 10))
        style.configure("TCombobox", padding=3)

    # ------------------------------------------------------------------ 布局
    def _build_header(self) -> None:
        bar = tk.Frame(self, bg=CARD, height=66)
        bar.pack(fill="x")
        bar.pack_propagate(False)

        tk.Label(bar, text=APP_TITLE, bg=CARD, fg=TEXT,
                 font=(FONT, 17, "bold")).pack(side="left", padx=(24, 10), pady=15)
        tk.Label(bar, text=SUBTITLE, bg=CARD, fg=MUTED,
                 font=(FONT, 9)).pack(side="left", pady=(22, 0))

        tk.Frame(self, bg=LINE, height=1).pack(fill="x")

    def _build_status_card(self) -> None:
        wrap = tk.Frame(self, bg=BG)
        wrap.pack(fill="x", padx=18, pady=(16, 8))

        self._card = tk.Frame(wrap, bg=CARD, highlightbackground=LINE, highlightthickness=1)
        self._card.pack(fill="x")

        left = tk.Frame(self._card, bg=CARD)
        left.pack(side="left", padx=(20, 6), pady=14)

        self.dot = StatusDot(left)
        self.dot.pack(side="left", padx=(0, 16))

        info = tk.Frame(left, bg=CARD)
        info.pack(side="left")

        self.state_label = tk.Label(info, text="检查中…", bg=CARD, fg=TEXT,
                                    font=(FONT, 18, "bold"))
        self.state_label.pack(anchor="w")
        self.detail_label = tk.Label(info, text="正在读取网络状态", bg=CARD, fg=MUTED,
                                     font=(FONT, 9))
        self.detail_label.pack(anchor="w", pady=(3, 0))

        # 右侧三个小指标，横向铺开，避免中间出现一大片空白
        right = tk.Frame(self._card, bg=CARD)
        right.pack(side="right", padx=(6, 22), pady=14)

        self.ip_label = self._stat(right, "本机 IP", "—", column=0, width=112)
        self.portal_label = self._stat(right, "认证门户", "—", column=1, width=170)
        self.time_label = self._stat(right, "更新时间", "—", column=2, width=86)

    def _stat(self, parent, caption: str, value: str, column: int, width: int = 110) -> tk.Label:
        box = tk.Frame(parent, bg=CARD, width=width, height=40)
        box.grid(row=0, column=column, sticky="w", padx=(0, 14))
        box.grid_propagate(False)
        tk.Label(box, text=caption, bg=CARD, fg=MUTED, font=(FONT, 8)).pack(anchor="w")
        label = tk.Label(box, text=value, bg=CARD, fg=TEXT, font=(FONT, 9))
        label.pack(anchor="w", pady=(2, 0))
        return label

    def _build_actions(self) -> None:
        bar = tk.Frame(self, bg=BG)
        bar.pack(fill="x", padx=18, pady=(0, 10))

        self.btn_login = RoundedButton(bar, "立即连接", lambda: self.do_login(False),
                                       width=124, height=40, fill=ACCENT)
        self.btn_login.pack(side="left")

        self.btn_force = RoundedButton(bar, "强制重新认证", lambda: self.do_login(True),
                                       width=138, height=40, fill="#ffffff",
                                       hover="#f4f7ff", press="#e8eefc",
                                       fg=ACCENT_DARK, font=(FONT, 10, "bold"),
                                       outline="#c7d2fe", bg=BG)
        self.btn_force.pack(side="left", padx=(10, 0))

        self.btn_refresh = RoundedButton(bar, "刷新状态", self.refresh_status,
                                         width=104, height=40, fill="#ffffff",
                                         hover="#f8fafc", press="#eef2f7",
                                         fg=TEXT, font=(FONT, 10),
                                         outline="#dbe3ec", bg=BG)
        self.btn_refresh.pack(side="left", padx=(10, 0))

        right = tk.Frame(bar, bg=BG)
        right.pack(side="right", padx=(0, 2))
        tk.Frame(right, bg=LINE, width=1, height=24).pack(side="left", padx=(0, 16), pady=(8, 0))
        tk.Label(right, text="开机自动登录", bg=BG, fg=TEXT,
                 font=(FONT, 10)).pack(side="left", padx=(0, 10))
        self.auto_switch = ToggleSwitch(right, value=core.autostart_installed(),
                                        command=self.toggle_autostart, bg=BG)
        self.auto_switch.pack(side="left", pady=(7, 0))

        # 进度条只在连接时出现，空闲时不显示，避免看起来像残缺控件
        self.progress = ttk.Progressbar(bar, mode="indeterminate", length=110)

    def _build_tabs(self) -> None:
        holder = tk.Frame(self, bg=CARD, highlightbackground=LINE, highlightthickness=1)
        holder.pack(fill="both", expand=True, padx=18, pady=(0, 8))

        self.tabbar = TabBar(holder, ["账号设置", "运行日志", "关于"], self._switch_tab, bg=CARD)
        self.tabbar.pack(fill="x", padx=6, pady=(4, 0))

        tk.Frame(holder, bg=LINE, height=1).pack(fill="x", padx=14)

        self.pages_holder = tk.Frame(holder, bg=CARD)
        self.pages_holder.pack(fill="both", expand=True, padx=4, pady=(2, 6))

        self.pages = []
        for builder in (self._build_account_page, self._build_log_page, self._build_about_page):
            page = tk.Frame(self.pages_holder, bg=CARD)
            page.place(relx=0, rely=0, relwidth=1, relheight=1)
            builder(page)
            self.pages.append(page)
        self.pages[0].lift()

    def _switch_tab(self, index: int) -> None:
        page = self.pages[index]
        page.lift()
        # 内容轻微上浮，做一个"翻上来"的手感
        page.place_configure(rely=0.012)
        self._anim.animate("page", 160, lambda t: page.place_configure(rely=lerp(0.012, 0, t)))

    # ------------------------------------------------------------------ 各页
    def _build_account_page(self, page: tk.Frame) -> None:
        wrapper = tk.Frame(page, bg=CARD)
        wrapper.pack(fill="both", expand=True, padx=20, pady=16)
        wrapper.grid_rowconfigure(0, weight=1)
        wrapper.grid_columnconfigure(1, weight=1)

        form_card = tk.Frame(wrapper, bg=CARD, highlightbackground=LINE, highlightthickness=1)
        form_card.grid(row=0, column=0, sticky="nsw")

        form = tk.Frame(form_card, bg=CARD)
        form.pack(padx=20, pady=14, anchor="n")

        def label(text, row):
            tk.Label(form, text=text, bg=CARD, fg=TEXT, font=(FONT, 10)).grid(
                row=row, column=0, sticky="w", pady=(0, 2), padx=(0, 16))

        label("校园网账号", 0)
        self.entry_user = RoundedInput(form, width=300, height=38, bg=CARD,
                                       on_enter=self.save_settings)
        self.entry_user.grid(row=0, column=1, sticky="w", pady=(0, 10))

        label("密码", 1)
        self.entry_pass = RoundedInput(form, width=300, height=38, bg=CARD, show="*",
                                       on_enter=self.save_settings)
        self.entry_pass.grid(row=1, column=1, sticky="w")
        self.show_pass = tk.BooleanVar(value=False)
        ttk.Checkbutton(form, text="显示", variable=self.show_pass,
                        command=self._toggle_pass).grid(row=1, column=2, sticky="w", padx=(10, 0))
        tk.Label(form, text="留空表示不修改；密码由 Windows 加密保存，只在本机可用",
                 bg=CARD, fg=MUTED, font=(FONT, 9)).grid(
            row=2, column=1, columnspan=2, sticky="w", pady=(2, 10))

        label("门户地址", 3)
        self.entry_portal = RoundedInput(form, width=300, height=38, bg=CARD,
                                         on_enter=self.save_settings)
        self.entry_portal.grid(row=3, column=1, sticky="w", pady=(0, 10))

        label("认证方式", 4)
        self.combo_provider = ttk.Combobox(form, width=33, values=PROVIDER_LABELS,
                                           state="readonly", font=(FONT, 10))
        self.combo_provider.grid(row=4, column=1, sticky="w")
        tk.Label(form, text="不确定就选「自动识别」，程序会自己判断学校用哪套系统",
                 bg=CARD, fg=MUTED, font=(FONT, 9)).grid(
            row=5, column=1, columnspan=2, sticky="w", pady=(2, 12))

        actions = tk.Frame(form, bg=CARD)
        actions.grid(row=6, column=1, columnspan=2, sticky="w")
        RoundedButton(actions, "保存设置", self.save_settings, width=118, height=38,
                      fill=ACCENT, bg=CARD).pack(side="left")
        self.account_hint = tk.Label(actions, text="", bg=CARD, fg="#475569", font=(FONT, 9))
        self.account_hint.pack(side="left", padx=(14, 0))

        # 右侧提示卡：和左侧表单卡同款边框，两栏视觉统一
        tip = tk.Frame(wrapper, bg="#f8fafc", highlightbackground=LINE, highlightthickness=1)
        tip.grid(row=0, column=1, sticky="nsew", padx=(18, 0))

        tk.Label(tip, text="小提示", bg="#f8fafc", fg=TEXT,
                 font=(FONT, 10, "bold")).pack(anchor="w", padx=16, pady=(14, 8))
        for line in (
            "账号就是学号，一般不需要运营商后缀",
            "密码由 Windows 加密保存，换电脑或重装系统需要重新填写",
            "保存后可直接点「重新连接」验证一次",
            "想让开机自动联网，把上方「开机自动登录」开关打开",
            "掉线后会自动重连，通常不用手动操作",
            "换到热点等非校园网时不会发送账号密码",
            "出问题先看「运行日志」，里面有每一步的报错",
        ):
            tk.Label(tip, text="· " + line, bg="#f8fafc", fg="#4b5563", font=(FONT, 9),
                     justify="left", wraplength=300).pack(anchor="w", padx=16, pady=3)

    def _build_log_page(self, page: tk.Frame) -> None:
        bar = tk.Frame(page, bg=CARD)
        bar.pack(fill="x", padx=18, pady=(12, 6))
        ttk.Button(bar, text="刷新", command=self.load_log).pack(side="left")
        ttk.Button(bar, text="打开日志文件", command=self.open_log_file).pack(side="left", padx=8)

        holder = tk.Frame(page, bg=CARD)
        holder.pack(fill="both", expand=True, padx=18, pady=(0, 14))
        self.log_text = tk.Text(holder, wrap="word", bg="#0f172a", fg="#d1d5db",
                                insertbackground="#d1d5db", relief="flat",
                                font=("Consolas", 9), padx=12, pady=10)
        scroll = ttk.Scrollbar(holder, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.log_text.pack(side="left", fill="both", expand=True)
        self.log_text.configure(state="disabled")

    def _build_about_page(self, page: tk.Frame) -> None:
        box = tk.Frame(page, bg=CARD)
        box.pack(fill="both", expand=True, padx=28, pady=20)

        tk.Label(box, text="{} v{}".format(APP_TITLE, VERSION), bg=CARD, fg=TEXT,
                 font=(FONT, 15, "bold")).pack(anchor="w")
        tk.Label(box, text="适配 Dr.COM 城市热点新版 eportal（172.30.255.42:801）",
                 bg=CARD, fg=MUTED, font=(FONT, 9)).pack(anchor="w", pady=(4, 14))

        text = tk.Text(box, bg=CARD, relief="flat", wrap="word", height=10,
                       font=(FONT, 9), fg=TEXT, padx=0, pady=0)
        text.insert("1.0",
                    "认证引擎：开源项目 campusnet（MIT License）\n"
                    "本程序在其上增加了本校适配、DPAPI 密码加密、开机自启与图形界面。\n\n"
                    "· 密码只保存在本机，用 Windows DPAPI 加密，换电脑/换 Windows 账户都解不开\n"
                    "· 只有在校园网网段内才会发送账号密码\n"
                    "· 认证协议细节见 docs/PROTOCOL.md\n\n"
                    "声明：仅供本人账号在自己电脑上自动登录使用，请遵守学校网络管理规定。")
        text.configure(state="disabled")
        text.pack(fill="both", expand=True)

        footer = tk.Frame(box, bg=CARD)
        footer.pack(fill="x", pady=(12, 0))
        self.btn_uninstall = ttk.Button(footer, text="清除账号数据", command=self.uninstall_all)
        self.btn_uninstall.pack(side="left")
        tk.Label(footer, text="清除自启、配置、密码；删除程序文件夹请双击文件夹里的 卸载.bat",
                 bg=CARD, fg=MUTED, font=(FONT, 8)).pack(side="left", padx=(12, 0))

        tk.Label(box, text="配置文件：" + str(core.CONFIG_PATH), bg=CARD, fg=GRAY,
                 font=(FONT, 8)).pack(anchor="w", pady=(10, 0))

    def uninstall_all(self) -> None:
        """清除开机自启、配置和密码；程序文件夹由 卸载.bat 删除。"""
        core.log("卸载：按钮被点击")
        if not messagebox.askyesno(
            APP_TITLE,
            "确定要清除账号数据吗？\n\n将删除：\n· 开机自启\n· 配置与加密密码\n\n"
            "清除后，请再双击本文件夹里的「卸载.bat」删除程序文件夹。",
            parent=self,
        ):
            core.log("卸载：用户取消")
            return

        core.log("卸载：确认执行")
        try:
            notes = core.purge_data()
        except Exception as exc:  # noqa: BLE001
            core.log("卸载：清理数据失败 {}".format(exc))
            messagebox.showerror(APP_TITLE, "清理失败：{}\n\n详见日志。".format(exc), parent=self)
            return

        core.log("卸载：清理完成 {}".format(notes))
        messagebox.showinfo(
            APP_TITLE,
            "已清除：\n" + "\n".join("· " + n for n in notes)
            + "\n\n最后一步：双击本文件夹里的「卸载.bat」，删除程序文件夹。",
            parent=self,
        )
        self.destroy()

    def _build_footer(self) -> None:
        bar = tk.Frame(self, bg=BG)
        bar.pack(fill="x", padx=20, pady=(0, 10))
        self.footer = tk.Label(bar, text="就绪", bg=BG, fg=MUTED, font=(FONT, 8))
        self.footer.pack(side="left")
        tk.Label(bar, text="v{} · MIT License".format(VERSION), bg=BG, fg=GRAY,
                 font=(FONT, 8)).pack(side="right")

    # ------------------------------------------------------------------ 数据
    def _auto_fill_from_config(self) -> None:
        try:
            cfg = core.load_config()
            self.entry_user.set(cfg.username or "")
            self.entry_portal.set(core.portal_of(cfg))
            self.combo_provider.set(PROVIDER_CODE_TO_LABEL.get(
                core.provider_of(cfg) or "auto", "自动识别（auto）"))
            self.account_hint.config(
                text="已保存账号：{}".format(core._mask_account(cfg.username))
                if cfg.username else "还没有配置账号，请填写后点「保存设置」")
        except Exception as exc:  # noqa: BLE001
            self.footer.config(text="读取配置失败：{}".format(exc))
        self.load_log()

    def _toggle_pass(self) -> None:
        self.entry_pass.set_show("" if self.show_pass.get() else "*")

    def save_settings(self) -> None:
        username = self.entry_user.get().strip()
        portal = self.entry_portal.get().strip().rstrip("/")
        selected = self.combo_provider.get().strip()
        provider = PROVIDER_LABEL_TO_CODE.get(selected, selected) or "auto"
        password = self.entry_pass.get()

        if not username:
            self.entry_user.flash_error()
            messagebox.showwarning(APP_TITLE, "请填写校园网账号。", parent=self)
            return
        if not portal.startswith("http"):
            self.entry_portal.flash_error()
            messagebox.showwarning(APP_TITLE, "门户地址要以 http:// 开头。", parent=self)
            return

        try:
            cfg = core.load_config()
            cfg.username = username
            cfg.portal_ip = portal
            cfg.provider = provider or "auto"
            cfg.save(str(core.CONFIG_PATH), include_password=False)
            if password:
                core.save_password(password)
                self.entry_pass.clear()
            self.entry_user.flash_ok()
            self.account_hint.config(text="✔ 已保存，账号：{}".format(core._mask_account(username)))
            self.footer.config(text="设置已保存 · " + datetime.now().strftime("%H:%M:%S"))
            if messagebox.askyesno(APP_TITLE, "设置已保存。现在立刻连接一次吗？", parent=self):
                self.do_login(False)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_TITLE, "保存失败：{}".format(exc), parent=self)

    def toggle_autostart(self) -> None:
        want = self.auto_switch.value
        ok = core.install_autostart() if want else core.uninstall_autostart()
        if not ok:
            self.auto_switch.set_value(not want)
            messagebox.showerror(APP_TITLE, "修改开机自启失败，详见日志。", parent=self)
            return
        self.footer.config(text="开机自启已{} · {}".format(
            "开启" if want else "关闭", datetime.now().strftime("%H:%M:%S")))

    # ------------------------------------------------------------------ 状态
    def refresh_status(self) -> None:
        if self._busy:
            return
        self._set_state("checking", "检查中…", "正在读取网络状态")

        def job():
            cfg = core.load_config()
            portal = core.portal_of(cfg)
            online = core.portal_online(portal)
            net = core.internet_quick() if online else False
            return cfg, portal, online, net

        self._run_async(job, self._apply_status)

    def _apply_status(self, result) -> None:
        if isinstance(result, Exception):
            self._set_state("error", "检查失败", str(result)[:80])
            return
        cfg, portal, online, net = result
        ip = core.local_ip() or "—"
        self.ip_label.config(text=ip)
        self.portal_label.config(text=portal.replace("http://", ""))
        self.time_label.config(text=datetime.now().strftime("%H:%M:%S"))

        if online and net:
            self._set_state("online", "已联网", "认证有效，可以正常上网")
            self.btn_login.set_text("重新连接")
        elif online:
            self._set_state("warn", "已认证", "门户显示在线，但外网探测未通过")
            self.btn_login.set_text("重新连接")
        else:
            self._set_state("offline", "未联网", "需要认证：点「立即连接」")
            self.btn_login.set_text("立即连接")

    def _set_state(self, state: str, text: str, detail: str) -> None:
        colors = {"checking": ACCENT, "connecting": ACCENT, "online": OK,
                  "offline": BAD, "warn": WARN, "error": BAD}
        color = colors.get(state, GRAY)
        pulse = state in ("checking", "connecting")
        ripple = state in ("online",)
        self.dot.set_color(color, pulse=pulse, ripple=ripple)
        self._stop_dots()
        self.state_label.config(text=text, fg=TEXT)
        self.detail_label.config(text=detail)
        if state == "connecting":
            self._start_dots()

    def _start_dots(self) -> None:
        self._dot_phase = 0

        def tick():
            self._dot_phase = (self._dot_phase + 1) % 4
            self.state_label.config(text="连接中" + "·" * self._dot_phase)
            self._dots_job = self.after(420, tick)

        tick()

    def _stop_dots(self) -> None:
        if self._dots_job:
            try:
                self.after_cancel(self._dots_job)
            except tk.TclError:
                pass
            self._dots_job = None

    # ------------------------------------------------------------------ 登录
    def do_login(self, force: bool = False) -> None:
        if self._busy:
            return
        self._set_busy(True)
        self._set_state("connecting", "连接中", "正在认证，请稍候（通常 5~20 秒）")
        self.footer.config(text="正在连接…")

        def job():
            return core.attempt_login(force=force, stream=False)

        def done(result):
            self._set_busy(False)
            if isinstance(result, Exception):
                self._set_state("error", "出错", str(result)[:80])
                messagebox.showerror(APP_TITLE, "连接过程出错：{}".format(result), parent=self)
                return
            code, output = result
            self._append_result(output)
            if code == 0:
                self._set_state("online", "已联网", "认证成功")
                self.footer.config(text="已联网 · " + datetime.now().strftime("%H:%M:%S"))
            elif code == 3:
                self._set_state("warn", "不在校园网", "当前网络不是校园网，已跳过")
            elif code == 4:
                self._set_state("offline", "账号或密码被拒绝", "请检查账号密码后重新保存")
                messagebox.showerror(APP_TITLE, "账号或密码被拒绝。\n\n为避免锁定，请不要连续重试。\n\n"
                                                + output[:400], parent=self)
            else:
                self._set_state("offline", "连接失败", "可查看「运行日志」排查")
            self.load_log()
            self.after(700, self.refresh_status)

        self._run_async(job, done)

    # ------------------------------------------------------------------ 工具
    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        for btn in (self.btn_login, self.btn_force, self.btn_refresh):
            btn.set_enabled(not busy)
        if busy:
            self.progress.pack(side="left", padx=(16, 0), after=self.btn_refresh)
            self.progress.start(11)
        else:
            self.progress.stop()
            self.progress.pack_forget()

    def _run_async(self, job, done) -> None:
        def runner():
            try:
                result = job()
            except Exception as exc:  # noqa: BLE001
                result = exc
            self.after(0, lambda: done(result))

        threading.Thread(target=runner, daemon=True).start()

    def _append_result(self, output: str) -> None:
        if not output:
            return
        self.log_text.configure(state="normal")
        self.log_text.insert("end", "\n── {} 本次操作输出 ──\n".format(
            datetime.now().strftime("%H:%M:%S")))
        self.log_text.insert("end", output.strip() + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def load_log(self) -> None:
        try:
            text = core.LOG_PATH.read_text(encoding="utf-8", errors="replace") \
                if core.LOG_PATH.exists() else "（还没有日志）"
        except OSError as exc:
            text = "读取日志失败：{}".format(exc)
        lines = text.splitlines()[-300:]
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.insert("end", "\n".join(lines))
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def open_log_file(self) -> None:
        try:
            if core.LOG_PATH.exists():
                os.startfile(str(core.LOG_PATH))  # type: ignore[attr-defined]
            else:
                messagebox.showinfo(APP_TITLE, "还没有日志文件。", parent=self)
        except OSError as exc:
            messagebox.showerror(APP_TITLE, "打开失败：{}".format(exc), parent=self)


def _dbg(message: str) -> None:
    target = os.environ.get("CAMPUS_DEBUG_FILE")
    if not target:
        return
    try:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(message + "\n")
    except OSError:
        pass


def main() -> int:
    try:
        app = App()
        _dbg("main: App created")

        def probe():
            try:
                _dbg("main: probe viewable={} alpha={} state={}".format(
                    app.winfo_viewable(), app.attributes("-alpha"), app.state()))
            except Exception as exc:  # noqa: BLE001
                _dbg("main: probe failed {}".format(exc))

        app.after(2000, probe)
        _dbg("main: entering mainloop")
        app.mainloop()
        _dbg("main: mainloop returned")
        return 0
    except Exception:  # noqa: BLE001
        detail = traceback.format_exc()
        _dbg("main: EXCEPTION\n" + detail)
        try:
            core.log("图形界面异常：\n" + detail)
        except Exception:  # noqa: BLE001
            pass
        try:
            messagebox.showerror(APP_TITLE, "程序出错：\n\n" + detail[-800:])
        except Exception:  # noqa: BLE001
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
