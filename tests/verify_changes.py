import sys
import os
import time
from unittest.mock import MagicMock

# Add src to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer

# Import classes to test
# Mock AudioRecorder to prevent PortAudio issues
# sys.modules['src.engine.audio'] = MagicMock()  <-- Removing this
# Strategy: Mock `src.engine.audio.AudioRecorder` *before* instantiating MainWindow
import src.engine.audio
src.engine.audio.AudioRecorder = MagicMock()

from src.gui.main_window import MainWindow
from src.engine.transcriber import TranscribeResult
from src.engine.subtitle import SubtitleSegment, SegmentStatus

def verify():
    app = QApplication(sys.argv)
    
    print("[1] Initializing MainWindow...", flush=True)
    try:
        window = MainWindow()
        window.show()
    except Exception as e:
        print(f"FAILED to init MainWindow: {e}")
        return
        
    print("[2] verifying Overlay setup...")
    if not hasattr(window, 'overlay'):
        print("FAILED: MainWindow has no 'overlay' attribute.")
        return
    if not window.btn_overlay:
        print("FAILED: No overlay toggle button.")
        return
        
    # Show overlay
    window.btn_overlay.setChecked(True)
    window._toggle_overlay()
    if not window.overlay.isVisible():
        print("FAILED: Overlay did not show after toggle.")
        return
    print(" - Overlay toggled successfully.")

    print("[3] Simulating Live Transcription...")
    # Simulate a batch result
    # We need to bypass the thread polling for this test or just call the processor directly
    
    # Fake result
    fake_result = TranscribeResult(
        segment_id="test_seg_1",
        source="live",
        start=0.0,
        end=2.0,
        text="테스트 자막입니다.",
        is_final=False,
        words=[],
        avg_logprob=-0.5
    )
    # Manually inject RMS so it passes filters
    fake_result.avg_rms = 0.05 
    
    try:
        window._process_transcription_batch([fake_result])
    except Exception as e:
        print(f"FAILED during transcription processing: {e}")
        import traceback
        traceback.print_exc()
        return

    # Check Overlay Text
    current_text = window.overlay.label.text()
    if "테스트 자막입니다." not in current_text:
        print(f"FAILED: Overlay text mismatch. Got: '{current_text}'")
        return
    print(f" - Overlay updated correctly: {current_text}")
    
    print("[4] Checking Subtitle Editor...")
    if window._subtitle_manager.segments:
        seg = window._subtitle_manager.segments[0]
        print(f" - Segment added: {seg.text}")
        
        # Check Playback Logic
        print("[5] Simulating Playback Request...")
        try:
            # Mock waveform playback to avoid real audio device issues in CI/Headless
            window.waveform.play_segment = MagicMock()
            window.waveform.stop_playback = MagicMock()
            
            # Trigger toggle
            window._toggle_playback(seg.id)
            
            # Check if editor state changed to PLAYING
            # Accessing private members for test is okay
            live_editor = window.live_editor
            if live_editor._current_playback_id == seg.id:
                 print(" - Editor state changed to playing (Correct).")
            else:
                 print(f"FAILED: Editor state did not update. ID: {live_editor._current_playback_id}")
                 
            # Check waveform call
            window.waveform.play_segment.assert_called_once()
            print(" - Waveform.play_segment called.")
            
        except Exception as e:
            print(f"FAILED during playback test: {e}")
            import traceback
            traceback.print_exc()
            return
            
    else:
        print("FAILED: No segments found in manager.")
        return

    print("=== VERIFICATION PASSED ===")
    app.quit()

if __name__ == "__main__":
    verify()
