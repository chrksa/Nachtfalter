"""
NACHTFALTER · render   (entspricht Abschnitt F der HTML)
====================================================================
Zeichnet die Szene mit pygame. Radiale Canvas-Gradienten -> Alpha-
Glühtexturen (source-over). Embers additiv ("lighter").

PERFORMANCE: Im Normalfall wird pro Frame nichts mehr alloziert.
- Falter: vorgedrehte Sprites (Winkel x Flügelschlag x Größe), gepickt.
- Embers/Sterne: wiederverwendete Sprite-Buckets.
- Hitze-Halos: vorab in Alpha-Stufen gerendert.
- Laternen-Glühen und Lichtkegel: einmal pro Laterne gebaut, dann nur
  noch geblittet (vorher: bildschirmgroße Surface pro Laterne/Frame).
"""
import math
import random

import numpy as np
import pygame
import pygame.gfxdraw

import assets
import config

ADD = pygame.BLEND_RGB_ADD
MUL = pygame.BLEND_RGBA_MULT

# Quantisierungs-Stufen der vorgerenderten Sprite-Buckets. Höher = weichere
# Verläufe (weniger Banding), kostet nur etwas RAM beim Start, keine Pro-Frame-CPU.
EMBER_LEVELS = 20     # vorher 12
HEAT_LEVELS = 16      # vorher 6
STAR_LEVELS = 10      # vorher 5


