"""Application entry point: QApplication setup, save selector, and MainWindow launch."""
import sys
import os
import logging
from typing import Optional

from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
from PySide6.QtGui import QColor, QPalette

from mewgenics.utils.paths import APP_VERSION
from mewgenics.utils.config import _saved_default_save, find_save_files
from mewgenics.utils.game_data import _GPAK_PATH, get_gpak_path
from mewgenics.dialogs import SaveSelectorDialog
from mewgenics.main_window import MainWindow, _ensure_gpak_path_interactive

logger = logging.getLogger("mewgenics")


def main():
    # Configure logging
    log_level_name = os.environ.get("MEWGENICS_LOG_LEVEL", "INFO").strip().upper()
    log_level = getattr(logging, log_level_name, logging.INFO)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler()],
    )
    logger.info("Mewgenics Breeding Manager %s starting", APP_VERSION)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    pal = QPalette()
    pal.setColor(QPalette.Window,          QColor(13,  13,  28))
    pal.setColor(QPalette.WindowText,      QColor(220, 220, 230))
    pal.setColor(QPalette.Base,            QColor(18,  18,  36))
    pal.setColor(QPalette.AlternateBase,   QColor(20,  20,  40))
    pal.setColor(QPalette.Text,            QColor(220, 220, 230))
    pal.setColor(QPalette.Button,          QColor(22,  22,  46))
    pal.setColor(QPalette.ButtonText,      QColor(200, 200, 210))
    pal.setColor(QPalette.Highlight,       QColor(30,  48, 100))
    pal.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
    pal.setColor(QPalette.ToolTipBase,     QColor(20,  20,  40))
    pal.setColor(QPalette.ToolTipText,     QColor(220, 220, 230))
    app.setPalette(pal)

    # Keep Qt initialized before showing dialogs on some Linux setups.
    from PySide6 import QtWidgets
    QtWidgets.QMessageBox()

    if not _GPAK_PATH:
        QMessageBox.information(
            None,
            "Locate Mewgenics",
            "Ability and mutation descriptions need the game's resources.gpak.\n"
            "If the app needs you to browse for it, the chooser will start from your configured save root first.",
        )
        _ensure_gpak_path_interactive()

    # Extract DefinedShape PNGs from catparts.swf if the cache is empty.
    #ensure_defined_shapes(get_gpak_path())

    # Open directly only when a valid default save exists; otherwise always show the save selector.
    default_save = _saved_default_save()
    initial_save: Optional[str] = default_save if default_save and os.path.isfile(default_save) else None

    if initial_save is None:
        saves = find_save_files()
        dlg = SaveSelectorDialog(saves)
        if dlg.exec() == QDialog.Accepted:
            initial_save = dlg.selected_path
        else:
            return 0

    win = MainWindow(initial_save=initial_save, use_saved_default=False)
    win.show()
    return app.exec()
