"""
RealSense-Tracking v3 -> WebSocket an die Nachtfalter-App (moon.py).

ZWEI RealSense-Kameras, ausgelegt auf REDUNDANZ:
  * Beide Kameras sehen dasselbe Spielfeld aus verschiedenen Winkeln.
  * Jede Kamera hat ihre EIGENE 4-Ecken-Homographie -> beide bilden in
    DIESELBEN Spielkoordinaten 0..1 ab.
  * Die Kandidaten BEIDER Kameras werden im Spielkoordinaten-Raum
    zusammengefuehrt und von EINEM gemeinsamen MoonTracker verfolgt.
    -> Verdeckt ein Spieler Kamera A, halten die Kandidaten von Kamera B
       exakt denselben Track am Leben. Kein Sprung, keine Luecke.
  * Sehen beide Kameras den Mond, wird der (score-gewichtete) Mittelpunkt
    genommen -> ruhiger als eine Einzelkamera.

Gegenueber tracking_v2.py:
  * Tracker arbeitet in SPIELKOORDINATEN (0..1) statt in Pixeln -> Fusion.
  * Alles Kamera-spezifische steckt in der Klasse RealSenseCamera
    (Pipeline, Sensoren, Homographie, ROI, Ausschluesse, Belichtung).
  * Aufnahme laeuft pro Kamera in einem Thread (entkoppelt, kein Blockieren).

WebSocket-Protokoll bleibt identisch: {"active","x","y"} alle 20 ms,
ws://<host>:8765. moon.py muss NICHT angepasst werden.

Tasten im Tracking-Fenster:
  1 / 2      Fokus auf Kamera A bzw. B (fuer Kalibrierung, Maske, Maus)
  C          Kalibrierung der FOKUS-Kamera starten (Mond in die 4 Ecken)
  Leertaste  aktuellen Kalibrier-Schritt aufnehmen
  T          zwischen BLOB (Farbkamera) und IR-Tracking umschalten
  M          Maske + Regler der Fokus-Kamera einblenden
  S          Settings speichern      R  ROI der Fokus-Kamera zuruecksetzen
  X          Ausschluss-Zonen der Fokus-Kamera loeschen
  Maus       im jeweiligen Fenster: links = ROI, rechts = Ausschluss-Zone
  ESC        Kalibrierung abbrechen / sonst Programm beenden
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

ENABLE_IR = True        # IR-Stream mitlaufen lassen (kostet USB-Bandbreite)
MAX_CAMS = 2

MASK_WIN = "Maske"


# =====================================================================
# One-Euro-Filter + MoonTracker (Gating, Re-Akquisition, Glaettung)
# Jetzt im SPIELKOORDINATEN-Raum (0..1) -> Fusion mehrerer Kameras.
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

    * Detektionen weit weg von der Vorhersage werden ignoriert (Gate).
    * Kandidaten mehrerer Kameras nah beieinander werden score-gewichtet
      gemittelt (`merge_radius`) -> ruhiger, wenn beide den Mond sehen.
    * Ein neuer Ort wird erst nach `reacquire_frames` stabilen Frames
      uebernommen -> einzelne Ausreisser reissen den Track nicht weg.
    * Bei kurzem Verlust wird die Position gehalten/praediziert.

    Alle Radien sind in Spielkoordinaten (Bilddiagonale ~1.4).
    """

    def __init__(self, gate=0.09, gate_growth=1.4, gate_max_factor=4.0,
                 reacquire_frames=5, reacquire_radius=0.06, hold_frames=12,
                 merge_radius=0.04, min_cutoff=0.8, beta=18.0):
        self.gate = gate
        self.gate_growth = gate_growth
        self.gate_max = gate * gate_max_factor
        self.reacquire_frames = reacquire_frames
        self.reacquire_radius = reacquire_radius
        self.hold_frames = hold_frames
        self.merge_radius = merge_radius
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

    def _merge(self, cands, center):
        """Score-gewichteter Mittelpunkt aller Kandidaten nahe `center`.

        So verschmelzen die Detektionen beider Kameras zu einem ruhigen
        Punkt, statt zwischen ihnen hin- und herzuspringen."""
        close = [c for c in cands if self._dist((c[0], c[1]), center) <= self.merge_radius]
        if len(close) <= 1:
            return center
        tot = sum(c[2] for c in close) or 1.0
        return (sum(c[0] * c[2] for c in close) / tot,
                sum(c[1] * c[2] for c in close) / tot)

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
        """candidates: Liste (x, y, score) in Spielkoordinaten (aus BEIDEN
        Kameras zusammengefuehrt). Rueckgabe geglaettete (x, y) oder None."""
        cands = list(candidates)

        if self._pos is None:                      # noch kein Track
            if cands:
                best = max(cands, key=lambda c: c[2])
                self._hard_reset_to(self._merge(cands, (best[0], best[1])))
                return self._smooth(dt)
            return None

        pred = self._predict(dt)
        gate = self.current_gate
        inside = [c for c in cands if self._dist((c[0], c[1]), pred) <= gate]

        if inside:                                  # naechster Kandidat gewinnt
            best = min(inside, key=lambda c: (self._dist((c[0], c[1]), pred), -c[2]))
            merged = self._merge(inside, (best[0], best[1]))
            self._accept(merged, dt)
            return self._smooth(dt)

        # --- nichts im Gate: Ausreisser oder Mond wirklich woanders? ---
        self._lost += 1
        if cands:
            best = max(cands, key=lambda c: c[2])
            p = self._merge(cands, (best[0], best[1]))
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
        x = min(1.0, max(0.0, self._fx.filt(self._pos[0], dt)))
        y = min(1.0, max(0.0, self._fy.filt(self._pos[1], dt)))
        return (x, y)


