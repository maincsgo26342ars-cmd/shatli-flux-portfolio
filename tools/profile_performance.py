"""Read-only Qt performance probe with isolated user data; never applies tweaks.

Measures this Python/Qt process, not child PowerShell CPU or GPU utilization.
Run the same command before/after with the same display settings.
"""
import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import sys
import tempfile
import time


def memory_mib():
    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in (
                "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage",
                "PagefileUsage", "PeakPagefileUsage", "PrivateUsage")]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        raise ctypes.WinError(ctypes.get_last_error())
    return {"working_set_mib": round(counters.WorkingSetSize / 2**20, 2),
            "private_mib": round(counters.PrivateUsage / 2**20, 2)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seconds", type=int, default=10, choices=range(3, 61))
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Windows only")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    runtime = Path(__file__).resolve().parents[1] / "runtime"
    runtime.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="shatli-performance-", dir=runtime) as folder:
        os.environ["APPDATA"] = folder
        start_wall, start_cpu = time.perf_counter(), time.process_time()
        from PySide6.QtCore import QEvent, QEventLoop, QObject, QTimer
        from app.main import create_application
        import app.ui.window as ui

        class PaintCounter(QObject):
            count = 0

            def eventFilter(self, watched, event):
                if event.type() == QEvent.Type.Paint:
                    self.count += 1
                return False

        def wait(milliseconds):
            loop = QEventLoop()
            QTimer.singleShot(milliseconds, loop.quit)
            loop.exec()

        probes, errors = [], []
        original = ui.read_system_info

        def read(config):
            probes.append(time.perf_counter())
            return original(config)

        ui.read_system_info = read
        ui.MainWindow.operation_error = lambda self, message: errors.append(message)
        app, config = create_application()
        counter = PaintCounter()
        app.installEventFilter(counter)
        window = ui.MainWindow(config)
        window.show()
        wait(100)
        deadline = time.monotonic() + 50
        while window.jobs and time.monotonic() < deadline:
            wait(100)
        if window.jobs:
            raise RuntimeError("Startup jobs did not finish within 50 seconds")
        result = {"platform": os.environ["QT_QPA_PLATFORM"],
                  "startup_wall_seconds": round(time.perf_counter() - start_wall, 3),
                  "startup_cpu_seconds": round(time.process_time() - start_cpu, 4),
                  "startup_diagnostic_probes": len(probes)}

        def measure(action):
            cpu, wall, paints = time.process_time(), time.perf_counter(), counter.count
            action()
            elapsed = time.perf_counter() - wall
            used = time.process_time() - cpu
            return {"wall_seconds": round(elapsed, 3), "cpu_seconds": round(used, 4),
                    "cpu_percent_one_core": round(100 * used / elapsed, 3),
                    "paint_events": counter.count - paints, **memory_mib()}

        result["idle"] = measure(lambda: wait(args.seconds * 1000))

        def navigate():
            # Exclude diagnostics: isolate rendering from on-demand system probes.
            for index in [2, 3, 4, 5, 6, 7, 8, 9, 0] * 3:
                window.set_tab(index)
                wait(250)

        result["navigation_27_switches"] = measure(navigate)
        window.showMinimized()
        wait(300)
        result["minimized"] = measure(lambda: wait(args.seconds * 1000))
        result["errors"] = errors
        window.close()
        app.processEvents()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
