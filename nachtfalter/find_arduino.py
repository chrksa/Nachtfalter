"""
Findet automatisch den seriellen Port des RFID-Arduino.

  1. Listet alle seriellen Ports (macOS/Linux/Windows).
  2. Prueft jeden Kandidaten, ob er das RFID-Protokoll sendet (Zeilen "0"/"1"
     bei 9600 Baud) -> eindeutige Identifikation, auch neben anderen Geraeten.
  3. Gibt den Port + fertige config.py-Zeile zum Kopieren aus.

    python3 find_arduino.py          # auflisten + aktiv pruefen
    python3 find_arduino.py --list   # nur auflisten (Ports nicht oeffnen)

Waehrend der Pruefung am besten einen Tag auflegen/abnehmen (falls der Arduino
nur bei Aenderung sendet). Arduino-IDE-Monitor und das laufende Spiel vorher
schliessen - ein Port kann nur von EINEM Programm geoeffnet werden.
"""
import sys
import glob
import time

BAUD = 9600
PROBE_SECONDS = 3.0

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    serial = None
    list_ports = None


def is_usb_serial(dev):
    """Echter USB-Serial-Port? (blendet Linux-Legacy /dev/ttyS* etc. aus)"""
    d = dev.lower()
    return (d.startswith("com")
            or any(k in d for k in ("usbmodem", "usbserial", "ttyusb",
                                    "ttyacm", "cu.usb", "wchusb", "slab")))


def candidate_ports():
    """Liste (device, beschreibung) der USB-Serial-Ports (Legacy-Ports raus)."""
    ports, others = [], []
    if list_ports is not None:
        for p in list_ports.comports():
            if "Bluetooth" in p.device or "Bluetooth" in (p.description or ""):
                continue
            (ports if is_usb_serial(p.device) else others).append(
                (p.device, p.description or "?"))
    else:                                   # Fallback ohne pyserial: nur Pfade
        for pat in ("/dev/cu.usbmodem*", "/dev/cu.usbserial*",
                    "/dev/ttyUSB*", "/dev/ttyACM*", "COM*"):
            for dev in glob.glob(pat):
                ports.append((dev, "?"))
    ports.sort()
    # Nur wenn gar keine USB-Serial-Ports da sind, die uebrigen zeigen.
    return ports if ports else others


def looks_like_rfid(dev, seconds=PROBE_SECONDS):
    """True, wenn der Port Zeilen '0'/'1' bei 9600 Baud sendet."""
    if serial is None:
        return False
    try:
        with serial.Serial(dev, BAUD, timeout=0.2) as ser:
            time.sleep(0.3)                 # Arduino-Reset nach dem Oeffnen abwarten
            buf = ""
            t0 = time.monotonic()
            while time.monotonic() - t0 < seconds:
                buf += ser.read(ser.in_waiting or 1).decode("utf-8", "ignore")
                if any(part.strip() in ("0", "1") for part in buf.split("\n")):
                    return True
                buf = buf[-64:]
    except Exception:
        return False
    return False


def print_config(port):
    print("\nTrage das in config.py ein:\n")
    print("ARDUINO = dict(")
    print("    enabled=True,")
    print(f'    port="{port}",')
    print("    baud=9600,")
    print(")")


def main():
    just_list = "--list" in sys.argv
    ports = candidate_ports()

    if not ports:
        print("Keine seriellen Ports gefunden.")
        print("-> Arduino eingesteckt? Datenkabel (nicht nur Ladekabel)? Treiber (CH340/CP210x)?")
        if serial is None:
            print("-> Ausserdem: pyserial ist nicht installiert (nur eingeschraenkte Suche).")
        return

    print("Gefundene serielle Ports:")
    for dev, desc in ports:
        print(f"  {dev}   [{desc}]")

    if serial is None:
        print("\npyserial fehlt -> kann nicht aktiv pruefen.")
        print("Installiere pyserial oder trage den usbmodem/usbserial-Port oben in config.py ein.")
        return
    if just_list:
        return

    print(f"\nPruefe, welcher Port das RFID-Signal (0/1 @ {BAUD} Baud) sendet ...")
    print("(jetzt ggf. einen Tag auflegen/abnehmen; Monitor & Spiel muessen zu sein)\n")
    found = None
    for dev, desc in ports:
        print(f"  ... teste {dev}", flush=True)
        if looks_like_rfid(dev):
            found = dev
            break

    if found:
        print(f"\n>>> RFID-Arduino gefunden auf: {found}")
        print_config(found)
        return

    print("\nKein Port hat 0/1 gesendet.")
    print("Moegliche Gruende: falsche Baud-Rate im Sketch, Port von Monitor/Spiel belegt,")
    print("oder es gab im Testfenster keinen Tag-Wechsel.")
    usb = next((d for d, _ in ports
                if any(k in d.lower() for k in ("usbmodem", "usbserial"))), None)
    if usb:
        print(f"\nWahrscheinlichster Kandidat nach Namen: {usb}")
        print_config(usb)


if __name__ == "__main__":
    main()