tracker = MoonTracker()


# =====================================================================
# Settings: Blob-Filter/Farbe sind GLOBAL (fuer beide Kameras gleich),
# Helligkeit (Hell), Belichtung/Gain, ROI und Ausschluesse sind PRO Kamera.
# =====================================================================
DEFAULT_BRIGHT = 200     # Helligkeits-Schwelle 0..255 (Startwert je Kamera)
BLOB_MIN_AREA   = 30     # Mindestflaeche (Pixel)
BLOB_MAX_AREA   = 0      # Maximalflaeche, 0 = aus
BLOB_MIN_CIRC   = 0.60   # Rundheit 0..1
BLOB_MIN_FILL   = 0.70   # Fuellgrad 0..1

COLOR_ON = 0             # HSV-Farbfenster an/aus (1/0)
HUE_MIN, HUE_MAX = 0, 60 # Farbton-Fenster (OpenCV-H: 0..179; warmweiss ~5..40)
SAT_MAX = 140            # max. Saettigung (Lichterkette = wenig gesaettigt)

SETTINGS_FILE = os.path.join(BASE, "blob_settings_v3.json")
CALIB_FILE = os.path.join(BASE, "homography_calib_v3.json")

CALIB_STEPS = ["OBEN LINKS", "OBEN RECHTS", "UNTEN RECHTS", "UNTEN LINKS"]
DST_PTS = np.float32([[0, 0], [1, 0], [1, 1], [0, 1]])

# --- 3D-Triangulation ------------------------------------------------
# Physisches Seitenverhaeltnis (Breite/Hoehe) des Spielfeld-Rechtecks, das
# du in die 4 Ecken kalibrierst. Nur das VERHAELTNIS zaehlt, nicht die Groesse.
# Fuer die Triangulation moeglichst genau messen (Standard 16:9).
FIELD_ASPECT = 16.0 / 9.0

# Die 4 Ecken als 3D-Weltpunkte auf der Ebene Z=0 (Reihenfolge = CALIB_STEPS).
# Breite = 1, Hoehe = 1/AR -> Weltframe teilt sich beide Kameras.
_FH = 1.0 / FIELD_ASPECT
OBJ_PTS = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0],
                    [1.0, _FH, 0.0], [0.0, _FH, 0.0]], np.float64).reshape(-1, 1, 3)

# Wenn die zwei Sichtstrahlen weiter als das voneinander entfernt sind (in
# Weltbreiten), gilt die Triangulation als unsicher (Fehlpaarung/Rauschen).
TRI_MAX_RESIDUAL = 0.15


def triangulate(rayA, rayB):
    """Zwei Weltstrahlen (C, d) -> (nx, ny, residual) in Spielkoordinaten.

    Schneidet die Strahlen (kuerzeste Verbindung), projiziert den Mittelpunkt
    senkrecht auf die Ebene Z=0 und normiert auf 0..1. Z faellt dabei raus."""
    C1, d1 = rayA
    C2, d2 = rayB
    b = float(d1 @ d2)
    denom = 1.0 - b * b
    if denom < 1e-6:                       # Strahlen fast parallel -> nutzlos
        return None
    w0 = C1 - C2
    dd = float(d1 @ w0)
    ee = float(d2 @ w0)
    s = (b * ee - dd) / denom
    t = (ee - b * dd) / denom
    p1 = C1 + s * d1
    p2 = C2 + t * d2
    P = 0.5 * (p1 + p2)
    resid = float(np.linalg.norm(p1 - p2))
    nx = min(1.0, max(0.0, float(P[0])))
    ny = min(1.0, max(0.0, float(P[1]) * FIELD_ASPECT))
    return nx, ny, resid


def _rect_norm(p0, p1):
    x0, x1 = sorted((p0[0], p1[0])); y0, y1 = sorted((p0[1], p1[1]))
    return [x0 / W_IMG, y0 / H_IMG, x1 / W_IMG, y1 / H_IMG]


