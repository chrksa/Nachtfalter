
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
    dict(poly=[(0.1319,0.326), (0.1245,0.4138), (0.0985,0.4165), (0.0841,0.3309), (0.083,0.2266), (0.0996,0.1587), (0.1184,0.1528), (0.1321,0.2276)], strength=0.4, col=(214, 230, 255)),
    dict(poly=[(0.8763,0.3759), (0.8715,0.3921), (0.865,0.3933), (0.8627,0.3672), (0.862,0.335), (0.8656,0.3164), (0.8738,0.3176), (0.8781,0.3424)], strength=0.4, col=(255, 220, 140)),
    dict(poly=[(0.4639,0.5502), (0.4387,0.6122), (0.4158,0.565), (0.4141,0.4469), (0.4148,0.4114), (0.4148,0.3426), (0.4232,0.2854), (0.4422,0.2707), (0.4614,0.3248), (0.4702,0.4232)], strength=1.0, col=(170, 200, 255)),
    dict(poly=[(0.4939,0.8809), (0.4847,0.878), (0.4784,0.8726), (0.471,0.878), (0.4671,0.8386), (0.4747,0.81), (0.4824,0.8051), (0.493,0.8219)], strength=1.0, col=(170, 200, 255)),
    dict(poly=[(0.5533,0.4107), (0.5681,0.6439), (0.5248,0.6725), (0.5227,0.3435), (0.5226,0.2395), (0.5215,0.139), (0.5413,0.123), (0.545,0.2146)], strength=0.4, col=(255, 170, 90)),
    dict(poly=[(0.6208,0.8933), (0.6171,0.913), (0.6119,0.913), (0.6082,0.8933), (0.6082,0.8653), (0.6118,0.8635), (0.6165,0.861), (0.6208,0.8653)], strength=0.4, col=(170, 200, 255)),
    dict(poly=[(0.914,0.3412), (0.9134,0.3685), (0.9029,0.3648), (0.9015,0.3437), (0.9009,0.3114), (0.9043,0.2878), (0.9121,0.2891), (0.9157,0.304)], strength=0.4, col=(255, 180, 100)),
    dict(poly=[(0.17,0.5707), (0.249,0.567), (0.2488,0.6526), (0.1689,0.6638)], strength=0.4, col=(255, 210, 120)),
    dict(poly=[(0.2644,0.2753), (0.2732,0.2728), (0.2782,0.3249), (0.2815,0.3584), (0.262,0.3747)], strength=1.0, col=(255, 210, 120)),
    dict(poly=[(0.2815,0.1849), (0.3043,0.1055), (0.307,0.2618), (0.2831,0.2208)], strength=1.0, col=(255, 210, 120)),
    dict(poly=[(0.5223,0.2208), (0.6376,0.1051), (0.6384,0.129), (0.5267,0.4467)], strength=1.0, col=(255, 210, 120)),
    dict(poly=[(0.6282,0.2779), (0.6913,0.2481), (0.6923,0.3399), (0.6325,0.4528)], strength=1.0, col=(255, 210, 120)),
    dict(poly=[(0.7032,0.2533), (0.7514,0.268), (0.7568,0.4094), (0.7027,0.34)], strength=1.0, col=(255, 210, 120)),
    dict(poly=[(0.2312,0.1377), (0.2558,0.1757), (0.256,0.2345), (0.2363,0.2779)], strength=1.0, col=(255, 210, 120)),
    dict(poly=[(0.7553,0.0769), (0.7756,0.1097), (0.7754,0.1551), (0.7552,0.1923)], strength=0.6, col=(255, 210, 120)),
    dict(poly=[(0.8011,0.1322), (0.8234,0.0893), (0.823,0.2208), (0.802,0.1675)], strength=0.6, col=(255, 210, 120)),
    dict(poly=[(0.3573,0.0558), (0.3671,0.0769), (0.3737,0.706), (0.3179,0.6898)], strength=0.6, col=(255, 210, 120)),
    dict(poly=[(0.7847,0.2057), (0.7912,0.2122), (0.7958,0.2978), (0.7776,0.299)], strength=0.6, col=(255, 210, 120)),
    dict(poly=[(0.7674,0.5819), (0.8481,0.5968), (0.8478,0.6514), (0.7684,0.6551)], strength=1.0, col=(255, 210, 120)),
    dict(poly=[(0.8811,0.4082), (0.8871,0.4194), (0.886,0.4454), (0.8849,0.4715), (0.8786,0.4677), (0.8751,0.4156)], strength=0.4, col=(255, 210, 120)),
    dict(poly=[(0.9054,0.402), (0.9133,0.3859), (0.9224,0.4007), (0.9182,0.4553), (0.9105,0.4566)], strength=0.4, col=(255, 210, 120)),
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

# Kurzzeitiger Geschwindigkeits-/Anziehungs-Boost der Falter WÄHREND des
# Übergangs Dämmerung->Nacht: jedes Mal wenn der RFID-Tag ausgeht, bis das
# Nachtbild voll da ist (bg_frame erreicht 19). 1.0 = aus, 2.0 = doppelt so
# schnell/stark zum Mondkegel.
TRANSITION_BOOST = 1.8

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
        panorama=["background/Final_v1_OFF.png", "background/Final_v1_ON.png"],
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

ARDUINO = dict(
    enabled=True,
    port=None,           # None = automatisch suchen (Linux: /dev/ttyACM* oder /dev/ttyUSB*)
    baud=9600,
)

MOON = dict(
    enabled=True,
    url="ws://192.168.1.6:8765",
    flipX=True,         # fern (gx=1) -> links, nah (gx=0) -> rechts
    flipY=False,        # max Hoehe -> oben
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
