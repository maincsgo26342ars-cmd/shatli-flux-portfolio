"""Developer-only real Qt widget screenshots, no mock rendering."""
import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtTest import QTest
from app.main import create_application
from app.ui.pages import SecurityDialog, TABS
from app.ui.window import MainWindow


def main():
    output = Path(sys.argv[1])
    output.mkdir(parents=True, exist_ok=True)
    app, config = create_application()
    window = MainWindow(config)
    window.show()
    for _ in range(100):
        QTest.qWait(100)
        if not window.jobs:
            break
    window.grab().save(str(output / "Shatli-FLUX-home.png"))
    names = ("home", "diagnostics", "fps-test", "game", "system", "input",
             "power", "privacy", "help", "profile")
    targets = list(enumerate(names))
    for size in ((1100, 720), (960, 640)):
        window.resize(*size)
        for index, name in targets:
            window.set_tab(index)
            scroll = window.stack.widget(index)
            scroll.verticalScrollBar().setValue(0)
            QTest.qWait(220)
            window.grab().save(str(output / f"{name}-{size[0]}x{size[1]}.png"))
            if name == "diagnostics":
                scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
                QTest.qWait(100)
                window.grab().save(str(output / f"system-benchmark-{size[0]}x{size[1]}.png"))
    dialog = SecurityDialog(config, window)
    dialog.show()
    QTest.qWait(100)
    dialog.grab().save(str(output / "security.png"))
    dialog.close()
    # The capture run may leave background jobs and timers alive; closing the
    # window would show the "operation in progress" dialog and block exit.
    window.hide()
    os._exit(0)


if __name__ == "__main__":
    main()