# =====================================================================
# RealSense-Kamera: kapselt Pipeline, Sensoren, Homographie, ROI, Maske.
# =====================================================================
class RealSenseCamera:
    def __init__(self, serial, label):
        self.serial = serial
        self.label = label                 # "A" / "B"
        self.win = f"Tracking {label}"

        self.exposure = 100                # RealSense-Rohwerte, pro Kamera
        self.gain = 16
        self._last_exp_gain = (None, None)

        self.bright = DEFAULT_BRIGHT       # Helligkeits-Schwelle, pro Kamera

        self.roi = [0.0, 0.0, 1.0, 1.0]    # normiert
        self.excludes = []                 # [[x0,y0,x1,y1], ...] normiert
        self._roi_drag = None
        self._excl_drag = None

        self._src_pts = {"BLOB": None, "IR": None}   # je 4 Pixelpunkte
        self._H = {"BLOB": None, "IR": None}         # Pixel -> Spielkoord
        self._Hinv = {"BLOB": None, "IR": None}      # Spielkoord -> Pixel
        self._pose = {"BLOB": None, "IR": None}      # 3D-Lage (R, Rt, C) je Modus
        self.K = {}                                  # Intrinsik-Matrix je Modus
        self.dist = {}                               # Verzeichnung je Modus

        # --- Pipeline nur fuer DIESE Kamera ---
        self.pipe = rs.pipeline()
        cfg = rs.config()
        cfg.enable_device(serial)
        if ENABLE_IR:
            cfg.enable_stream(rs.stream.infrared, 1, W_IMG, H_IMG, rs.format.y8, 30)
        cfg.enable_stream(rs.stream.color, W_IMG, H_IMG, rs.format.bgr8, 30)
        self.profile = self.pipe.start(cfg)
        self._rgb_sensor = None
        self._setup_sensors()
        self._read_intrinsics()

        # --- Aufnahme-Thread: haelt immer den neuesten Frame bereit ---
        self._latest = None                # (color_bgr, ir_gray_or_None)
        self._lock = threading.Lock()
        self._run = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()

    # -- Sensor-Setup: Emitter aus, Farbkamera fest belichtet --
    def _setup_sensors(self):
        dev = self.profile.get_device()
        try:
            stereo = dev.first_depth_sensor()
            stereo.set_option(rs.option.emitter_enabled, 0)   # keine Laser-Dots
            stereo.set_option(rs.option.enable_auto_exposure, 0)
            stereo.set_option(rs.option.exposure, 800)        # IR-Fallback
            stereo.set_option(rs.option.gain, 16)
        except Exception as e:
            print(f"[{self.label}] Stereo-Sensor-Setup fehlgeschlagen: {e}")
        for s in dev.query_sensors():
            try:
                if s.get_info(rs.camera_info.name) == "RGB Camera":
                    self._rgb_sensor = s
            except Exception:
                pass
        if self._rgb_sensor is None:
            print(f"[{self.label}] RGB-Sensor nicht gefunden - Belichtung bleibt Auto!")
            return
        try:
            self._rgb_sensor.set_option(rs.option.enable_auto_exposure, 0)
            self._rgb_sensor.set_option(rs.option.enable_auto_white_balance, 0)
        except Exception as e:
            print(f"[{self.label}] Auto-Belichtung abschalten fehlgeschlagen: {e}")
        self.apply_exposure(force=True)

    def apply_exposure(self, force=False):
        if self._rgb_sensor is None:
            return
        if not force and self._last_exp_gain == (self.exposure, self.gain):
            return
        try:
            self._rgb_sensor.set_option(rs.option.exposure, float(self.exposure))
            self._rgb_sensor.set_option(rs.option.gain, float(self.gain))
            self._last_exp_gain = (self.exposure, self.gain)
        except Exception as e:
            print(f"[{self.label}] Belichtung setzen fehlgeschlagen: {e}")

    def _capture_loop(self):
        while self._run:
            try:
                frames = self.pipe.wait_for_frames()
            except Exception:
                break
            color = frames.get_color_frame()
            if not color:
                continue
            color_img = np.asanyarray(color.get_data()).copy()
            ir_img = None
            if ENABLE_IR:
                ir = frames.get_infrared_frame(1)
                if ir:
                    ir_img = np.asanyarray(ir.get_data()).copy()
            with self._lock:
                self._latest = (color_img, ir_img)

    def read(self):
        with self._lock:
            return self._latest

    def stop(self):
        self._run = False
        try:
            self.pipe.stop()
        except Exception:
            pass

    # -- Kamera-Intrinsik (fuer 3D-Strahlen) --
    def _read_intrinsics(self):
        streams = [("BLOB", rs.stream.color, 0)]
        if ENABLE_IR:
            streams.append(("IR", rs.stream.infrared, 1))
        for m, strm, idx in streams:
            try:
                vp = self.profile.get_stream(strm, idx).as_video_stream_profile()
                i = vp.get_intrinsics()
                self.K[m] = np.array([[i.fx, 0, i.ppx],
                                      [0, i.fy, i.ppy],
                                      [0, 0, 1]], np.float64)
                self.dist[m] = np.array(i.coeffs, np.float64)
            except Exception as e:
                print(f"[{self.label}] Intrinsik {m} nicht lesbar: {e}")

    # -- Homographie --
    def _rebuild_h(self, m):
        pts = self._src_pts.get(m)
        if pts is not None and len(pts) == 4:
            src = np.float32(pts)
            self._H[m] = cv2.getPerspectiveTransform(src, DST_PTS)
            self._Hinv[m] = cv2.getPerspectiveTransform(DST_PTS, src)
        else:
            self._H[m] = None
            self._Hinv[m] = None

    def H(self, m):
        return self._H[m]

    def to_game(self, u, v, m):
        """Pixel -> Spielkoordinaten 0..1 ueber die Homographie des Modus.
        Ohne Kalibrierung: Notbetrieb ueber das volle Bild (linear)."""
        if self._H[m] is None:
            return u / W_IMG, v / H_IMG
        p = cv2.perspectiveTransform(np.float32([[[u, v]]]), self._H[m])[0, 0]
        return float(min(1.0, max(0.0, p[0]))), float(min(1.0, max(0.0, p[1])))

    def from_game(self, gx, gy, m):
        """Spielkoordinaten -> Pixel (fuer die Anzeige des Tracks je Kamera)."""
        if self._Hinv[m] is None:
            return gx * W_IMG, gy * H_IMG
        p = cv2.perspectiveTransform(np.float32([[[gx, gy]]]), self._Hinv[m])[0, 0]
        return float(p[0]), float(p[1])

    def _rebuild_pose(self, m):
        """Aus den 4 Ecken die 3D-Lage der Kamera bestimmen (solvePnP)."""
        pts = self._src_pts.get(m)
        K = self.K.get(m)
        if pts is None or len(pts) != 4 or K is None:
            self._pose[m] = None
            return
        img = np.array(pts, np.float64).reshape(-1, 1, 2)
        dist = self.dist.get(m, np.zeros(5))
        # SQPNP ist bei schraeger Montage robuster als IPPE (keine falsche
        # Loesung der planaren Pose-Mehrdeutigkeit); ITERATIVE als Rueckfall.
        ok = False
        for flag in (cv2.SOLVEPNP_SQPNP, cv2.SOLVEPNP_ITERATIVE):
            try:
                ok, rvec, tvec = cv2.solvePnP(OBJ_PTS, img, K, dist, flags=flag)
            except cv2.error:
                ok = False
            if ok:
                break
        if not ok:
            self._pose[m] = None
            return
        R, _ = cv2.Rodrigues(rvec)
        C = (-R.T @ tvec).reshape(3)
        self._pose[m] = dict(R=R, Rt=R.T, C=C)

    def has_pose(self, m):
        return self._pose.get(m) is not None

    def ray(self, u, v, m):
        """Pixel -> Weltstrahl (C, d) fuer die Triangulation, oder None."""
        pose = self._pose.get(m)
        K = self.K.get(m)
        if pose is None or K is None:
            return None
        dist = self.dist.get(m, np.zeros(5))
        und = cv2.undistortPoints(np.array([[[u, v]]], np.float64), K, dist)
        xn, yn = und[0, 0]
        d = pose["Rt"] @ np.array([xn, yn, 1.0])
        n = np.linalg.norm(d)
        if n < 1e-9:
            return None
        return pose["C"], d / n

    def set_calib(self, m, pts):
        self._src_pts[m] = list(pts)
        self._rebuild_h(m)
        self._rebuild_pose(m)

    def src_pts(self, m):
        return self._src_pts[m]

    # -- ROI/Ausschluesse auf eine Maske anwenden --
    def apply_roi(self, mask):
        h, w = mask.shape[:2]
        r = self.roi
        x0 = max(0, min(w, int(r[0] * w))); x1 = max(0, min(w, int(r[2] * w)))
        y0 = max(0, min(h, int(r[1] * h))); y1 = max(0, min(h, int(r[3] * h)))
        if x0 > 0: mask[:, :x0] = 0
        if x1 < w: mask[:, x1:] = 0
        if y0 > 0: mask[:y0, :] = 0
        if y1 < h: mask[y1:, :] = 0
        for e in self.excludes:
            ex0, ey0 = int(e[0] * w), int(e[1] * h)
            ex1, ey1 = int(e[2] * w), int(e[3] * h)
            mask[ey0:ey1, ex0:ex1] = 0
        return mask

    def on_mouse(self, event, x, y, flags, param):
        """Links ziehen = ROI, rechts ziehen = Ausschluss-Zone (in diesem Fenster)."""
        if event == cv2.EVENT_LBUTTONDOWN:
            self._roi_drag = (x, y)
        elif event == cv2.EVENT_LBUTTONUP and self._roi_drag is not None:
            r = _rect_norm(self._roi_drag, (x, y))
            self._roi_drag = None
            if (r[2] - r[0]) < 0.02 or (r[3] - r[1]) < 0.02:
                self.roi[:] = [0.0, 0.0, 1.0, 1.0]
                print(f"[{self.label}] ROI zurueckgesetzt (ganzes Bild)")
            else:
                self.roi[:] = r
                print(f"[{self.label}] ROI gesetzt: {[round(c, 3) for c in self.roi]}")
        elif event == cv2.EVENT_RBUTTONDOWN:
            self._excl_drag = (x, y)
        elif event == cv2.EVENT_RBUTTONUP and self._excl_drag is not None:
            r = _rect_norm(self._excl_drag, (x, y))
            self._excl_drag = None
            if (r[2] - r[0]) >= 0.01 and (r[3] - r[1]) >= 0.01:
                self.excludes.append(r)
                print(f"[{self.label}] Ausschluss-Zone #{len(self.excludes)}")
        elif event == cv2.EVENT_MOUSEMOVE:
            if self._roi_drag is not None and (flags & cv2.EVENT_FLAG_LBUTTON):
                self.roi[:] = _rect_norm(self._roi_drag, (x, y))


