
import sys
import unittest
from unittest.mock import MagicMock, patch
import time
import numpy as np

# Mock modules that might not be installed in the CI env
sys.modules['faster_whisper'] = MagicMock()
sys.modules['sounddevice'] = MagicMock()
sys.modules['pyqtgraph'] = MagicMock()
sys.modules['PyQt6.QtWidgets'] = MagicMock()
sys.modules['PyQt6.QtCore'] = MagicMock()
sys.modules['PyQt6.QtGui'] = MagicMock()

# Add project root to sys.path
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Now import our modules
from src.engine.transcriber import WhisperTranscriberProcess, ControlCommand
from src.engine.subtitle import SubtitleManager, SubtitleSegment, SegmentStatus

class TestPipeline(unittest.TestCase):
    
    def test_transcriber_queue_logic(self):
        """Verify the 4-Queue architecture logic."""
        print("\n[Test] Verifying Transcriber Pipeline...")
        
        # Instantiate
        transcriber = WhisperTranscriberProcess()
        
        # Check Queues
        self.assertIsNotNone(transcriber.audio_queue)
        self.assertIsNotNone(transcriber.result_queue)
        self.assertIsNotNone(transcriber.control_queue)
        self.assertIsNotNone(transcriber.log_queue)
        
        print("  - Queues initialized successfully")
        
        # Simulate sending a LOAD_MODEL command
        transcriber.load_model()
        cmd, _ = transcriber.control_queue.get()
        self.assertEqual(cmd, ControlCommand.LOAD_MODEL)
        print("  - LOAD_MODEL command correctly enqueued")
        
        # Simulate sending Audio
        fake_audio = np.zeros(16000, dtype=np.float32)
        transcriber.transcribe_live(fake_audio, 0.0, 1.0)
        
        req = transcriber.audio_queue.get()
        self.assertEqual(req.start_time, 0.0)
        self.assertEqual(req.is_final, False) # Live = False
        print("  - Audio Request correctly enqueued (Live Mode)")
        
    def test_subtitle_manager_logic(self):
        """Verify Subtitle Manager logic (Draft -> Final)."""
        print("\n[Test] Verifying Subtitle Manager...")
        
        manager = SubtitleManager()
        
        # Add DRAFT segment
        seg = SubtitleSegment(id="1", start=0.0, end=1.0, text="Hello", status=SegmentStatus.DRAFT)
        manager.add_segment(seg)
        self.assertEqual(len(manager.segments), 1)
        self.assertEqual(manager.segments[0].status, SegmentStatus.DRAFT)
        print("  - DRAFT segment added")
        
        # Finalize it
        manager.finalize_segment("1", [])
        self.assertEqual(manager.segments[0].status, SegmentStatus.FINAL)
        print("  - Segment correctly finalized")
        
        # Test Undo
        manager.update_segment("1", text="Modified")
        self.assertEqual(manager.segments[0].text, "Modified")
        
        manager.undo()
        self.assertEqual(manager.segments[0].text, "Hello")
        print("  - Undo logic works")

if __name__ == "__main__":
    unittest.main()
