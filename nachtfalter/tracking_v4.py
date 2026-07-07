"""
IR-LED-Tracking v4 -> WebSocket an die Nachtfalter-App (moon.py).

EINE D455 trackt allein. Der Mond hat IR-LEDs eingebaut, die als helle Punkte
im IR-Bild erkannt werden. Aus der Tiefe (D455-Stereo) wird die 3D-Position
bestimmt und daraus:

  * Spiel-HOEHE   = reale Hoehe des Mondes (entfernungs-unabhaengig, aus 3D).
  * Spiel LINKS/RECHTS = Naehe zur Kamera (Tiefe): je naeher -> weiter RECHTS.

Eine 6-Punkt-Kalibrierung (je 3 bei FERN und NAH: oben/mitte/unten) legt die
Spielkoordinaten fest: gx aus der Distanz (nah->0/fern->1), gy aus einem
DISTANZ-ABHAENGIGEN Hoehenband (Trapez) -> deckt die ganze Spielflaeche ab,
auch wenn der erreichbare Hoehenbereich nah anders ist als fern.
Die Bildschirm-Seite (links/rechts) stellt flipX in config.py.

Tasten:
  C          Kalibrierung starten (6 Punkte: 3 fern, 3 nah)
  Leertaste  aktuellen Kalibrier-Schritt aufnehmen
  M          Regler (Schwelle, IR-Belichtung, Tiefen-Fenster) ein/aus
  S          Settings speichern      R  ROI zuruecksetzen
  X          Ausschluss-Zonen loeschen
  Maus       links ziehen = Erkennungsbereich, rechts ziehen = Ausschluss-Zone
  E          IR-Emitter an/aus (falls die Tiefe ihn braucht)
  ESC        Kalibrierung abbrechen / sonst Programm beenden

WebSocket-Protokoll identisch zu v2/v3: {"active","x","y"} alle 20 ms.
moon.py muss NICHT angepasst werden.
"""
import os
import json
import math
import time
import threading
import asyncio

import pyrealsense2 as rs
import numpy as np
import cv2

try:
    import websockets
except ImportError:
    websockets = None

W_IMG, H_IMG = 848, 480
FONT = cv2.FONT_HERSHEY_SIMPLEX
BASE = os.path.dirname(__file__)

TRACK_WIN = "IR-Tracking"
MASK_WIN = "Regler"

USE_EMITTER = False      # IR-Emitter aus -> sauberes LED-Bild (E schaltet live um)

# Welche Kamera-Achse ist die REALE HOEHE?
#   "x" = Kamera um 90 Grad gedreht -> Hoehe = Bild-Horizontale, Bild-Vertikale
#         (Kamera-Y) wird ignoriert (hoch/runter im Bild ohne Wirkung).
#   "y" = Kamera aufrecht montiert.
# Distanz (links/rechts im Spiel) ist immer die Tiefe Z, von der Drehung unberuehrt.
HEIGHT_AXIS = "x"


# =====================================================================
# One-Euro-Filter + MoonTracker (Glaettung/Gating im Spielkoordinaten-Raum)
# =====================================================================
class _LowPass:
    def __init__(self):
        self.y = None

    def filt(self, x, alpha):
        self.y = x if self.y is None else alpha * x + (1.0 - alpha) * self.y
        return self.y


