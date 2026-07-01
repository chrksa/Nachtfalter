NACHTFALTER · Asset-Ordner
==========================

Lege deine Bilder hier ab und trage die Dateinamen in der HTML unter
Abschnitt "B · ASSETS" (Konstante ASSETS) ein. Pfade dort sind relativ
zu diesem Ordner (ASSET_DIR = "assets/").

Ordner
------
  schmetterlinge/   ein oder mehrere Falter-PNGs (transparent, Kopf nach oben).
                    Jeder Falter wählt zufällig eins aus ASSETS.moths.
                    Bereits enthalten: mondspinner.png (Spanischer Mondspinner).

  laternen/         ein oder mehrere Laternen-PNGs (transparent, Lampenkopf oben
                    im Bild). Eintragen in ASSETS.lamps. Leer lassen = die
                    Laterne wird prozedural gezeichnet.

  background/       Hintergrund-Ebenen. Eintragen in ASSETS.bg:
                      sky   = Vollbild-Himmel ohne Parallax
                      moon  = optionaler Mond
                      far   = Parallax hinten (langsam)   – ersetzt hinterste Hügel
                      mid   = Parallax mitte               – ersetzt mittlere Hügel
                      near  = Parallax vorne (schnell)     – ersetzt Häuser-Silhouette
                    Parallax-Bilder werden horizontal gekachelt und unten
                    ausgerichtet -> am besten nahtlos (links/rechts) gestalten.

Beispiel-Eintrag in der HTML
----------------------------
  const ASSETS = {
    moths: ["schmetterlinge/mondspinner.png", "schmetterlinge/eule.png"],
    lamps: ["laternen/laterne.png"],
    bg: {
      sky:  "background/himmel.png",
      moon: "background/mond.png",
      far:  "background/fern.png",
      mid:  "background/mitte.png",
      near: "background/nah.png"
    }
  };

Hinweis
-------
Fehlt eine Datei oder ist eine Liste leer, zeichnet das Programm automatisch
eine prozedurale Ersatzgrafik. Es geht also nie etwas kaputt, wenn ein Bild
fehlt – du kannst Schritt für Schritt eigene Assets ergänzen.
