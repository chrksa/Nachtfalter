"""
NACHTFALTER · arduino
====================================================================
Liest RFID-Tags (0 oder 1) über eine separate serielle Verbindung (9600 Baud).
Bei '0' wird das Laternenlicht deaktiviert, bei '1' wird es aktiviert.
"""
import glob
import threading
try:
    import serial
except ImportError:
    serial = None

class ArduinoRFID:
    def __init__(self, enabled=True, port=None, baud=9600):
        self.ser = None
        self.light_state = True
        self.enabled = enabled
        self._stop = False

        if not enabled or serial is None:
            return

        # --- KORREKTUR: Wenn in der config ein Port steht, nutze ihn direkt! ---
        if port is None or port == "":
            port = self._autodetect()

        if port is None:
            print("[arduino] Kein Port für Arduino gefunden -> RFID inaktiv")
            return

        try:
            # Hier wird jetzt garantiert dein "/dev/cu.usbmodem11101" geöffnet:
            self.ser = serial.Serial(port, baud, timeout=0.1)
            print(f"[arduino] Verbunden auf {port} mit {baud} Baud")
            threading.Thread(target=self._read_loop, daemon=True).start()
        except Exception as e:
            print(f"[arduino] Fehler beim Verbinden auf {port}: {e}")

    def _autodetect(self):
        # macOS: /dev/cu.usbmodem* /dev/cu.usbserial*  |  Linux: /dev/ttyACM* /dev/ttyUSB*  |  Windows: COM*
        cands = sorted(glob.glob("/dev/cu.usbmodem*") + glob.glob("/dev/cu.usbserial*")
                       + glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*")
                       + glob.glob("COM*"))
        return cands[0] if cands else None

    def _read_loop(self):
        buffer = ""
        while not self._stop and self.ser:
            try:
                if self.ser.in_waiting > 0:
                    data = self.ser.read(self.ser.in_waiting).decode('utf-8', errors='ignore')
                    buffer += data
                    if "\n" in buffer:
                        lines = buffer.split("\n")
                        buffer = lines[-1]  # Unvollständigen Rest aufheben

                        for line in lines[:-1]:
                            clean_line = line.strip()
                            if not clean_line:
                                continue  # Leere Zeilen ignorieren

                            # Strikt vergleichen statt mit "in"
                            if clean_line == "0":
                                self.light_state = False
                                # print("[DEBUG] RFID sagt: LICHT AUS (0)") # Zum Testen einkommentieren
                            elif clean_line == "1":
                                self.light_state = True
                                # print("[DEBUG] RFID sagt: LICHT AN (1)")  # Zum Testen einkommentieren
            except Exception as e:
                print(f"[arduino] Loop-Fehler: {e}")
                break

    def is_connected(self):
        return self.ser is not None

    def is_light_on(self):
        return self.light_state if self.enabled else True

    def close(self):
        self._stop = True
        if self.ser:
            try: self.ser.close()
            except: pass