class OneEuroFilter:
    """Casiez et al. 2012. min_cutoff runter = glatter, beta hoch = reaktiver."""

    def __init__(self, min_cutoff=0.8, beta=18.0, d_cutoff=1.0):
        self.min_cutoff, self.beta, self.d_cutoff = min_cutoff, beta, d_cutoff
        self._x, self._dx = _LowPass(), _LowPass()
        self._last = None

    @staticmethod
    def _alpha(cutoff, dt):
        tau = 1.0 / (2.0 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def filt(self, x, dt):
        if dt <= 0:
            dt = 1e-3
        dx = 0.0 if self._last is None else (x - self._last) / dt
        self._last = x
        edx = self._dx.filt(dx, self._alpha(self.d_cutoff, dt))
        cutoff = self.min_cutoff + self.beta * abs(edx)
        return self._x.filt(x, self._alpha(cutoff, dt))

    def reset(self):
        self._x, self._dx, self._last = _LowPass(), _LowPass(), None


class MoonTracker:
    """Zeitlich konsistentes 2D-Tracking im Spielkoordinaten-Raum (0..1).

    Gate/Hold/Re-Akquisition gegen Ausreisser + One-Euro-Glaettung.
    Alle Radien sind in Spielkoordinaten (Bilddiagonale ~1.4)."""

    def __init__(self, gate=0.09, gate_growth=1.4, gate_max_factor=4.0,
                 reacquire_frames=5, reacquire_radius=0.06, hold_frames=12,
                 min_cutoff=0.8, beta=18.0):
        self.gate = gate
        self.gate_growth = gate_growth
        self.gate_max = gate * gate_max_factor
        self.reacquire_frames = reacquire_frames
        self.reacquire_radius = reacquire_radius
        self.hold_frames = hold_frames
        self._fx = OneEuroFilter(min_cutoff, beta)
        self._fy = OneEuroFilter(min_cutoff, beta)
        self.reset()

    def reset(self):
        self._pos = None
        self._vel = (0.0, 0.0)
        self._lost = 0
        self._cand, self._cand_count = None, 0
        self._fx.reset()
        self._fy.reset()

    @staticmethod
    def _dist(a, b):
        return math.hypot(a[0] - b[0], a[1] - b[1])

    def _predict(self, dt):
        return (self._pos[0] + self._vel[0] * dt,
                self._pos[1] + self._vel[1] * dt)

    def _accept(self, p, dt):
        if dt > 0:
            vx = (p[0] - self._pos[0]) / dt
            vy = (p[1] - self._pos[1]) / dt
            self._vel = (0.6 * self._vel[0] + 0.4 * vx,
                         0.6 * self._vel[1] + 0.4 * vy)
        self._pos = p
        self._lost = 0
        self._cand, self._cand_count = None, 0

    def _hard_reset_to(self, p):
        self._pos, self._vel = p, (0.0, 0.0)
        self._lost, self._cand, self._cand_count = 0, None, 0
        self._fx.reset()
        self._fy.reset()

    @property
    def current_gate(self):
        return min(self.gate * (self.gate_growth ** self._lost), self.gate_max)

    def update(self, candidates, dt):
        """candidates: Liste (x, y, score) in Spielkoordinaten -> geglaettete (x, y) oder None."""
        cands = list(candidates)

        if self._pos is None:
            if cands:
                best = max(cands, key=lambda c: c[2])
                self._hard_reset_to((best[0], best[1]))
                return self._smooth(dt)
            return None

        pred = self._predict(dt)
        gate = self.current_gate
        inside = [c for c in cands if self._dist((c[0], c[1]), pred) <= gate]

        if inside:
            best = min(inside, key=lambda c: (self._dist((c[0], c[1]), pred), -c[2]))
            self._accept((best[0], best[1]), dt)
            return self._smooth(dt)

        self._lost += 1
        if cands:
            best = max(cands, key=lambda c: c[2])
            p = (best[0], best[1])
            if self._cand is not None and self._dist(p, self._cand) <= self.reacquire_radius:
                self._cand_count += 1
                n = self._cand_count
                self._cand = ((self._cand[0] * (n - 1) + p[0]) / n,
                              (self._cand[1] * (n - 1) + p[1]) / n)
            else:
                self._cand, self._cand_count = p, 1
            if self._cand_count >= self.reacquire_frames:
                self._hard_reset_to(self._cand)
                return self._smooth(dt)

        if self._lost <= self.hold_frames:
            self._pos = self._predict(dt)
            return self._smooth(dt)

        self._pos = None
        self._vel = (0.0, 0.0)
        self._fx.reset()
        self._fy.reset()
        return None

    def _smooth(self, dt):
        x = min(1.0, max(0.0, self._fx.filt(self._pos[0], dt)))
        y = min(1.0, max(0.0, self._fy.filt(self._pos[1], dt)))
        return (x, y)


tracker = MoonTracker()


# =====================================================================
# Settings (Schwelle, IR-Belichtung, Tiefen-Fenster, ROI, Ausschluesse)
# =====================================================================
IR_THRESH   = 200        # Helligkeits-Schwelle fuer LED-Erkennung 0..255
IR_EXPOSURE = 4000        # feste Belichtung der IR/Stereo-Kamera (RealSense-Rohwert)
DEPTH_WIN   = 9          # Kantenlaenge des Tiefen-Messfensters (Pixel)
DEPTH_MIN_VALID = 0.25   # Mindestanteil gueltiger Tiefen-Pixel im Fenster
DEPTH_PCT   = 20         # Perzentil der Tiefe im Fenster: NIEDRIG = naechste
                         # Flaeche (Mond), ignoriert weiter entfernten Hintergrund.
                         # 50 = Median (alt), 20 = vorderste 20 %.

ROI = [0.0, 0.0, 1.0, 1.0]
EXCLUDES = []
_roi_drag = None
_excl_drag = None

SETTINGS_FILE = os.path.join(BASE, "blob_settings_v4.json")
CALIB_FILE = os.path.join(BASE, "tracking_v4_calib.json")


def load_settings():
    global IR_THRESH, IR_EXPOSURE, DEPTH_WIN, DEPTH_MIN_VALID, DEPTH_PCT
    try:
        d = json.load(open(SETTINGS_FILE, encoding="utf-8"))
        IR_THRESH   = int(d.get("thresh", IR_THRESH))
        IR_EXPOSURE = int(d.get("exposure", IR_EXPOSURE))
        DEPTH_WIN   = int(d.get("depth_win", DEPTH_WIN))
        DEPTH_MIN_VALID = float(d.get("depth_min_valid", DEPTH_MIN_VALID))
        DEPTH_PCT   = int(d.get("depth_pct", DEPTH_PCT))
        if isinstance(d.get("roi"), list) and len(d["roi"]) == 4:
            ROI[:] = [float(c) for c in d["roi"]]
        if isinstance(d.get("excludes"), list):
            EXCLUDES[:] = [list(map(float, r)) for r in d["excludes"] if len(r) == 4]
        print(f"[tracking] Settings geladen: {d}")
    except Exception:
        print("[tracking] keine Settings -> Defaults (Taste S speichert)")


def save_settings():
    d = dict(thresh=int(IR_THRESH), exposure=int(IR_EXPOSURE),
             depth_win=int(DEPTH_WIN), depth_min_valid=round(float(DEPTH_MIN_VALID), 2),
             depth_pct=int(DEPTH_PCT),
             roi=[round(c, 4) for c in ROI],
             excludes=[[round(c, 4) for c in r] for r in EXCLUDES])
    try:
        json.dump(d, open(SETTINGS_FILE, "w", encoding="utf-8"))
        print(f"[tracking] Settings gespeichert: {d}")
    except OSError as e:
        print(f"[tracking] Speichern fehlgeschlagen: {e}")


def open_mask_window():
    cv2.namedWindow(MASK_WIN)
    cv2.createTrackbar("Schwelle", MASK_WIN, int(IR_THRESH),            255, lambda v: None)
    cv2.createTrackbar("Exp",      MASK_WIN, int(IR_EXPOSURE),        20000, lambda v: None)
    cv2.createTrackbar("TiefeWin", MASK_WIN, int(DEPTH_WIN),             41, lambda v: None)
    cv2.createTrackbar("Gueltig%", MASK_WIN, int(DEPTH_MIN_VALID * 100), 100, lambda v: None)
    cv2.createTrackbar("TiefePct", MASK_WIN, int(DEPTH_PCT),            100, lambda v: None)


def read_mask_trackbars():
    global IR_THRESH, IR_EXPOSURE, DEPTH_WIN, DEPTH_MIN_VALID, DEPTH_PCT
    try:
        IR_THRESH   = cv2.getTrackbarPos("Schwelle", MASK_WIN)
        IR_EXPOSURE = max(1, cv2.getTrackbarPos("Exp", MASK_WIN))
        DEPTH_WIN   = max(3, cv2.getTrackbarPos("TiefeWin", MASK_WIN) | 1)  # ungerade
        DEPTH_MIN_VALID = cv2.getTrackbarPos("Gueltig%", MASK_WIN) / 100.0
        DEPTH_PCT   = max(1, cv2.getTrackbarPos("TiefePct", MASK_WIN))
    except cv2.error:
        pass


# =====================================================================
# ROI + Ausschluss-Zonen
# =====================================================================
def _rect_norm(p0, p1):
    x0, x1 = sorted((p0[0], p1[0])); y0, y1 = sorted((p0[1], p1[1]))
    return [x0 / W_IMG, y0 / H_IMG, x1 / W_IMG, y1 / H_IMG]


def on_tracking_mouse(event, x, y, flags, param):
    global _roi_drag, _excl_drag
    if event == cv2.EVENT_LBUTTONDOWN:
        _roi_drag = (x, y)
    elif event == cv2.EVENT_LBUTTONUP and _roi_drag is not None:
        r = _rect_norm(_roi_drag, (x, y))
        _roi_drag = None
        if (r[2] - r[0]) < 0.02 or (r[3] - r[1]) < 0.02:
            ROI[:] = [0.0, 0.0, 1.0, 1.0]
            print("[tracking] ROI zurueckgesetzt (ganzes Bild)")
        else:
            ROI[:] = r
            print(f"[tracking] ROI gesetzt: {[round(c, 3) for c in ROI]}")
    elif event == cv2.EVENT_RBUTTONDOWN:
        _excl_drag = (x, y)
    elif event == cv2.EVENT_RBUTTONUP and _excl_drag is not None:
        r = _rect_norm(_excl_drag, (x, y))
        _excl_drag = None
        if (r[2] - r[0]) >= 0.01 and (r[3] - r[1]) >= 0.01:
            EXCLUDES.append(r)
            print(f"[tracking] Ausschluss-Zone #{len(EXCLUDES)}")
    elif event == cv2.EVENT_MOUSEMOVE:
        if _roi_drag is not None and (flags & cv2.EVENT_FLAG_LBUTTON):
            ROI[:] = _rect_norm(_roi_drag, (x, y))


def apply_roi(mask):
    h, w = mask.shape[:2]
    x0 = max(0, min(w, int(ROI[0] * w))); x1 = max(0, min(w, int(ROI[2] * w)))
    y0 = max(0, min(h, int(ROI[1] * h))); y1 = max(0, min(h, int(ROI[3] * h)))
    if x0 > 0: mask[:, :x0] = 0
    if x1 < w: mask[:, x1:] = 0
    if y0 > 0: mask[:y0, :] = 0
    if y1 < h: mask[y1:, :] = 0
    for r in EXCLUDES:
        ex0, ey0 = int(r[0] * w), int(r[1] * h)
        ex1, ey1 = int(r[2] * w), int(r[3] * h)
        mask[ey0:ey1, ex0:ex1] = 0
    return mask


# =====================================================================
# Erkennung: helle IR-Punkte (LED-Cluster) -> flaechengewichteter Schwerpunkt
# =====================================================================
def detect_leds(img, vis):
    """Alle hellen IR-Punkte -> EIN Schwerpunkt (u, v, score) oder None."""
    _, mask = cv2.threshold(img, IR_THRESH, 255, cv2.THRESH_BINARY)
    apply_roi(mask)
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    pts, total = [], 0.0
    for c in cnts:
        area = cv2.contourArea(c)
        if area < 2:
            continue
        M = cv2.moments(c)
        if M["m00"] == 0:
            continue
        u, v = M["m10"] / M["m00"], M["m01"] / M["m00"]
        pts.append((u, v, area))
        total += area
        cv2.circle(vis, (int(u), int(v)), 6, (0, 160, 255), 1)
    if not pts:
        return None
    mu = sum(p[0] * p[2] for p in pts) / total
    mv = sum(p[1] * p[2] for p in pts) / total
    return (mu, mv, total)


# =====================================================================
# Tiefe -> Messvektor (Distanz, Hoehe)
# =====================================================================
def sample_depth(depth_m, u, v):
    """Median-Entfernung (Meter) in einem Fenster um (u, v) + Gueltig-Quote."""
    if depth_m is None:
        return None, 0.0
    r = DEPTH_WIN // 2
    y0, y1 = max(0, int(v) - r), min(depth_m.shape[0], int(v) + r + 1)
    x0, x1 = max(0, int(u) - r), min(depth_m.shape[1], int(u) + r + 1)
    patch = depth_m[y0:y1, x0:x1]
    if patch.size == 0:
        return None, 0.0
    valid = patch[patch > 0]
    ratio = valid.size / patch.size
    if valid.size == 0 or ratio < DEPTH_MIN_VALID:
        return None, ratio
    # NAECHSTE Flaeche statt Median: der Mond ist immer das vorderste Objekt,
    # so zieht weiter entfernter Hintergrund (bes. bei LED-Saettigungsloechern
    # nah an der Kamera) den Wert nicht mehr Richtung "fern".
    return float(np.percentile(valid, DEPTH_PCT)), ratio


def measure(u, v, Z, intrin):
    """(u, v, Z) -> Messvektor (distance, height) in Metern.

    distance = Z (Tiefe entlang Achse = Naehe zur Kamera),
    height   = deprojizierte, entfernungs-unabhaengige reale Hoehe.

    Bei GEDREHTER Kamera (HEIGHT_AXIS='x') ist die reale Hoehe die Kamera-X-
    Achse (Bild-Horizontale); die Kamera-Y-Achse (Bild-Vertikale) wird komplett
    IGNORIERT -> hoch/runter im Kamerabild hat keine Wirkung."""
    x, y, z = rs.rs2_deproject_pixel_to_point(intrin, [float(u), float(v)], float(Z))
    height = x if HEIGHT_AXIS == "x" else y
    return z, height        # (distance, height)


# =====================================================================
# 6-Punkt-Kalibrierung (3 fern, 3 nah) -> Trapez-Mapping (Distanz, Hoehe)
# =====================================================================
# 6-Punkt-Kalibrierung: je 3 Punkte bei FERN und NAH (oben/mitte/unten).
# Damit deckt das Tracking die ganze Spielflaeche ab, auch wenn der erreichbare
# Hoehenbereich nah anders ist als fern (Trapez statt Rechteck).
CALIB_STEPS = [
    "FERN OBEN",   # 0  fern, hoechste Position
    "FERN MITTE",  # 1  fern, mittlere Hoehe
    "FERN UNTEN",  # 2  fern, tiefste Position
    "NAH OBEN",    # 3  nah,  hoechste Position
    "NAH MITTE",   # 4  nah,  mittlere Hoehe
    "NAH UNTEN",   # 5  nah,  tiefste Position
]
N_CALIB = len(CALIB_STEPS)

_src_pts = None          # 6 Messvektoren (distance, height)
_model = None            # (znear, zfar, near=(top,mid,bot), far=(top,mid,bot))
_calib_active = False
_calib_step = 0
_calib_pts = []


def _rebuild_model():
    """Aus den 6 Punkten das Mapping-Modell bauen: Distanz nah/fern (Mittel je 3)
    und die Hoehen-Anker (oben/mitte/unten) bei nah und fern."""
    global _model
    if _src_pts is None or len(_src_pts) != N_CALIB:
        _model = None
        return
    Z = [p[0] for p in _src_pts]
    H = [p[1] for p in _src_pts]
    z_far  = (Z[0] + Z[1] + Z[2]) / 3.0
    z_near = (Z[3] + Z[4] + Z[5]) / 3.0
    near = (H[3], H[4], H[5])                 # oben, mitte, unten (nah)
    far  = (H[0], H[1], H[2])                 # oben, mitte, unten (fern)
    _model = (z_near, z_far, near, far)


def _piecewise(v, a, b, c):
    """Stueckweise linear: a->0.0, b->0.5, c->1.0 (a,b,c monoton oben->unten,
    Richtung egal). Ausserhalb wird geklemmt."""
    if (v - b) * (a - b) >= 0:                # v auf der a-Seite von b
        return 0.5 * (v - a) / (b - a) if abs(b - a) > 1e-9 else 0.0
    return 0.5 + 0.5 * (v - b) / (c - b) if abs(c - b) > 1e-9 else 1.0


def load_calib():
    global _src_pts
    try:
        d = json.load(open(CALIB_FILE, encoding="utf-8"))
        pts = d.get("points")
        if isinstance(pts, list) and len(pts) == N_CALIB:
            _src_pts = [[float(p[0]), float(p[1])] for p in pts]
            _rebuild_model()
            print(f"[tracking] Kalibrierung geladen ({N_CALIB} Punkte)")
        else:
            print(f"[tracking] Kalibrierung passt nicht ({N_CALIB} Punkte noetig) -> Taste C")
    except Exception:
        print(f"[tracking] keine Kalibrierung -> Taste C ({N_CALIB} Punkte aufnehmen)")


def save_calib():
    if _src_pts is None:
        return
    try:
        json.dump({"points": _src_pts}, open(CALIB_FILE, "w", encoding="utf-8"))
        print(f"[tracking] Kalibrierung gespeichert ({len(_src_pts)} Punkte)")
    except OSError as e:
        print(f"[tracking] Speichern fehlgeschlagen: {e}")


def calib_start():
    global _calib_active, _calib_step, _calib_pts
    _calib_active, _calib_step, _calib_pts = True, 0, []
    print(f"[tracking] Kalibrierung: Mond auf {CALIB_STEPS[0]} halten, dann Leertaste")


def calib_capture(m):
    """m: aktueller Messvektor (distance, height) oder None."""
    global _calib_active, _calib_step, _src_pts
    if not _calib_active:
        return
    if m is None:
        print("[tracking] keine gueltige Tiefe -> nichts aufgenommen")
        return
    _calib_pts.append([float(m[0]), float(m[1])])
    _calib_step += 1
    if _calib_step < N_CALIB:
        print(f"[tracking] ok. Jetzt {CALIB_STEPS[_calib_step]}, dann Leertaste")
        return
    _src_pts = list(_calib_pts)
    _rebuild_model()
    _calib_active = False
    save_calib()
    tracker.reset()
    print("[tracking] Kalibrierung fertig - Tracking laeuft in Spielkoordinaten")


def to_game(m):
    """Messvektor (distance, height) -> Spielkoordinaten (gx, gy) oder None.

    gx aus der Distanz (nah->0, fern->1). gy aus einem DISTANZ-ABHAENGIGEN
    Hoehenband: oben/mitte/unten werden zwischen nah und fern interpoliert und
    stueckweise auf 0/0.5/1 abgebildet -> volle Spielhoehe bei jeder Distanz."""
    if _model is None or m is None:
        return None
    Z, h = m
    zn, zf, near, far = _model
    nt, nm, nb = near
    ft, fm, fb = far
    dz = zf - zn
    t = (Z - zn) / dz if abs(dz) > 1e-9 else 0.5
    gx = min(1.0, max(0.0, t))
    tc = min(1.0, max(0.0, t))                       # fuer die Hoehen-Anker
    top = nt + (ft - nt) * tc
    mid = nm + (fm - nm) * tc
    bot = nb + (fb - nb) * tc
    gy = _piecewise(h, top, mid, bot)
    return (gx, min(1.0, max(0.0, gy)))


# =====================================================================
# WebSocket-Server (identisch: {"active","x","y"} alle 20 ms)
# =====================================================================
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


# =====================================================================
# RealSense: IR(1) + Tiefe, Emitter aus, IR fest belichtet
# =====================================================================
def find_device():
    ctx = rs.context()
    for d in ctx.devices:
        try:
            if "platform camera" in d.get_info(rs.camera_info.name).lower():
                continue
            return d.get_info(rs.camera_info.serial_number)
        except Exception:
            pass
    return None


serial = find_device()
if serial is None:
    raise SystemExit("[tracking] keine RealSense-Kamera gefunden.")

pipe = rs.pipeline()
cfg = rs.config()
cfg.enable_device(serial)
cfg.enable_stream(rs.stream.infrared, 1, W_IMG, H_IMG, rs.format.y8, 30)
cfg.enable_stream(rs.stream.depth, W_IMG, H_IMG, rs.format.z16, 30)
profile = pipe.start(cfg)

_depth_sensor = profile.get_device().first_depth_sensor()
_depth_scale = _depth_sensor.get_depth_scale()
_depth_intrin = profile.get_stream(rs.stream.depth) \
    .as_video_stream_profile().get_intrinsics()

_last_exp = None


def set_emitter(on):
    try:
        _depth_sensor.set_option(rs.option.emitter_enabled, 1 if on else 0)
    except Exception as e:
        print(f"[tracking] Emitter setzen fehlgeschlagen: {e}")


def apply_ir_exposure(force=False):
    """Feste IR-Belichtung -> LEDs bleiben scharfe Punkte (kein Bloom)."""
    global _last_exp
    if not force and _last_exp == IR_EXPOSURE:
        return
    try:
        _depth_sensor.set_option(rs.option.enable_auto_exposure, 0)
        _depth_sensor.set_option(rs.option.exposure, float(IR_EXPOSURE))
        _last_exp = IR_EXPOSURE
    except Exception as e:
        print(f"[tracking] IR-Belichtung setzen fehlgeschlagen: {e}")


def setup_sensor():
    set_emitter(USE_EMITTER)
    apply_ir_exposure(force=True)


# =====================================================================
# Hauptprogramm
# =====================================================================
load_settings()
load_calib()
setup_sensor()
start_server(host="0.0.0.0")

show_mask = False
emitter_on = USE_EMITTER

cv2.namedWindow(TRACK_WIN)
cv2.setMouseCallback(TRACK_WIN, on_tracking_mouse)

_last_t = time.monotonic()

try:
    while True:
        frames = pipe.wait_for_frames()
        now = time.monotonic()
        dt = max(1e-3, now - _last_t)
        _last_t = now

        if show_mask:
            read_mask_trackbars()
            apply_ir_exposure()

        ir = frames.get_infrared_frame(1)
        depth = frames.get_depth_frame()
        img = np.asanyarray(ir.get_data())
        vis = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        depth_m = (np.asanyarray(depth.get_data()).astype(np.float32) * _depth_scale
                   if depth else None)

        # --- Erkennung + Tiefe -> Messvektor ---
        led = detect_leds(img, vis)
        m = None
        Zval = ratio = None
        if led is not None:
            Zval, ratio = sample_depth(depth_m, led[0], led[1])
            if Zval is not None:
                m = measure(led[0], led[1], Zval, _depth_intrin)   # (distance, height)

        # --- Kalibrierung oder Tracking ---
        if _calib_active:
            set_state(False)
            gp = None
        else:
            gp = to_game(m)                                        # (gx, gy) oder None
            cand = [(gp[0], gp[1], led[2])] if gp is not None else []
            tp = tracker.update(cand, dt)
            if tp is not None:
                set_state(True, tp[0], tp[1])
                gp = tp
            else:
                set_state(False)
                gp = None

        # --- Overlays ---
        if ROI != [0.0, 0.0, 1.0, 1.0]:
            rx0, ry0 = int(ROI[0] * W_IMG), int(ROI[1] * H_IMG)
            rx1, ry1 = int(ROI[2] * W_IMG), int(ROI[3] * H_IMG)
            cv2.rectangle(vis, (rx0, ry0), (rx1, ry1), (255, 0, 255), 1)
        for i, r in enumerate(EXCLUDES):
            ex0, ey0 = int(r[0] * W_IMG), int(r[1] * H_IMG)
            ex1, ey1 = int(r[2] * W_IMG), int(r[3] * H_IMG)
            cv2.rectangle(vis, (ex0, ey0), (ex1, ey1), (0, 0, 255), 1)
            cv2.putText(vis, f"X{i + 1}", (ex0 + 3, ey0 + 15), FONT, 0.5, (0, 0, 255), 1)

        if led is not None:
            col = (0, 255, 0) if (ratio or 0) >= DEPTH_MIN_VALID else (0, 0, 255)
            cv2.drawMarker(vis, (int(led[0]), int(led[1])), col, cv2.MARKER_CROSS, 18, 2)

        cv2.putText(vis, f"IR-Tracking  Emitter {'AN' if emitter_on else 'AUS'}",
                    (10, 28), FONT, 0.6, (0, 255, 255), 2)

        if Zval is not None:
            cv2.putText(vis, f"Dist {Zval:.2f} m  Hoehe {m[1]:+.2f} m  "
                             f"Tiefe-gueltig {ratio*100:.0f}%",
                        (10, 52), FONT, 0.55, (200, 255, 200), 1)
        elif led is not None:
            cv2.putText(vis, f"LED gefunden, aber TIEFE ungueltig "
                             f"({(ratio or 0)*100:.0f}%) -> Belichtung/Emitter pruefen",
                        (10, 52), FONT, 0.55, (0, 0, 255), 1)
        else:
            cv2.putText(vis, "kein IR-Punkt (Schwelle/ROI pruefen)",
                        (10, 52), FONT, 0.55, (0, 0, 255), 1)

        if _calib_active:
            cv2.putText(vis, f"KALIBRIERUNG {_calib_step + 1}/{N_CALIB}: "
                             f"{CALIB_STEPS[_calib_step]} -> LEER",
                        (10, 82), FONT, 0.6, (0, 255, 0), 2)
            cv2.putText(vis, "ESC = abbrechen", (10, 104), FONT, 0.5, (0, 255, 0), 1)
        elif gp is not None:
            cv2.putText(vis, f"send x={gp[0]:.2f} y={gp[1]:.2f}",
                        (10, 82), FONT, 0.6, (0, 255, 0), 1)
            if _model is None:
                cv2.putText(vis, "UNKALIBRIERT -> Taste C", (10, 104), FONT, 0.6, (0, 0, 255), 2)
        elif _model is None:
            cv2.putText(vis, f"UNKALIBRIERT -> Taste C ({N_CALIB} Punkte)",
                        (10, 82), FONT, 0.6, (0, 0, 255), 2)

        hint = ("C=Kalibrieren  M=Regler  S=Speichern  R=ROI  X=Ausschl.  "
                "E=Emitter  L-Ziehen=ROI  R-Ziehen=Ausschluss")
        cv2.putText(vis, hint, (10, H_IMG - 12), FONT, 0.42, (0, 255, 0), 1)

        cv2.imshow(TRACK_WIN, vis)
        if show_mask:
            _, dmask = cv2.threshold(img, IR_THRESH, 255, cv2.THRESH_BINARY)
            apply_roi(dmask)
            cv2.imshow(MASK_WIN, dmask)

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
            calib_capture(m)
        elif key == ord('s') and not _calib_active:
            save_settings()
        elif key == ord('r') and not _calib_active:
            ROI[:] = [0.0, 0.0, 1.0, 1.0]
            print("[tracking] ROI zurueckgesetzt (ganzes Bild)")
        elif key == ord('x') and not _calib_active:
            EXCLUDES.clear()
            print("[tracking] Ausschluss-Zonen geloescht")
        elif key == ord('e'):
            emitter_on = not emitter_on
            set_emitter(emitter_on)
            print(f"[tracking] Emitter -> {'AN' if emitter_on else 'AUS'}")
        elif key == ord('m'):
            show_mask = not show_mask
            if show_mask:
                open_mask_window()
            else:
                try:
                    cv2.destroyWindow(MASK_WIN)
                except Exception:
                    pass
finally:
    pipe.stop()
    cv2.destroyAllWindows()
