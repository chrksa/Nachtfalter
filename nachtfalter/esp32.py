"""
NACHTFALTER · esp32   (entspricht Abschnitt D der HTML)
====================================================================
Haptik-Feedback. Jeder Tod sendet b"DEAD\\n" an den ESP32. Ersetzt die
Web-Serial-API durch pyserial — robuster und ohne Browser. Schlägt das
Öffnen fehl, läuft die Simulation trotzdem (nur ohne Vibration).
"""
import glob

try:
    import serial
except ImportError:
    serial = None


class ESP32:
    def __init__(self, enabled=True, port=None, baud=115200):
        self.ser = None
        self.state = "getrennt"
        if not enabled:
            self.state = "deaktiviert"
            return
        if serial is None:
            self.state = "pyserial fehlt"
            print("[esp32] pyserial nicht installiert -> kein Feedback")
            return
        if port is None:
            port = self._autodetect()
        if port is None:
            self.state = "kein Port"
            print("[esp32] kein serieller Port gefunden -> kein Feedback")
            return
        try:
            self.ser = serial.Serial(port, baud, timeout=0, write_timeout=0)
            self.state = f"verbunden @ {baud}"
            print(f"[esp32] {self.state} ({port})")
        except Exception as e:
            self.state = "Fehler"
            print(f"[esp32] Verbindung fehlgeschlagen: {e}")

    @staticmethod
    def _autodetect():
        cands = sorted(glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*"))
        return cands[0] if cands else None

    def send_death(self):
        if not self.ser:
            return
        try:
            self.ser.write(b"DEAD\n")
        except Exception as e:
            self.state = "Schreibfehler"
            print(f"[esp32] {e}")

    def close(self):
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None
        self.state = "getrennt"
