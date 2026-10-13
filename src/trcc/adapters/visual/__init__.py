"""Audio visuals: what the computer is playing, turned into a panel's
background.  Importing the package registers every visual in ``VISUALS``."""
from . import cavalier, pipes, projectm  # noqa: F401 -- each class registers itself
from ._base import VISUALS, BaseVisual, catalog

__all__ = ["VISUALS", "BaseVisual", "catalog"]