# =====================================================================
# Settings laden/speichern (global + pro Kamera).
# =====================================================================
def load_settings(cams):
    global BLOB_MIN_AREA, BLOB_MAX_AREA, BLOB_MIN_CIRC, BLOB_MIN_FILL
    global COLOR_ON, HUE_MIN, HUE_MAX, SAT_MAX
    try:
        d = json.load(open(SETTINGS_FILE, encoding="utf-8"))
        BLOB_MIN_AREA   = int(d.get("area", BLOB_MIN_AREA))
        BLOB_MAX_AREA   = int(d.get("area_max", BLOB_MAX_AREA))
        BLOB_MIN_CIRC   = float(d.get("circ", BLOB_MIN_CIRC))
        BLOB_MIN_FILL   = float(d.get("fill", BLOB_MIN_FILL))
        COLOR_ON        = int(d.get("color_on", COLOR_ON))
        HUE_MIN         = int(d.get("hue_min", HUE_MIN))
        HUE_MAX         = int(d.get("hue_max", HUE_MAX))
        SAT_MAX         = int(d.get("sat_max", SAT_MAX))
        old_bright      = d.get("bright")   # alt: globale Helligkeit -> Fallback
        per = d.get("cameras", {})
        for cam in cams:
            c = per.get(cam.serial, {})
            cam.bright = int(c.get("bright", old_bright if old_bright is not None else cam.bright))
            cam.exposure = int(c.get("exposure", cam.exposure))
            cam.gain = int(c.get("gain", cam.gain))
            if isinstance(c.get("roi"), list) and len(c["roi"]) == 4:
                cam.roi[:] = [float(x) for x in c["roi"]]
            if isinstance(c.get("excludes"), list):
                cam.excludes[:] = [list(map(float, r)) for r in c["excludes"] if len(r) == 4]
            cam.apply_exposure(force=True)
        print(f"[tracking] Settings geladen ({SETTINGS_FILE})")
    except Exception:
        print("[tracking] keine Settings -> Defaults (Taste S speichert)")


