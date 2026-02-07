import sys
import os
import time
from PySide6.QtWidgets import QApplication, QFileDialog
from PySide6.QtMultimedia import QMediaPlayer

# Add src to path
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from src.gui.main_window import MainWindow

def verify_audio_output():
    print("="*50)
    print("AUDIO OUTPUT VERIFICATION")
    print("="*50)

    app = QApplication.instance() or QApplication(sys.argv)
    
    # Mock QFileDialog to load 7.json
    target_project = r"C:\Users\Goryeng\.gemini\antigravity\playground\fractal-equinox\projects\7.json"
    if not os.path.exists(target_project):
        print(f"FAIL: Project file not found: {target_project}")
        return

    def mock_get_open_filename(*args, **kwargs):
        return target_project, "JSON Files (*.json)"
    QFileDialog.getOpenFileName = mock_get_open_filename

    window = MainWindow()
    window.resize(1200, 800)
    window.show()
    
    def wait(seconds):
        end = time.time() + seconds
        while time.time() < end:
            app.processEvents()
            time.sleep(0.05)

    print("[1] Loading Project...")
    window._on_load_work()
    wait(5.0) 
    
    mgr = window._file_subtitle_manager
    if len(mgr.segments) == 0:
        print("FAIL: Segments not loaded.")
        return

    print("[2] Starting Playback...")
    # Play first segment
    target_seg_id = mgr.segments[0].id
    window._toggle_playback(target_seg_id)
    wait(1.0) # Let it spin up

    waveform = window.waveform_right
    media_player = None
    
    if hasattr(window, "_media_view") and window._media_view:
        print("[DEBUG] Using _media_view instance.")
        media_player = window._media_view.player()
    elif hasattr(window, "media_view") and window.media_view:
         print("[DEBUG] Using media_view property.")
         media_player = window.media_view.player()
    else:
        print("[FAIL] MediaView not found on MainWindow.")
        # Try to find it in children purely for debug
        print(f" -> Children: {[c.objectName() for c in window.children()]}")
        return

    print("[3] Monitoring Audio Engine...")
    
    # Check 1: Audio Hardware Stream Active?
    stream = getattr(waveform, "_playback_stream", None)
    if stream and stream.active:
        print(" -> [PASS] SoundDevice Stream is ACTIVE.")
    else:
        print(f" -> [FAIL] SoundDevice Stream is INACTIVE or None. ({stream})")
        
    # Check 2: Player State
    if media_player.playbackState() == QMediaPlayer.PlayingState:
        print(" -> [PASS] Media Player is Playing.")
    else:
         print(f" -> [FAIL] Media Player state: {media_player.playbackState()}")

    # Check 3: Samples Processing (Logic Proof of Audio)
    # Monitor curr_sample advancing
    if hasattr(waveform, "_pb_state"):
        initial_sample = waveform._pb_state['curr_sample']
        print(f" -> Initial Sample: {initial_sample}")
        
        wait(2.0)
        
        final_sample = waveform._pb_state['curr_sample']
        print(f" -> Final Sample: {final_sample}")
        
        diff = final_sample - initial_sample
        # 44100Hz * 2s = ~88200 samples
        print(f" -> Processed Samples: {diff}")
        
        if diff > 1000:
            print(" -> [PASS] Audio Engine is CONSUMING data (Audio is playing).")
        else:
            print(" -> [FAIL] Audio Engine is STUCK (No audio output).")
    else:
        print(" -> [FAIL] Playback State (_pb_state) not initialized.")

    print("\n[4] Cleanup...")
    window._stop_all_playback()
    wait(0.5)
    window.close()
    print("="*50)
    print("VERIFICATION COMPLETE")
    print("="*50)

if __name__ == "__main__":
    try:
        verify_audio_output()
    except Exception as e:
        print(f"FATAL: {e}")
