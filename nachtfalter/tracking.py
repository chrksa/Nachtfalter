"""
RealSense-Tracking -> WebSocket an die Nachtfalter-App (moon.py).

Erkennt helle Punkte im IR-Bild, mittelt ihren Schwerpunkt und sendet die
normierte Position als {"active":.., "x":.., "y":..} an ws://127.0.0.1:8765.

Tasten im IR-Fenster:
  C          Kalibrierung starten (Mond GANZ LINKS/RECHTS/OBEN/UNTEN halten)
  Leertaste  aktuellen Kalibrier-Schritt aufnehmen
  ESC        Kalibrierung abbrechen / sonst Programm beenden

Die Kalibrierung wird in calib.json gespeichert und beim nächsten Start geladen.
"""
import os
import json
import threading
import asyncio

import pyrealsense2 as rs
import numpy as np
import cv2

try:
    import websockets
except ImportError:
    websockets = None

W_IR, H_IR = 848, 480            # IR-Auflösung der Kamera
FONT = cv2.FONT_HERSHEY_SIMPLEX

# --- Kalibrierung: welcher Kamera-Ausschnitt auf 0..1 (voller Screen) geht ---
CROP_X0, CROP_X1 = 0.0, 1.0
CROP_Y0, CROP_Y1 = 0.0, 1.0
CALIB_FILE = os.path.join(os.path.dirname(__file__), "calib.json")

CALIB_STEPS = ["GANZ LINKS", "GANZ RECHTS", "GANZ OBEN", "GANZ UNTEN"]
_calib_active = False
_calib_step = 0
_calib = {}


def load_calib():
    global CROP_X0, CROP_X1, CROP_Y0, CROP_Y1
    try:
        d = json.load(open(CALIB_FILE, encoding="utf-8"))
        CROP_X0, CROP_X1 = d["cx0"], d["cx1"]
        CROP_Y0, CROP_Y1 = d["cy0"], d["cy1"]
        print(f"[tracking] Kalibrierung geladen: {d}")
    except Exception:
        print("[tracking] keine Kalibrierung gefunden -> ganzes Bild (Taste C)")


def save_calib():
    d = dict(cx0=CROP_X0, cx1=CROP_X1, cy0=CROP_Y0, cy1=CROP_Y1)
    try:
        json.dump(d, open(CALIB_FILE, "w", encoding="utf-8"))
        print(f"[tracking] Kalibrierung gespeichert: {d}")
    except OSError as e:
        print(f"[tracking] Speichern fehlgeschlagen: {e}")


def calib_start():
    global _calib_active, _calib_step, _calib
    _calib_active, _calib_step, _calib = True, 0, {}
    print("[tracking] Kalibrierung: Mond GANZ LINKS halten, dann Leertaste")


def calib_capture(mu, mv):
    """Nimmt für den aktuellen Schritt die Position auf (nur wenn ein Punkt sichtbar)."""
    global _calib_active, _calib_step, CROP_X0, CROP_X1, CROP_Y0, CROP_Y1
    if mu is None:
        print("[tracking] kein Punkt sichtbar -> nichts aufgenommen")
        return
    key = ("xl", "xr", "yt", "yb")[_calib_step]
    _calib[key] = mu / W_IR if key[0] == "x" else mv / H_IR
    _calib_step += 1
    if _calib_step < 4:
        print(f"[tracking] ok. Jetzt Mond {CALIB_STEPS[_calib_step]} halten, dann Leertaste")
        return
    x0, x1 = sorted((_calib["xl"], _calib["xr"]))
    y0, y1 = sorted((_calib["yt"], _calib["yb"]))
    CROP_X0, CROP_X1 = x0, max(x1, x0 + 0.02)
    CROP_Y0, CROP_Y1 = y0, max(y1, y0 + 0.02)
    _calib_active = False
    save_calib()


def normalize(u, v):
    """Pixel (u, v) -> normiert 0..1 über den Kalibrier-Ausschnitt."""
    x = (u / W_IR - CROP_X0) / (CROP_X1 - CROP_X0)
    y = (v / H_IR - CROP_Y0) / (CROP_Y1 - CROP_Y0)
    return min(1.0, max(0.0, x)), min(1.0, max(0.0, y))


# --- WebSocket-Server (sendet die Position an moon.py) ----------------
_state = {"active": False, "x": 0.5, "y": 0.5}
_state_lock = threading.Lock()


def set_state(active, x=0.5, y=0.5):
    with _state_lock:
        _state.update(active=active, x=x, y=y)


def _get_state():
    with _state_lock:
        return dict(_state)


async def _handler(ws, *_):
    try:
        while True:
            await ws.send(json.dumps(_get_state()))
            await asyncio.sleep(0.02)
    except Exception:
        pass


