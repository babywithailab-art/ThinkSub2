import sys
import os
import datetime
import traceback
import subprocess

def main():
    log_file = os.path.join(os.path.dirname(__file__), "crash_log.txt")
    
    with open(log_file, "w", encoding="utf-8") as f:
        f.write(f"=== Debug Session Started at {datetime.datetime.now()} ===\n")
        f.write(f"Python: {sys.version}\n")
        f.flush()
        
        # Run main.py as subprocess to capture C-level output if possible (though difficult on Windows)
        # We use python -u for unbuffered output
        cmd = [sys.executable, "-u", "src/main.py"]
        
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        
        try:
            process = subprocess.Popen(
                cmd, 
                stdout=subprocess.PIPE, 
                stderr=subprocess.STDOUT, 
                text=True, 
                bufsize=1, 
                cwd=os.path.dirname(__file__)
            )
            
            # Stream output to file
            for line in process.stdout:
                print(line, end="") # Echo to console
                f.write(line)
                f.flush()
                
            process.wait()
            f.write(f"\n=== Process Exited with Code {process.returncode} ===\n")
            
        except Exception as e:
            f.write(f"\n=== Launcher Error: {e} ===\n")
            traceback.print_exc(file=f)

if __name__ == "__main__":
    main()
