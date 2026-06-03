# HWP parent isolation worker. ASCII output.
# Each run = 1 worker: new Hwp instance -> hold (concurrent overlap window) ->
# independent edit (own marker) -> verify read-back. Launcher snapshots hwp.exe count mid-hold.
# Usage: python scripts/hwp_isolation_test.py <worker_id>
import sys, os, time

def main():
    wid = sys.argv[1] if len(sys.argv) > 1 else "0"
    t0 = time.time()
    try:
        from pyhwpx import Hwp
    except Exception as e:
        print(f"WORKER {wid} IMPORT_FAIL {e}", flush=True); return 2
    hwp = None
    try:
        hwp = Hwp(new=True, visible=False)
        print(f"WORKER {wid} UP py_pid={os.getpid()} t={time.time()-t0:.1f}s", flush=True)
        time.sleep(12)  # hold so K workers overlap; launcher snapshots hwp.exe count here
        # independent control: write own marker, read back; isolation => only own marker
        hwp.HAction.Run("MoveDocBegin")
        marker = f"ISO-{wid}-{os.getpid()}"
        try:
            hwp.insert_text(marker)
        except Exception:
            pass
        hwp.HAction.Run("SelectAll")
        got = (hwp.get_selected_text() or "")
        print(f"WORKER {wid} EDIT_OK={marker in got} py_pid={os.getpid()} t={time.time()-t0:.1f}s", flush=True)
        return 0
    except Exception as e:
        print(f"WORKER {wid} FAIL {type(e).__name__}: {str(e)[:120]} t={time.time()-t0:.1f}s", flush=True)
        return 1
    finally:
        try:
            if hwp is not None: hwp.quit()
        except Exception:
            pass

if __name__ == "__main__":
    sys.exit(main())
