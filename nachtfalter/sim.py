"""
NACHTFALTER · sim   (entspricht Abschnitt C der HTML — das "Gehirn")
====================================================================
Wortgetreuer Port der Boids-/Laternen-/Kollisions-/Scroll-Logik.
Reine Mathe, keine Grafik. esp32 und moon werden injiziert (siehe
main.py), damit es keine zirkulären Importe gibt.
"""
import csv
import datetime
import math
import os
import random
from types import SimpleNamespace

import config

rand = lambda a, b: a + random.random() * (b - a)

SEP_R = 26
EDGE = 70


def point_in_poly(x, y, poly):
    """Ray-Casting: liegt (x, y) innerhalb des Polygons (Liste von (x, y))?"""
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]; xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside

# Halbwinkel des sichtbaren Lichtkegels (aus den Kegel-Faktoren im Renderer,
# config.CONE). Bei voll abgeschirmten Lampen (up=0) gilt dieser enge Kegel,
# bei offenen (up=1) wird bis zum Vollkreis (pi) aufgeweitet.
CONE_HALF = math.atan2(config.CONE["spread"], config.CONE["length"])

# --- Einfang-Verhalten: voll im Lichtkegel -> anfliegen, kreisen, sterben ---
CAPTURE_ORBIT_TIME = 5.0     # s, die der Falter um die Quelle kreist, bevor er abstürzt
CAPTURE_ORBIT_R = 24.0       # Bahnradius um die Lichtquelle (Render-Pixel)
CAPTURE_ORBIT_FORCE = 0.55   # Tangentialkraft -> Kreisbewegung


class Moth:
    __slots__ = ("x", "y", "vx", "vy", "maxSpeed", "wander", "spin", "flap",
                 "flapSpeed", "size", "col", "tone", "exposure", "dying",
                 "rot", "spriteSrc", "captured", "orbitTimer")


class Light:
    __slots__ = ("x", "y", "active")
    def __init__(self, x, y):
        self.x, self.y, self.active = x, y, False


