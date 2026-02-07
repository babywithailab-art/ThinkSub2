import sys
import os
import json
import time
import queue
from PySide6.QtWidgets import QApplication, QFileDialog
from PySide6.QtCore import QTimer, QCoreApplication, Qt

# Add src to path
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from src.gui.main_window import MainWindow

def test_project_playback():
    app = QApplication(sys.argv)
    
    # Mock QFileDialog to return our specific file
    target_project = r"C:\Users\Goryeng\.gemini\antigravity\playground\fractal-equinox\projects\7.json"
    
    # Monkey patch QFileDialog.getOpenFileName
    def mock_get_open_filename(*args, **kwargs):
        return target_project, "JSON Files (*.json)"
        
    QFileDialog.getOpenFileName = mock_get_open_filename

    window = MainWindow()
    window.show() # Must show to initialize visible elements
    
    print("[Test] Window initialized.")
    
    # 1. Trigger Load Work
    # This will call our mocked QFileDialog and load 7.json
    print("[Test] Triggering _on_load_work...")
    window._on_load_work()
    
    # 2. Waiting for loading to complete (Audio loading is async)
    print("[Test] Waiting for audio/files to load (5 seconds)...")
    
    # Process events loop
    start_wait = time.time()
    while time.time() - start_wait < 5.0:
        app.processEvents()
        time.sleep(0.1)
        
    # Check if loaded
    if not window._file_subtitle_manager.segments:
        print("[Test] WARNING: No file segments loaded!")
    else:
        print(f"[Test] Loaded {len(window._file_subtitle_manager.segments)} file segments.")
        
    # 3. Trigger Playback
    # Get first segment ID
    if window._file_subtitle_manager.segments:
        first_seg_id = window._file_subtitle_manager.segments[0].id
        print(f"[Test] Triggering playback for segment {first_seg_id}...")
        
        # Simulate button click signal or call _toggle_playback directly
        # We connected file_editor.playback_requested -> _toggle_playback
        window.file_editor.playback_requested.emit(first_seg_id)
        
        # Wait and let it play for 3 seconds
        print("[Test] Playing for 3 seconds...")
        play_wait = time.time()
        while time.time() - play_wait < 3.0:
            app.processEvents()
            time.sleep(0.1)
            
        print("[Test] Playback duration passed.")
        
        # Stop
        window._stop_all_playback()
    else:
        print("[Test] No segments to play.")
        
    print("[Test] Closing window...")
    window.close()
    print("[Test] SUCCESS: No crash detected.")

if __name__ == "__main__":
    try:
        test_project_playback()
    except Exception as e:
        print(f"[Test] CRITICAL FAILURE: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
