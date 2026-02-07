
import time
import numpy as np
from dataclasses import dataclass

# Mock Structures
@dataclass
class AudioChunk:
    data: np.ndarray
    start_time: float
    rms: float

class MockMainWindow:
    def __init__(self):
        self._anchor_timestamp = None
        self._state = "RECORDING"
        self.logs = []

    def on_audio_chunk(self, chunk):
        if self._anchor_timestamp is None:
            self._anchor_timestamp = chunk.start_time
            print(f"Anchor Set: {self._anchor_timestamp}")
        
        rel_time = chunk.start_time - self._anchor_timestamp
        self.logs.append(rel_time)
        return rel_time

# Simulation
def test_delayed_start():
    print("Testing Delayed Start Logic...")
    
    # 1. "Application Start"
    session_start_time = 1000.0 # sim time.time()
    
    # 2. Audio Recorder Starts (T=0 relative to session)
    # Simulator hardware callbacks
    adc_bases = 50000.0 # hardware clock
    
    mw = MockMainWindow()
    
    # Simulate LOADING phase (chunks arrive but dropped)
    # Let's say loading takes 4 seconds.
    # Chunks: 0s, 0.1s, ... 3.9s
    
    chunks = []
    for i in range(40): # 4 seconds
        elapsed = i * 0.1
        chunk_time = session_start_time + elapsed
        # In real app, these are dropped.
        pass
        
    print("Model Loaded (T=4s). State=RECORDING.")
    
    # 3. First Chunk AFTER Load
    # i = 40 (T=4.0s)
    current_elapsed = 4.0
    chunk_time = session_start_time + current_elapsed
    chunk = AudioChunk(np.zeros(1600), chunk_time, 0.0)
    
    # MainWindow sees this
    rel_time = mw.on_audio_chunk(chunk)
    print(f"First Chunk RelTime: {rel_time} (Expected 0.0)")
    
    if abs(rel_time - 0.0) < 0.001:
        print("PASS: First chunk is 0.0")
    else:
        print(f"FAIL: First chunk is {rel_time}")

    # 4. User waits 4 more seconds (T=8s)
    # i = 80
    current_elapsed = 8.0
    chunk_time = session_start_time + current_elapsed
    chunk = AudioChunk(np.zeros(1600), chunk_time, 0.0)
    
    rel_time = mw.on_audio_chunk(chunk)
    print(f"Speech Chunk RelTime: {rel_time} (Expected 4.0)")
    
    if abs(rel_time - 4.0) < 0.001:
        print("PASS: Speech chunk is 4.0")
    else:
        print(f"FAIL: Speech chunk is {rel_time}")

if __name__ == "__main__":
    test_delayed_start()
