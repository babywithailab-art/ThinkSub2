
import os
import io
import sys
import zipfile
import shutil
import urllib.request as request

# Reliable URL for Windows LGPL-compatible build (Gyan.dev or similar)
# Using Gyan.dev shared build (LGPL) or Essentials (GPL). 
# Wait, Gyan's "essentials" are GPL. "full" is GPL.
# "shared" builds are LGPL compliant IF we only use the DLLs? No, Gyan's builds are often GPL unless specified.
# However, BtbN's builds on GitHub have "lgpl" variants.
# URL: https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl-shared.zip 
# No, we need LGPL.
# URL: https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-lgpl.zip
# or "ffmpeg-master-latest-win64-lgpl-shared.zip".
# Let's use the static lgpl build to have a single exe if possible, or usually lgpl requires shared libs.
# Actually, static linking LGPL usually requires object distribution.
# Dynamic linking (shared) is safer for LGPL compliance in a closed app.
# But for simplicity of "single exe" bundle, we usually just put ffmpeg.exe next to it.
# If ffmpeg.exe itself is an LGPL build, that's a separate process, so it's fine regardless of static/shared, 
# because we are just calling it via subprocess (Command Line Interface), not linking against it.
# "System exec" exception implies we can invoke the binary.
# Use https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-lgpl.zip

FFMPEG_URL = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-lgpl.zip"
TARGET_DIR = os.path.join("src", "bin")

def install_ffmpeg():
    print(f"Downloading FFmpeg (LGPL) from {FFMPEG_URL}...")
    try:
        if not os.path.exists(TARGET_DIR):
            os.makedirs(TARGET_DIR)

        # Download
        with request.urlopen(FFMPEG_URL) as response:
            data = response.read()
    except Exception as e:
        print(f"Failed to download: {e}")
        return

    print("Download complete. Extracting...")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            # Find ffmpeg.exe in the zip
            ffmpeg_path = None
            for name in z.namelist():
                if name.endswith("ffmpeg.exe"):
                    ffmpeg_path = name
                    break
            
            if not ffmpeg_path:
                print("ffmpeg.exe not found in zip!")
                return

            # Extract to target
            with z.open(ffmpeg_path) as zf, open(os.path.join(TARGET_DIR, "ffmpeg.exe"), "wb") as f:
                shutil.copyfileobj(zf, f)
            
            print(f"Success! ffmpeg.exe installed to {TARGET_DIR}")

    except Exception as e:
        print(f"Extraction failed: {e}")

if __name__ == "__main__":
    install_ffmpeg()
