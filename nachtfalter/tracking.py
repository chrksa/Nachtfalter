"""
RealSense-Tracking -> WebSocket an die Nachtfalter-App (moon.py).

Erkennt helle Punkte im IR-Bild, mittelt ihren Schwerpunkt und sendet die
normierte Position als {"active":.., "x":.., "y":..} an ws://127.0.0.1:8765.

Tasten im Tracking-Fenster:
  C          Kalibrierung starten (Mond GANZ LINKS/RECHTS/OBEN/UNTEN halten)
  Leertaste  aktuellen Kalibrier-Schritt aufnehmen
  T          zwischen IR- und Blob-(Helligkeits-)Tracking umschalten
  M          im Blob-Modus die Maske einblenden (zum Tunen von BLOB_MIN_*)
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

# --- Blob-Tracking (Farbkamera) als Alternative zum IR-Tracking ----------
# Getrackt wird der hellste, moeglichst runde Blob (der leuchtende Mond).
# Es wird ueber die Helligkeit geschwellt und zusaetzlich nach Rundheit
# gefiltert. Zum Tunen im BLOB-Modus die Maske mit Taste M einblenden und die
# Werte hier anpassen.
BLOB_MIN_BRIGHT = 200    # Helligkeit 0..255, dunklere Pixel werden ignoriert
BLOB_MIN_AREA   = 30     # kleinere Blobs (Pixel) werden ignoriert
BLOB_MIN_CIRC   = 0.60   # Rundheit 0..1 (1 = perfekter Kreis); darunter verworfen

# Werden aus dieser Datei geladen/gespeichert (Taste S) und ueberschreiben dann
# die obigen Defaults; im BLOB-Modus per Regler im Maske-Fenster einstellbar.
SETTINGS_FILE = os.path.join(os.path.dirname(__file__), "blob_settings.json")
MASK_WIN = "Maske"


def load_settings():
    global BLOB_MIN_BRIGHT, BLOB_MIN_AREA, BLOB_MIN_CIRC
    try:
        d = json.load(open(SETTINGS_FILE, encoding="utf-8"))
        BLOB_MIN_BRIGHT = int(d["bright"])
        BLOB_MIN_AREA   = int(d["area"])
        BLOB_MIN_CIRC   = float(d["circ"])
        print(f"[tracking] Blob-Settings geladen: {d}")
    except Exception:
        print("[tracking] keine Blob-Settings -> Defaults (Taste S speichert)")


def save_settings():
    d = dict(bright=int(BLOB_MIN_BRIGHT), area=int(BLOB_MIN_AREA),
             circ=round(float(BLOB_MIN_CIRC), 2))
    try:
        json.dump(d, open(SETTINGS_FILE, "w", encoding="utf-8"))
        print(f"[tracking] Blob-Settings gespeichert: {d}")
    except OSError as e:
        print(f"[tracking] Speichern fehlgeschlagen: {e}")


def open_mask_window():
    """Maske-Fenster mit Reglern fuer die Blob-Settings anlegen."""
    cv2.namedWindow(MASK_WIN)
    cv2.createTrackbar("Hell",  MASK_WIN, int(BLOB_MIN_BRIGHT),       255,  lambda v: None)
    cv2.createTrackbar("Area",  MASK_WIN, int(BLOB_MIN_AREA),        2000,  lambda v: None)
    cv2.createTrackbar("Rund%", MASK_WIN, int(BLOB_MIN_CIRC * 100),   100,  lambda v: None)


def read_mask_trackbars():
    """Reglerstellungen in die Blob-Settings uebernehmen (falls Fenster offen)."""
    global BLOB_MIN_BRIGHT, BLOB_MIN_AREA, BLOB_MIN_CIRC
    try:
        BLOB_MIN_BRIGHT = cv2.getTrackbarPos("Hell",  MASK_WIN)
        BLOB_MIN_AREA   = cv2.getTrackbarPos("Area",  MASK_WIN)
        BLOB_MIN_CIRC   = cv2.getTrackbarPos("Rund%", MASK_WIN) / 100.0
    except cv2.error:
        pass   # Fenster (noch) nicht da


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
    peer = getattr(ws, "remote_address", None)
    print(f"[tracking] Client verbunden: {peer}")
    try:
        while True:
            await ws.send(json.dumps(_get_state()))
            await asyncio.sleep(0.02)
    except Exception:
        pass
    finally:
        print(f"[tracking] Client getrennt: {peer}")


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


# --- Erkennung: IR-Punkte bzw. Farb-Blob -----------------------------
def detect_ir(img, vis):
    """Helle IR-Punkte -> Liste von (u, v)-Schwerpunkten; zeichnet Marker in vis."""
    _, mask = cv2.threshold(img, 200, 255, cv2.THRESH_BINARY)
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    uv = []
    for c in cnts:
        if cv2.contourArea(c) < 2:
            continue
        M = cv2.moments(c)
        if M["m00"] == 0:
            continue
        u, v = M["m10"] / M["m00"], M["m01"] / M["m00"]
        uv.append((u, v))
        cv2.circle(vis, (int(u), int(v)), 6, (0, 255, 0), 1)
    return uv


def detect_blob(bgr):
    """Hellsten, runden Blob finden -> ([(u, v)], mask); Marker in bgr."""
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    _, mask = cv2.threshold(gray, BLOB_MIN_BRIGHT, 255, cv2.THRESH_BINARY)
    kernel = np.ones((5, 5), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN,  kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    # hell genug + rund genug -> davon den groessten Blob nehmen
    best, best_area = None, 0.0
    for c in cnts:
        area = cv2.contourArea(c)
        if area < BLOB_MIN_AREA:
            continue
        peri = cv2.arcLength(c, True)
        if peri == 0:
            continue
        circ = 4.0 * np.pi * area / (peri * peri)   # 1.0 = perfekter Kreis
        if circ < BLOB_MIN_CIRC:
            continue
        if area > best_area:
            best, best_area = c, area

    if best is None:
        return [], mask
    M = cv2.moments(best)
    if M["m00"] == 0:
        return [], mask
    u, v = M["m10"] / M["m00"], M["m01"] / M["m00"]
    (cx, cy), r = cv2.minEnclosingCircle(best)
    cv2.circle(bgr, (int(cx), int(cy)), int(r), (0, 255, 0), 2)
    cv2.circle(bgr, (int(u), int(v)), 4, (0, 0, 255), -1)
    return [(u, v)], mask


# --- RealSense-Pipeline ----------------------------------------------
pipe = rs.pipeline()
cfg = rs.config()
cfg.enable_stream(rs.stream.infrared, 1, 848, 480, rs.format.y8, 30)   # linker IR
cfg.enable_stream(rs.stream.depth,       848, 480, rs.format.z16, 30)
cfg.enable_stream(rs.stream.color,       848, 480, rs.format.bgr8, 30)  # RGB fuer Blob
profile = pipe.start(cfg)

ds = profile.get_device().first_depth_sensor()
ds.set_option(rs.option.emitter_enabled, 0)          # Punktmuster aus
ds.set_option(rs.option.enable_auto_exposure, 0)
ds.set_option(rs.option.exposure, 800)               # runterregeln bis nur LEDs sichtbar
ds.set_option(rs.option.gain, 16)

intr = profile.get_stream(rs.stream.infrared, 1).as_video_stream_profile().get_intrinsics()

load_calib()
load_settings()
start_server(host="0.0.0.0")   # ans LAN binden, damit der Mac ueber Kabel verbindet

mode = "IR"          # aktueller Tracking-Modus: "IR" oder "BLOB"
show_mask = False    # im BLOB-Modus die Farbmaske in extra Fenster zeigen

try:
    while True:
        frames = pipe.wait_for_frames()

        # --- Erkennung je nach Modus -> vis (Anzeige) + uv_list (Punkte) ---
        if mode == "IR":
            ir  = frames.get_infrared_frame(1)
            img = np.asanyarray(ir.get_data())
            vis = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
            uv_list = detect_ir(img, vis)

            depth = frames.get_depth_frame()   # nur im IR-Modus fuer 3D-Anzeige
            pts3d = []
            for (u, v) in uv_list:
                z = robust_depth(depth, u, v)
                if z > 0:
                    pts3d.append(rs.rs2_deproject_pixel_to_point(intr, [u, v], z))
            if pts3d:
                pos = np.mean(pts3d, axis=0)   # gemittelte Position in Metern (Kamera-Koords)
                cv2.putText(vis, f"X={pos[0]:+.3f} Y={pos[1]:+.3f} Z={pos[2]:+.3f} m  (n={len(pts3d)})",
                            (10, 30), FONT, 0.6, (0, 255, 0), 1)
        else:  # BLOB (Farbkamera)
            color = frames.get_color_frame()
            vis   = np.asanyarray(color.get_data()).copy()
            if show_mask:
                read_mask_trackbars()          # Regler -> Settings uebernehmen
            uv_list, mask = detect_blob(vis)
            if show_mask:
                cv2.imshow(MASK_WIN, mask)

        # --- gemittelter Schwerpunkt -> normieren -> an App senden (beide Modi) ---
        last_mu = last_mv = None
        if uv_list:
            last_mu = sum(p[0] for p in uv_list) / len(uv_list)
            last_mv = sum(p[1] for p in uv_list) / len(uv_list)
            nx, ny = normalize(last_mu, last_mv)
            set_state(True, nx, ny)
            cv2.putText(vis, f"send x={nx:.2f} y={ny:.2f}", (10, 55), FONT, 0.6, (0, 255, 0), 1)
        else:
            set_state(False)

        cv2.putText(vis, f"Modus: {mode}  (T = wechseln)", (10, 80), FONT, 0.6, (0, 255, 255), 2)
        if _calib_active:
            cv2.putText(vis, f"KALIBRIERUNG {_calib_step + 1}/4: Mond {CALIB_STEPS[_calib_step]} halten -> LEER",
                        (10, 110), FONT, 0.7, (0, 255, 0), 2)
            cv2.putText(vis, "ESC = abbrechen", (10, 135), FONT, 0.5, (0, 255, 0), 1)
        else:
            hint = "C = Kalibrieren   T = IR/Blob"
            if mode == "BLOB":
                hint += "   M = Maske/Regler   S = Settings speichern"
            cv2.putText(vis, hint, (10, H_IR - 12), FONT, 0.5, (0, 255, 0), 1)

        cv2.imshow("Tracking", vis)
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
        elif key == ord('t') and not _calib_active:
            mode = "BLOB" if mode == "IR" else "IR"
            if mode == "IR":                    # Maske gehoert nur zum BLOB-Modus
                show_mask = False
                try:
                    cv2.destroyWindow(MASK_WIN)
                except Exception:
                    pass
            print(f"[tracking] Modus -> {mode}")
        elif key == ord('s') and not _calib_active:
            save_settings()
        elif key == ord('m') and mode == "BLOB":
            show_mask = not show_mask
            if show_mask:
                open_mask_window()              # Fenster + Regler anlegen
            else:
                try:
                    cv2.destroyWindow(MASK_WIN)
                except Exception:
                    pass
finally:
    pipe.stop()
    cv2.destroyAllWindows()
