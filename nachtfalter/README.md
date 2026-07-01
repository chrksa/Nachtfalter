# Nachtfalter · Python (pygame)

Port der Browser-Installation `nachtfalter-laternen.html` nach reinem
Python. Eine Codebasis, kein Browser, kein WebSocket-Umweg fürs
Rendering — Logik, Hardware-Anbindung und Darstellung laufen in einem
Prozess. Die Modul-Aufteilung entspricht 1:1 den Abschnitten A–G aus
deinem HTML-Kommentar.

## Installation
```bash
pip install -r requirements.txt
```

## Start
```bash
python main.py
```

## Steuerung
| Taste | Wirkung |
|-------|---------|
| `H`   | HUD ein/aus (für die Projektion) |
| `R`   | Schwarm zurücksetzen |
| `F`   | Vollbild umschalten |
| `ESC` / `Q` | Beenden |
| Maus  | Leitlicht (Fallback, wenn kein Mond-Tracking aktiv ist) |

## Tuning
Alle Parameter (frühere Slider + wissenschaftliche Lampendaten) liegen
zentral in `config.py`. Werte ändern, neu starten. Für den Beamer dort
`WINDOW["fullscreen"] = True` setzen.

## Hardware

**Mond-Tracking (RealSense).** Bleibt wie im HTML ein WebSocket-*Client*
gegen deinen `tracking.py`-Server (`ws://127.0.0.1:8765`). Deine
RealSense-Pipeline muss also nicht angefasst werden — erst `tracking.py`
starten, dann `main.py`. Abschalten via `config.MOON["enabled"]=False`
(dann steuert die Maus das Leitlicht). Glättungs- und Ausreißer-Logik
sind 1:1 übernommen.

**ESP32 (Haptik).** Web Serial wurde durch `pyserial` ersetzt. Jeder Tod
sendet `b"DEAD\n"` an den Port. `config.ESP32["port"]=None` sucht
automatisch den ersten `/dev/ttyUSB*`/`/dev/ttyACM*`. Schlägt das Öffnen
fehl, läuft die Simulation trotzdem (nur ohne Vibration).

## Assets
Bilder liegen unter `assets/` (Unterordner `schmetterlinge/`,
`laternen/`, `background/`), eingetragen in `config.ASSETS`. Fehlt eine
Datei, wird prozedural gezeichnet — es geht nie etwas kaputt.

> **Hinweis zum Falter-PNG:** Dein `mondspinner.png` ist ein 24-bit-RGB
> *ohne* Alphakanal (schwarzer Hintergrund). Der Loader keyed Schwarz
> automatisch weg (`assets.KEY_LO/KEY_HI`), damit kein schwarzer Kasten
> entsteht. Sauberer ist ein echtes transparentes PNG — dann greift das
> Keying gar nicht erst.

## Performance
Zwei Stellschrauben, falls es auf dem WUXGA-Beamer (1920×1200) klemmt:

- **Schwarmgröße.** Die Separation ist O(n²). 80 Falter sind in Python
  problemlos; Richtung 180 kann es eng werden. Dann entweder `count`
  senken oder die Nachbar-Schleife in `sim.py` mit numpy vektorisieren.
- **Render-Auflösung.** `config.WINDOW["render_scale"]` auf z.B. `0.75`
  setzen → intern kleiner rendern, dann hochskalieren. Für eine
  Projektion optisch kaum sichtbar, spart aber viel Fill-Rate.

## Module (entspricht A–G der HTML)
| Datei | HTML-Abschnitt | Inhalt |
|-------|----------------|--------|
| `config.py` | A/B | Konstanten, Lampentypen, Parameter, Hardware-Einstellungen |
| `assets.py` | B | Bild-Laden, Schwarz-Keying, Glühtexturen |
| `sim.py`    | C | Boids/Laternen/Kollision/Scroll (das „Gehirn") |
| `esp32.py`  | D | Haptik via pyserial |
| `moon.py`   | E | RealSense-Tracking via WebSocket-Client |
| `render.py` | F | Zeichnen der Szene |
| `main.py`   | G | Fenster, Hauptschleife, Verdrahtung |
