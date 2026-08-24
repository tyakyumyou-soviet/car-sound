from __future__ import annotations

import tkinter as tk
from collections import deque
from time import monotonic
from tkinter import ttk

from .audio import SoundEngine
from .detector import BackTurboDetector, SurgeEvent
from .model import DriverInput, OBDFrame
from .simulator import VirtualVehicle


BG = "#0b0e12"
PANEL = "#151a21"
TEXT = "#eef4fa"
MUTED = "#8996a5"
CYAN = "#42d3ff"
ORANGE = "#ff9b42"
RED = "#ff4e64"


class SimulatorApp:
    TICK_MS = 16

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("ZN6 Back-Turbine Lab")
        self.root.geometry("980x680")
        self.root.minsize(840, 600)
        self.root.configure(bg=BG)

        self.vehicle = VirtualVehicle()
        self.detector = BackTurboDetector()
        self.sound = SoundEngine()
        self.controls = DriverInput(gear=2)
        self.keys_down: set[str] = set()
        self.last_tick = monotonic()
        self.events: deque[str] = deque(maxlen=6)
        self.last_frame = OBDFrame.stopped()

        self.throttle_var = tk.DoubleVar(value=0.0)
        self.clutch_var = tk.BooleanVar(value=False)
        self.brake_var = tk.BooleanVar(value=False)
        self.sound_var = tk.BooleanVar(value=True)
        self.gear_var = tk.IntVar(value=2)
        self.status_var = tk.StringVar(value="READY — 2速でアクセルを踏み、急に戻してください")

        self._configure_styles()
        self._build_ui()
        self._bind_keys()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(self.TICK_MS, self._tick)

    def _configure_styles(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("TLabel", background=BG, foreground=TEXT, font=("Helvetica Neue", 12))
        style.configure("Muted.TLabel", foreground=MUTED, font=("Helvetica Neue", 10))
        style.configure("Title.TLabel", foreground=TEXT, font=("Helvetica Neue", 23, "bold"))
        style.configure("Value.TLabel", background=PANEL, foreground=TEXT, font=("Menlo", 19, "bold"))
        style.configure("Caption.TLabel", background=PANEL, foreground=MUTED, font=("Helvetica Neue", 10))
        style.configure("TCheckbutton", background=PANEL, foreground=TEXT, font=("Helvetica Neue", 11))
        style.map("TCheckbutton", background=[("active", PANEL)])
        style.configure("Gear.TRadiobutton", background=PANEL, foreground=TEXT, padding=(12, 8), font=("Menlo", 13, "bold"))
        style.map("Gear.TRadiobutton", background=[("selected", CYAN), ("active", "#26313c")], foreground=[("selected", BG)])

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=22)
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(0, 16))
        ttk.Label(header, text="ZN6  BACK-TURBINE LAB", style="Title.TLabel").pack(side="left")
        ttk.Label(header, text="VIRTUAL OBD2 SOURCE  •  60 Hz", style="Muted.TLabel").pack(side="right", pady=(10, 0))

        telemetry = ttk.Frame(outer, style="Panel.TFrame", padding=18)
        telemetry.pack(fill="x")
        self.value_labels: dict[str, ttk.Label] = {}
        values = (("rpm", "ENGINE RPM"), ("speed", "SPEED km/h"), ("boost", "BOOST bar"), ("throttle", "THROTTLE %"), ("gear", "GEAR"))
        for column, (key, caption) in enumerate(values):
            cell = ttk.Frame(telemetry, style="Panel.TFrame")
            cell.grid(row=0, column=column, sticky="ew", padx=10)
            telemetry.columnconfigure(column, weight=1)
            label = ttk.Label(cell, text="—", style="Value.TLabel")
            label.pack(anchor="w")
            ttk.Label(cell, text=caption, style="Caption.TLabel").pack(anchor="w", pady=(3, 0))
            self.value_labels[key] = label

        middle = ttk.Frame(outer)
        middle.pack(fill="both", expand=True, pady=16)
        middle.columnconfigure(0, weight=3)
        middle.columnconfigure(1, weight=2)
        middle.rowconfigure(0, weight=1)

        graph_panel = ttk.Frame(middle, style="Panel.TFrame", padding=16)
        graph_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        ttk.Label(graph_panel, text="LIVE POWERTRAIN", style="Caption.TLabel").pack(anchor="w")
        self.canvas = tk.Canvas(graph_panel, bg=PANEL, highlightthickness=0, height=280)
        self.canvas.pack(fill="both", expand=True, pady=(8, 0))

        controls_panel = ttk.Frame(middle, style="Panel.TFrame", padding=18)
        controls_panel.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        ttk.Label(controls_panel, text="DRIVER INPUT", style="Caption.TLabel").pack(anchor="w")
        ttk.Label(controls_panel, text="アクセル  [ W / ↑ ]", style="Caption.TLabel").pack(anchor="w", pady=(16, 2))
        self.throttle_scale = tk.Scale(
            controls_panel, from_=100, to=0, variable=self.throttle_var, orient="vertical",
            command=self._slider_changed, length=160, showvalue=True, resolution=1,
            bg=PANEL, fg=TEXT, troughcolor="#29313a", activebackground=ORANGE,
            highlightthickness=0, bd=0, font=("Menlo", 10),
        )
        self.throttle_scale.pack(side="left", padx=(0, 22), pady=4)

        right_controls = ttk.Frame(controls_panel, style="Panel.TFrame")
        right_controls.pack(side="left", fill="both", expand=True, pady=(16, 0))
        ttk.Checkbutton(right_controls, text="クラッチ  [ C ]", variable=self.clutch_var, command=self._check_changed).pack(anchor="w", pady=4)
        ttk.Checkbutton(right_controls, text="ブレーキ  [ S / ↓ ]", variable=self.brake_var, command=self._check_changed).pack(anchor="w", pady=4)
        ttk.Checkbutton(right_controls, text="サウンド ON", variable=self.sound_var, command=self._sound_changed).pack(anchor="w", pady=(4, 14))
        ttk.Label(right_controls, text="GEAR  [ 0—6 ]", style="Caption.TLabel").pack(anchor="w")
        gears = ttk.Frame(right_controls, style="Panel.TFrame")
        gears.pack(anchor="w", pady=6)
        for gear in range(7):
            ttk.Radiobutton(
                gears, text="N" if gear == 0 else str(gear), value=gear,
                variable=self.gear_var, command=self._gear_changed, style="Gear.TRadiobutton",
            ).grid(row=gear // 4, column=gear % 4, padx=2, pady=2)

        footer = ttk.Frame(outer, style="Panel.TFrame", padding=(16, 12))
        footer.pack(fill="x")
        ttk.Label(footer, textvariable=self.status_var, style="Caption.TLabel").pack(side="left")
        availability = "AUDIO: afplay READY" if self.sound.available else "AUDIO: unavailable"
        ttk.Label(footer, text=availability, style="Caption.TLabel").pack(side="right")

    def _bind_keys(self) -> None:
        self.root.bind_all("<KeyPress>", self._key_press)
        self.root.bind_all("<KeyRelease>", self._key_release)
        self.root.focus_force()

    @staticmethod
    def _key_name(event: tk.Event) -> str:
        return str(event.keysym).lower()

    def _key_press(self, event: tk.Event) -> None:
        key = self._key_name(event)
        if key in self.keys_down:
            return
        self.keys_down.add(key)
        if key in {str(number) for number in range(7)}:
            self.gear_var.set(int(key))
            self._gear_changed()
        elif key == "space":
            self.throttle_var.set(0.0)
            self.controls.throttle = 0.0
        elif key == "escape":
            self.close()
        self._sync_momentary_keys()

    def _key_release(self, event: tk.Event) -> None:
        key = self._key_name(event)
        self.keys_down.discard(key)
        if key in {"w", "up"} and not ({"w", "up"} & self.keys_down):
            self.controls.throttle = 0.0
            self.throttle_var.set(0.0)
        self._sync_momentary_keys()

    def _sync_momentary_keys(self) -> None:
        self.controls.clutch_pressed = "c" in self.keys_down
        self.controls.brake_pressed = bool({"s", "down"} & self.keys_down)
        self.clutch_var.set(self.controls.clutch_pressed)
        self.brake_var.set(self.controls.brake_pressed)

    def _slider_changed(self, value: str) -> None:
        self.controls.throttle = float(value) / 100.0

    def _check_changed(self) -> None:
        self.controls.clutch_pressed = self.clutch_var.get()
        self.controls.brake_pressed = self.brake_var.get()

    def _gear_changed(self) -> None:
        self.controls.gear = self.gear_var.get()

    def _sound_changed(self) -> None:
        self.sound.enabled = self.sound_var.get()

    def _tick(self) -> None:
        now = monotonic()
        dt = now - self.last_tick
        self.last_tick = now

        if {"w", "up"} & self.keys_down:
            self.controls.throttle = min(1.0, self.controls.throttle + dt * 1.25)
            self.throttle_var.set(round(self.controls.throttle * 100.0))

        self.last_frame = self.vehicle.update(self.controls, dt)
        event = self.detector.update(self.last_frame)
        if event is not None:
            self._handle_event(event)
        self._render(self.last_frame)
        self.root.after(self.TICK_MS, self._tick)

    def _handle_event(self, event: SurgeEvent) -> None:
        self.sound.play(event)
        reason = "CLUTCH" if event.reason == "clutch" else "THROTTLE LIFT"
        message = f"SURGE  {event.intensity * 100:02.0f}%  •  {reason}  •  {event.rpm:,.0f} rpm"
        self.events.appendleft(message)
        self.status_var.set(message)
        self.root.after(1200, lambda: self.status_var.set("READY — ブーストを溜めてアクセルオフ"))

    def _render(self, frame: OBDFrame) -> None:
        self.value_labels["rpm"].configure(text=f"{frame.rpm:,.0f}")
        self.value_labels["speed"].configure(text=f"{frame.speed_kmh:05.1f}")
        self.value_labels["boost"].configure(text=f"{frame.boost_bar:+.2f}", foreground=ORANGE if frame.boost_bar > 0 else CYAN)
        self.value_labels["throttle"].configure(text=f"{frame.throttle * 100:03.0f}")
        self.value_labels["gear"].configure(text="N" if frame.gear == 0 else str(frame.gear))

        canvas = self.canvas
        canvas.delete("all")
        width = max(300, canvas.winfo_width())
        height = max(200, canvas.winfo_height())
        left, right = 30, width - 30

        def bar(y: float, label: str, fraction: float, color: str, value: str) -> None:
            fraction = max(0.0, min(1.0, fraction))
            canvas.create_text(left, y, text=label, anchor="w", fill=MUTED, font=("Helvetica Neue", 10))
            canvas.create_text(right, y, text=value, anchor="e", fill=TEXT, font=("Menlo", 10, "bold"))
            canvas.create_rectangle(left, y + 14, right, y + 31, fill="#252c34", outline="")
            canvas.create_rectangle(left, y + 14, left + (right - left) * fraction, y + 31, fill=color, outline="")

        bar(20, "RPM", frame.rpm / 7600.0, RED if frame.rpm > 6800 else CYAN, f"{frame.rpm:,.0f}")
        bar(82, "THROTTLE", frame.throttle, ORANGE, f"{frame.throttle * 100:.0f}%")
        boost_fraction = (frame.boost_bar + 0.75) / 1.75
        bar(144, "MANIFOLD PRESSURE", boost_fraction, ORANGE if frame.boost_bar > 0 else CYAN, f"{frame.boost_bar:+.2f} bar")
        canvas.create_text(left, 214, text="SURGE EVENTS", anchor="w", fill=MUTED, font=("Helvetica Neue", 10))
        for line, message in enumerate(self.events):
            canvas.create_text(left, 238 + line * 20, text=message, anchor="w", fill=ORANGE if line == 0 else MUTED, font=("Menlo", 9))

    def close(self) -> None:
        self.sound.close()
        self.root.destroy()


def run() -> None:
    root = tk.Tk()
    SimulatorApp(root)
    root.mainloop()