class Sim:
    def __init__(self, w, h, esp32=None, moon=None):
        self.W, self.H = w, h
        self.esp32 = esp32
        self.moon = moon
        self.P = dict(config.PARAMS)
        self.bg_frame = 0.0
        self.light = Light(w / 2, h / 2)
        self.moths = []
        self.hazards = []
        self.embers = []
        self.deadCount = 0
        self.survived = 0.0
        self.spawnTimer = 0.0
        self.gameOver = False
        self.bg_scroll = 0.0          # Panorama-Scroll in Render-Pixeln (geteilt mit render)
        self.scroll_frozen = False    # Editor: Auto-Scroll aus, manuell per A/D
        self.editing = False          # Editor aktiv: Falter können nicht sterben

        # --- Interaktions-Tracking (Mond angehoben -> RFID-Tag inaktiv -> Lichter AN) ---
        # Eine Interaktion läuft, solange der Mond angehoben ist (Lichter an); genau in
        # dieser Zeit fliegen die Falter zu den Laternen und sterben. Beim Ablegen des
        # Mondes (RFID-Tag wieder aktiv, Lichter aus) endet sie.
        # Das Tracking selbst wird per Taste T ein-/ausgeschaltet (nicht der RFID-Tag).
        self.tracking_enabled = False
        self.interacting = False
        self.interaction_time = 0.0        # Dauer der laufenden Interaktion (s)
        self.interaction_deaths = 0        # in der laufenden Interaktion gestorbene Falter
        self._interaction_dead_start = 0   # deadCount beim Start der Interaktion
        self.interaction_count = 0         # Anzahl abgeschlossener Interaktionen (pro Session)
        self.last_interaction_time = 0.0   # Dauer der letzten abgeschlossenen Interaktion
        self.last_interaction_deaths = 0   # Tote der letzten abgeschlossenen Interaktion
        self._interaction_start_ts = None  # Wanduhr-Zeitstempel beim Start der Interaktion

        # CSV-Log: pro abgeschlossener Interaktion eine Zeile in logs/interaktionen.csv.
        # Die Datei liegt neben diesem Skript, unabhängig vom Arbeitsverzeichnis.
        _base_dir = os.path.dirname(os.path.abspath(__file__))
        self.log_dir = os.path.join(_base_dir, "logs")
        self.log_path = os.path.join(self.log_dir, "interaktionen.csv")
        self._log_id = 0                   # fortlaufende ID (auch über Neustarts hinweg)
        self._ensure_log_file()

        self.spawn_swarm()

        if not config.USE_BG_LIGHTS:
            for _ in range(4):
                self.spawn_hazard()

    # --- Leitlicht-Quellen ----------------------------------------
    def update_pointer(self, x, y, inside):
        """Maus = Leitlicht (Fallback, wenn kein Mond-Tracking aktiv)."""
        if inside:
            self.light.x, self.light.y = x, y
            self.light.active = True
        else:
            self.light.active = False

    def resize(self, w, h):
        self.W, self.H = w, h

    # --- Schwarm / Reset ------------------------------------------
    def make_moth(self):
        cx = self.light.x if self.light.active else self.W / 2
        cy = self.light.y if self.light.active else self.H / 2
        m = Moth()
        m.x = cx + rand(-90, 90); m.y = cy + rand(-90, 90)
        m.vx = rand(-1, 1); m.vy = rand(-1, 1)
        m.maxSpeed = rand(2.0, 3.4); m.wander = rand(0, 6.28)
        m.spin = 1 if random.random() < .5 else -1
        m.flap = rand(0, 6.28); m.flapSpeed = rand(0.15, 0.3)
        m.size = rand(7, 11)
        m.col = config.PALETTE[int(random.random() * len(config.PALETTE))]
        m.tone = rand(.7, 1.1)
        m.exposure = 0.0; m.dying = 0.0; m.rot = 0.0
        m.captured = None; m.orbitTimer = 0.0
        moths = config.ASSETS["moths"]
        m.spriteSrc = moths[int(random.random() * len(moths))] if moths else None
        return m

    def spawn_swarm(self):
        self.moths = [self.make_moth() for _ in range(int(self.P["count"]))]

    def reset(self):
        self.spawn_swarm()
        self.hazards = []; self.embers = []
        self.deadCount = 0; self.survived = 0.0; self.spawnTimer = 0.0
        self.gameOver = False

        # Interaktions-Tracking ebenfalls zurücksetzen
        self.interacting = False
        self.interaction_time = 0.0
        self.interaction_deaths = 0
        self._interaction_dead_start = 0
        self.interaction_count = 0
        self.last_interaction_time = 0.0
        self.last_interaction_deaths = 0

        if not config.USE_BG_LIGHTS:
            for _ in range(4):
                self.spawn_hazard()

    def set_count(self, v):
        v = int(v)
        o = self.P["count"]; self.P["count"] = v
        if v > o:
            for _ in range(v - o):
                self.moths.append(self.make_moth())
        else:
            del self.moths[v:]

    # --- Laternen --------------------------------------------------
    def spawn_hazard(self):
        W, H, P = self.W, self.H, self.P
        y = rand(0.60, 0.80) * H
        t = random.choice(config.LAMP_TYPES)
        c = random.choice(config.CUTOFF_TYPES)

        # Mindestens eine kleine Laterne im Set erzwingen, sonst zufälliger Radius
        has_small = any(hz.r < P["hazardR"] * 0.7 for hz in self.hazards)
        if not has_small and self.hazards:
            r = P["hazardR"] * rand(0.40, 0.60)
        else:
            r = P["hazardR"] * (0.55 + t["strength"] * 0.75) * c["lure"] * rand(0.9, 1.12)

        # x mit Zufalls-Abstand zu bestehenden Laternen suchen
        min_distance = r * 2.0
        final_x = rand(r, W - r)
        for _ in range(30):
            candidate_x = rand(r, W - r)
            too_close = any(abs(hz.x - candidate_x) < min_distance + rand(50, 300)
                            for hz in self.hazards)
            if not too_close:
                final_x = candidate_x
                break

        lamps = config.ASSETS["lamps"]
        self.hazards.append(SimpleNamespace(
            x=final_x, y=y, top=True, r=r,
            name=t["name"], glow=t["glow"], strength=t["strength"],
            armDir=1 if random.random() < .5 else -1,
            cutName=c["name"], up=c["up"], lure=c["lure"],
            lampSrc=random.choice(lamps) if lamps else None,
            glow_cache=None,
        ))

    # --- Hintergrund-Lichtquellen (Polygone, scrollen mit dem Panorama) -----
    def _rebuild_bg_hazards(self, rfid_light_on):
        """Erzeugt die Polygon-Hazards aus config.BG_LIGHTS an den aktuellen
        Bildschirm-Positionen des scrollenden Panoramas. Nur wenn Lichter AN."""
        self.hazards = []
        if not rfid_light_on:
            return
        W, H = self.W, self.H
        dw = int(H * config.BG_ASPECT)          # identisch zur Kachelbreite im Renderer
        if dw <= 0:
            return
        base_x = (-self.bg_scroll) % dw - dw        # erste Kachel beginnt jetzt rechts außerhalb
        n_tiles = int(W / dw) + 2
        for L in config.BG_LIGHTS:
            poly_uv = L["poly"]
            if len(poly_uv) < 3:
                continue
            cu = sum(p[0] for p in poly_uv) / len(poly_uv)
            cv = sum(p[1] for p in poly_uv) / len(poly_uv)
            for k in range(n_tiles):
                tile_x = base_x + k * dw
                cx = tile_x + cu * dw; cy = cv * H
                pts = [(tile_x + u * dw, v * H) for (u, v) in poly_uv]
                r = max(math.hypot(px - cx, py - cy) for px, py in pts)
                if cx + r * config.BG_LURE_MARGIN < 0 or cx - r * config.BG_LURE_MARGIN > W:
                    continue
                self.hazards.append(SimpleNamespace(
                    poly=pts, cx=cx, cy=cy, r=r,
                    strength=L.get("strength", 0.5),
                    glow=L.get("col", (255, 200, 120)),
                ))

    def kill_moth(self, m):
        m.dying = 0.001
        m.vx *= 0.3; m.vy *= 0.3
        self.deadCount += 1
        for _ in range(9):
            self.embers.append(SimpleNamespace(
                x=m.x, y=m.y, vx=rand(-0.8, 0.8), vy=rand(-1.2, -0.2),
                life=rand(0.5, 1.1), maxLife=1.1))
        if self.esp32:
            self.esp32.send_death()

    # --- CSV-Log --------------------------------------------------
    def _ensure_log_file(self):
        """Legt den Log-Ordner an und schreibt die Kopfzeile, falls die CSV neu ist.
        Existiert die Datei schon, wird die höchste bereits vergebene ID gelesen,
        damit die IDs über Programm-Neustarts hinweg fortlaufend bleiben."""
        try:
            os.makedirs(self.log_dir, exist_ok=True)
            if os.path.exists(self.log_path):
                with open(self.log_path, newline="", encoding="utf-8") as fh:
                    rows = list(csv.reader(fh))
                # Alle Zeilen minus Kopfzeile = zuletzt vergebene ID
                self._log_id = max(0, len(rows) - 1)
            else:
                with open(self.log_path, "w", newline="", encoding="utf-8") as fh:
                    csv.writer(fh).writerow(
                        ["id", "start", "ende", "dauer_s", "gestorbene_falter"])
        except OSError as e:
            print(f"[Log] Konnte Log-Datei nicht vorbereiten: {e}")

    def _log_interaction(self):
        """Hängt die gerade beendete Interaktion als eine Zeile an die CSV an."""
        self._log_id += 1
        start = self._interaction_start_ts
        end = datetime.datetime.now()
        row = [
            self._log_id,
            start.isoformat(timespec="seconds") if start else "",
            end.isoformat(timespec="seconds"),
            f"{self.interaction_time:.1f}",
            self.interaction_deaths,
        ]
        try:
            with open(self.log_path, "a", newline="", encoding="utf-8") as fh:
                csv.writer(fh).writerow(row)
        except OSError as e:
            print(f"[Log] Konnte Interaktion nicht speichern: {e}")

    # --- Interaktions-Tracking ------------------------------------
    def _track_interaction(self, rfid_light_on, dts):
        """Misst pro Interaktion (Mond angehoben = Lichter AN) die Dauer und wie
        viele Falter in dieser Zeit sterben. Startet, sobald der Mond angehoben
        wird (RFID-Tag inaktiv), und endet beim Ablegen (RFID-Tag wieder aktiv).
        Läuft nur, wenn das Tracking per Taste T aktiviert ist."""
        if not self.tracking_enabled:
            # Tracking aus -> eine evtl. laufende Messung wird verworfen
            self.interacting = False
            return
        if rfid_light_on and not self.interacting:
            # Interaktion beginnt: Mond angehoben, Lichter gehen an
            self.interacting = True
            self.interaction_time = 0.0
            self.interaction_deaths = 0
            self._interaction_dead_start = self.deadCount
            self._interaction_start_ts = datetime.datetime.now()
        elif self.interacting and not rfid_light_on:
            # Interaktion endet: Mond abgelegt, RFID-Tag wieder aktiv
            self.interaction_time += dts
            self.interaction_deaths = self.deadCount - self._interaction_dead_start
            self.interacting = False
            self.interaction_count += 1
            self.last_interaction_time = self.interaction_time
            self.last_interaction_deaths = self.interaction_deaths
            self._log_interaction()
            print(f"[Interaktion #{self._log_id}] "
                  f"Dauer: {self.interaction_time:.1f}s · "
                  f"gestorbene Falter: {self.interaction_deaths} "
                  f"-> {self.log_path}")
        elif self.interacting:
            # Laufende Interaktion fortschreiben
            self.interaction_time += dts
            self.interaction_deaths = self.deadCount - self._interaction_dead_start

    # --- Hauptschritt (1:1 zur HTML) ------------------------------
    def step(self, dt, dts, mouse_inside=True, rfid_light_on=True):
        self.dts = dts
        self.rfid_light_on = rfid_light_on
        W, H, P = self.W, self.H, self.P

        # Interaktion tracken (Mond angehoben <-> abgelegt)
        self._track_interaction(rfid_light_on, dts)

        # bg_frame 0..19: Lichter AN -> vorwärts (ON-Bild), sonst zurück (OFF-Bild)
        if not self.gameOver:
            self.survived += dts
            anim_speed = 12.0 * dts
            if rfid_light_on:
                self.bg_frame = min(19.0, self.bg_frame + anim_speed)
            else:
                self.bg_frame = max(0.0, self.bg_frame - anim_speed)
        if self.moon:
            self.moon.apply(self.light, W, H)

        # Lichter AUS: auch Leitlicht/Mondkegel aus -> Falter fliegen frei
        if not rfid_light_on:
            self.light.active = False

        if config.USE_BG_LIGHTS:
            # Scrollen nur wenn Lichter AN und nicht im Editor eingefroren.
            if rfid_light_on and not self.scroll_frozen:
                self.bg_scroll += config.BG_SCROLL_SPEED * dts
            self._rebuild_bg_hazards(rfid_light_on)
        else:
            if not self.gameOver:
                self.spawnTimer -= dts
                if self.spawnTimer < 0:
                    if len(self.hazards) < 4:
                        self.spawn_hazard()
                    self.spawnTimer = P["spawn"] * rand(0.75, 1.25)
            for i in range(len(self.hazards) - 1, -1, -1):
                hz = self.hazards[i]
                hz.x += config.SCROLL_DIR * P["speed"] * dts
                if (config.SCROLL_DIR < 0 and hz.x < -hz.r - 90) or \
                   (config.SCROLL_DIR > 0 and hz.x > W + hz.r + 90):
                    self.hazards.pop(i)

        cx = cy = avx = avy = 0.0; n = 0
        for m in self.moths:
            if m.dying == 0:
                cx += m.x; cy += m.y; avx += m.vx; avy += m.vy; n += 1
        if n > 0:
            cx /= n; cy /= n; avx /= n; avy /= n

        follow = P["follow"]; cohesion = P["cohesion"]
        deathTime = P["deathTime"]
        light = self.light
        moths = self.moths

        for i in range(len(moths) - 1, -1, -1):
            m = moths[i]
            if m.dying > 0:
                m.dying += dts / 0.75; m.vy += 0.05 * dt; m.rot += 0.2 * dt * m.spin
                m.x += m.vx * dt; m.y += m.vy * dt
                if m.dying >= 1:
                    moths.pop(i)
                continue

            # --- Gefangen im Lichtkegel: zur Quelle fliegen, 5 s kreisen, dann sterben ---
            if m.captured is not None:
                if not rfid_light_on or self.editing:
                    # Laternen AUS / Editor -> freilassen, normaler Flug geht weiter
                    m.captured = None
                    m.orbitTimer = 0.0
                    m.exposure = 0.0
                else:
                    cx0, cs0, cy = m.captured
                    # Quelle mit dem Panorama mitführen: das Bild scrollt bei
                    # wachsendem bg_scroll nach LINKS, also muss der Orbit-Mittelpunkt
                    # ebenfalls nach links wandern (Minus, sonst laeuft der Schwarm weg).
                    hzx = cx0 - (self.bg_scroll - cs0)
                    dx = hzx - m.x; dy = cy - m.y
                    d = math.hypot(dx, dy) or 1
                    # Radiale Feder: auf den Bahnradius einschwingen (erst anfliegen, dann halten)
                    radial = (d - CAPTURE_ORBIT_R) * 0.02
                    ax = dx / d * radial; ay = dy / d * radial
                    # Tangentialkraft: um die Quelle kreisen
                    ax += -dy / d * CAPTURE_ORBIT_FORCE * m.spin
                    ay += dx / d * CAPTURE_ORBIT_FORCE * m.spin
                    # Flatter-Bewegung: kein gerader/glatter Flug, sondern Haken wie ein echter Falter
                    sp = math.hypot(m.vx, m.vy) or 0.001
                    hx = m.vx / sp; hy = m.vy / sp
                    m.wander += (random.random() - 0.5) * 0.55
                    wa = math.atan2(hy, hx) + m.wander
                    ax += math.cos(wa) * 0.32; ay += math.sin(wa) * 0.32
                    if random.random() < 0.05 * dt:
                        a = rand(0, 6.28); ax += math.cos(a) * 0.5; ay += math.sin(a) * 0.5
                    m.vx += ax * dt; m.vy += ay * dt
                    s = math.hypot(m.vx, m.vy)
                    if s > m.maxSpeed:
                        m.vx = m.vx / s * m.maxSpeed; m.vy = m.vy / s * m.maxSpeed; s = m.maxSpeed
                    m.x += m.vx * dt; m.y += m.vy * dt
                    m.flap += (m.flapSpeed + s * 0.06) * dt
                    # nur leichtes Glühen statt grellem Orange
                    m.exposure = deathTime * 0.18
                    # 5-Sekunden-Zähler erst starten, wenn er bei der Quelle angekommen ist
                    if d < CAPTURE_ORBIT_R * 2.5:
                        m.orbitTimer += dts
                    if m.orbitTimer >= CAPTURE_ORBIT_TIME:
                        self.kill_moth(m)           # 5 s gekreist -> tot herunterfallen
                    continue

            ax = (cx - m.x) * cohesion; ay = (cy - m.y) * cohesion
            ax += (avx - m.vx) * 0.04; ay += (avy - m.vy) * 0.04
            for o in moths:
                if o is m or o.dying > 0:
                    continue
                dx = m.x - o.x; dy = m.y - o.y; d2 = dx * dx + dy * dy
                if 0.01 < d2 < SEP_R * SEP_R:
                    d = math.sqrt(d2); ax += dx / d * 0.5; ay += dy / d * 0.5

            if light.active:
                dx = light.x - m.x; dy = light.y - m.y
                d = math.hypot(dx, dy) or 1
                f = follow * min(d / 120, 1.4)
                ax += dx / d * f; ay += dy / d * f
                ax += -dy / d * follow * 0.25 * m.spin
                ay += dx / d * follow * 0.25 * m.spin

            in_hazard = False
            if config.USE_BG_LIGHTS:
                for hz in (() if self.editing else self.hazards):
                    dx = hz.cx - m.x; dy = hz.cy - m.y
                    d = math.hypot(dx, dy)
                    if d >= hz.r * config.BG_LURE_MARGIN:
                        continue
                    if point_in_poly(m.x, m.y, hz.poly):
                        # Im Polygon -> gefangen (Orbit um den Schwerpunkt, siehe oben)
                        m.captured = (hz.cx, self.bg_scroll, hz.cy)
                        m.orbitTimer = 0.0
                        in_hazard = True
                        break
                    # Im Randbereich -> nur sanft zur Form hin anziehen (kein Glühen;
                    # das entsteht erst beim Kreisen an der Quelle, siehe captured-Handler)
                    d = d or 1
                    inten = max(0.0, 1 - d / (hz.r * config.BG_LURE_MARGIN))
                    pull = (0.025 + hz.strength * 0.07) * (0.4 + inten)
                    ax += dx / d * pull; ay += dy / d * pull
            else:
                for hz in self.hazards:
                    if not rfid_light_on:
                        break
                    dx = hz.x - m.x; dy = hz.y - m.y; d = math.hypot(dx, dy)
                    if 0.5 < d < hz.r:
                        cos_ang = (-dy) / d
                        half = CONE_HALF + (math.pi - CONE_HALF) * hz.up
                        if config.CONE_LURE and cos_ang < math.cos(half):
                            continue
                        inten = 1 - d / hz.r
                        pull = (0.025 + hz.strength * 0.07) * (0.4 + inten) * hz.lure
                        ax += dx / d * pull; ay += dy / d * pull
                        m.exposure += dts * (0.3 + inten * 1.3) * (0.5 + hz.strength)
                        in_hazard = True
                        if d < hz.r - m.size:
                            m.captured = (hz.x, self.bg_scroll, hz.y)
                            m.orbitTimer = 0.0
                            break

            if m.dying > 0:
                continue
            if not in_hazard:
                m.exposure = max(0, m.exposure - dts * 0.8)

            sp = math.hypot(m.vx, m.vy) or 0.001
            hx = m.vx / sp; hy = m.vy / sp

            # Ruhiger, stabiler Flug. Das nervöse Flattern passiert ausschließlich
            # beim Anflug auf die Laternen (siehe captured-Handler oben), nicht beim
            # Folgen des Cursor-Lichts.
            if random.random() < 0.01 * dt:
                m.wander = rand(0, 6.28)  # Neue feste Richtung auswürfeln
            ax += (hx * 14 + math.cos(m.wander) * 9) * 0.05
            ay += (hy * 14 + math.sin(m.wander) * 9) * 0.05

            if m.x < EDGE:
                ax += (1 - m.x / EDGE) * 0.25
            elif m.x > W - EDGE:
                ax -= (1 - (W - m.x) / EDGE) * 0.25
            if m.y < EDGE:
                ay += (1 - m.y / EDGE) * 0.25
            elif m.y > H - EDGE:
                ay -= (1 - (H - m.y) / EDGE) * 0.25

            m.vx += ax * dt; m.vy += ay * dt
            s = math.hypot(m.vx, m.vy)
            if s > m.maxSpeed:
                m.vx = m.vx / s * m.maxSpeed; m.vy = m.vy / s * m.maxSpeed; s = m.maxSpeed
            if s < 0.5:
                m.vx += hx * 0.3; m.vy += hy * 0.3
            m.x += m.vx * dt; m.y += m.vy * dt
            if m.x < 2: m.x = 2; m.vx = abs(m.vx)
            if m.x > W - 2: m.x = W - 2; m.vx = -abs(m.vx)
            if m.y < 2: m.y = 2; m.vy = abs(m.vy)
            if m.y > H - 2: m.y = H - 2; m.vy = -abs(m.vy)
            m.flap += (m.flapSpeed + s * 0.06) * dt

        for i in range(len(self.embers) - 1, -1, -1):
            e = self.embers[i]
            e.x += e.vx * dt; e.y += e.vy * dt; e.vy -= 0.02 * dt
            e.vx *= 0.97; e.life -= dts
            if e.life <= 0:
                self.embers.pop(i)

        if not self.gameOver:
            alive = sum(1 for m in self.moths if m.dying == 0)
            if alive == 0:
                self.gameOver = True

    def alive_count(self):
        return sum(1 for m in self.moths if m.dying == 0)