def save_settings(cams):
    d = dict(area=int(BLOB_MIN_AREA), area_max=int(BLOB_MAX_AREA),
             circ=round(float(BLOB_MIN_CIRC), 2), fill=round(float(BLOB_MIN_FILL), 2),
             color_on=int(COLOR_ON), hue_min=int(HUE_MIN), hue_max=int(HUE_MAX),
             sat_max=int(SAT_MAX), cameras={})
    for cam in cams:
        d["cameras"][cam.serial] = dict(
            bright=int(cam.bright),
            exposure=int(cam.exposure), gain=int(cam.gain),
            roi=[round(c, 4) for c in cam.roi],
            excludes=[[round(c, 4) for c in r] for r in cam.excludes])
    try:
        json.dump(d, open(SETTINGS_FILE, "w", encoding="utf-8"))
        print(f"[tracking] Settings gespeichert: {SETTINGS_FILE}")
    except OSError as e:
        print(f"[tracking] Speichern fehlgeschlagen: {e}")


def load_calib(cams):
    try:
        d = json.load(open(CALIB_FILE, encoding="utf-8"))
        for cam in cams:
            c = d.get(cam.serial, {})
            for m in ("BLOB", "IR"):
                if isinstance(c.get(m), list) and len(c[m]) == 4:
                    cam.set_calib(m, [[float(p[0]), float(p[1])] for p in c[m]])
        ready = [f"{cam.label}:{m}" for cam in cams for m in ("BLOB", "IR")
                 if cam.H(m) is not None]
        print(f"[tracking] Homographie geladen: {ready or 'keine'}")
    except Exception:
        print("[tracking] keine Homographie-Kalibrierung -> Fokus-Kamera waehlen, dann C")


def save_calib(cams):
    d = {}
    for cam in cams:
        entry = {m: cam.src_pts(m) for m in ("BLOB", "IR") if cam.src_pts(m) is not None}
        if entry:
            d[cam.serial] = entry
    try:
        json.dump(d, open(CALIB_FILE, "w", encoding="utf-8"))
        print(f"[tracking] Homographie gespeichert ({list(d)})")
    except OSError as e:
        print(f"[tracking] Speichern fehlgeschlagen: {e}")


# =====================================================================
# Maske / Regler (eine Fenster-Instanz, steuert Fokus-Kamera).
# =====================================================================
def open_mask_window(focus):
    cv2.namedWindow(MASK_WIN)
    cv2.createTrackbar("Hell",    MASK_WIN, int(focus.bright),         255, lambda v: None)
    cv2.createTrackbar("AreaMin", MASK_WIN, int(BLOB_MIN_AREA),       2000, lambda v: None)
    cv2.createTrackbar("AreaMax", MASK_WIN, int(BLOB_MAX_AREA),      20000, lambda v: None)
    cv2.createTrackbar("Rund%",   MASK_WIN, int(BLOB_MIN_CIRC * 100),  100, lambda v: None)
    cv2.createTrackbar("Fuell%",  MASK_WIN, int(BLOB_MIN_FILL * 100),  100, lambda v: None)
    cv2.createTrackbar("Farbe",   MASK_WIN, int(COLOR_ON),               1, lambda v: None)
    cv2.createTrackbar("Hmin",    MASK_WIN, int(HUE_MIN),              179, lambda v: None)
    cv2.createTrackbar("Hmax",    MASK_WIN, int(HUE_MAX),              179, lambda v: None)
    cv2.createTrackbar("Smax",    MASK_WIN, int(SAT_MAX),              255, lambda v: None)
    cv2.createTrackbar("Exp",     MASK_WIN, int(focus.exposure),      1000, lambda v: None)
    cv2.createTrackbar("Gain",    MASK_WIN, int(focus.gain),           128, lambda v: None)


def sync_mask_focus(focus):
    """Nach Fokuswechsel Hell/Exp/Gain-Regler auf die neue Kamera setzen."""
    try:
        cv2.setTrackbarPos("Hell", MASK_WIN, int(focus.bright))
        cv2.setTrackbarPos("Exp", MASK_WIN, int(focus.exposure))
        cv2.setTrackbarPos("Gain", MASK_WIN, int(focus.gain))
    except cv2.error:
        pass


