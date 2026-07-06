"""
RealSense-Tracking v2 -> WebSocket an die Nachtfalter-App (moon.py).

Neu gegenueber tracking.py:
  * MoonTracker: Gating + Re-Akquisition + One-Euro-Filter -> keine Spruenge
    mehr auf Fremdlicht; kurze Verdeckungen werden ueberbrueckt.
  * Feste, niedrige Belichtung der Farbkamera (Regler im Maske-Fenster):
    nur der selbstleuchtende Mond bleibt im Bild.
  * Homographie-Kalibrierung (4 ECKEN statt 4 Kanten): funktioniert fuer
    frontale UND schraege Montage -> beide Achsen aus Pixeln, KEINE Tiefe mehr.
  * Optionaler HSV-Farbfilter (warmweisse Lichterkette vs. Beamer-Farben).
  * Ausschluss-Zonen: Rechtsklick-Ziehen maskiert Stoerquellen dauerhaft weg.

Tasten im Tracking-Fenster:
  C          Kalibrierung starten (Mond in die 4 SPIELFELD-ECKEN halten)
  Leertaste  aktuellen Kalibrier-Schritt aufnehmen
  T          zwischen BLOB- (Farbkamera) und IR-Tracking umschalten
  M          Maske + Regler einblenden (Belichtung, Farbe, Blob-Filter)
  S          Settings speichern      R  ROI zuruecksetzen
  X          Ausschluss-Zonen loeschen
  Maus       links ziehen = Erkennungsbereich, rechts ziehen = Ausschluss-Zone
  ESC        Kalibrierung abbrechen / sonst Programm beenden

Kalibrierung wird pro Modus in homography_calib.json gespeichert (BLOB und IR
haben verschiedene Kameras -> nach Moduswechsel ggf. neu kalibrieren).
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

MASK_WIN = "Maske"
TRACK_WIN = "Tracking"


# =====================================================================
# One-Euro-Filter + MoonTracker (Gating, Re-Akquisition, Glaettung)
# =====================================================================
class _LowPass:
    def __init__(self):
        self.y = None

    def filt(self, x, alpha):
        self.y = x if self.y is None else alpha * x + (1.0 - alpha) * self.y
        return self.y


class OneEuroFilter:
    """Casiez et al. 2012. min_cutoff runter = glatter, beta hoch = reaktiver."""

    def __init__(self, min_cutoff=1.0, beta=0.02, d_cutoff=1.0):
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
    """Zeitlich konsistentes 2D-Tracking im Pixelraum.

    * Detektionen weit weg von der Vorhersage werden ignoriert (Gate).
    * Ein neuer Ort wird erst nach `reacquire_frames` stabilen Frames
      uebernommen -> einzelne Ausreisser koennen den Track nicht wegreissen.
    * Bei kurzem Verlust wird die Position gehalten/praediziert.
    """

    def __init__(self, gate=70.0, gate_growth=1.4, gate_max_factor=4.0,
                 reacquire_frames=5, reacquire_radius=40.0,
                 hold_frames=12, min_cutoff=1.0, beta=0.02):
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

    @property
    def raw_pos(self):
        return self._pos

    def update(self, candidates, dt):
        """candidates: Liste (u, v, score); Rueckgabe geglaettete (u, v) oder None."""
        cands = list(candidates)

        if self._pos is None:                      # noch kein Track
            if cands:
                best = max(cands, key=lambda c: c[2])
                self._hard_reset_to((best[0], best[1]))
                return self._smooth(dt)
            return None

        pred = self._predict(dt)
        gate = self.current_gate
        inside = [c for c in cands if self._dist((c[0], c[1]), pred) <= gate]

        if inside:                                  # naechster Kandidat gewinnt
            best = min(inside, key=lambda c: (self._dist((c[0], c[1]), pred), -c[2]))
            self._accept((best[0], best[1]), dt)
            return self._smooth(dt)

        # --- nichts im Gate: Ausreisser oder Mond wirklich woanders? ---
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
                self._hard_reset_to(self._cand)     # stabil genug -> umziehen
                return self._smooth(dt)

        if self._lost <= self.hold_frames:          # kurzer Dropout: halten
            self._pos = self._predict(dt)
            return self._smooth(dt)

        self._pos = None                            # zu lange weg -> verloren
        self._vel = (0.0, 0.0)
        self._fx.reset()
        self._fy.reset()
        return None

    def _smooth(self, dt):
        return (self._fx.filt(self._pos[0], dt),
                self._fy.filt(self._pos[1], dt))


tracker = MoonTracker()


# =====================================================================
# Settings (Blob-Filter, Farbfenster, Belichtung, ROI, Ausschluesse)
# =====================================================================
BLOB_MIN_BRIGHT = 200    # Helligkeit 0..255
BLOB_MIN_AREA   = 30     # Mindestflaeche (Pixel)
BLOB_MAX_AREA   = 0      # Maximalflaeche, 0 = aus
BLOB_MIN_CIRC   = 0.60   # Rundheit 0..1
BLOB_MIN_FILL   = 0.70   # Fuellgrad 0..1

COLOR_ON = 0             # HSV-Farbfenster an/aus (1/0)
HUE_MIN, HUE_MAX = 0, 60 # Farbton-Fenster (OpenCV-H: 0..179; warmweiss ~5..40)
SAT_MAX = 140            # max. Saettigung (Lichterkette = wenig gesaettigt)

RGB_EXPOSURE = 100       # manuelle Belichtung der Farbkamera (RealSense-Rohwert)
RGB_GAIN     = 16

ROI = [0.0, 0.0, 1.0, 1.0]        # Erkennungsbereich (normiert)
EXCLUDES = []                     # Ausschluss-Rechtecke [x0,y0,x1,y1] (normiert)
_roi_drag = None
_excl_drag = None

SETTINGS_FILE = os.path.join(BASE, "blob_settings.json")


def load_settings():
    global BLOB_MIN_BRIGHT, BLOB_MIN_AREA, BLOB_MAX_AREA, BLOB_MIN_CIRC, BLOB_MIN_FILL
    global COLOR_ON, HUE_MIN, HUE_MAX, SAT_MAX, RGB_EXPOSURE, RGB_GAIN
    try:
        d = json.load(open(SETTINGS_FILE, encoding="utf-8"))
        BLOB_MIN_BRIGHT = int(d.get("bright", BLOB_MIN_BRIGHT))
        BLOB_MIN_AREA   = int(d.get("area", BLOB_MIN_AREA))
        BLOB_MAX_AREA   = int(d.get("area_max", BLOB_MAX_AREA))
        BLOB_MIN_CIRC   = float(d.get("circ", BLOB_MIN_CIRC))
        BLOB_MIN_FILL   = float(d.get("fill", BLOB_MIN_FILL))
        COLOR_ON        = int(d.get("color_on", COLOR_ON))
        HUE_MIN         = int(d.get("hue_min", HUE_MIN))
        HUE_MAX         = int(d.get("hue_max", HUE_MAX))
        SAT_MAX         = int(d.get("sat_max", SAT_MAX))
        RGB_EXPOSURE    = int(d.get("exposure", RGB_EXPOSURE))
        RGB_GAIN        = int(d.get("gain", RGB_GAIN))
        if isinstance(d.get("roi"), list) and len(d["roi"]) == 4:
            ROI[:] = [float(c) for c in d["roi"]]
        if isinstance(d.get("excludes"), list):
            EXCLUDES[:] = [list(map(float, r)) for r in d["excludes"] if len(r) == 4]
        print(f"[tracking] Settings geladen: {d}")
    except Exception:
        print("[tracking] keine Settings -> Defaults (Taste S speichert)")


def save_settings():
    d = dict(bright=int(BLOB_MIN_BRIGHT), area=int(BLOB_MIN_AREA),
             area_max=int(BLOB_MAX_AREA),
             circ=round(float(BLOB_MIN_CIRC), 2), fill=round(float(BLOB_MIN_FILL), 2),
             color_on=int(COLOR_ON), hue_min=int(HUE_MIN), hue_max=int(HUE_MAX),
             sat_max=int(SAT_MAX), exposure=int(RGB_EXPOSURE), gain=int(RGB_GAIN),
             roi=[round(c, 4) for c in ROI],
             excludes=[[round(c, 4) for c in r] for r in EXCLUDES])
    try:
        json.dump(d, open(SETTINGS_FILE, "w", encoding="utf-8"))
        print(f"[tracking] Settings gespeichert: {d}")
    except OSError as e:
        print(f"[tracking] Speichern fehlgeschlagen: {e}")


def open_mask_window():
    cv2.namedWindow(MASK_WIN)
    cv2.createTrackbar("Hell",    MASK_WIN, int(BLOB_MIN_BRIGHT),      255, lambda v: None)
    cv2.createTrackbar("AreaMin", MASK_WIN, int(BLOB_MIN_AREA),       2000, lambda v: None)
    cv2.createTrackbar("AreaMax", MASK_WIN, int(BLOB_MAX_AREA),      20000, lambda v: None)
    cv2.createTrackbar("Rund%",   MASK_WIN, int(BLOB_MIN_CIRC * 100),  100, lambda v: None)
    cv2.createTrackbar("Fuell%",  MASK_WIN, int(BLOB_MIN_FILL * 100),  100, lambda v: None)
    cv2.createTrackbar("Farbe",   MASK_WIN, int(COLOR_ON),               1, lambda v: None)
    cv2.createTrackbar("Hmin",    MASK_WIN, int(HUE_MIN),              179, lambda v: None)
    cv2.createTrackbar("Hmax",    MASK_WIN, int(HUE_MAX),              179, lambda v: None)
    cv2.createTrackbar("Smax",    MASK_WIN, int(SAT_MAX),              255, lambda v: None)
    cv2.createTrackbar("Exp",     MASK_WIN, int(RGB_EXPOSURE),        1000, lambda v: None)
    cv2.createTrackbar("Gain",    MASK_WIN, int(RGB_GAIN),             128, lambda v: None)


def read_mask_trackbars():
    global BLOB_MIN_BRIGHT, BLOB_MIN_AREA, BLOB_MAX_AREA, BLOB_MIN_CIRC, BLOB_MIN_FILL
    global COLOR_ON, HUE_MIN, HUE_MAX, SAT_MAX, RGB_EXPOSURE, RGB_GAIN
    try:
        BLOB_MIN_BRIGHT = cv2.getTrackbarPos("Hell",    MASK_WIN)
        BLOB_MIN_AREA   = cv2.getTrackbarPos("AreaMin", MASK_WIN)
        BLOB_MAX_AREA   = cv2.getTrackbarPos("AreaMax", MASK_WIN)
        BLOB_MIN_CIRC   = cv2.getTrackbarPos("Rund%",   MASK_WIN) / 100.0
        BLOB_MIN_FILL   = cv2.getTrackbarPos("Fuell%",  MASK_WIN) / 100.0
        COLOR_ON        = cv2.getTrackbarPos("Farbe",   MASK_WIN)
        HUE_MIN         = cv2.getTrackbarPos("Hmin",    MASK_WIN)
        HUE_MAX         = cv2.getTrackbarPos("Hmax",    MASK_WIN)
        SAT_MAX         = cv2.getTrackbarPos("Smax",    MASK_WIN)
        RGB_EXPOSURE    = max(1, cv2.getTrackbarPos("Exp", MASK_WIN))
        RGB_GAIN        = cv2.getTrackbarPos("Gain",    MASK_WIN)
    except cv2.error:
        pass


# =====================================================================
# ROI + Ausschluss-Zonen
# =====================================================================
def _rect_norm(p0, p1):
    x0, x1 = sorted((p0[0], p1[0])); y0, y1 = sorted((p0[1], p1[1]))
    return [x0 / W_IMG, y0 / H_IMG, x1 / W_IMG, y1 / H_IMG]


def on_tracking_mouse(event, x, y, flags, param):
    """Links ziehen = Erkennungsbereich, rechts ziehen = Ausschluss-Zone."""
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
            print(f"[tracking] Ausschluss-Zone #{len(EXCLUDES)}: {[round(c, 3) for c in r]}")
    elif event == cv2.EVENT_MOUSEMOVE:
        if _roi_drag is not None and (flags & cv2.EVENT_FLAG_LBUTTON):
            ROI[:] = _rect_norm(_roi_drag, (x, y))


def apply_roi(mask):
    """Alles ausserhalb des ROI und innerhalb der Ausschluss-Zonen nullen."""
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
# Homographie-Kalibrierung: 4 Spielfeld-ECKEN -> Spielkoordinaten 0..1
# =====================================================================
CALIB_STEPS = ["OBEN LINKS", "OBEN RECHTS", "UNTEN RECHTS", "UNTEN LINKS"]
CALIB_FILE = os.path.join(BASE, "homography_calib.json")
DST_PTS = np.float32([[0, 0], [1, 0], [1, 1], [0, 1]])

_src_pts = {"BLOB": None, "IR": None}   # je Modus 4 Pixelpunkte
_H = {"BLOB": None, "IR": None}         # daraus gebaute 3x3-Matrizen
_calib_active = False
_calib_step = 0
_calib_pts = []


def _rebuild_h(m):
    pts = _src_pts.get(m)
    _H[m] = (cv2.getPerspectiveTransform(np.float32(pts), DST_PTS)
             if pts is not None and len(pts) == 4 else None)


def load_calib():
    try:
        d = json.load(open(CALIB_FILE, encoding="utf-8"))
        for m in ("BLOB", "IR"):
            if isinstance(d.get(m), list) and len(d[m]) == 4:
                _src_pts[m] = [[float(p[0]), float(p[1])] for p in d[m]]
                _rebuild_h(m)
        print(f"[tracking] Homographie geladen: "
              f"{[m for m in _H if _H[m] is not None] or 'keine'}")
    except Exception:
        print("[tracking] keine Homographie-Kalibrierung -> Taste C")


def save_calib():
    d = {m: _src_pts[m] for m in ("BLOB", "IR") if _src_pts[m] is not None}
    try:
        json.dump(d, open(CALIB_FILE, "w", encoding="utf-8"))
        print(f"[tracking] Homographie gespeichert: {list(d)}")
    except OSError as e:
        print(f"[tracking] Speichern fehlgeschlagen: {e}")


def calib_start():
    global _calib_active, _calib_step, _calib_pts
    _calib_active, _calib_step, _calib_pts = True, 0, []
    print(f"[tracking] Kalibrierung: Mond in Ecke {CALIB_STEPS[0]} halten, dann Leertaste")


def calib_capture(raw_uv, m):
    """Nimmt fuer den aktuellen Schritt die ROHE Blob-Position auf."""
    global _calib_active, _calib_step
    if raw_uv is None:
        print("[tracking] kein Blob sichtbar -> nichts aufgenommen")
        return
    _calib_pts.append([float(raw_uv[0]), float(raw_uv[1])])
    _calib_step += 1
    if _calib_step < 4:
        print(f"[tracking] ok. Jetzt Ecke {CALIB_STEPS[_calib_step]}, dann Leertaste")
        return
    _src_pts[m] = list(_calib_pts)
    _rebuild_h(m)
    _calib_active = False
    save_calib()
    tracker.reset()
    print("[tracking] Homographie fertig - Tracking laeuft in Spielkoordinaten")


def normalize_h(u, v, m):
    """Pixel -> Spielkoordinaten 0..1 ueber die Homographie des Modus.

    Ohne Kalibrierung: Notbetrieb ueber das volle Bild (linear)."""
    if _H[m] is None:
        return u / W_IMG, v / H_IMG
    p = cv2.perspectiveTransform(np.float32([[[u, v]]]), _H[m])[0, 0]
    return float(min(1.0, max(0.0, p[0]))), float(min(1.0, max(0.0, p[1])))


# =====================================================================
# WebSocket-Server (unveraendert: {"active", "x", "y"} alle 20 ms)
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
# Erkennung: liefert ALLE Kandidaten (u, v, score) - Auswahl macht der Tracker
# =====================================================================
def detect_blob(bgr):
    """Alle plausiblen Blobs -> ([(u, v, score)], mask); zeichnet Kandidaten."""
    if COLOR_ON:
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, (HUE_MIN, 0, BLOB_MIN_BRIGHT),
                                (HUE_MAX, SAT_MAX, 255))
    else:
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        _, mask = cv2.threshold(gray, BLOB_MIN_BRIGHT, 255, cv2.THRESH_BINARY)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    apply_roi(mask)
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    cands = []
    for c in cnts:
        area = cv2.contourArea(c)
        if area < BLOB_MIN_AREA:
            continue
        if BLOB_MAX_AREA > 0 and area > BLOB_MAX_AREA:
            continue
        peri = cv2.arcLength(c, True)
        if peri == 0:
            continue
        circ = 4.0 * np.pi * area / (peri * peri)
        if circ < BLOB_MIN_CIRC:
            continue
        (cx, cy), r = cv2.minEnclosingCircle(c)
        fill = area / (np.pi * r * r) if r > 0 else 0.0
        if fill < BLOB_MIN_FILL:
            continue
        M = cv2.moments(c)
        if M["m00"] == 0:
            continue
        u, v = M["m10"] / M["m00"], M["m01"] / M["m00"]
        cands.append((u, v, area))
        cv2.circle(bgr, (int(cx), int(cy)), int(r), (0, 160, 255), 1)  # Kandidat
    return cands, mask


def detect_ir(img, vis):
    """Helle IR-Punkte -> gemittelter Schwerpunkt als EIN Kandidat."""
    _, mask = cv2.threshold(img, 200, 255, cv2.THRESH_BINARY)
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
        return []
    mu = sum(p[0] * p[2] for p in pts) / total     # flaechengewichtetes Mittel
    mv = sum(p[1] * p[2] for p in pts) / total
    return [(mu, mv, total)]


# =====================================================================
# RealSense-Pipeline: NUR Farb- und IR-Stream, KEINE Tiefe mehr
# =====================================================================
pipe = rs.pipeline()
cfg = rs.config()
cfg.enable_stream(rs.stream.infrared, 1, W_IMG, H_IMG, rs.format.y8, 30)
cfg.enable_stream(rs.stream.color,       W_IMG, H_IMG, rs.format.bgr8, 30)
profile = pipe.start(cfg)

_rgb_sensor = None
_last_exp_gain = (None, None)


def setup_sensors():
    """Emitter aus, IR kurz belichtet, Farbkamera fest belichtet (kein Auto)."""
    global _rgb_sensor
    dev = profile.get_device()
    try:
        stereo = dev.first_depth_sensor()
        stereo.set_option(rs.option.emitter_enabled, 0)      # keine Laser-Dots
        stereo.set_option(rs.option.enable_auto_exposure, 0)
        stereo.set_option(rs.option.exposure, 800)           # IR-Fallback-Modus
        stereo.set_option(rs.option.gain, 16)
    except Exception as e:
        print(f"[tracking] Stereo-Sensor-Setup fehlgeschlagen: {e}")
    for s in dev.query_sensors():
        try:
            if s.get_info(rs.camera_info.name) == "RGB Camera":
                _rgb_sensor = s
        except Exception:
            pass
    if _rgb_sensor is None:
        print("[tracking] RGB-Sensor nicht gefunden - Belichtung bleibt Auto!")
        return
    try:
        _rgb_sensor.set_option(rs.option.enable_auto_exposure, 0)
        _rgb_sensor.set_option(rs.option.enable_auto_white_balance, 0)
    except Exception as e:
        print(f"[tracking] Auto-Belichtung abschalten fehlgeschlagen: {e}")
    apply_rgb_exposure(force=True)


def apply_rgb_exposure(force=False):
    """Belichtung/Gain der Farbkamera setzen - nur bei Aenderung (Regler)."""
    global _last_exp_gain
    if _rgb_sensor is None:
        return
    if not force and _last_exp_gain == (RGB_EXPOSURE, RGB_GAIN):
        return
    try:
        _rgb_sensor.set_option(rs.option.exposure, float(RGB_EXPOSURE))
        _rgb_sensor.set_option(rs.option.gain, float(RGB_GAIN))
        _last_exp_gain = (RGB_EXPOSURE, RGB_GAIN)
    except Exception as e:
        print(f"[tracking] Belichtung setzen fehlgeschlagen: {e}")


# =====================================================================
# Hauptprogramm
# =====================================================================
load_settings()
load_calib()
setup_sensors()
start_server(host="0.0.0.0")   # ans LAN binden, damit der Mac ueber Kabel verbindet

mode = "BLOB"        # Hauptmodus (Farbkamera); IR-Fallback mit Taste T
show_mask = False

cv2.namedWindow(TRACK_WIN)
cv2.setMouseCallback(TRACK_WIN, on_tracking_mouse)

_last_t = time.monotonic()

try:
    while True:
        frames = pipe.wait_for_frames()
        now = time.monotonic()
        dt = max(1e-3, now - _last_t)
        _last_t = now

        # --- Erkennung je nach Modus -> vis (Anzeige) + Kandidatenliste ---
        if mode == "IR":
            ir  = frames.get_infrared_frame(1)
            img = np.asanyarray(ir.get_data())
            vis = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
            cands = detect_ir(img, vis)
        else:  # BLOB (Farbkamera)
            color = frames.get_color_frame()
            vis = np.asanyarray(color.get_data()).copy()
            if show_mask:
                read_mask_trackbars()
                apply_rgb_exposure()
            cands, mask = detect_blob(vis)
            if show_mask:
                cv2.imshow(MASK_WIN, mask)

        # rohe beste Detektion (fuer die Kalibrierung, ohne Tracker/Glaettung)
        raw_best = max(cands, key=lambda c: c[2]) if cands else None

        # --- Tracker: Gating + Re-Akquisition + Glaettung ---
        tp = tracker.update(cands, dt)
        if tp is not None:
            nx, ny = normalize_h(tp[0], tp[1], mode)
            set_state(True, nx, ny)
            cv2.circle(vis, (int(tp[0]), int(tp[1])), 8, (0, 255, 0), 2)
            gate = int(tracker.current_gate)
            cv2.circle(vis, (int(tp[0]), int(tp[1])), gate, (0, 255, 0), 1)
            cv2.putText(vis, f"send x={nx:.2f} y={ny:.2f}"
                             f"{'' if _H[mode] is not None else '  (UNKALIBRIERT)'}",
                        (10, 55), FONT, 0.6, (0, 255, 0), 1)
        else:
            set_state(False)
            cv2.putText(vis, "kein Mond", (10, 55), FONT, 0.6, (0, 0, 255), 1)

        # --- Overlays: Spielfeld-Ecken, ROI, Ausschluesse, HUD ---
        if _src_pts[mode] is not None:
            q = np.int32(_src_pts[mode])
            cv2.polylines(vis, [q], True, (0, 200, 200), 1)
        if ROI != [0.0, 0.0, 1.0, 1.0]:
            rx0, ry0 = int(ROI[0] * W_IMG), int(ROI[1] * H_IMG)
            rx1, ry1 = int(ROI[2] * W_IMG), int(ROI[3] * H_IMG)
            cv2.rectangle(vis, (rx0, ry0), (rx1, ry1), (255, 0, 255), 1)
            cv2.putText(vis, "ROI", (rx0 + 3, ry0 + 15), FONT, 0.5, (255, 0, 255), 1)
        for i, r in enumerate(EXCLUDES):
            ex0, ey0 = int(r[0] * W_IMG), int(r[1] * H_IMG)
            ex1, ey1 = int(r[2] * W_IMG), int(r[3] * H_IMG)
            cv2.rectangle(vis, (ex0, ey0), (ex1, ey1), (0, 0, 255), 1)
            cv2.putText(vis, f"X{i + 1}", (ex0 + 3, ey0 + 15), FONT, 0.5, (0, 0, 255), 1)

        cv2.putText(vis, f"Modus: {mode}  (T = wechseln)", (10, 30), FONT, 0.6, (0, 255, 255), 2)
        if _calib_active:
            cv2.putText(vis, f"KALIBRIERUNG {_calib_step + 1}/4: Mond in Ecke "
                             f"{CALIB_STEPS[_calib_step]} -> LEER",
                        (10, 110), FONT, 0.7, (0, 255, 0), 2)
            cv2.putText(vis, "ESC = abbrechen", (10, 135), FONT, 0.5, (0, 255, 0), 1)
            if raw_best is not None:
                cv2.drawMarker(vis, (int(raw_best[0]), int(raw_best[1])),
                               (0, 255, 0), cv2.MARKER_CROSS, 20, 2)
        else:
            hint = ("C = Kalibrieren (4 Ecken)   T = Modus   L-Ziehen = ROI   "
                    "R-Ziehen = Ausschluss   R = ROI ganz   X = Ausschluesse weg")
            if mode == "BLOB":
                hint += "   M = Maske/Regler   S = speichern"
            cv2.putText(vis, hint, (10, H_IMG - 12), FONT, 0.42, (0, 255, 0), 1)

        cv2.imshow(TRACK_WIN, vis)
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
            calib_capture((raw_best[0], raw_best[1]) if raw_best else None, mode)
        elif key == ord('t') and not _calib_active:
            mode = "BLOB" if mode == "IR" else "IR"
            tracker.reset()                 # anderer Sensor -> anderer Pixelraum
            if mode == "IR":
                show_mask = False
                try:
                    cv2.destroyWindow(MASK_WIN)
                except Exception:
                    pass
            if _H[mode] is None:
                print(f"[tracking] Modus {mode} ist UNKALIBRIERT -> Taste C")
            print(f"[tracking] Modus -> {mode}")
        elif key == ord('s') and not _calib_active:
            save_settings()
        elif key == ord('r') and not _calib_active:
            ROI[:] = [0.0, 0.0, 1.0, 1.0]
            print("[tracking] ROI zurueckgesetzt (ganzes Bild)")
        elif key == ord('x') and not _calib_active:
            EXCLUDES.clear()
            print("[tracking] Ausschluss-Zonen geloescht")
        elif key == ord('m') and mode == "BLOB":
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
