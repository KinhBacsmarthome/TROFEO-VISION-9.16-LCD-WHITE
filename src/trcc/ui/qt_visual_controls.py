"""The playing audio visual's controls, for either window.

Under the gallery: one slider per adjustment the visual declares (its
``VisualInfo.adjustments`` -- TRCC Pipes has Growth, Thickness, Centre
picture and Fire), and for a visual that shows a centre picture, a button to
pick any image and one to take it away.  A slider changes the
visual LIVE when it is let go (``SetVisualSettings``): the visual keeps
playing.  What the controls show comes from ``ListVisuals(key=...)``, so
they show the same truth as every other UI, and follow a change another UI
made.

The window builds the Commands, as the RGB page's skins do: *sliders* turns
``{name: value}`` into its panel's ``SetVisualSettings``, *center* turns a
picture path into ``SetVisualCenter``.  Each window styles the widget.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..core.commands import Command
from ..core.models import VisualAdjustment, VisualInfo
from ..core.ports import CommandBus
from ..core.results import Result, VisualsResult
from .qt_background import dispatch_in_background

log = logging.getLogger(__name__)

#: Slider steps between an adjustment's minimum and maximum.
_STEPS = 100
#: What the picture dialog offers.
_PICTURES = "Pictures (*.png *.jpg *.jpeg *.bmp *.gif *.webp)"


class VisualControls(QWidget):
    """Sliders and the centre picture for the visual a panel plays."""

    def __init__(self, bus: CommandBus,
                 sliders: Callable[[dict[str, float]], Command[Any]],
                 center: Callable[[str], Command[Any]],
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        log.debug("VisualControls.__init__")
        self._bus, self._sliders, self._center = bus, sliders, center
        self._adjustments: dict[str, VisualAdjustment] = {}
        self._picture = ""
        self._values: dict[str, QLabel] = {}
        self._rows = QGridLayout()
        self._rows.setColumnStretch(1, 1)
        self._hint = QLabel(self)
        self._hint.setWordWrap(True)
        self._pick = QPushButton("Centre picture…", self)
        self._pick.clicked.connect(self._on_pick_picture)
        self._none = QPushButton("No picture", self)
        self._none.clicked.connect(self._on_no_picture)
        pictures = QHBoxLayout()
        pictures.addWidget(self._pick)
        pictures.addWidget(self._none)
        pictures.addStretch(1)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addLayout(self._rows)
        root.addLayout(pictures)
        root.addWidget(self._hint)
        self.show_for(VisualsResult(ok=True))

    def show_for(self, listed: VisualsResult) -> None:
        """The controls of the visual *listed* says the panel plays."""
        info: VisualInfo | None = next(
            (v for v in listed.visuals if v.kind == listed.playing), None)
        log.info("VisualControls.show_for: %s %s", listed.playing or "-",
                 dict(listed.sliders))
        while self._rows.count():
            item = self._rows.takeAt(0)
            if item is not None and (widget := item.widget()) is not None:
                widget.deleteLater()
        self._adjustments, self._values = {}, {}
        self._picture = listed.center
        for row, adjustment in enumerate(info.adjustments if info else ()):
            self._add_slider(row, adjustment, listed.sliders.get(
                adjustment.name, adjustment.default))
        centred = info is not None and info.uses_center
        self._pick.setVisible(centred)
        self._none.setVisible(centred)
        self._hint.setText(
            "Pick an audio visual above -- its sliders show here."
            if info is None else
            "Hearing nothing -- play some music.  If music IS playing, TRCC "
            "cannot hear this computer's sound output."
            if not listed.hearing else
            "Let go of a slider and the visual changes as it plays.")

    def _add_slider(self, row: int, adjustment: VisualAdjustment,
                    value: float) -> None:
        log.debug("VisualControls._add_slider: %s = %s", adjustment.name, value)
        slider = QSlider(Qt.Orientation.Horizontal, self)
        slider.setRange(0, _STEPS)
        slider.setTracking(False)
        slider.setValue(round((value - adjustment.minimum)
                              / (adjustment.maximum - adjustment.minimum)
                              * _STEPS))
        slider.setProperty("adjustment", adjustment.name)
        slider.sliderMoved.connect(self._on_moved)
        slider.valueChanged.connect(self._on_let_go)
        shown = QLabel(f"{value:.2f}", self)
        self._adjustments[adjustment.name] = adjustment
        self._values[adjustment.name] = shown
        name = QLabel(adjustment.label, self)
        for column, widget in enumerate((name, slider, shown)):
            self._rows.addWidget(widget, row, column)
            # Shown now: Qt would show a new child on the next event-loop
            # pass, and a layout leaves hidden widgets out of its height --
            # the window sized the settings at 70 px for 214 px of sliders.
            widget.show()

    def _value(self, name: str, step: int) -> float:
        adjustment = self._adjustments[name]
        value = (adjustment.minimum
                 + (adjustment.maximum - adjustment.minimum) * step / _STEPS)
        log.debug("VisualControls._value: %s step %d -> %.3f", name, step, value)
        return value

    def _on_moved(self, step: int) -> None:
        """While dragging: the number follows the thumb."""
        name = str(self.sender().property("adjustment"))
        log.debug("VisualControls._on_moved: %s %d", name, step)
        self._values[name].setText(f"{self._value(name, step):.2f}")

    def _on_let_go(self, step: int) -> None:
        """Let go: the visual takes the value, live."""
        name = str(self.sender().property("adjustment"))
        value = self._value(name, step)
        log.info("VisualControls._on_let_go: %s = %.3f", name, value)
        self._values[name].setText(f"{value:.2f}")
        dispatch_in_background(self._bus, self._sliders({name: value}),
                               self._on_answer)

    def _on_pick_picture(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Centre picture",
                                              self._picture_folder(), _PICTURES)
        log.info("VisualControls._on_pick_picture: %r", path)
        if path:
            dispatch_in_background(self._bus, self._center(path),
                                   self._on_answer)

    def _picture_folder(self) -> str:
        """Where the picker opens: the current picture's folder, else the
        user's Pictures -- never wherever TRCC happened to be started."""
        from PySide6.QtCore import QStandardPaths
        where = QStandardPaths.StandardLocation
        folder = (str(Path(self._picture).parent) if self._picture else
                  QStandardPaths.writableLocation(where.PicturesLocation)
                  or QStandardPaths.writableLocation(where.HomeLocation))
        log.debug("VisualControls._picture_folder: %s", folder)
        return folder

    def _on_no_picture(self) -> None:
        log.info("VisualControls._on_no_picture")
        dispatch_in_background(self._bus, self._center(""), self._on_answer)

    def _on_answer(self, result: Result) -> None:
        """A refusal shows here; success shows on the panel itself."""
        log.info("VisualControls._on_answer: ok=%s %s", result.ok,
                 result.message)
        if not result.ok:
            self._hint.setText(result.message)
