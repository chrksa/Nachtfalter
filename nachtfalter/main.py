"""
NACHTFALTER · main   (entspricht Abschnitt G der HTML — Boot/Loop)
====================================================================
Startet alles. Verdrahtet esp32, moon, sim, renderer und fährt die
Hauptschleife. Steuerung:
  H   HUD ein/aus        R   Schwarm zurücksetzen
  F   Vollbild wechseln  D   Debug-Overlay (Lichtquellen)
  T   RFID simulieren    E   Lichtquellen-Editor
  Maus = Leitlicht (wenn kein Mond-Tracking aktiv ist)

Start:  python main.py
"""
import sys
import pygame

import config
import assets
from esp32 import ESP32
from moon import Moon
from sim import Sim
from render import Renderer
from editor import Editor

from arduino import ArduinoRFID


def make_window(fullscreen):
    flags = pygame.FULLSCREEN | pygame.SCALED if fullscreen else pygame.RESIZABLE
    if fullscreen:
        screen = pygame.display.set_mode((0, 0), flags)
    else:
        screen = pygame.display.set_mode(
            (config.WINDOW["width"], config.WINDOW["height"]), flags)
    pygame.mouse.set_visible(not fullscreen)
    return screen


def main():
    pygame.init()
    pygame.mixer.init()
    pygame.display.set_caption(config.WINDOW["caption"])
    fullscreen = config.WINDOW["fullscreen"]
    screen = make_window(fullscreen)
    fonts = assets.init_fonts()

    scale = config.WINDOW["render_scale"]
    win_w, win_h = screen.get_size()
    rw, rh = max(1, int(win_w * scale)), max(1, int(win_h * scale))
    render_surf = pygame.Surface((rw, rh)) if scale != 1.0 else screen

    esp32 = ESP32(**config.ESP32)
    arduino_rfid = ArduinoRFID(**config.ARDUINO)
    moon = Moon(**config.MOON)
    sim = Sim(rw, rh, esp32=esp32, moon=moon)
    renderer = Renderer(render_surf, fonts, config.ASSET_DIR)
    editor = Editor(sim)

    # --- SOUND INTEGRATION ---
    # Sounds laden mit den korrekten Keys aus config.py
    snd_01 = pygame.mixer.Sound(config.SOUNDS["bg_loop"])
    snd_02 = pygame.mixer.Sound(config.SOUNDS["focus_in"])
    snd_03 = pygame.mixer.Sound(config.SOUNDS["active_loop"])
    snd_04 = pygame.mixer.Sound(config.SOUNDS["focus_out"])

    # Eigene Event-Typen für das Ende von Einzelsounds definieren
    SND_02_DONE = pygame.USEREVENT + 1
    SND_04_DONE = pygame.USEREVENT + 2

    # Einen festen Audio-Channel reservieren, damit sich nichts abschneidet
    sound_channel = pygame.mixer.Channel(0)

    # Startzustand: Maus draußen -> 01 läuft im endlosen Loop (-1)
    sound_channel.play(snd_01, loops=-1)
    mouse_was_inside = False
    # -------------------------
    rfid_sim_on = False        # Fallback-Wert des Debug-Buttons
    # -------------------------

    clock = pygame.time.Clock()
    running = True
    while running:
        # Maus -> Leitlicht (Fallback). Fenster- in Render-Koordinaten.

        mx, my = pygame.mouse.get_pos()
        mouse_inside = pygame.mouse.get_focused() == 1
        # Im Editor ist die Maus zum Bearbeiten da, nicht als Leitlicht.
        sim.update_pointer(mx * rw / win_w, my * rh / win_h, mouse_inside and not editor.active)

        # --- SOUND STATUS-PRÜFUNG ---
        if mouse_inside != mouse_was_inside:
            if mouse_inside:
                # Maus kommt REIN >> 02 einmal abspielen
                sound_channel.set_endevent(SND_02_DONE) # Event feuern, wenn fertig
                sound_channel.play(snd_02)
            else:
                # Maus geht RAUS >> 04 einmal abspielen
                sound_channel.set_endevent(SND_04_DONE) # Event feuern, wenn fertig
                sound_channel.play(snd_04)
            mouse_was_inside = mouse_inside
        # -----------------------------

        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False

            # --- SOUND EVENTS ABFANGEN ---
            elif ev.type == SND_02_DONE:
                # 02 ist fertig >> Wenn Maus noch da ist, 03 im Loop abspielen
                sound_channel.set_endevent() # Event-Registrierung löschen
                if mouse_inside:
                    sound_channel.play(snd_03, loops=-1)

            elif ev.type == SND_04_DONE:
                # 04 ist fertig >> Wenn Maus noch weg ist, 01 im Loop abspielen
                sound_channel.set_endevent() # Event-Registrierung löschen
                if not mouse_inside:
                    sound_channel.play(snd_01, loops=-1)
            # ------------------------------

            elif ev.type == pygame.MOUSEBUTTONDOWN:
                ex, ey = ev.pos
                rx, ry = ex * rw / win_w, ey * rh / win_h
                if ev.button == 1 and renderer.rfid_btn and renderer.rfid_btn.collidepoint(rx, ry):
                    rfid_sim_on = not rfid_sim_on
                elif editor.active:
                    editor.on_mouse_down(rx, ry, ev.button)
            elif ev.type == pygame.MOUSEBUTTONUP:
                if editor.active:
                    editor.on_mouse_up()
            elif ev.type == pygame.MOUSEMOTION:
                if editor.active:
                    ex, ey = ev.pos
                    editor.on_mouse_move(ex * rw / win_w, ey * rh / win_h)
            elif ev.type == pygame.MOUSEWHEEL:
                if editor.active:
                    editor.on_wheel(ev.y)

            elif ev.type == pygame.KEYDOWN:
                if ev.key in (pygame.K_ESCAPE, pygame.K_q):
                    running = False
                elif ev.key == pygame.K_t:
                    rfid_sim_on = not rfid_sim_on
                elif ev.key == pygame.K_e:
                    editor.toggle()
                elif ev.key == pygame.K_n and editor.active:
                    editor.new_light(mx * rw / win_w, my * rh / win_h)
                elif ev.key in (pygame.K_DELETE, pygame.K_BACKSPACE) and editor.active:
                    editor.delete_selected()
                elif ev.key == pygame.K_s and editor.active:
                    editor.save()
                elif ev.key in (pygame.K_PLUS, pygame.K_KP_PLUS, pygame.K_EQUALS, pygame.K_UP) and editor.active:
                    editor.adjust_strength(+0.05)
                elif ev.key in (pygame.K_MINUS, pygame.K_KP_MINUS, pygame.K_DOWN) and editor.active:
                    editor.adjust_strength(-0.05)
                elif ev.key == pygame.K_c and editor.active:
                    editor.cycle_color()
                elif ev.key == pygame.K_x and editor.active:
                    editor.delete_vertex_at(mx * rw / win_w, my * rh / win_h)
                elif ev.key == pygame.K_h:
                    renderer.hud_visible = not renderer.hud_visible
                elif ev.key == pygame.K_d and not editor.active:
                    sim.P["showField"] = not sim.P["showField"]
                elif ev.key == pygame.K_r:
                    sim.reset()
                elif ev.key == pygame.K_f:
                    fullscreen = not fullscreen
                    screen = make_window(fullscreen)
                    win_w, win_h = screen.get_size()
                    rw, rh = max(1, int(win_w * scale)), max(1, int(win_h * scale))
                    render_surf = pygame.Surface((rw, rh)) if scale != 1.0 else screen
                    sim.resize(rw, rh); renderer.resize(render_surf)
            elif ev.type == pygame.VIDEORESIZE and not fullscreen:
                screen = pygame.display.set_mode((ev.w, ev.h), pygame.RESIZABLE)
                win_w, win_h = ev.w, ev.h
                rw, rh = max(1, int(win_w * scale)), max(1, int(win_h * scale))
                render_surf = pygame.Surface((rw, rh)) if scale != 1.0 else screen
                sim.resize(rw, rh); renderer.resize(render_surf)

        dts = clock.tick(config.WINDOW["fps"]) / 1000.0
        if dts > 0.05:
            dts = 0.05
        dt = dts * 60.0

        editor.handle_held_keys(pygame.key.get_pressed(), dts)

        # RFID-Tag-Status: Hardware bevorzugen, sonst Debug-Button.
        # Arduino sendet "0"=bekannter Tag da, "1"=keine Karte (is_light_on()).
        hw = arduino_rfid.is_connected()
        rfid_tag_on = (not arduino_rfid.is_light_on()) if hw else rfid_sim_on
        # INVERTIERT: RFID an -> Lichter AUS, RFID aus -> Lichter AN
        lights_on = not rfid_tag_on
        sim.rfid_tag_on = rfid_tag_on        # fürs HUD/Button
        sim.rfid_connected = hw              # Quelle: Hardware oder Debug

        sim.step(dt, dts, mouse_inside, lights_on) # <-- Lichter-Status hier übergeben
        renderer.frame(sim)
        if editor.active:
            editor.draw(render_surf, fonts)

        if render_surf is not screen:
            pygame.transform.smoothscale(render_surf, (win_w, win_h), screen)
        pygame.display.flip()

    esp32.close(); arduino_rfid.close(); moon.close(); pygame.quit(); sys.exit(0)


if __name__ == "__main__":
    main()