def read_mask_trackbars(focus):
    global BLOB_MIN_AREA, BLOB_MAX_AREA, BLOB_MIN_CIRC, BLOB_MIN_FILL
    global COLOR_ON, HUE_MIN, HUE_MAX, SAT_MAX
    try:
        focus.bright    = cv2.getTrackbarPos("Hell",    MASK_WIN)
        BLOB_MIN_AREA   = cv2.getTrackbarPos("AreaMin", MASK_WIN)
        BLOB_MAX_AREA   = cv2.getTrackbarPos("AreaMax", MASK_WIN)
        BLOB_MIN_CIRC   = cv2.getTrackbarPos("Rund%",   MASK_WIN) / 100.0
        BLOB_MIN_FILL   = cv2.getTrackbarPos("Fuell%",  MASK_WIN) / 100.0
        COLOR_ON        = cv2.getTrackbarPos("Farbe",   MASK_WIN)
        HUE_MIN         = cv2.getTrackbarPos("Hmin",    MASK_WIN)
        HUE_MAX         = cv2.getTrackbarPos("Hmax",    MASK_WIN)
        SAT_MAX         = cv2.getTrackbarPos("Smax",    MASK_WIN)
        focus.exposure  = max(1, cv2.getTrackbarPos("Exp", MASK_WIN))
        focus.gain      = cv2.getTrackbarPos("Gain",    MASK_WIN)
    except cv2.error:
        pass


# Erklaerung der (kurzen) Regler-Namen -> als Legende ins Maske-Fenster.
MASK_LEGEND = [
    ("Hell",    "Helligkeits-Schwelle der FOKUS-Kamera (0..255)"),
    ("AreaMin", "min. Blob-Flaeche in Pixeln"),
    ("AreaMax", "max. Blob-Flaeche (0 = aus)"),
    ("Rund%",   "Mindest-Rundheit 0..100 (Kreis = 100)"),
    ("Fuell%",  "Mindest-Fuellgrad 0..100"),
    ("Farbe",   "HSV-Farbfilter an (1) / aus (0)"),
    ("Hmin",    "Farbton unten   (nur wenn Farbe = 1)"),
    ("Hmax",    "Farbton oben    (nur wenn Farbe = 1)"),
    ("Smax",    "max. Saettigung (nur wenn Farbe = 1)"),
    ("Exp",     "Belichtung der FOKUS-Kamera"),
    ("Gain",    "Verstaerkung der FOKUS-Kamera"),
]


def render_mask_view(mask, focus):
    """Binaere Maske als BGR + eingeblendete Regler-Legende (halbtransparent)."""
    view = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
    x, y0, line = 8, 20, 17
    w, h = 300, y0 + line * (len(MASK_LEGEND) + 1) + 6
    panel = view.copy()
    cv2.rectangle(panel, (4, 4), (4 + w, 4 + h), (0, 0, 0), -1)
    cv2.addWeighted(panel, 0.55, view, 0.45, 0, view)
    cv2.putText(view, f"Regler  (Fokus: Kamera {focus.label})", (x, y0),
                FONT, 0.5, (0, 255, 255), 1)
    for i, (name, desc) in enumerate(MASK_LEGEND):
        y = y0 + line * (i + 1) + 6
        cv2.putText(view, name, (x, y), FONT, 0.42, (0, 200, 255), 1)
        cv2.putText(view, "= " + desc, (x + 66, y), FONT, 0.42, (230, 230, 230), 1)
    return view


# =====================================================================
# Erkennung: liefert ALLE Kandidaten (u, v, score) je Kamera.
# =====================================================================
def detect_blob(bgr, cam):
    """Alle plausiblen Blobs -> ([(u, v, score)], mask); zeichnet Kandidaten."""
    if COLOR_ON:
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, (HUE_MIN, 0, cam.bright),
                                (HUE_MAX, SAT_MAX, 255))
    else:
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        _, mask = cv2.threshold(gray, cam.bright, 255, cv2.THRESH_BINARY)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    cam.apply_roi(mask)
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
        cv2.circle(bgr, (int(cx), int(cy)), int(r), (0, 160, 255), 1)
    return cands, mask


def detect_ir(img, vis, cam):
    """Helle IR-Punkte -> gemittelter Schwerpunkt als EIN Kandidat."""
    _, mask = cv2.threshold(img, 200, 255, cv2.THRESH_BINARY)
    cam.apply_roi(mask)
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
    mu = sum(p[0] * p[2] for p in pts) / total
    mv = sum(p[1] * p[2] for p in pts) / total
    return [(mu, mv, total)]


# =====================================================================
# WebSocket-Server (identisch zu v2: {"active","x","y"} alle 20 ms).
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
# Kamera-Erkennung: RealSense-Geraete finden.
# =====================================================================
def enumerate_cameras():
    ctx = rs.context()
    serials = []
    for d in ctx.devices:
        try:
            name = d.get_info(rs.camera_info.name)
            if "platform camera" in name.lower():
                continue
            serials.append(d.get_info(rs.camera_info.serial_number))
        except Exception:
            pass
    serials.sort()   # stabile Reihenfolge -> A/B bleiben gleich
    return serials


# =====================================================================
# Kalibrierung: ALLE Kameras gleichzeitig. Ein Leertasten-Druck nimmt die
# aktuelle Ecke in JEDER Kamera auf (jede an ihrer eigenen Pixelposition).
# =====================================================================
_calib_active = False
_calib_step = 0
_calib_pts = {}          # cam -> Liste der bisher aufgenommenen [u, v]


