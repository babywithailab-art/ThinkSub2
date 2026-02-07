
import sys
import os
import uuid
import json

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.engine.subtitle import SubtitleManager, SubtitleSegment, SegmentStatus, Word
from src.gui.main_window import MainWindow # Helper to access export logic if needed? 
# Actually better to test logic in isolation first.

def test_persistence_logic():
    print("--- [TEST] SubtitleManager Persistence Logic ---")
    
    manager = SubtitleManager()
    
    # 1. Create Segments
    seg1 = SubtitleSegment(text="Hello", start=0.0, end=1.0)
    seg2 = SubtitleSegment(text="World", start=1.0, end=2.0)
    
    manager.add_segment(seg1)
    manager.add_segment(seg2)
    
    print(f"Initial Count: {len(manager.segments)}")
    assert len(manager.segments) == 2
    
    # 2. Mock Save (Export)
    def mock_export(mgr):
        return [
            {"id": s.id, "text": s.text} for s in mgr.segments
        ]
        
    saved_initial = mock_export(manager)
    print(f"Saved Initial: {json.dumps(saved_initial)}")
    
    # 3. Delete Segment 1
    print("\n--- Deleting Segment 1 ---")
    removed = manager.delete_segments([seg1.id])
    print(f"Removed IDs: {removed}")
    
    print(f"Count after Delete: {len(manager.segments)}")
    assert len(manager.segments) == 1
    assert manager.segments[0].id == seg2.id
    
    # 4. Mock Save (Export) - CHECK PERISTENCE OF DELETE
    saved_after_delete = mock_export(manager)
    print(f"Saved After Delete: {json.dumps(saved_after_delete)}")
    
    found_id = next((s["id"] for s in saved_after_delete if s["id"] == seg1.id), None)
    if found_id:
        print("[FAIL] Deleted segment still found in export!")
    else:
        print("[PASS] Deleted segment NOT found in export.")
        
    # 5. Undo
    print("\n--- Undo Delete ---")
    manager.undo()
    print(f"Count after Undo: {len(manager.segments)}")
    assert len(manager.segments) == 2
    
    # 6. Mock Save (Export) - CHECK RESTORATION
    saved_after_undo = mock_export(manager)
    print(f"Saved After Undo: {json.dumps(saved_after_undo)}")
    
    found_id_undo = next((s["id"] for s in saved_after_undo if s["id"] == seg1.id), None)
    if found_id_undo:
        print("[PASS] Undo restored segment in export.")
    else:
        print("[FAIL] Undo did NOT restore segment in export!")

if __name__ == "__main__":
    test_persistence_logic()
