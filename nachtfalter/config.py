
SCROLL_DIR = 0          # -1 - welt nach links

# Scroll-Tempo des Panorama-Hintergrunds in Render-Pixeln/Sekunde (langsam = klein).
BG_SCROLL_SPEED = 30

# Seitenverhältnis des Panorama-Bilds (Breite/Höhe). Muss zum PNG passen,
# damit die Lichtquellen-Attraktoren exakt auf den gemalten Kegeln sitzen.
BG_ASPECT = 24000 / 4500

# True: KEINE Laternen mehr spawnen. Stattdessen werden die im Hintergrund
# gemalten Lichtquellen (BG_LIGHTS) zu den Anziehungs-/Tötungspunkten und
# scrollen mit dem Panorama mit. False: altes Laternen-Spawn-System.
USE_BG_LIGHTS = True

# Randbereich um jedes Polygon (Faktor), in dem Falter noch sanft angezogen
# werden, bevor sie ins Polygon geraten und gefangen werden.
BG_LURE_MARGIN = 1.6

# Lichtquellen im Panorama als Freiform-Polygone (mit dem Editor bearbeitbar: Taste E).
#   poly = Liste (u, v) normalisiert: u über Bildbreite (0=links..1=rechts),
#          v über Bildhöhe (0=oben..1=unten). Reihenfolge = Umriss der Form.
#   strength = Anziehungs-/Tötungsstärke
#   col = Farbe im Debug-/Editor-Overlay
BG_LIGHTS = [
    dict(poly=[(0.1329,0.3353), (0.1487,0.5098), (0.0995,0.4258), (0.0851,0.3402), (0.084,0.2359), (0.1006,0.168), (0.1194,0.1621), (0.1331,0.2369)], strength=1.95, col=(214, 230, 255)),
    dict(poly=[(0.295,0.2128), (0.2749,0.3199), (0.2465,0.3199), (0.2264,0.2128), (0.2264,0.0612), (0.2465,0.0), (0.2749,0.0), (0.295,0.0612)], strength=0.5, col=(255, 220, 140)),
    dict(poly=[(0.3671,0.0513), (0.3652,0.0616), (0.3624,0.0616), (0.3605,0.0513), (0.3605,0.0367), (0.3624,0.0264), (0.3652,0.0264), (0.3671,0.0367)], strength=0.5, col=(200, 210, 220)),
    dict(poly=[(0.4639,0.5502), (0.4387,0.6122), (0.4158,0.565), (0.4141,0.4469), (0.4148,0.4114), (0.4148,0.3426), (0.4232,0.2854), (0.4422,0.2707), (0.4614,0.3248), (0.4702,0.4232)], strength=0.5, col=(170, 200, 255)),
    dict(poly=[(0.4939,0.8809), (0.4847,0.878), (0.4784,0.8726), (0.471,0.878), (0.4671,0.8386), (0.4747,0.81), (0.4824,0.8051), (0.493,0.8219)], strength=0.5, col=(170, 200, 255)),
    dict(poly=[(0.5626,0.2263), (0.5681,0.6811), (0.5397,0.6693), (0.5227,0.3435), (0.5194,0.2234), (0.522,0.048), (0.5413,0.123), (0.5626,0.0997)], strength=0.5, col=(255, 170, 90)),
    dict(poly=[(0.6709,0.1856), (0.6498,0.2982), (0.62,0.2982), (0.5989,0.1856), (0.5989,0.0264), (0.62,0.0), (0.6498,0.0), (0.6709,0.0264)], strength=0.5, col=(255, 220, 140)),
    dict(poly=[(0.7042,0.256), (0.7005,0.2757), (0.6953,0.2757), (0.6916,0.256), (0.6916,0.228), (0.6953,0.2083), (0.7005,0.2083), (0.7042,0.228)], strength=0.5, col=(170, 200, 255)),
    dict(poly=[(0.8293,0.2136), (0.8084,0.3249), (0.779,0.3249), (0.7581,0.2136), (0.7581,0.0564), (0.779,0.0), (0.8084,0.0), (0.8293,0.0564)], strength=0.5, col=(200, 210, 220)),
    dict(poly=[(0.9115,0.3432), (0.9035,0.386), (0.8921,0.386), (0.8841,0.3432), (0.8841,0.2828), (0.8921,0.24), (0.9035,0.24), (0.9115,0.2828)], strength=0.5, col=(255, 180, 100)),
    dict(poly=[(0.3556,0.06), (0.3709,0.0797), (0.3665,0.748), (0.3196,0.7037)], strength=0.5, col=(255, 210, 120)),
]

PARAMS = dict(
    count=30,            # Schwarmgröße            (10 .. 180)
    follow=0.40,         # Folgekraft zum Leitlicht
    cohesion=0.00005,      # Zusammenhalt vorher 0.004
    hazardR=240,         # Basis-Radius einer Laterne in px
    deathTime=5,       # Verweildauer bis Tod in s
    speed=5,            # Scroll-Tempo px/s
    spawn=3,           # Laternen-Spawnrate in s
    showField=True,      # Gefahr-Info (Radius + Beschriftung) zeichnen
)

# Wenn True: Laternen ziehen Falter nur im (nach unten gerichteten) Licht-
# kegel an statt im vollen Kreis. Wie weit der Kegel ist, ergibt sich aus
# dem Cutoff-Typ (up): up=0 (Full cut-off) = enger Kegel, up=1 (No cut-off)
# = voller Kreis/Radius. Dazwischen wird linear interpoliert.
CONE_LURE = True

