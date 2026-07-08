"""
NACHTFALTER · moon   (entspricht Abschnitt E der HTML)
====================================================================
Mond-Tracking. Bleibt — wie im HTML — WebSocket-CLIENT gegen deinen
tracking.py-Server (ws://127.0.0.1:8765). Deine RealSense-Pipeline muss
also NICHT angefasst werden. Läuft in einem Daemon-Thread mit Auto-
Reconnect; die Glättungs-/Ausreißer-Logik ist 1:1 übernommen.
"""
import json
import math
import threading

try:
    import websocket
except ImportError:
    websocket = None


class Moon:
    def __init__(self, enabled=True, url="ws://127.0.0.1:8765",
                 flipX=True, flipY=False, smooth=0.22, maxJump=0.3, confirm=2):
        self.enabled = enabled
        self.url = url
        self.flipX, self.flipY = flipX, flipY
        self.smooth, self.maxJump, self.confirm = smooth, maxJump, confirm
        self.target = None          # (nx, ny) normalisiert 0..1 oder None
        self._last = None
        self._jump_cand = None
        self._jump_count = 0
        self._stop = False
        if not enabled:
            return
        if websocket is None:
            print("[moon] websocket-client fehlt -> Maus als Leitlicht")
            self.enabled = False
            return
        threading.Thread(target=self._run, daemon=True).start()

    # --- WebSocket im Thread (Auto-Reconnect) ---------------------
    def _run(self):
        while not self._stop:
            try:
                ws = websocket.WebSocketApp(
                    self.url,
                    on_message=self._on_message,
                    on_close=self._on_close,
                    on_error=self._on_error,
                )
                ws.run_forever()
            except Exception:
                pass
            if self._stop:
                break
            self._reset_track()
            threading.Event().wait(1.5)   # 1.5 s bis Reconnect

    def _on_message(self, ws, raw):
        try:
            d = json.loads(raw)
        except Exception:
            return
        if not d.get("active"):
            self._reset_track()
            return
        nx = 1 - d["x"] if self.flipX else d["x"]
        ny = 1 - d["y"] if self.flipY else d["y"]
        # zweite Absicherung gegen Springen auf ein anderes rundes Objekt
        if self._last and math.hypot(nx - self._last[0], ny - self._last[1]) > self.maxJump:
            if self._jump_cand and math.hypot(nx - self._jump_cand[0],
                                              ny - self._jump_cand[1]) <= self.maxJump:
                self._jump_count += 1
            else:
                self._jump_cand = (nx, ny); self._jump_count = 1
            if self._jump_count < self.confirm:
                return
        self._jump_cand = None; self._jump_count = 0
        self._last = (nx, ny); self.target = (nx, ny)

    def _on_close(self, ws, *a):
        self._reset_track()

    def _on_error(self, ws, *a):
        pass

    def _reset_track(self):
        self.target = None; self._last = None
        self._jump_cand = None; self._jump_count = 0

    # --- vom Sim aufgerufen ---------------------------------------
    def apply(self, light, W, H):
        t = self.target
        if not t:
            return
        light.x += (t[0] * W - light.x) * self.smooth   # 0..1 -> Pixel
        light.y += (t[1] * H - light.y) * self.smooth
        light.active = True

    def close(self):
        self._stop = True
