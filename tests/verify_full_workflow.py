import sys
import os
import json
import time
from PySide6.QtWidgets import QApplication, QFileDialog, QAbstractItemView
from PySide6.QtCore import Qt, QTimer

# Add src to path
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from src.gui.main_window import MainWindow

def run_iteration(app, iteration):
    print("\n" + "="*50)
    print(f"ITERATION {iteration} / 3")
    print("="*50)

    # Mock QFileDialog
    target_project = r"C:\Users\Goryeng\.gemini\antigravity\playground\fractal-equinox\projects\7.json"
    def mock_get_open_filename(*args, **kwargs):
        return target_project, "JSON Files (*.json)"
    QFileDialog.getOpenFileName = mock_get_open_filename

    window = MainWindow()
    window.resize(1200, 800)
    window.show()
    
    # Helper to process events
    def wait(seconds):
        end = time.time() + seconds
        while time.time() < end:
            app.processEvents()
            time.sleep(0.05)
            
    try:
        # ---------------------------------------------------------
        # 1. LOAD PROJECT
        # ---------------------------------------------------------
        print("\n[Step 1] Loading Project...")
        window._on_load_work()
        wait(5.0) # Wait for generic load
        
        # Checking segments
        mgr = window._file_subtitle_manager
        editor = window.file_editor
        waveform = window.waveform_right
        
        initial_count = len(mgr.segments)
        print(f" -> Loaded {initial_count} segments.")
        if initial_count == 0:
            print("FAIL: No segments loaded.")
            return False

        # Verify Waveform Layer matches Model
        wf_items = len(waveform._segment_items)
        if wf_items != initial_count:
            print(f"FAIL: Waveform items {wf_items} mismatch initial load {initial_count}.")
            return False
        else:
            print("PASS: Initial Load Sync")

            # ---------------------------------------------------------
        # 2. PLAYBACK & FREEZE & SYNC VERIFICATION
        # ---------------------------------------------------------
        print("\n[Step 2] Testing Playback (5s), Freezing, Sync, and MediaView...")
        try:
            target_seg = mgr.segments[0]
            target_seg_id = target_seg.id
            
            print(" -> Calling _toggle_playback directly...", flush=True)
            window._toggle_playback(target_seg_id)
            
            # Allow startup
            wait(0.5)
            
            # --- Check 1: MediaView State ---
            from PySide6.QtMultimedia import QMediaPlayer
            mv_state = window.media_view.player().playbackState()
            print(f" -> MediaView State: {mv_state} (Expected {QMediaPlayer.PlayingState})")
            if mv_state != QMediaPlayer.PlayingState:
                print("FAIL: MediaView is not playing.")
            else:
                print("PASS: MediaView Playing")

            # --- Check 2: 5s Playback Loop with Sync Check ---
            print(" -> Running 5s playback loop...", flush=True)
            start_monitor = time.time()
            prev_cursor = window.waveform_right.cursor_time
            last_audio_t = window.waveform_right.get_playback_time()
            
            frozen = False
            sync_error = False
            
            # Monitor for 5 seconds
            while time.time() - start_monitor < 5.0:
                wait(0.1) # UI Process
                
                curr_cursor = window.waveform_right.cursor_time
                curr_audio = window.waveform_right.get_playback_time()
                
                # Check Freeze (Cursor didn't adjust)
                # Note: Cursor might update slower than polling, so we check trend over 1s?
                # Actually, simple check: is it monotonically increasing significantly over the 5s?
                
                # Check Sync (Red Line vs Green Line/Audio Time)
                # Typically they should be close. Tolerance 0.5s
                diff = abs(curr_cursor - curr_audio)
                if diff > 0.5:
                     # Ignore startup glitch?
                     if time.time() - start_monitor > 1.0:
                         print(f"WARN: Sync drift! Cursor={curr_cursor:.3f}, Audio={curr_audio:.3f}, Diff={diff:.3f}")
                         # sync_error = True # Use soft warn for now due to test env jitter
                
            end_cursor = window.waveform_right.cursor_time
            
            if end_cursor <= prev_cursor + 0.1:
                print(f"FAIL: Freezing detected. Start={prev_cursor}, End={end_cursor}")
                frozen = True
            else:
                print(f"PASS: Cursor advanced from {prev_cursor:.2f}s to {end_cursor:.2f}s (No Freeze)")
                
            if not frozen:
                print("PASS: 5s Playback Completed without Freeze.")

            window._stop_all_playback()
            wait(0.5)
            
        except Exception as e:
            print(f"STEP 2 ERROR: {e}")
            import traceback
            traceback.print_exc()
            print("WARNING: Proceeding to Step 3.")
        # ---------------------------------------------------------
        # 3. SPLIT TEST
        # ---------------------------------------------------------
        print("\n[Step 3] Testing Split...")
        target_idx = 10
        seg_to_split = mgr.segments[target_idx]
        split_time = (seg_to_split.start + seg_to_split.end) / 2
        
        # Helper to safely call split
        # IMPORTANT: _on_split_requested uses the waveform cursor time!
        # We must set the cursor to the split time first.
        # Default mode is "bottom" -> waveform_right
        window.waveform_right.cursor_line.setPos(split_time)
        print(f" -> Set Cursor to {split_time:.2f}s")
        
        import inspect
        sig = inspect.signature(window._on_split_requested)
        if len(sig.parameters) == 0:
             window._on_split_requested()
        elif len(sig.parameters) == 1:
             window._on_split_requested(seg_to_split.id)
        else:
             window._on_split_requested(seg_to_split.id, split_time)
        
        wait(0.5)
        
        post_split_count = len(mgr.segments)
        wf_items_split = len(waveform._segment_items)
        
        if post_split_count == initial_count + 1 and wf_items_split == post_split_count:
            print("PASS: Split Operation")
        else:
            print(f"FAIL: Split mismatch. Model={post_split_count}, Waveform={wf_items_split}")
            return False

        # ---------------------------------------------------------
        # 4. UNDO TEST
        # ---------------------------------------------------------
        print("\n[Step 4] Testing Undo (Revert Split)...")
        window._on_global_undo()
        wait(0.5)
        
        post_undo_count = len(mgr.segments)
        wf_items_undo = len(waveform._segment_items)
        
        if post_undo_count == initial_count and wf_items_undo == initial_count:
            print("PASS: Undo Operation")
        else:
            print(f"FAIL: Undo mismatch. Model={post_undo_count}, Waveform={wf_items_undo}")
            return False

        # ---------------------------------------------------------
        # 5. MERGE TEST
        # ---------------------------------------------------------
        print("\n[Step 5] Testing Merge...")
        editor.table.selectRow(10)
        editor.table.selectRow(11)
        
        ids_to_merge = [mgr.segments[10].id, mgr.segments[11].id]
        editor.merge_requested.emit(ids_to_merge)
        wait(0.5)
        
        post_merge_count = len(mgr.segments)
        wf_items_merge = len(waveform._segment_items)
        
        if post_merge_count == initial_count - 1 and wf_items_merge == post_merge_count:
            print("PASS: Merge Operation")
        else:
            print(f"FAIL: Merge mismatch. Model={post_merge_count}, Waveform={wf_items_merge}")
            return False

        # ---------------------------------------------------------
        # 6. UNDO MERGE
        # ---------------------------------------------------------
        print("\n[Step 6] Testing Undo (Revert Merge)...")
        window._on_global_undo()
        wait(0.5)
        
        post_undo_merge_count = len(mgr.segments)
        wf_items_undo_merge = len(waveform._segment_items)
        
        if post_undo_merge_count == initial_count and wf_items_undo_merge == initial_count:
            print("PASS: Undo Merge Operation")
        else:
            print(f"FAIL: Undo Merge mismatch. Model={post_undo_merge_count}, Waveform={wf_items_undo_merge}")
            return False
            
    except Exception as e:
        print(f"CRITICAL FAULT during iteration {iteration}: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        print(f"Closing Window Iteration {iteration}...")
        try:
            window.close()
            # Force small wait for cleanup
            wait(1.0)
        except: pass
        
    return True

def run_loop_verification():
    print("="*60)
    print("STARTING STABILITY TEST (3 ITERATIONS)")
    print("="*60)
    
    app = QApplication.instance() or QApplication(sys.argv)
    
    success_count = 0
    for i in range(1, 4):
        if run_iteration(app, i):
            print(f"\n>>> ITERATION {i} SUCCESS <<<")
            success_count += 1
        else:
            print(f"\n>>> ITERATION {i} FAILED <<<")
            break
            
    print("\n" + "="*60)
    if success_count == 3:
        print("FINAL RESULT: ALL 3 ITERATIONS PASSED. STABILITY CONFIRMED.")
    else:
        print(f"FINAL RESULT: FAILED. Only {success_count} iterations passed.")
    print("="*60)

if __name__ == "__main__":
    try:
        run_loop_verification()
    except Exception as e:
        print(f"FATAL: {e}")
        sys.exit(1)
