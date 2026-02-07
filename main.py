"""
ThinkSub2 - Real-time Subtitle Generation Application
Entry point for the application.
"""

import sys
import os
import multiprocessing

# Add project root to Python path for imports
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

from src.gui.main_window import MainWindow
from src.gui import i18n


def main():
    # Required for multiprocessing on Windows
    multiprocessing.freeze_support()

    # Enable High DPI scaling
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName("ThinkSub2")
    app.setOrganizationName("ThinkSub")

    i18n.install_translator(i18n.get_lang())

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