# Form des Lichtkegels, jeweils als Faktor des Laternen-Radius hz.r:
#   length = Länge (Reichweite) des Kegels nach unten
#   spread = halbe Breite an der Kegel-Basis
# Das Verhältnis spread/length bestimmt zugleich den Öffnungswinkel der
# Anziehungs-Zone (siehe CONE_LURE) -> Optik und Wirkung bleiben gekoppelt.
CONE = dict(
    length=1.15,
    spread=0.55,
)

LAMP_TYPES = [
    dict(name="Niederdruck-Natrium",    glow=(255, 138, 30),  strength=0.08),
    dict(name="Warmweiße LED 2700K",    glow=(255, 217, 168), strength=0.18),
    dict(name="Neutralweiße LED 4000K", glow=(243, 239, 224), strength=0.32),
    dict(name="Hochdruck-Natrium",      glow=(255, 178, 77),  strength=0.48),
    dict(name="Kaltweiße LED 5000K",    glow=(214, 230, 255), strength=0.58),
    dict(name="Quecksilberdampf/UV",    glow=(188, 210, 255), strength=1.00),
]

CUTOFF_TYPES = [
    dict(name="No cut-off",   up=1.00, lure=1.35),
    dict(name="Semi cut-off", up=0.55, lure=1.12),
    dict(name="Cut-off",      up=0.18, lure=0.88),
    dict(name="Full cut-off", up=0.00, lure=0.62),
]
# Falter-Körperfarben (Fallback)
PALETTE = [
    (107, 93, 79), (122, 106, 88), (92, 81, 69), (133, 118, 98),
    (79, 74, 66), (115, 99, 85), (146, 133, 113), (64, 59, 52), (90, 79, 67),
]


SIZES = dict(
    moth=0.55,        # Nachtfalter
    lamp=12.0,        # physischer Laternen-Körper: Mast, Arm, Lampenkopf
    lamp_glow=2.0,   # Lichthof des LEITLICHTS (Cursor-Punkt)
    lens=2.0,        # heller Linsen-Kern (Leitlicht UND Laterne)
    moon=0.0,        # Mond + Mond-Halo (ohne Mond-PNG)
    heat_halo=2.0,   # Hitze-Halo um überhitzte Falter
)
# Hinweis: Der Lichthof + Lichtkegel der Straßenlaterne und damit auch
# der Falter-Anziehungsradius wird über PARAMS["hazardR"] gesteuert.
# Falter-Basisgröße in px je m.size-Einheit (vor SIZES["moth"]).
# m.size kommt aus sim.py (rand(7, 12)); diese Spanne muss zur
# Sprite-Tabelle in assets.build_moth_table passen.
MOTH_BASE_PX = 10

ASSET_DIR = "assets/"
ASSETS = dict(
    moths=[f"schmetterlinge/edge/{i:02d}.png" for i in range(1, 12)],
    # moths=[f"schmetterlinge/neu.png"],
    # moths=["schmetterlinge/Acherontia_atropos.png", "schmetterlinge/Euplagia-quadripunctaria.png", "schmetterlinge/oleanderschwärmer.png", "schmetterlinge/Ourapteryx-sambucaria.png"],
    # moths=[f"schmetterlinge/{char}.png" for char in ["a", "b", "c", "d", "e", "f"]],
    lamps=["laternen/04.png", "laternen/Laterne-Schirm.png", "laternen/IMG_2960.png"],            # z.B. ["laternen/laterne.png"]
    bg=dict(
        # Durchlaufendes Panorama: [0] = unbeleuchtet, [1] = beleuchtet (Lichtkegel).
        # Wird seitlich gescrollt und nahtlos geloopt. Leere Liste -> wieder die
        # 20-Frame-Animation (animation) als Fallback.
        panorama=["background/04_Lightmockup_noBG_OFF.png", "background/04_Lightmockup_noBG_ON.png"],
        animation=[f"background/dawn/{i:04d}.png" for i in range(1, 21)],
        sky="",   # Vollbild-Himmel ohne Parallax
        show_moon=False,                  # False = gar kein Mond (auch kein Default-Mond)
        moon="",                          # optional moon
        far="",                           # Parallax hinten (langsam)
        mid="",                           # Parallax mitte
        near="",                          # Parallax vorne (schnell)
    ),
)

WINDOW = dict(
    width=1920,          # Startgröße im Fenstermodus
    height=1080,
    fullscreen=False,    # True = startet im Vollbild (für den Beamer)
    fps=60,
    # Interne Render-Auflösung. Bei Fill-Rate-Problemen auf dem WUXGA-
    # Beamer (1920x1200) hier z.B. 0.75 setzen -> intern kleiner rendern,
    # dann hochskalieren. 1.0 = native Auflösung. >1.0 = Supersampling
    # (intern größer rendern, schärfer, aber höhere Last).
    render_scale=2.0,
    caption="Nachtfalter · Straßenlaternen",
)

ESP32 = dict(
    enabled=False,       # Haptik wird nicht mehr verwendet
    port=None,           # z.B. "/dev/ttyUSB0", None = automatisch suchen
    baud=115200,
)

ARDUINO = dict(
    enabled=True,
    port=None,           # None = automatisch suchen (Linux: /dev/ttyACM* oder /dev/ttyUSB*)
    baud=9600,
)

MOON = dict(
    enabled=True,
    url="ws://127.0.0.1:8765",
    flipX=True,
    flipY=False,
    smooth=0.5,         # Trägheit pro Frame (0=träge/sehr weich .. 1=hart/sofort)
    maxJump=0.5,         # Ausreißer-Schwelle (normalisiert 0..1): groesserer Sprung -> erst bestaetigen
    confirm=2,           # so viele Messages muss ein Sprung halten, sonst verworfen
)

SOUNDS = dict(
    bg_loop="assets/sounds/01.wav",
    focus_in="assets/sounds/02.wav",
    active_loop="assets/sounds/03.wav",
    focus_out="assets/sounds/04.wav",
)
