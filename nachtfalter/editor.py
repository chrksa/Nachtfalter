"""
NACHTFALTER · editor
====================================================================
Debug-Editor für die Hintergrund-Lichtquellen (config.BG_LIGHTS als
Freiform-Polygone). Einschalten mit Taste E. Dann:

  A / D         Panorama nach links / rechts schieben (Auto-Scroll ist aus)
  Linksklick    Eckpunkt ziehen · auf Kante = Eckpunkt einfügen · in Fläche = ganze Quelle ziehen
  Rechtsklick   Eckpunkt löschen (bei 3 Ecken: ganze Quelle)
  N             neue Lichtquelle an der Maus
  Entf          ausgewählte Lichtquelle löschen
  S             config.BG_LIGHTS in config.py speichern (überschreibt)

Alle Koordinaten in config.BG_LIGHTS sind normalisiert: u über die
Panorama-Breite (0..1), v über die Höhe (0..1).
"""
import math
import os

import pygame

import config
from sim import point_in_poly

VERT_HIT = 12       # Trefferradius für Vertices (Render-Pixel)
EDGE_HIT = 10       # Trefferradius für Kanten
SCROLL_SPEED = 800  # Render-Pixel/Sekunde beim manuellen Scrollen (A/D)
STRENGTH_STEP = 0.05
STRENGTH_MAX = 2.0
COLORS = [(255, 170, 90), (255, 210, 120), (255, 220, 140), (243, 239, 224),
          (214, 230, 255), (170, 200, 255), (188, 210, 255)]


def _seg_dist(px, py, x1, y1, x2, y2):
    dx, dy = x2 - x1, y2 - y1
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / L2))
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))


