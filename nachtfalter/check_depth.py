"""
Test: Liefert die RealSense-Tiefe auf dem SELBSTLEUCHTENDEN Mond brauchbare Werte?

Aktiviert Farbe + Tiefe (mit IR-Emitter), richtet die Tiefe auf das Farbbild aus,
sucht den hellsten Blob (Mond) und misst dort:
  * Entfernung in Metern (Median eines Fensters um den Blob)
  * Gueltig-Quote: wie viele Pixel im Fenster ueberhaupt Tiefe haben
    (0 = Loch). Viel Gruen/hohe Quote = Tiefe brauchbar. Viel Rot/0 % = Mond
    ueberstrahlt -> Tiefe NICHT nutzbar (dann Zwei-Kamera-Triangulation nehmen).

Bedienung:  ESC beendet.  E schaltet den IR-Emitter an/aus (Vergleich).
            + / - aendert die Farb-Belichtung (Mond isolieren).

    python3 check_depth.py
"""
import numpy as np
import cv2
import pyrealsense2 as rs

W, H = 848, 480
FONT = cv2.FONT_HERSHEY_SIMPLEX
WIN_SAMPLE = 15          # Kantenlaenge des Messfensters um den Blob (Pixel)
BRIGHT = 200             # Helligkeits-Schwelle fuer die Blob-Suche
EXPOSURE = 100           # Start-Belichtung der Farbkamera


def find_blob(bgr):
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    _, mask = cv2.threshold(gray, BRIGHT, 255, cv2.THRESH_BINARY)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best, ba = None, 0
    for c in cnts:
        a = cv2.contourArea(c)
        if a > ba:
            M = cv2.moments(c)
            if M["m00"] > 0:
                best, ba = (M["m10"] / M["m00"], M["m01"] / M["m00"]), a
    return best


def sample_depth(depth_frame, u, v):
    """Median-Entfernung + Gueltig-Quote in einem Fenster um (u, v)."""
    r = WIN_SAMPLE // 2
    vals = []
    valid = total = 0
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            x, y = int(u) + dx, int(v) + dy
            if 0 <= x < W and 0 <= y < H:
                total += 1
                d = depth_frame.get_distance(x, y)
                if d > 0:
                    vals.append(d)
                    valid += 1
    if not vals:
        return None, 0.0
    return float(np.median(vals)), valid / max(1, total)


def main():
    pipe = rs.pipeline()
    cfg = rs.config()
    cfg.enable_stream(rs.stream.color, W, H, rs.format.bgr8, 30)
    cfg.enable_stream(rs.stream.depth, W, H, rs.format.z16, 30)
    profile = pipe.start(cfg)
    align = rs.align(rs.stream.color)

    dev = profile.get_device()
    depth_sensor = dev.first_depth_sensor()
    emitter = True
    depth_sensor.set_option(rs.option.emitter_enabled, 1)

    rgb = next((s for s in dev.query_sensors()
                if s.get_info(rs.camera_info.name) == "RGB Camera"), None)
    exposure = EXPOSURE
    if rgb is not None:
        try:
            rgb.set_option(rs.option.enable_auto_exposure, 0)
            rgb.set_option(rs.option.exposure, float(exposure))
        except Exception as e:
            print(f"Belichtung setzen fehlgeschlagen: {e}")

    print("ESC beenden | E Emitter an/aus | +/- Belichtung")
    hist = []
    try:
        while True:
            frames = align.process(pipe.wait_for_frames())
            color = frames.get_color_frame()
            depth = frames.get_depth_frame()
            if not color or not depth:
                continue
            bgr = np.asanyarray(color.get_data()).copy()

            blob = find_blob(bgr)
            info = f"Emitter {'AN' if emitter else 'AUS'}  Exp {exposure}"
            if blob is not None:
                u, v = blob
                dist, ratio = sample_depth(depth, u, v)
                col = (0, 255, 0) if ratio >= 0.6 else \
                      (0, 200, 255) if ratio >= 0.2 else (0, 0, 255)
                r = WIN_SAMPLE // 2
                cv2.rectangle(bgr, (int(u) - r, int(v) - r),
                              (int(u) + r, int(v) + r), col, 2)
                if dist is not None:
                    txt = f"Mond: {dist:.2f} m   gueltig {ratio*100:.0f}%"
                    hist.append(dist)
                    if len(hist) > 30:
                        hist.pop(0)
                    if len(hist) >= 5:
                        txt += f"   Streuung +-{np.std(hist)*100:.1f} cm"
                else:
                    txt = "Mond gefunden, aber TIEFE = 0 (Loch) -> nicht nutzbar"
                cv2.putText(bgr, txt, (10, 60), FONT, 0.6, col, 2)
            else:
                cv2.putText(bgr, "kein Blob (Belichtung mit +/- anpassen)",
                            (10, 60), FONT, 0.6, (0, 0, 255), 2)

            cv2.putText(bgr, info, (10, 30), FONT, 0.6, (0, 255, 255), 2)
            cv2.imshow("Tiefen-Test (Mond)", bgr)

            # Tiefe eingefaerbt zum Draufschauen
            dimg = np.asanyarray(depth.get_data())
            dvis = cv2.applyColorMap(
                cv2.convertScaleAbs(dimg, alpha=0.03), cv2.COLORMAP_JET)
            cv2.imshow("Tiefe (roh)", dvis)

            k = cv2.waitKey(1) & 0xFF
            if k == 27:
                break
            elif k == ord('e'):
                emitter = not emitter
                depth_sensor.set_option(rs.option.emitter_enabled, 1 if emitter else 0)
            elif k in (ord('+'), ord('=')) and rgb is not None:
                exposure = min(2000, exposure + 20)
                rgb.set_option(rs.option.exposure, float(exposure))
            elif k in (ord('-'), ord('_')) and rgb is not None:
                exposure = max(1, exposure - 20)
                rgb.set_option(rs.option.exposure, float(exposure))
    finally:
        pipe.stop()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
