import pyrealsense2 as rs
import numpy as np
import cv2

pipe = rs.pipeline()
cfg = rs.config()
cfg.enable_stream(rs.stream.infrared, 1, 848, 480, rs.format.y8, 30)   # linker IR
cfg.enable_stream(rs.stream.depth,       848, 480, rs.format.z16, 30)
profile = pipe.start(cfg)

ds = profile.get_device().first_depth_sensor()
ds.set_option(rs.option.emitter_enabled, 0)          # Punktmuster aus
ds.set_option(rs.option.enable_auto_exposure, 0)
ds.set_option(rs.option.exposure, 800)               # runterregeln bis nur LEDs sichtbar
ds.set_option(rs.option.gain, 16)

intr = profile.get_stream(rs.stream.infrared, 1).as_video_stream_profile().get_intrinsics()

def robust_depth(depth, u, v, win=4):
    """Median der gültigen Tiefen in einem Fenster um (u,v) – umgeht den Sättigungskern."""
    h, w = 480, 848
    vals = []
    for du in range(-win, win + 1):
        for dv in range(-win, win + 1):
            x, y = int(round(u)) + du, int(round(v)) + dv
            if 0 <= x < w and 0 <= y < h:
                d = depth.get_distance(x, y)
                if d > 0:
                    vals.append(d)
    return float(np.median(vals)) if vals else 0.0

try:
    while True:
        frames = pipe.wait_for_frames()
        ir    = frames.get_infrared_frame(1)
        depth = frames.get_depth_frame()
        img = np.asanyarray(ir.get_data())

        _, mask = cv2.threshold(img, 200, 255, cv2.THRESH_BINARY)
        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        pts3d = []
        for c in cnts:
            if cv2.contourArea(c) < 2:
                continue
            M = cv2.moments(c)
            if M["m00"] == 0:
                continue
            u, v = M["m10"] / M["m00"], M["m01"] / M["m00"]
            z = robust_depth(depth, u, v)
            if z == 0:
                continue
            X, Y, Z = rs.rs2_deproject_pixel_to_point(intr, [u, v], z)
            pts3d.append((X, Y, Z))
            cv2.circle(img, (int(u), int(v)), 6, 255, 1)

        if pts3d:
            pos = np.mean(pts3d, axis=0)     # gemittelte Position in Metern (Kamera-Koordinaten)
            cv2.putText(img, f"X={pos[0]:+.3f} Y={pos[1]:+.3f} Z={pos[2]:+.3f} m  (n={len(pts3d)})",
                        (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, 255, 1)

        cv2.imshow("IR", img)
        if cv2.waitKey(1) == 27:
            break
finally:
    pipe.stop()
    cv2.destroyAllWindows()