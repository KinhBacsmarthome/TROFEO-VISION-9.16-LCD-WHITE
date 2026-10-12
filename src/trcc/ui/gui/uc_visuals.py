"""The Audio visuals gallery -- the Eq tab, right of Cloud backgrounds.

The same grid and tiles as the cloud backgrounds: one tile per visual (and
per preset), each the App's picture of what that visual draws for noise
(``VisualTile``), so a tile looks the same whatever is playing.  Clicking
one makes it the panel's background; picking any other background ends it.

The tiles are drawn by the App, one at a time and off the GUI thread, so
the window never waits on a visual.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap

from ...core.commands import ListVisuals, VisualTile
from ...core.results import VisualsResult
from ..presentation.visual_gallery import VisualItem, gallery_items
from ..qt_background import dispatch_in_background
from ..qt_visual_tiles import VisualTiles
from .base import BaseThemeBrowser, BaseThumbnail
from .constants import Sizes

if TYPE_CHECKING:
    from PySide6.QtGui import QShowEvent

    from ...core.ports import CommandBus

log = logging.getLogger(__name__)


class VisualThumbnail(BaseThumbnail):
    """A visual's tile: its name until the App's picture of it arrives."""

    def _get_display_name(self, info: VisualItem) -> str:
        log.debug("VisualThumbnail._get_display_name: %s", info.name)
        return info.name

    def _get_image_path(self, info: VisualItem) -> str | None:
        """None: the picture comes from the App, not a file."""
        log.debug("VisualThumbnail._get_image_path: %s draws its own", info.name)
        return None

    def show_tile(self, png: bytes) -> None:
        """The App's picture of this visual, in place of the placeholder."""
        image = QImage.fromData(png)
        if image.isNull():
            log.warning("VisualThumbnail.show_tile: %s -- not a picture "
                        "(%d bytes)", self.item_info.name, len(png))
            return
        log.debug("VisualThumbnail.show_tile: %s", self.item_info.name)
        self.thumb_label.setPixmap(QPixmap.fromImage(image.scaled(
            Sizes.THUMB_IMAGE, Sizes.THUMB_IMAGE,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)))


class UCVisuals(BaseThemeBrowser):
    """The Audio visuals gallery.  ``theme_selected`` carries the clicked
    :class:`VisualItem`."""

    def __init__(self, parent=None, app: CommandBus | None = None) -> None:
        log.info("UCVisuals.__init__: app=%s", app is not None)
        self._app = app
        self._listed = False
        self._tiles = (VisualTiles(app, self._tile_query, self._show_tile)
                       if app is not None else None)
        super().__init__(parent)

    def _create_thumbnail(self, item_info: VisualItem) -> VisualThumbnail:
        log.debug("UCVisuals._create_thumbnail: %s", item_info.name)
        return VisualThumbnail(item_info)

    def _no_items_message(self) -> str:
        log.debug("UCVisuals._no_items_message")
        return "No audio visuals on this computer"

    def showEvent(self, event: QShowEvent) -> None:
        """The gallery is listed the first time it is shown."""
        log.debug("UCVisuals.showEvent: listed=%s", self._listed)
        super().showEvent(event)
        if not self._listed:
            self.refresh()

    def refresh(self) -> None:
        """Ask the App for the visuals; the tiles follow, one at a time."""
        if self._app is None:
            log.warning("UCVisuals.refresh: no App -- nothing to list")
            return
        log.info("UCVisuals.refresh")
        self._listed = True
        dispatch_in_background(self._app, ListVisuals(), self._on_listed)

    def _on_listed(self, listed: VisualsResult) -> None:
        log.info("UCVisuals._on_listed: ok=%s %s", listed.ok, listed.message)
        self._clear_grid()
        items = gallery_items(listed) if listed.ok else []
        self._populate_grid(items)
        if self._tiles is not None:
            self._tiles.fetch(items)

    def _tile_query(self, item: VisualItem) -> VisualTile:
        """The Query for *item*'s tile, at this grid's tile size."""
        log.debug("UCVisuals._tile_query: %s", item.name)
        return VisualTile(visual=item.visual, preset=item.preset,
                          size=Sizes.THUMB_IMAGE)

    def _show_tile(self, item: VisualItem, png: bytes) -> None:
        """A tile arrived from the App: onto its thumbnail."""
        log.debug("UCVisuals._show_tile: %s", item.name)
        for widget in self.item_widgets:
            if isinstance(widget, VisualThumbnail) and widget.item_info == item:
                widget.show_tile(png)