def start_server(host="127.0.0.1", port=8765):
    if websockets is None:
        print("[tracking] Paket 'websockets' fehlt -> pip install websockets (kein Senden)")
        return

    async def _serve():
        async with websockets.serve(_handler, host, port):
            await asyncio.Future()

    threading.Thread(target=lambda: asyncio.run(_serve()), daemon=True).start()
    print(f"[tracking] WebSocket-Server auf ws://{host}:{port}")


def robust_depth(depth, u, v, win=4):
    """Median der gültigen Tiefen in einem Fenster um (u,v) – umgeht den Sättigungskern."""
    h, w = H_IR, W_IR
    vals = []
    for du in range(-win, win + 1):
        for dv in range(-win, win + 1):
            x, y = int(round(u)) + du, int(round(v)) + dv
            if 0 <= x < w and 0 <= y < h:
                d = depth.get_distance(x, y)
                if d > 0:
                    vals.append(d)
    return float(np.median(vals)) if vals else 0.0


# --- RealSense-Pipeline ----------------------------------------------
pipe = rs.pipeline()
cfg = rs.config()
cfg.enable_stream(rs.stream.infrared, 1, 848, 480, rs.format.y8, 30)   # linker IR
cfg.enable_stream(rs.stream.depth,       848, 480, rs.format.z16, 30)
profile = pipe.start(cfg)

ds = profile.get_device().first_depth_sensor()
ds.set_option(rs.option.emitter_enabled, 0)          # Punktmuster aus
ds.set_option(rs.option.enable_auto_exposure, 0)
ds.set_option(rs.option.exposure, 800)               # runterregeln bis nur LEDs sichtbar
ds.set_option(rs.option.gain, 16)

intr = profile.get_stream(rs.stream.infrared, 1).as_video_stream_profile().get_intrinsics()

load_calib()
start_server()

try:
    while True:
        frames = pipe.wait_for_frames()
        ir    = frames.get_infrared_frame(1)
        depth = frames.get_depth_frame()
        img = np.asanyarray(ir.get_data())

        _, mask = cv2.threshold(img, 200, 255, cv2.THRESH_BINARY)
        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        pts3d = []
        uv_list = []
        for c in cnts:
            if cv2.contourArea(c) < 2:
                continue
            M = cv2.moments(c)
            if M["m00"] == 0:
                continue
            u, v = M["m10"] / M["m00"], M["m01"] / M["m00"]
            uv_list.append((u, v))
            cv2.circle(img, (int(u), int(v)), 6, 255, 1)
            z = robust_depth(depth, u, v)
            if z == 0:
                continue
            X, Y, Z = rs.rs2_deproject_pixel_to_point(intr, [u, v], z)
            pts3d.append((X, Y, Z))

        # --- gemittelter Schwerpunkt -> normieren -> an App senden ---
        last_mu = last_mv = None
        if uv_list:
            last_mu = sum(p[0] for p in uv_list) / len(uv_list)
            last_mv = sum(p[1] for p in uv_list) / len(uv_list)
            nx, ny = normalize(last_mu, last_mv)
            set_state(True, nx, ny)
            cv2.putText(img, f"send x={nx:.2f} y={ny:.2f}", (10, 55), FONT, 0.6, 255, 1)
        else:
            set_state(False)

        if pts3d:
            pos = np.mean(pts3d, axis=0)     # gemittelte Position in Metern (Kamera-Koordinaten)
            cv2.putText(img, f"X={pos[0]:+.3f} Y={pos[1]:+.3f} Z={pos[2]:+.3f} m  (n={len(pts3d)})",
                        (10, 30), FONT, 0.6, 255, 1)

        if _calib_active:
            cv2.putText(img, f"KALIBRIERUNG {_calib_step + 1}/4: Mond {CALIB_STEPS[_calib_step]} halten -> LEER",
                        (10, 100), FONT, 0.7, 255, 2)
            cv2.putText(img, "ESC = abbrechen", (10, 125), FONT, 0.5, 255, 1)
        else:
            cv2.putText(img, "C = Kalibrieren", (10, H_IR - 12), FONT, 0.5, 255, 1)

        cv2.imshow("IR", img)
        key = cv2.waitKey(1) & 0xFF
        if key == 27:                       # ESC
            if _calib_active:
                _calib_active = False
                print("[tracking] Kalibrierung abgebrochen")
            else:
                break
        elif key == ord('c') and not _calib_active:
            calib_start()
        elif key == ord(' ') and _calib_active:
            calib_capture(last_mu, last_mv)
finally:
    pipe.stop()
    cv2.destroyAllWindows()