def calib_start(cams):
    global _calib_active, _calib_step, _calib_pts
    _calib_active, _calib_step = True, 0
    _calib_pts = {cam: [] for cam in cams}
    print(f"[tracking] Kalibrierung ALLER Kameras: Mond in Ecke {CALIB_STEPS[0]} "
          f"halten (beide muessen ihn sehen), dann Leertaste")


def calib_capture(raw_by_cam, mode):
    """raw_by_cam: {cam: (u, v) oder None}. Nimmt die aktuelle Ecke in allen
    Kameras auf - aber nur, wenn JEDE Kamera den Mond gerade sieht."""
    global _calib_active, _calib_step
    if not _calib_active:
        return
    missing = [cam.label for cam in _calib_pts if raw_by_cam.get(cam) is None]
    if missing:
        print(f"[tracking] Ecke {CALIB_STEPS[_calib_step]}: kein Blob bei Kamera "
              f"{', '.join(missing)} -> Ecke wiederholen (Mond muss in ALLEN Bildern sein)")
        return
    for cam in _calib_pts:
        u, v = raw_by_cam[cam][:2]
        _calib_pts[cam].append([float(u), float(v)])
    _calib_step += 1
    if _calib_step < 4:
        print(f"[tracking] ok. Jetzt Ecke {CALIB_STEPS[_calib_step]}, dann Leertaste")
        return
    for cam, pts in _calib_pts.items():
        cam.set_calib(mode, list(pts))
    _calib_active = False
    save_calib(CAMS)
    tracker.reset()
    print(f"[tracking] Homographie ({mode}) fertig fuer: "
          f"{', '.join(cam.label for cam in _calib_pts)}")


# =====================================================================
# Hauptprogramm
# =====================================================================
serials = enumerate_cameras()
if not serials:
    raise SystemExit("[tracking] keine RealSense-Kamera gefunden.")
serials = serials[:MAX_CAMS]
if len(serials) < 2:
    print(f"[tracking] WARNUNG: nur {len(serials)} Kamera(s) gefunden - laeuft ohne Redundanz.")

CAMS = [RealSenseCamera(s, chr(ord('A') + i)) for i, s in enumerate(serials)]
print(f"[tracking] Kameras: " + ", ".join(f"{c.label}={c.serial}" for c in CAMS))

load_settings(CAMS)
load_calib(CAMS)
start_server(host="0.0.0.0")   # ans LAN binden, damit der Mac ueber Kabel verbindet

mode = "BLOB"
show_mask = False
focus = CAMS[0]

for cam in CAMS:
    cv2.namedWindow(cam.win)
    cv2.setMouseCallback(cam.win, cam.on_mouse)

_last_t = time.monotonic()

