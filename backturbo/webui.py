from __future__ import annotations

import json
import os
import threading
import time
import webbrowser
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .audio import SoundEngine
from .detector import BackTurboDetector
from .model import DriverInput, OBDFrame
from .simulator import VirtualVehicle


HTML = r"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ZN6 Back-Turbine Lab</title>
<style>
:root{color-scheme:dark;--bg:#080b0f;--panel:#121820;--line:#25303b;--text:#eef4fa;--muted:#82909f;--cyan:#42d3ff;--orange:#ff9b42;--red:#ff4e64}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 15% 0,#16202a 0,transparent 35%),var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"Helvetica Neue",sans-serif;min-height:100vh}
main{max-width:1120px;margin:auto;padding:28px}.header{display:flex;justify-content:space-between;align-items:end;margin-bottom:18px}.eyebrow{color:var(--cyan);font:700 11px ui-monospace;letter-spacing:.2em}.header h1{margin:6px 0 0;font-size:28px;letter-spacing:.04em}.source{color:var(--muted);font:12px ui-monospace}
.telemetry{display:grid;grid-template-columns:repeat(5,1fr);gap:1px;background:var(--line);border:1px solid var(--line);border-radius:12px;overflow:hidden}.metric{padding:18px;background:var(--panel)}.metric b{display:block;font:700 25px ui-monospace}.metric span,.label{color:var(--muted);font:10px ui-monospace;letter-spacing:.12em}
.layout{display:grid;grid-template-columns:1.65fr 1fr;gap:16px;margin-top:16px}.panel{background:linear-gradient(145deg,#151c24,#10151b);border:1px solid var(--line);border-radius:12px;padding:20px;min-height:500px}.gauges{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:12px 0 4px}.gauge{position:relative;text-align:center;min-width:0}.gauge svg{display:block;width:100%;height:auto;filter:drop-shadow(0 8px 12px #0008)}.gauge-bg{fill:#0b1015;stroke:#27323d;stroke-width:2}.gauge-track{fill:none;stroke:#2c3742;stroke-width:8;stroke-linecap:round}.gauge-arc{fill:none;stroke:var(--cyan);stroke-width:8;stroke-linecap:round;stroke-dasharray:100;stroke-dashoffset:100;transition:stroke-dashoffset .08s linear,stroke .2s}.gauge-arc.orange{stroke:var(--orange)}.needle{stroke:#f3f7fa;stroke-width:2.5;stroke-linecap:round;transition:transform .08s linear}.hub{fill:var(--text);stroke:#0b1015;stroke-width:3}.dial-value{fill:var(--text);font:700 17px ui-monospace;text-anchor:middle}.dial-unit{fill:var(--muted);font:8px ui-monospace;text-anchor:middle;letter-spacing:.12em}.dial-limit{fill:#627181;font:8px ui-monospace;text-anchor:middle}.bar-row{margin:18px 0}.bar-head{display:flex;justify-content:space-between;font:11px ui-monospace;color:var(--muted);margin-bottom:7px}.track{height:14px;background:#252e37;border-radius:3px;overflow:hidden}.fill{height:100%;width:0;background:var(--cyan);transition:width .08s linear}.fill.orange{background:var(--orange)}
.events{border-top:1px solid var(--line);margin-top:26px;padding-top:16px;min-height:110px}.event{font:11px ui-monospace;color:var(--muted);padding:5px 0}.event:first-child{color:var(--orange)}
.control-grid{display:grid;grid-template-columns:85px 1fr;gap:18px;margin-top:20px}.pedal{height:220px;writing-mode:vertical-lr;direction:rtl;accent-color:var(--orange);width:42px}.throttle-value{font:700 24px ui-monospace;margin-top:6px}.toggle{display:flex;align-items:center;gap:9px;padding:10px 0;color:var(--text);font-size:14px}.toggle input{accent-color:var(--cyan);width:18px;height:18px}.gears{display:grid;grid-template-columns:repeat(4,1fr);gap:6px;margin-top:10px}.gear{border:1px solid var(--line);background:#1b232c;color:var(--text);border-radius:7px;padding:10px;font:700 15px ui-monospace;cursor:pointer}.gear.active{background:var(--cyan);color:#071016;border-color:var(--cyan)}
.status{display:flex;justify-content:space-between;gap:12px;margin-top:16px;padding:14px 18px;border:1px solid var(--line);border-radius:9px;background:var(--panel);color:var(--muted);font:11px ui-monospace}.ready{color:var(--cyan)}kbd{border:1px solid #475360;background:#202832;border-radius:4px;padding:2px 5px;font:10px ui-monospace;color:#cbd5df}
@media(max-width:760px){main{padding:14px}.telemetry{grid-template-columns:repeat(2,1fr)}.layout{grid-template-columns:1fr}.header{align-items:start}.source{display:none}}
</style>
</head>
<body><main>
<div class="header"><div><div class="eyebrow">VIRTUAL POWERTRAIN / ZN6</div><h1>BACK-TURBINE LAB</h1></div><div class="source">LOCAL OBD2 SOURCE · 60 Hz</div></div>
<section class="telemetry">
 <div class="metric"><b id="rpm">850</b><span>ENGINE RPM</span></div><div class="metric"><b id="speed">000.0</b><span>SPEED km/h</span></div>
 <div class="metric"><b id="boost">−0.65</b><span>BOOST bar</span></div><div class="metric"><b id="throttle">000</b><span>THROTTLE %</span></div><div class="metric"><b id="gearTop">2</b><span>GEAR</span></div>
</section>
<div class="layout">
 <section class="panel"><div class="label">LIVE POWERTRAIN</div>
  <div class="gauges">
   <div class="gauge"><svg viewBox="0 0 160 160" role="img" aria-label="速度計"><circle class="gauge-bg" cx="80" cy="80" r="72"/><path class="gauge-track" pathLength="100" d="M37.6 122.4A60 60 0 1 1 122.4 122.4"/><path id="speedArc" class="gauge-arc" pathLength="100" d="M37.6 122.4A60 60 0 1 1 122.4 122.4"/><text class="dial-limit" x="30" y="137">0</text><text class="dial-limit" x="130" y="137">240</text><line id="speedNeedle" class="needle" x1="80" y1="80" x2="80" y2="25"/><circle class="hub" cx="80" cy="80" r="6"/><text id="speedDial" class="dial-value" x="80" y="112">0</text><text class="dial-unit" x="80" y="126">km/h</text></svg></div>
   <div class="gauge"><svg viewBox="0 0 160 160" role="img" aria-label="回転計"><circle class="gauge-bg" cx="80" cy="80" r="72"/><path class="gauge-track" pathLength="100" d="M37.6 122.4A60 60 0 1 1 122.4 122.4"/><path id="rpmArc" class="gauge-arc" pathLength="100" d="M37.6 122.4A60 60 0 1 1 122.4 122.4"/><text class="dial-limit" x="30" y="137">0</text><text class="dial-limit" x="130" y="137">8</text><line id="rpmNeedle" class="needle" x1="80" y1="80" x2="80" y2="25"/><circle class="hub" cx="80" cy="80" r="6"/><text id="rpmDial" class="dial-value" x="80" y="112">0.9</text><text class="dial-unit" x="80" y="126">×1000 rpm</text></svg></div>
   <div class="gauge"><svg viewBox="0 0 160 160" role="img" aria-label="ブースト計"><circle class="gauge-bg" cx="80" cy="80" r="72"/><path class="gauge-track" pathLength="100" d="M37.6 122.4A60 60 0 1 1 122.4 122.4"/><path id="boostArc" class="gauge-arc orange" pathLength="100" d="M37.6 122.4A60 60 0 1 1 122.4 122.4"/><text class="dial-limit" x="28" y="137">−1</text><text class="dial-limit" x="132" y="137">+1</text><line id="boostNeedle" class="needle" x1="80" y1="80" x2="80" y2="25"/><circle class="hub" cx="80" cy="80" r="6"/><text id="boostDial" class="dial-value" x="80" y="112">−0.65</text><text class="dial-unit" x="80" y="126">BOOST bar</text></svg></div>
  </div>
  <div class="bar-row"><div class="bar-head"><span>THROTTLE</span><span id="throttleSmall">0%</span></div><div class="track"><div class="fill orange" id="throttleBar"></div></div></div>
  <div class="events"><div class="label">SURGE EVENTS</div><div id="events"><div class="event">ブーストを溜めてアクセルを急に戻してください</div></div></div>
 </section>
 <section class="panel"><div class="label">DRIVER INPUT</div><div class="control-grid">
  <div><input id="pedal" class="pedal" type="range" min="0" max="100" value="0" aria-label="アクセル開度"><div id="pedalValue" class="throttle-value">0%</div></div>
  <div><label class="toggle"><input id="clutch" type="checkbox">クラッチ <kbd>C</kbd></label><label class="toggle"><input id="brake" type="checkbox">ブレーキ <kbd>S / ↓</kbd></label><label class="toggle"><input id="engineSound" type="checkbox" checked>エンジン音 ON</label><label class="toggle"><input id="sound" type="checkbox" checked>バックタービン音 ON</label>
   <div class="label" style="margin-top:20px">GEAR <kbd>0—6</kbd></div><div id="gears" class="gears"></div>
  </div></div>
 </section>
</div>
<div class="status"><span id="status" class="ready" aria-live="polite">READY — Nから1速へ。クラッチとアクセルで発進してください</span><span id="audioBackend">AUDIO INITIALIZING…</span></div>
</main>
<script>
const $=id=>document.getElementById(id);let controls={throttle:0,clutch_pressed:false,brake_pressed:false,gear:0,sound:true,engine_sound:true};let down=new Set();
async function send(patch){Object.assign(controls,patch);try{await fetch('/api/controls',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(patch)})}catch(e){}}
function showControls(){ $('pedal').value=Math.round(controls.throttle*100);$('pedalValue').textContent=Math.round(controls.throttle*100)+'%';$('clutch').checked=controls.clutch_pressed;$('brake').checked=controls.brake_pressed;document.querySelectorAll('.gear').forEach(x=>x.classList.toggle('active',+x.dataset.gear===controls.gear)) }
for(let g=0;g<=6;g++){let b=document.createElement('button');b.className='gear';b.dataset.gear=g;b.textContent=g===0?'N':g;b.onclick=()=>{controls.gear=g;showControls();send({gear:g})};$('gears').appendChild(b)}showControls();
$('pedal').oninput=e=>{controls.throttle=+e.target.value/100;$('pedalValue').textContent=e.target.value+'%';send({throttle:controls.throttle})};$('clutch').onchange=e=>send({clutch_pressed:e.target.checked});$('brake').onchange=e=>send({brake_pressed:e.target.checked});$('engineSound').onchange=e=>send({engine_sound:e.target.checked});$('sound').onchange=e=>send({sound:e.target.checked});
const isAccel=k=>k==='w'||k==='arrowup';const isBrake=k=>k==='s'||k==='arrowdown';
addEventListener('keydown',e=>{let k=e.key.toLowerCase();if(down.has(k))return;down.add(k);if(isAccel(k)){e.preventDefault();controls.throttle=1;showControls();send({throttle:1})}else if(isBrake(k)){e.preventDefault();controls.brake_pressed=true;showControls();send({brake_pressed:true})}else if(k==='c'){controls.clutch_pressed=true;showControls();send({clutch_pressed:true})}else if(k===' '){e.preventDefault();controls.throttle=0;showControls();send({throttle:0})}else if(k==='n'){controls.gear=0;showControls();send({gear:0})}else if(/^[0-6]$/.test(k)){controls.gear=+k;showControls();send({gear:+k})}});
addEventListener('keyup',e=>{let k=e.key.toLowerCase();down.delete(k);if(isAccel(k)){controls.throttle=0;showControls();send({throttle:0})}else if(isBrake(k)){controls.brake_pressed=false;showControls();send({brake_pressed:false})}else if(k==='c'){controls.clutch_pressed=false;showControls();send({clutch_pressed:false})}});
addEventListener('blur',()=>{down.clear();controls.throttle=0;controls.clutch_pressed=false;controls.brake_pressed=false;showControls();send({throttle:0,clutch_pressed:false,brake_pressed:false})});
function num(v,n=0){return Number(v).toFixed(n)}
function gauge(prefix,value,min,max,text){let ratio=Math.max(0,Math.min(1,(value-min)/(max-min)));$(prefix+'Arc').style.strokeDashoffset=100-ratio*100;$(prefix+'Needle').style.transform='rotate('+(-135+ratio*270)+'deg)';$(prefix+'Needle').style.transformOrigin='80px 80px';$(prefix+'Dial').textContent=text}
async function poll(){try{let s=await (await fetch('/api/state')).json();$('rpm').textContent=Math.round(s.rpm).toLocaleString();$('speed').textContent=num(s.speed_kmh,1).padStart(5,'0');$('boost').textContent=(s.boost_bar>=0?'+':'')+num(s.boost_bar,2);$('boost').style.color=s.boost_bar>0?'var(--orange)':'var(--cyan)';$('throttle').textContent=String(Math.round(s.throttle*100)).padStart(3,'0');$('gearTop').textContent=s.gear||'N';$('throttleSmall').textContent=Math.round(s.throttle*100)+'%';$('throttleBar').style.width=s.throttle*100+'%';gauge('speed',s.speed_kmh,0,240,num(s.speed_kmh,0));gauge('rpm',s.rpm,0,8000,num(s.rpm/1000,1));gauge('boost',s.boost_bar,-1,1,(s.boost_bar>=0?'+':'')+num(s.boost_bar,2));$('rpmArc').style.stroke=s.rpm>=6800?'var(--red)':'var(--cyan)';$('events').innerHTML=(s.events.length?s.events:['ブーストを溜めてアクセルを急に戻してください']).map(x=>'<div class="event">'+x+'</div>').join('');$('status').textContent=s.status;$('status').className=s.status.startsWith('SURGE')?'':'ready';$('audioBackend').textContent='AUDIO · '+s.audio_backend}catch(e){$('status').textContent='CONNECTION LOST — Pythonプロセスを確認してください'}setTimeout(poll,50)}poll();
</script></body></html>"""


class SimulationEngine:
    def __init__(self) -> None:
        self.vehicle = VirtualVehicle()
        self.detector = BackTurboDetector()
        self.sound = SoundEngine()
        self.controls = DriverInput(gear=0)
        self.frame = OBDFrame.stopped()
        self.events: deque[str] = deque(maxlen=5)
        self.status = "READY — Nから1速へ。クラッチとアクセルで発進してください"
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._loop, name="vehicle-simulation", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def _loop(self) -> None:
        last = time.monotonic()
        while not self.stop_event.is_set():
            now = time.monotonic()
            with self.lock:
                self.frame = self.vehicle.update(self.controls, now - last)
                self.sound.update_engine(self.frame.rpm, self.frame.throttle, self.frame.boost_bar)
                event = self.detector.update(self.frame)
                if event is not None:
                    self.sound.play(event)
                    reason = {
                        "clutch": "CLUTCH",
                        "valve_release": "POU RELEASE",
                        "pressure_release": "PSH RELEASE",
                    }.get(event.reason, "THROTTLE LIFT")
                    message = (
                        f"SURGE {event.intensity * 100:02.0f}% · {reason} · "
                        f"{event.rpm:,.0f} rpm · {event.boost_bar:+.2f} bar · "
                        f"LIFT {event.throttle_drop * 100:.0f}%"
                    )
                    self.events.appendleft(message)
                    self.status = message
            last = now
            self.stop_event.wait(1.0 / 60.0)

    def apply(self, data: dict[str, Any]) -> None:
        with self.lock:
            if "throttle" in data:
                self.controls.throttle = max(0.0, min(1.0, float(data["throttle"])))
            if "clutch_pressed" in data:
                self.controls.clutch_pressed = bool(data["clutch_pressed"])
            if "brake_pressed" in data:
                self.controls.brake_pressed = bool(data["brake_pressed"])
            if "gear" in data:
                self.controls.gear = max(0, min(6, int(data["gear"])))
            if "sound" in data:
                self.sound.enabled = bool(data["sound"])
            if "engine_sound" in data:
                self.sound.engine_enabled = bool(data["engine_sound"])

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            frame = self.frame
            return {
                "throttle": frame.throttle, "clutch_pressed": frame.clutch_pressed,
                "gear": frame.gear, "rpm": frame.rpm, "speed_kmh": frame.speed_kmh,
                "boost_bar": frame.boost_bar, "events": list(self.events),
                "status": self.status, "audio_available": self.sound.available,
                "audio_backend": self.sound.backend,
            }

    def close(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=1.0)
        self.sound.close()


def make_handler(engine: SimulationEngine) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: HTTPStatus, content_type: str, payload: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/":
                self._send(HTTPStatus.OK, "text/html; charset=utf-8", HTML.encode())
            elif self.path == "/api/state":
                self._send(HTTPStatus.OK, "application/json", json.dumps(engine.snapshot()).encode())
            else:
                self._send(HTTPStatus.NOT_FOUND, "text/plain", b"Not found")

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/api/controls":
                self._send(HTTPStatus.NOT_FOUND, "text/plain", b"Not found")
                return
            try:
                length = min(int(self.headers.get("Content-Length", "0")), 4096)
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError("JSON object required")
                engine.apply(data)
                self._send(HTTPStatus.OK, "application/json", b'{"ok":true}')
            except (ValueError, TypeError, json.JSONDecodeError):
                self._send(HTTPStatus.BAD_REQUEST, "application/json", b'{"ok":false}')

        def log_message(self, format: str, *args: Any) -> None:
            return

    return Handler


def run() -> None:
    engine = SimulationEngine()
    port = int(os.environ.get("BACKTURBO_PORT", "8765"))
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(engine))
    engine.start()
    url = f"http://127.0.0.1:{port}"
    print(f"ZN6 Back-Turbine Lab: {url}")
    print("終了するには Control+C を押してください。")
    threading.Timer(0.35, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        engine.close()
