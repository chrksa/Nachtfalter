"""
Schneller Check: welche RealSense-Kameras sind verbunden?

Zeigt Name, Seriennummer und USB-Typ jeder gefundenen Kamera und bewertet,
ob der Zwei-Kamera-Redundanzbetrieb von tracking_v3.py moeglich ist.

    python3 check_cameras.py
"""
import pyrealsense2 as rs


def main():
    ctx = rs.context()
    devs = list(ctx.devices)

    cams = []
    for d in devs:
        try:
            name = d.get_info(rs.camera_info.name)
        except Exception:
            name = "?"
        if "platform camera" in name.lower():
            continue          # interne Laptop-Kamera ignorieren
        info = {"name": name}
        for key, field in (("serial", rs.camera_info.serial_number),
                           ("usb", rs.camera_info.usb_type_descriptor),
                           ("fw", rs.camera_info.firmware_version)):
            try:
                info[key] = d.get_info(field)
            except Exception:
                info[key] = "?"
        cams.append(info)

    print(f"Gefundene RealSense-Kameras: {len(cams)}\n")
    for i, c in enumerate(cams):
        label = chr(ord('A') + i)
        usb = str(c["usb"])
        usb_ok = usb.startswith("3")
        warn = "" if usb_ok else "  <-- WARNUNG: kein USB3! (nur reduzierte Aufloesung)"
        print(f"  Kamera {label}:  {c['name']}")
        print(f"     Serial   = {c['serial']}")
        print(f"     USB      = {usb}{warn}")
        print(f"     Firmware = {c['fw']}")
        print()

    # --- Bewertung fuer tracking_v3.py ---
    if len(cams) == 0:
        print("=> Keine Kamera. tracking_v3.py wird nicht starten.")
    elif len(cams) == 1:
        print("=> Nur EINE Kamera: tracking_v3.py laeuft, aber OHNE Redundanz.")
    else:
        print("=> Zwei (oder mehr) Kameras: Redundanzbetrieb moeglich.")
        print("   tracking_v3.py nutzt die ersten zwei (sortiert nach Serial) als A/B.")
        if not all(str(c["usb"]).startswith("3") for c in cams[:2]):
            print("   ACHTUNG: mindestens eine Kamera nicht an USB3.")
        else:
            print("   Falls der Start hakt/Frames fehlen: USB-Bandbreite. Dann in")
            print("   tracking_v3.py ENABLE_IR=False setzen oder getrennte USB-Controller nutzen.")


if __name__ == "__main__":
    main()