try:
    while True:
        now = time.monotonic()
        dt = max(1e-3, now - _last_t)
        _last_t = now

        if show_mask:
            read_mask_trackbars(focus)
            focus.apply_exposure()

        # --- pro Kamera erkennen -> Anzeige + Kandidaten (Pixel) ---
        per_cam = []                       # (cam, vis, cands_pixel)
        for cam in CAMS:
            latest = cam.read()
            if latest is None:
                continue
            color_img, ir_img = latest
            if mode == "IR" and ir_img is not None:
                vis = cv2.cvtColor(ir_img, cv2.COLOR_GRAY2BGR)
                cands = detect_ir(ir_img, vis, cam)
                mask = None
            else:
                vis = color_img.copy()
                cands, mask = detect_blob(vis, cam)
            per_cam.append((cam, vis, cands))
            if show_mask and cam is focus and mask is not None:
                cv2.imshow(MASK_WIN, render_mask_view(mask, focus))

        # rohe beste Detektion PRO Kamera (fuer Kalibrierung + Triangulation)
        raw_by_cam = {cam: (max(cands, key=lambda c: c[2]) if cands else None)
                      for cam, _, cands in per_cam}

        # --- Position bestimmen ---
        # Bevorzugt 3D-TRIANGULATION: sehen >=2 kalibrierte Kameras den Mond,
        # schneiden sich ihre Sichtstrahlen im echten 3D-Punkt -> Hoehe (Z)
        # faellt raus. Sonst Rueckfall auf die (ebene) Homographie-Fusion.
        fused = []
        tri = None                         # (nx, ny, residual) wenn trianguliert
        posed = [(cam, raw_by_cam[cam]) for cam in CAMS
                 if cam.has_pose(mode) and raw_by_cam.get(cam) is not None]
        if len(posed) >= 2:
            (camA, bA), (camB, bB) = posed[0], posed[1]
            rA = camA.ray(bA[0], bA[1], mode)
            rB = camB.ray(bB[0], bB[1], mode)
            if rA is not None and rB is not None:
                res = triangulate(rA, rB)
                if res is not None and res[2] <= TRI_MAX_RESIDUAL:
                    tri = res
                    fused.append((res[0], res[1], bA[2] + bB[2]))

        if not fused:                      # Rueckfall: ebene Homographie
            calibrated = [cam for cam in CAMS if cam.H(mode) is not None]
            sources = calibrated if calibrated else CAMS[:1]
            for cam, _, cands in per_cam:
                if cam not in sources:
                    continue
                for (u, v, score) in cands:
                    gx, gy = cam.to_game(u, v, mode)
                    fused.append((gx, gy, score))

        # --- gemeinsamer Tracker im Spielkoordinaten-Raum ---
        tp = tracker.update(fused, dt)     # (x, y) in 0..1 oder None
        if tp is not None:
            set_state(True, tp[0], tp[1])
        else:
            set_state(False)

        # --- Anzeige je Kamera ---
        for cam, vis, cands in per_cam:
            # getrackten Punkt in DIESES Kamerabild zurueckprojizieren
            if tp is not None:
                px, py = cam.from_game(tp[0], tp[1], mode)
                if -50 <= px <= W_IMG + 50 and -50 <= py <= H_IMG + 50:
                    cv2.circle(vis, (int(px), int(py)), 8, (0, 255, 0), 2)
                src_tag = f"3D-Tri (res {tri[2]:.2f})" if tri is not None else "2D-Homogr."
                cv2.putText(vis, f"send x={tp[0]:.2f} y={tp[1]:.2f}  [{src_tag}]",
                            (10, 55), FONT, 0.6, (0, 255, 0), 1)
            else:
                cv2.putText(vis, "kein Mond", (10, 55), FONT, 0.6, (0, 0, 255), 1)

            # Spielfeld-Ecken, ROI, Ausschluesse
            if cam.src_pts(mode) is not None:
                q = np.int32(cam.src_pts(mode))
                cv2.polylines(vis, [q], True, (0, 200, 200), 1)
            if cam.roi != [0.0, 0.0, 1.0, 1.0]:
                rx0, ry0 = int(cam.roi[0] * W_IMG), int(cam.roi[1] * H_IMG)
                rx1, ry1 = int(cam.roi[2] * W_IMG), int(cam.roi[3] * H_IMG)
                cv2.rectangle(vis, (rx0, ry0), (rx1, ry1), (255, 0, 255), 1)
            for i, r in enumerate(cam.excludes):
                ex0, ey0 = int(r[0] * W_IMG), int(r[1] * H_IMG)
                ex1, ey1 = int(r[2] * W_IMG), int(r[3] * H_IMG)
                cv2.rectangle(vis, (ex0, ey0), (ex1, ey1), (0, 0, 255), 1)
                cv2.putText(vis, f"X{i + 1}", (ex0 + 3, ey0 + 15), FONT, 0.5, (0, 0, 255), 1)

            tag = "[FOKUS]" if cam is focus else ""
            calib_flag = "" if cam.H(mode) is not None else " UNKALIBRIERT"
            cv2.putText(vis, f"Kamera {cam.label} {tag}  Modus {mode}{calib_flag}",
                        (10, 30), FONT, 0.6, (0, 255, 255), 2)

            if _calib_active:
                seen = raw_by_cam.get(cam) is not None
                col = (0, 255, 0) if seen else (0, 0, 255)
                cv2.putText(vis, f"KALIBRIERUNG {_calib_step + 1}/4: Ecke "
                                 f"{CALIB_STEPS[_calib_step]} -> LEER",
                            (10, 110), FONT, 0.7, col, 2)
                cv2.putText(vis, "Mond sichtbar" if seen else "KEIN Mond - Ecke wiederholen",
                            (10, 135), FONT, 0.5, col, 1)
                rb = raw_by_cam.get(cam)
                if rb is not None:
                    cv2.drawMarker(vis, (int(rb[0]), int(rb[1])),
                                   (0, 255, 0), cv2.MARKER_CROSS, 20, 2)
            else:
                cv2.putText(vis, "1/2=Fokus  C=Kalib  T=Modus  M=Maske  "
                                 "S=Speichern  R=ROI  X=Ausschl.",
                            (10, H_IMG - 12), FONT, 0.42, (0, 255, 0), 1)

            cv2.imshow(cam.win, vis)

        key = cv2.waitKey(1) & 0xFF
        if key == 27:                       # ESC
            if _calib_active:
                _calib_active = False
                print("[tracking] Kalibrierung abgebrochen")
            else:
                break
        elif key == ord('1'):
            focus = CAMS[0]
            if show_mask:
                sync_mask_focus(focus)
        elif key == ord('2') and len(CAMS) > 1:
            focus = CAMS[1]
            if show_mask:
                sync_mask_focus(focus)
        elif key == ord('c') and not _calib_active:
            calib_start(CAMS)
        elif key == ord(' ') and _calib_active:
            calib_capture(raw_by_cam, mode)
        elif key == ord('t') and not _calib_active:
            mode = "BLOB" if mode == "IR" else "IR"
            tracker.reset()
            if mode == "IR" and show_mask:
                show_mask = False
                try:
                    cv2.destroyWindow(MASK_WIN)
                except Exception:
                    pass
            print(f"[tracking] Modus -> {mode}")
        elif key == ord('s') and not _calib_active:
            save_settings(CAMS)
        elif key == ord('r') and not _calib_active:
            focus.roi[:] = [0.0, 0.0, 1.0, 1.0]
            print(f"[{focus.label}] ROI zurueckgesetzt (ganzes Bild)")
        elif key == ord('x') and not _calib_active:
            focus.excludes.clear()
            print(f"[{focus.label}] Ausschluss-Zonen geloescht")
        elif key == ord('m') and mode == "BLOB":
            show_mask = not show_mask
            if show_mask:
                open_mask_window(focus)
            else:
                try:
                    cv2.destroyWindow(MASK_WIN)
                except Exception:
                    pass
finally:
    for cam in CAMS:
        cam.stop()
    cv2.destroyAllWindows()