class Renderer:
    def __init__(self, surface, fonts, asset_dir):
        self.s = surface
        self.fonts = fonts
        self.asset_dir = asset_dir
        self.W, self.H = surface.get_size()

        # --- Bildsequenz für den Endscreen laden ---
        import os
        self.end_frames = []
        self.current_frame_time = 0.0  # Interner Timer für die Abspielgeschwindigkeit
        for i in range(60):
            filename = f"background/endscreen/Komp 1_{i:05d}.png"  # Erzeugt z.B. Komp 1_00015.png
            img_path = os.path.join(asset_dir, filename)
            try:
                raw_img = pygame.image.load(img_path).convert_alpha()
                scaled_img = pygame.transform.smoothscale(raw_img, (self.W, self.H))
                self.end_frames.append(scaled_img)
            except Exception as e:
                # Falls ein Frame fehlt, geben wir eine Warnung aus, brechen aber nicht ab
                print(f"Warnung: Konnte {filename} nicht laden: {e}")

        self.hud_visible = True
        self.rfid_btn = None        # Klick-Rechteck des Debug-Buttons (Render-Koordinaten)
        self.stars = [dict(x=random.random(), y=random.random() * 0.6,
                           s=(0.5 + random.random() * 1.1),
                           t=random.random() * 6.28) for _ in range(90)]
        self._sky_cache = None
        self._sky_size = None
        self._scaled_cache = {}   # (id(img), w, h) -> skalierte Surface

        # --- zentrale Objekt-Größen (config.SIZES) -> Radien in px ------
        S = config.SIZES
        self._glow_r = max(2, int(150 * S["lamp_glow"]))
        self._lens_r = max(2, int(13 * S["lens"]))
        self._heat_r = max(2, int(34 * S["heat_halo"]))
        self._moth_px = config.MOTH_BASE_PX * S["moth"]

        self._light_glow = assets.make_glow(self._glow_r, (251, 251, 236), 1.0)
        self._lens = assets.make_glow(self._lens_r, (255, 248, 235), 1.0, profile="lens")

        # --- vorgerenderte Sprite-Caches (keine Pro-Frame-Allokation) ---
        self._ember_sprites = self._build_embers(EMBER_LEVELS)
        self._star_sprites = {}
        self._heat_halos = [assets.make_glow(self._heat_r, (255, 150, 70),
                                             (i + 1) / HEAT_LEVELS * 0.5,
                                             profile="soft") for i in range(HEAT_LEVELS)]
        self._moth_tables = {}
        for path in config.ASSETS["moths"]:
            spr = assets.load_image(path, asset_dir, key_black=True)
            self._moth_tables[path] = assets.build_moth_table(spr, self._moth_px) if spr else None

        # --- Durchlaufendes Panorama (hat Vorrang vor der Frame-Animation) ---
        self._pano = []
        self._pano_full = None      # vorkomponiertes vollbeleuchtetes Panorama (Cache)
        for path in config.ASSETS["bg"].get("panorama", []):
            img = assets.load_image(path, asset_dir)
            if img:
                self._pano.append(img)

        self._bg_frames = []
        # Änderung: Wir laden die Animation IMMER, damit sie als Himmel hinter dem Panorama liegt,
        # und skalieren sie direkt flüssig auf die Render-Auflösung (self.W, self.H)
        if "animation" in config.ASSETS["bg"]:
            for path in config.ASSETS["bg"]["animation"]:
                img = assets.load_image(path, asset_dir)
                if img:
                    img_scaled = pygame.transform.smoothscale(img, (self.W, self.H))
                    self._bg_frames.append(img_scaled)

    def resize(self, surface):
        self.s = surface
        self.W, self.H = surface.get_size()
        self._sky_cache = None
        self._scaled_cache.clear()
        self._pano_full = None

    def img(self, rel):
        return assets.load_image(rel, self.asset_dir)

    def _scaled(self, im, w, h, flip_v=False):
        """smoothscale-Ergebnis cachen (Surfaces aus load_image leben dauerhaft)."""
        w, h = max(1, int(w)), max(1, int(h))
        key = (id(im), w, h, flip_v)
        sc = self._scaled_cache.get(key)
        if sc is None:
            sc = pygame.transform.smoothscale(im, (w, h))
            if flip_v:
                sc = pygame.transform.flip(sc, False, True)
            self._scaled_cache[key] = sc
        return sc

    # --- Sprite-Bucket-Builder ------------------------------------
    def _build_embers(self, levels):
        out = []
        for i in range(levels):
            a = (i + 1) / levels
            rad = 1.4 + a * 1.3
            d = int(rad * 2) + 2
            surf = pygame.Surface((d, d), pygame.SRCALPHA)
            pygame.draw.circle(surf, (255, int(150 + a * 60), 80, int(a * 200)),
                               (d // 2, d // 2), max(1, int(rad)))
            out.append(surf)
        return out

    # --- Antialiasing-Helfer (pygame.draw zeichnet sonst harte Kanten) ----
    def _aapoly(self, surf, col, pts):
        ipts = [(int(round(x)), int(round(y))) for x, y in pts]
        pygame.gfxdraw.filled_polygon(surf, ipts, col)
        pygame.gfxdraw.aapolygon(surf, ipts, col)

    def _aacircle(self, surf, cx, cy, r, col):
        cx, cy, r = int(round(cx)), int(round(cy)), int(round(r))
        pygame.gfxdraw.filled_circle(surf, cx, cy, r, col)
        pygame.gfxdraw.aacircle(surf, cx, cy, r, col)

    def _star_sprite(self, size, ai):
        key = (size, ai)
        spr = self._star_sprites.get(key)
        if spr is None:
            av = 0.3 + 0.4 * ai / (STAR_LEVELS - 1)
            spr = pygame.Surface((size, size), pygame.SRCALPHA)
            spr.fill((220, 228, 245, int(av * 255)))
            self._star_sprites[key] = spr
        return spr

    def _heat_halo(self, heat):
        return self._heat_halos[min(HEAT_LEVELS - 1, int(heat * HEAT_LEVELS))]

    # === Hintergrund ===============================================
    def _sky_gradient(self):
        if self._sky_cache is not None and self._sky_size == (self.W, self.H):
            return self._sky_cache
        top = np.array([10, 15, 30]); midc = np.array([12, 19, 34]); bot = np.array([14, 17, 22])
        H = self.H
        col = np.zeros((H, 3), np.float32)
        mid = int(H * 0.55)
        for i in range(H):
            if i < mid:
                f = i / max(1, mid); col[i] = top * (1 - f) + midc * f
            else:
                f = (i - mid) / max(1, H - mid); col[i] = midc * (1 - f) + bot * f
        arr = np.repeat(col[None, :, :], self.W, axis=0).astype(np.uint8)
        surf = pygame.Surface((self.W, H))
        pygame.surfarray.blit_array(surf, arr)
        self._sky_cache, self._sky_size = surf, (self.W, self.H)
        return surf

    def _hill(self, off, baseY, amp, step, col):
        pts = [(-100, self.H)]
        x = -100
        while x <= self.W + 100:
            y = baseY + math.sin((x + off) * 0.004) * amp + math.sin((x + off) * 0.013) * amp * 0.4
            pts.append((x, y)); x += step
        pts.append((self.W + 100, self.H))
        self._aapoly(self.s, col, pts)

    def _parallax(self, im, factor, hFrac, scroll):
        dh = self.H * hFrac
        aspect = (im.get_width() / im.get_height()) or 2
        dw = dh * aspect
        scaled = self._scaled(im, dw, dh)
        off = ((scroll * factor) % dw + dw) % dw
        x = -off
        while x < self.W + dw:
            self.s.blit(scaled, (x, self.H - dh)); x += dw

    def _draw_panorama(self, sim):
        """Panorama seitlich scrollen (spawnt links -> nach rechts -> nahtloser Loop).
        [0] = unbeleuchtet (Basis), [1] = beleuchtet (per RFID/bg_frame eingeblendet)."""
        s, W, H = self.s, self.W, self.H
        if self._bg_frames:
            # Holt den aktuellen Index aus sim.bg_frame (wird in main.py von 0.0 bis 19.0 gezählt)
            current_frame_idx = int(clamp(sim.bg_frame, 0, len(self._bg_frames) - 1))
            s.blit(self._bg_frames[current_frame_idx], (0, 0))
        else:
            s.blit(self._sky_gradient(), (0, 0))   # Fallback, falls die Liste leer ist

        dw = max(1, int(H * config.BG_ASPECT))
        base = self._scaled(self._pano[0], dw, H)
        lit = self._scaled(self._pano[1], dw, H) if len(self._pano) > 1 else None

        # Vollbeleuchtetes Panorama einmalig fehlerfrei vorkomponieren
        if lit is not None and self._pano_full is None:
            full = base.copy()
            full.blit(lit, (0, 0)) 
            self._pano_full = full

        # Offset aus der Sim
        off = (-sim.bg_scroll) % dw
        blend = clamp(sim.bg_frame / 19.0, 0.0, 1.0) if lit else 0.0

        # Variablen für den Zeichen-Loop vorbereiten
        layer = base
        second = None
        second_alpha = 255

        if blend >= 0.999 and self._pano_full is not None:
            layer = self._pano_full
        elif blend <= 0.001 or lit is None:
            layer = base
        else:
            # Wenn wir dazwischen sind, nutzen wir das originale 'lit',
            # merken uns aber den Alpha-Wert für den Blit-Vorgang unten!
            second = lit
            second_alpha = int(blend * 255)

        # Zeichnen der Kacheln im Loop
        x = off - dw
        while x < W:
            s.blit(layer, (x, 0))
            
            if second is not None:
                # Hier ist der Trick für moderne Pygame-Versionen:
                # Wir erstellen ein minimales, temporäres Alpha-Steuer-Surface,
                # um den Blit zu modulieren, OHNE das 'second'-Bild zu verändern.
                alpha_modifier = pygame.Surface((dw, H), pygame.SRCALPHA)
                alpha_modifier.fill((255, 255, 255, second_alpha))
                
                # Erst das beleuchtete Bild auf ein frisches Temp-Surface kopieren...
                temp_tile = second.copy()
                # ...dann die Transparenz per MULTIPLY (Multiplikation) draufrechnen
                temp_tile.blit(alpha_modifier, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
                
                # Jetzt erst auf den echten Bildschirm blitten
                s.blit(temp_tile, (x, 0))
                
            x += dw

    def background(self, sim):
        s, W, H = self.s, self.W, self.H

        if self._pano:
            self._draw_panorama(sim)
        elif self._dawn_images:
            # Falls kein Panorama da ist, spiele die Animation gestreckt ab
            current_frame_idx = int(clamp(sim.bg_frame, 0, len(self._dawn_images) - 1))
            s.blit(self._dawn_images[current_frame_idx], (0, 0))
        else:
            s.blit(self._sky_gradient(), (0, 0))

    # === Laternen ==================================================
    def _ensure_caches(self, hz):
        if hz.glow_cache is None:
            hz.glow_cache = assets.make_glow(hz.r, hz.glow, 1.0)
            hz._glow_up = assets.make_glow(hz.r, hz.glow, hz.up) if hz.up > 0.01 else None
            spread = hz.r * config.CONE["spread"]; length = hz.r * config.CONE["length"]
            w = max(2, int(spread * 2)); h = max(2, int(length))
            cone = pygame.Surface((w, h), pygame.SRCALPHA)
            for y in range(h):
                hw = (y / h) * spread
                al = int(41 * (1 - y / h))
                pygame.draw.line(cone, (*hz.glow, al), (spread - hw, y), (spread + hw, y))
            hz._cone = cone
            hz._spread = spread

    def _blit_half(self, glow, cx, cy, r, lower):
        if lower:
            self.s.blit(glow, (cx - r, cy), pygame.Rect(0, r, 2 * r, r))
        else:
            self.s.blit(glow, (cx - r, cy - r), pygame.Rect(0, 0, 2 * r, r))

    def streetlamp(self, hz, sim):
        s, H = self.s, self.H
        self._ensure_caches(hz)
        lx, ly, top, dir_ = hz.x, hz.y, hz.top, hz.armDir
        r = int(hz.r)
        lp = config.SIZES["lamp"]
        poleX = lx + dir_ * 32 * lp
        edgeY = H
        armY = ly + 14 * lp
        into_lower = True
        lamp = self.img(hz.lampSrc)

        # Lichteffekte nur bei aktivem Licht; Mast/Kopf werden immer gezeichnet
        if getattr(sim, 'rfid_light_on', True):
            if not lamp:
                s.blit(hz._cone, (lx - hz._spread, ly))
            self._blit_half(hz.glow_cache, int(lx), int(ly), r, lower=into_lower)
            if hz._glow_up is not None:
                self._blit_half(hz._glow_up, int(lx), int(ly), r, lower=not into_lower)
            s.blit(self._lens, (lx - self._lens_r, ly - self._lens_r))

        if lamp:
            dispH = 950
            dispW = dispH * ((lamp.get_width() / lamp.get_height()) or 0.3)
            scaled = self._scaled(lamp, dispW, dispH, flip_v=False)
            s.blit(scaled, (lx - dispW / 2, ly - dispH * 0.12))
        else:
            pygame.draw.line(s, (23, 27, 34), (poleX, edgeY), (poleX, armY), max(1, int(5 * lp)))
            self._quad(s, (poleX, armY), ((poleX + lx) / 2, armY), (lx, ly), (23, 27, 34), max(1, int(4 * lp)))
            hw, hh = max(2, int(34 * lp)), max(2, int(16 * lp))
            rx, ry = (hw - 2) // 2, (hh - 2) // 2
            head = pygame.Surface((hw, hh), pygame.SRCALPHA)
            pygame.gfxdraw.filled_ellipse(head, rx, ry, rx, ry, (18, 21, 28))
            pygame.gfxdraw.aaellipse(head, rx, ry, rx, ry, (18, 21, 28))
            s.blit(head, (lx - hw / 2, ly - hh / 2))

        # --- Debug-Kreise ---
        if sim.P["showField"]:
            self._dashed_circle((lx, ly), r, hz.glow)
            f = self.fonts["small"]
            self._text_center(f"{hz.name} · {hz.strength:.2f}", lx,
                              ly + 18 if top else ly - 14, hz.glow, f)
            dim = tuple(int(c * 0.65) for c in hz.glow)
            self._text_center(hz.cutName, lx, ly + 30 if top else ly - 26, dim, f)

    def _bg_light_debug(self, hz):
        """Debug-Marker für eine Hintergrund-Lichtquelle: Polygon-Umriss +
        Schwerpunkt-Fadenkreuz. Mit Taste D ein-/ausblenden."""
        col = tuple(hz.glow[:3])
        if len(hz.poly) >= 2:
            pygame.draw.aalines(self.s, col, True, hz.poly)
        x, y = int(hz.cx), int(hz.cy)
        pygame.draw.line(self.s, col, (x - 14, y), (x + 14, y), 2)
        pygame.draw.line(self.s, col, (x, y - 14), (x, y + 14), 2)

    def _quad(self, surf, p0, p1, p2, col, width):
        pts = []
        for i in range(13):
            t = i / 12
            x = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t * t * p2[0]
            y = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t * t * p2[1]
            pts.append((x, y))
        pygame.draw.lines(surf, col, False, pts, width)
        pygame.draw.aalines(surf, col, False, pts)

    def _dashed_circle(self, c, r, col, dashes=46):
        for i in range(0, dashes, 2):
            a0 = i / dashes * 6.283; a1 = (i + 1) / dashes * 6.283
            pygame.draw.aaline(self.s, col[:3],
                               (c[0] + math.cos(a0) * r, c[1] + math.sin(a0) * r),
                               (c[0] + math.cos(a1) * r, c[1] + math.sin(a1) * r))

    def _text_center(self, text, cx, y, col, font):
        surf = font.render(text, True, col[:3])
        self.s.blit(surf, (cx - surf.get_width() / 2, y - surf.get_height() / 2))

    # === Leitlicht =================================================
    def light(self, sim):
        L = sim.light
        if not L.active: return
        self.s.blit(self._light_glow, (L.x - self._glow_r, L.y - self._glow_r))

    # === Falter ====================================================
    def moth(self, m, sim):
        heat = min(1, m.exposure / sim.P["deathTime"])
        fade = (1 - m.dying) if m.dying > 0 else 1
        if fade <= 0: return
        flap = 0.32 + 0.68 * abs(math.sin(m.flap))
        ph = m.size * self._moth_px * ((1 - m.dying * 0.4) if m.dying > 0 else 1)

        td = self._moth_tables.get(m.spriteSrc)
        if td is None and m.spriteSrc and m.spriteSrc not in self._moth_tables:
            spr = assets.load_image(m.spriteSrc, self.asset_dir, key_black=True)
            td = assets.build_moth_table(spr, self._moth_px) if spr else None
            self._moth_tables[m.spriteSrc] = td

        if td:
            deg = -90 - math.degrees(math.atan2(m.vy, m.vx)) - math.degrees(m.rot)
            base = assets.pick_moth(td, deg, flap, ph)
            if m.dying == 0 and heat <= 0.02:
                self.s.blit(base, base.get_rect(center=(m.x, m.y)))
                return
            if heat > 0.05:
                halo = self._heat_halo(heat)
                self.s.blit(halo, (m.x - halo.get_width() / 2, m.y - halo.get_height() / 2))
            surf = base.copy()
            if heat > 0.02:
                k = heat * 0.7
                surf.fill((int(255 * k), int(120 * k), int(50 * k), 0), special_flags=ADD)
            if fade < 1:
                surf.fill((255, 255, 255, int(fade * 255)), special_flags=MUL)
            self.s.blit(surf, surf.get_rect(center=(m.x, m.y)))
            return

        r, g, b = m.col
        r = min(255, r * m.tone + heat * 130); g = min(255, g * m.tone + heat * 45); b = min(255, b * m.tone)
        ang = math.atan2(m.vy, m.vx) + math.pi / 2 + m.rot
        s_ = m.size * ((1 - m.dying * 0.4) if m.dying > 0 else 1)
        surf = pygame.Surface((int(s_ * 4), int(s_ * 4)), pygame.SRCALPHA)
        c = s_ * 2; wing = (int(r), int(g), int(b))
        for side in (1, -1):
            self._aapoly(surf, wing, [
                (c, c - s_ * 0.5), (c + side * s_ * 1.5 * flap, c - s_ * 0.7),
                (c + side * s_ * 1.4 * flap, c + s_ * 0.1), (c, c + s_ * 0.2)])
        body = (int(r * 0.5), int(g * 0.45), int(b * 0.4))
        pygame.gfxdraw.filled_ellipse(surf, int(c), int(c), max(1, int(s_ * 0.2)),
                                      max(1, int(s_ * 0.8)), body)
        pygame.gfxdraw.aaellipse(surf, int(c), int(c), max(1, int(s_ * 0.2)),
                                 max(1, int(s_ * 0.8)), body)
        surf.set_alpha(int(fade * 255))
        rot = pygame.transform.rotate(surf, -math.degrees(ang))
        self.s.blit(rot, rot.get_rect(center=(m.x, m.y)))

    def embers(self, sim):
        n = len(self._ember_sprites)
        for e in sim.embers:
            a = max(0.0, e.life / e.maxLife)
            spr = self._ember_sprites[min(n - 1, int(a * n))]
            self.s.blit(spr, (e.x - spr.get_width() / 2, e.y - spr.get_height() / 2),
                        special_flags=ADD)

    # === HUD / Overlay =============================================
    def hud(self, sim):
        if not self.hud_visible: return
        f = self.fonts["small"]; lab = self.fonts["label"]
        ink = (205, 198, 186); dim = (125, 119, 108); amber = (255, 202, 122)
        x, y = 16, 14
        self.s.blit(lab.render("NACHTFALTER", True, amber), (x, y)); y += 22

        # --- RFID-Tag-Status (1/0) und der daraus invertierte Lichter-Status ---
        rfid_val = "1" if getattr(sim, 'rfid_tag_on', False) else "0"
        lights_val = "AN" if getattr(sim, 'rfid_light_on', True) else "AUS"

        for k, v in (("RFID Tag", rfid_val),
                     ("Lichter", lights_val),
                     ("Schwarm", str(sim.alive_count())),
                     ("Verloren", str(sim.deadCount)),
                     ("Überlebt", f"{sim.survived:.1f} s")):
            self.s.blit(f.render(k, True, dim), (x, y))
            vs = f.render(v, True, ink); self.s.blit(vs, (x + 150 - vs.get_width(), y)); y += 16

        # --- Tracking-Status (Taste T) ----------------------------------
        tracking_on = getattr(sim, "tracking_enabled", False)
        self.s.blit(f.render("Tracking", True, dim), (x, y))
        tcol = amber if tracking_on else dim
        vs = f.render("AN" if tracking_on else "AUS", True, tcol)
        self.s.blit(vs, (x + 150 - vs.get_width(), y)); y += 16

        # --- Interaktions-Tracking: laufend (amber) bzw. letzte abgeschlossene ---
        if getattr(sim, "interacting", False):
            self.s.blit(f.render("Interaktion", True, amber), (x, y))
            vs = f.render(f"{sim.interaction_time:.1f} s", True, amber)
            self.s.blit(vs, (x + 150 - vs.get_width(), y)); y += 16
            self.s.blit(f.render("· Falter †", True, dim), (x, y))
            vs = f.render(str(sim.interaction_deaths), True, ink)
            self.s.blit(vs, (x + 150 - vs.get_width(), y)); y += 16
        elif getattr(sim, "interaction_count", 0) > 0:
            self.s.blit(f.render("Letzte Int.", True, dim), (x, y))
            vs = f.render(f"{sim.last_interaction_time:.1f} s", True, ink)
            self.s.blit(vs, (x + 150 - vs.get_width(), y)); y += 16
            self.s.blit(f.render("· Falter †", True, dim), (x, y))
            vs = f.render(str(sim.last_interaction_deaths), True, ink)
            self.s.blit(vs, (x + 150 - vs.get_width(), y)); y += 16

        self.s.blit(f.render("H Panel · T Tracking · R Reset · F Vollbild · D Text · ESC", True, dim), (x, y + 4))

    def rfid_debug_button(self, sim):
        """Klickbarer Debug-Button oben rechts: schaltet den simulierten RFID-Status.
        INVERTIERT: RFID AN -> Lichter AUS, RFID AUS -> Lichter AN.
        Grün = Lichter AN, Rot = Lichter AUS."""
        tag = getattr(sim, "rfid_tag_on", False)
        lights = getattr(sim, "rfid_light_on", True)
        hw = getattr(sim, "rfid_connected", False)
        f = self.fonts["small"]
        pad = 10
        src = "HW" if hw else "Debug"
        label = f"RFID {'AN' if tag else 'AUS'} · Lichter {'AN' if lights else 'AUS'} [{src}]"
        txt = f.render(label, True, (15, 15, 18))
        w = txt.get_width() + pad * 2
        h = txt.get_height() + pad
        rect = pygame.Rect(self.W - w - 16, 14, w, h)
        col = (120, 210, 130) if lights else (220, 110, 110)
        pygame.draw.rect(self.s, col, rect, border_radius=6)
        pygame.draw.rect(self.s, (15, 15, 18), rect, width=2, border_radius=6)
        self.s.blit(txt, (rect.x + pad, rect.y + pad // 2))
        self.rfid_btn = rect
    
    # --- Zurücksetzen der end game Animation ---
    def reset_animation(self):
        self.current_frame_time = 0.0

    def game_over(self, sim):
        W, H = self.W, self.H
        
        # 1. Hintergrund abdunkeln
        veil = pygame.Surface((W, H), pygame.SRCALPHA)
        veil.fill((5, 6, 10, 220)) 
        self.s.blit(veil, (0, 0))
        
        if self.end_frames:
            frame_fps = 15.0 
            self.current_frame_time += sim.dts
            total_frames = len(self.end_frames)
            
            # Ein kompletter Hin- und Rückweg hat (total_frames * 2 - 2) Schritte
            loop_length = (total_frames * 2) - 2
            raw_idx = int(self.current_frame_time * frame_fps) % loop_length
            
            # Wenn wir in der zweiten Hälfte sind, laufen wir rückwärts
            if raw_idx >= total_frames:
                frame_idx = loop_length - raw_idx
            else:
                frame_idx = raw_idx
            
            # Aktuellen Frame auf den Bildschirm blitten
            self.s.blit(self.end_frames[frame_idx], (0, 0))

    # === Frame =====================================================
    def frame(self, sim):
        self.background(sim)

        if config.USE_BG_LIGHTS:
            # Lichtquellen kommen aus dem Hintergrundbild -> keine eigenen Laternen
            # zeichnen. Nur optionales Debug-Overlay zum Ausrichten (Taste D).
            if sim.P["showField"]:
                for hz in sim.hazards:
                    self._bg_light_debug(hz)
        else:
            for hz in sim.hazards:
                self.streetlamp(hz, sim)

        self.light(sim)
        for m in sim.moths:
            self.moth(m, sim)
        self.embers(sim)
        self.hud(sim)
        self.rfid_debug_button(sim)
        if sim.gameOver:
            self.game_over(sim)


# Die Hilfsfunktion gehört ganz nach unten an den Rand ohne Einrückung:
def clamp(n, minn, maxn):
    return max(min(maxn, n), minn)