class Editor:
    def __init__(self, sim):
        self.sim = sim
        self.active = False
        self.sel = None            # Index der ausgewählten Lichtquelle
        self.drag = None           # ("vert", vi) | ("poly", None)
        self._last_uv = None
        self.message = ""

    def toggle(self):
        self.active = not self.active
        self.sim.scroll_frozen = self.active
        self.sim.editing = self.active     # im Editor können Falter nicht sterben
        self.drag = None
        self.message = ""

    # --- Koordinaten-Umrechnung Panorama <-> Bildschirm -------------
    def _dims(self):
        H = self.sim.H
        dw = max(1, int(H * config.BG_ASPECT))
        off = self.sim.bg_scroll % dw
        return dw, off, H

    def screen_to_uv(self, x, y):
        dw, off, H = self._dims()
        return ((x - off) / dw) % 1.0, y / H

    def _nearest_x(self, u, mouse_x):
        """Screen-x der u-Koordinate in der zur Maus nächsten Panorama-Kachel."""
        dw, off, _ = self._dims()
        x0 = u * dw - off - dw
        return x0 + round((mouse_x - x0) / dw) * dw

    # --- Treffer-Suche ---------------------------------------------
    def _pick_vertex(self, mx, my):
        H = self.sim.H
        best, bestd = None, VERT_HIT
        for li, L in enumerate(config.BG_LIGHTS):
            for vi, (u, v) in enumerate(L["poly"]):
                d = math.hypot(self._nearest_x(u, mx) - mx, v * H - my)
                if d < bestd:
                    best, bestd = (li, vi), d
        return best

    def _pick_edge(self, mx, my):
        H = self.sim.H
        best, bestd = None, EDGE_HIT
        for li, L in enumerate(config.BG_LIGHTS):
            poly = L["poly"]; n = len(poly)
            for i in range(n):
                u1, v1 = poly[i]; u2, v2 = poly[(i + 1) % n]
                d = _seg_dist(mx, my, self._nearest_x(u1, mx), v1 * H,
                              self._nearest_x(u2, mx), v2 * H)
                if d < bestd:
                    best, bestd = (li, i), d
        return best

    def _pick_poly(self, mx, my):
        H = self.sim.H
        for li, L in enumerate(config.BG_LIGHTS):
            pts = [(self._nearest_x(u, mx), v * H) for (u, v) in L["poly"]]
            if point_in_poly(mx, my, pts):
                return li
        return None

    # --- Maus-Interaktion (Render-Koordinaten) ---------------------
    def on_mouse_down(self, mx, my, button):
        if button == 1:
            v = self._pick_vertex(mx, my)
            if v:
                self.sel, self.drag = v[0], ("vert", v[1])
                return
            e = self._pick_edge(mx, my)
            if e:
                li, i = e
                u, vv = self.screen_to_uv(mx, my)
                config.BG_LIGHTS[li]["poly"].insert(i + 1, (round(u, 4), round(vv, 4)))
                self.sel, self.drag = li, ("vert", i + 1)
                return
            p = self._pick_poly(mx, my)
            if p is not None:
                self.sel, self.drag = p, ("poly", None)
                self._last_uv = self.screen_to_uv(mx, my)
                return
            self.sel = None
        elif button == 3:
            self.delete_vertex_at(mx, my)

    def delete_vertex_at(self, mx, my):
        """Eckpunkt unter dem Cursor löschen (Rechtsklick oder Taste X)."""
        v = self._pick_vertex(mx, my)
        if not v:
            return
        li, vi = v
        poly = config.BG_LIGHTS[li]["poly"]
        if len(poly) > 3:
            poly.pop(vi)
        else:
            config.BG_LIGHTS.pop(li)
            self.sel = None

    def on_mouse_move(self, mx, my):
        if not self.drag or self.sel is None:
            return
        u, v = self.screen_to_uv(mx, my)
        kind, idx = self.drag
        light = config.BG_LIGHTS[self.sel]
        if kind == "vert":
            light["poly"][idx] = (round(u, 4), round(v, 4))
        elif kind == "poly":
            lu, lv = self._last_uv; du, dv = u - lu, v - lv
            light["poly"] = [(round(pu + du, 4), round(pv + dv, 4))
                             for pu, pv in light["poly"]]
            self._last_uv = (u, v)

    def on_mouse_up(self):
        self.drag = None

    # --- Tasten -----------------------------------------------------
    def new_light(self, mx, my):
        u, v = self.screen_to_uv(mx, my)
        du, dv = 0.02, 0.05
        poly = [(round(u - du, 4), round(v - dv, 4)), (round(u + du, 4), round(v - dv, 4)),
                (round(u + du, 4), round(v + dv, 4)), (round(u - du, 4), round(v + dv, 4))]
        config.BG_LIGHTS.append(dict(poly=poly, strength=0.5, col=(255, 210, 120)))
        self.sel = len(config.BG_LIGHTS) - 1

    def delete_selected(self):
        if self.sel is not None and 0 <= self.sel < len(config.BG_LIGHTS):
            config.BG_LIGHTS.pop(self.sel)
            self.sel = None
            self.drag = None

    def _selected(self):
        if self.sel is not None and 0 <= self.sel < len(config.BG_LIGHTS):
            return config.BG_LIGHTS[self.sel]
        return None

    def adjust_strength(self, delta):
        L = self._selected()
        if L is None:
            return
        s = max(0.0, min(STRENGTH_MAX, L.get("strength", 0.5) + delta))
        L["strength"] = round(s, 3)
        self.message = f"Intensität: {L['strength']:.2f}"

    def on_wheel(self, dir):
        self.adjust_strength(dir * STRENGTH_STEP)

    def cycle_color(self):
        L = self._selected()
        if L is None:
            return
        cur = tuple(int(c) for c in L.get("col", COLORS[0]))
        i = COLORS.index(cur) if cur in COLORS else -1
        L["col"] = COLORS[(i + 1) % len(COLORS)]
        self.message = f"Farbe: {L['col']}"

    def handle_held_keys(self, keys, dts):
        if not self.active:
            return
        step = SCROLL_SPEED * dts
        if keys[pygame.K_a]:
            self.sim.bg_scroll -= step
        if keys[pygame.K_d]:
            self.sim.bg_scroll += step

    # --- Speichern in config.py ------------------------------------
    def save(self):
        path = os.path.join(os.path.dirname(__file__), "config.py")
        try:
            lines = open(path, encoding="utf-8").read().split("\n")
        except OSError as e:
            self.message = f"Speichern fehlgeschlagen: {e}"
            return
        start = next((i for i, ln in enumerate(lines) if ln.startswith("BG_LIGHTS = [")), None)
        if start is None:
            self.message = "BG_LIGHTS in config.py nicht gefunden"
            return
        end = next((i for i in range(start + 1, len(lines)) if lines[i].strip() == "]"), None)
        if end is None:
            self.message = "BG_LIGHTS-Ende in config.py nicht gefunden"
            return
        block = ["BG_LIGHTS = ["]
        for L in config.BG_LIGHTS:
            ps = ", ".join(f"({round(u, 4)},{round(v, 4)})" for u, v in L["poly"])
            col = tuple(int(c) for c in L.get("col", (255, 200, 120)))
            block.append(f"    dict(poly=[{ps}], strength={L.get('strength', 0.5)}, col={col}),")
        block.append("]")
        lines[start:end + 1] = block
        try:
            open(path, "w", encoding="utf-8").write("\n".join(lines))
            self.message = f"gespeichert: {len(config.BG_LIGHTS)} Lichtquellen -> config.py"
        except OSError as e:
            self.message = f"Speichern fehlgeschlagen: {e}"

    # --- Zeichnen ---------------------------------------------------
    def _screen_polys(self, uvpoly):
        dw, off, H = self._dims()
        W = self.sim.W; base_x = - off - dw
        out = []
        for k in range(int(W / dw) + 2):
            tile_x = base_x + k * dw
            pts = [(tile_x + u * dw, v * H) for u, v in uvpoly]
            cx = sum(p[0] for p in pts) / len(pts)
            if -dw < cx < W + dw:
                out.append(pts)
        return out

    def draw(self, surface, fonts):
        if not self.active:
            return
        f = fonts["small"]
        for li, L in enumerate(config.BG_LIGHTS):
            col = tuple(int(c) for c in L.get("col", (255, 210, 120)))[:3]
            selected = (li == self.sel)
            outline = (255, 255, 255) if selected else col
            for pts in self._screen_polys(L["poly"]):
                if len(pts) >= 2:
                    pygame.draw.aalines(surface, outline, True, pts)
                for (vx, vy) in pts:
                    rad = 6 if selected else 4
                    pygame.draw.circle(surface, outline, (int(vx), int(vy)), rad)
                    pygame.draw.circle(surface, (20, 20, 24), (int(vx), int(vy)), rad, 1)
                # Farb-Swatch + Intensität am Schwerpunkt
                cx = int(sum(p[0] for p in pts) / len(pts))
                cy = int(sum(p[1] for p in pts) / len(pts))
                pygame.draw.circle(surface, col, (cx, cy), 7)
                pygame.draw.circle(surface, (20, 20, 24), (cx, cy), 7, 1)
                if selected:
                    lab = f.render(f"I {L.get('strength', 0.5):.2f}", True, (255, 255, 255))
                    surface.blit(lab, (cx + 12, cy - 10))
        self._draw_hints(surface, fonts)

    def _draw_hints(self, surface, fonts):
        f = fonts["small"]
        lines = [
            "EDITOR (E aus)  ·  A/D scrollen  ·  N neu  ·  Entf löschen  ·  S speichern",
            "Linksklick: Eckpunkt ziehen / Kante = einfügen / Fläche = verschieben  ·  Rechtsklick: Eckpunkt löschen",
            "Mausrad / +- / Pfeil hoch-runter : Intensität  ·  C : Farbe  ·  X : Eckpunkt am Cursor löschen",
        ]
        if self.message:
            lines.append(self.message)
        y = surface.get_height() - 18 * len(lines) - 12
        for ln in lines:
            surf = f.render(ln, True, (255, 235, 150))
            surface.blit(surf, (16, y)); y += 18
