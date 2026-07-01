"""
NACHTFALTER · assets   (entspricht Abschnitt B der HTML)
====================================================================
Lädt Bilder aus ASSET_DIR und erzeugt die radialen Glühtexturen, mit
denen Canvas-`createRadialGradient` ersetzt wird. WICHTIG: Die Glüh-
texturen tragen den Lichtabfall im ALPHA-Kanal und werden NORMAL
überblendet (source-over) — genau wie im Canvas. Nur die Embers werden
additiv geblittet ("lighter").

Schwarz-Keying: Falter-PNGs ohne Alphakanal (schwarzer Hintergrund)
bekommen über die Helligkeit eine weiche Transparenz, damit kein
schwarzer Kasten entsteht. Sauberste Lösung bleibt ein echtes
transparentes PNG.
"""
import os
import numpy as np
import pygame

_img_cache = {}

# Schwarz-Keying-Schwellen (Helligkeit 0..255): darunter transparent,
# dazwischen weicher Übergang, darüber voll deckend.
KEY_LO, KEY_HI = 16, 46


def load_image(rel_path, asset_dir, key_black=False):
    if not rel_path:
        return None
    cache_key = (rel_path, key_black)
    if cache_key in _img_cache:
        return _img_cache[cache_key]
    full = os.path.join(asset_dir, rel_path)
    surf = None
    try:
        surf = pygame.image.load(full)
        has_alpha = bool(surf.get_flags() & pygame.SRCALPHA) and surf.get_bitsize() == 32
        surf = surf.convert_alpha()
        if key_black and not has_alpha:
            surf = _key_black(surf)
    except Exception as e:
        print(f"[assets] nicht geladen: {full}  ({e})")
    _img_cache[cache_key] = surf
    return surf


def _key_black(surf):
    """Erzeugt Alpha aus der Helligkeit: schwarzer Hintergrund -> transparent."""
    rgb = pygame.surfarray.array3d(surf).astype(np.float32)
    lum = 0.30 * rgb[:, :, 0] + 0.59 * rgb[:, :, 1] + 0.11 * rgb[:, :, 2]
    a = np.clip((lum - KEY_LO) / (KEY_HI - KEY_LO), 0.0, 1.0)
    out = surf.copy()
    alpha = pygame.surfarray.pixels_alpha(out)
    alpha[:] = (a * 255).astype(np.uint8)
    del alpha
    return out


def _radial_alpha(size, stops):
    """Glühtextur: RGB weiß, Lichtabfall im ALPHA-Kanal (für source-over)."""
    half = size / 2.0
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    r = np.clip(np.sqrt((xx - half) ** 2 + (yy - half) ** 2) / half, 0.0, 1.0)
    val = np.interp(r, [s[0] for s in stops], [s[1] for s in stops])
    surf = pygame.Surface((size, size), pygame.SRCALPHA)
    rgb = pygame.surfarray.pixels3d(surf); rgb[:] = 255; del rgb
    alpha = pygame.surfarray.pixels_alpha(surf)
    alpha[:] = (val * 255).astype(np.uint8).T
    del alpha
    return surf


_GLOW_SIZE = 1024
_PROFILES = {
    "lamp": [(0.0, 0.40), (0.65, 0.10), (1.0, 0.0)],
    "lens": [(0.0, 0.95), (0.35, 0.85), (1.0, 0.0)],
    "soft": [(0.0, 1.00), (1.0, 0.0)],
}
_base_glow = {}


def glow_base(profile="lamp"):
    if profile not in _base_glow:
        _base_glow[profile] = _radial_alpha(_GLOW_SIZE, _PROFILES[profile])
    return _base_glow[profile]


def make_glow(radius, color, alpha=1.0, profile="lamp"):
    """Eingefärbte Glühtextur (2*radius) zum NORMALEN Blitten (source-over)."""
    radius = max(2, int(radius))
    g = pygame.transform.smoothscale(glow_base(profile), (radius * 2, radius * 2))
    g.fill((color[0], color[1], color[2], int(max(0, min(1, alpha)) * 255)),
           special_flags=pygame.BLEND_RGBA_MULT)
    return g


def init_fonts():
    pygame.font.init()
    name = pygame.font.match_font("courier,couriernew,dejavusansmono,monospace")
    return dict(
        small=pygame.font.Font(name, 22),
        label=pygame.font.Font(name, 26),
        big=pygame.font.Font(name, 52),
    )


# ====================================================================
# Falter-Sprite-Cache: einmal beim Laden vorrendern (Winkel x Flügel-
# schlag x Größe), zur Laufzeit nur noch den nächstgelegenen blitten,
# statt jeden Frame smoothscale+rotate aufzurufen.
# ====================================================================
MOTH_ANGLES = 36      # 10deg-Schritte (vorher 24 = 15deg, sichtbar ruckeliger)
MOTH_FLAPS = 6        # Flügelschlag-Stufen
MOTH_SIZES = 2     # Größen-Stufen


def build_moth_table(sprite, px_per_unit=100):
    aspect = (sprite.get_width() / sprite.get_height()) or 1
    # 7 und 12 entsprechen der m.size-Spanne aus sim.py (rand(7, 12)).
    ph_min, ph_max = 7 * px_per_unit, 12 * px_per_unit
    sizes = [ph_min + (ph_max - ph_min) * i / (MOTH_SIZES - 1) for i in range(MOTH_SIZES)]
    flaps = [0.32 + (1.0 - 0.32) * i / (MOTH_FLAPS - 1) for i in range(MOTH_FLAPS)]
    step = 360.0 / MOTH_ANGLES
    table = []
    for ph in sizes:
        flap_rows = []
        for f in flaps:
            pw = max(1, ph * aspect * f)
            scaled = pygame.transform.smoothscale(sprite, (int(pw), int(ph)))
            flap_rows.append([pygame.transform.rotate(scaled, i * step)
                              for i in range(MOTH_ANGLES)])
        table.append(flap_rows)
    return dict(table=table, sizes=sizes, flaps=flaps, step=step)


def pick_moth(td, deg, flap, ph):
    sizes, flaps = td["sizes"], td["flaps"]
    si = min(range(len(sizes)), key=lambda i: abs(sizes[i] - ph))
    fi = min(range(len(flaps)), key=lambda i: abs(flaps[i] - flap))
    ai = int(round((deg % 360) / td["step"])) % MOTH_ANGLES
    return td["table"][si][fi][ai]
