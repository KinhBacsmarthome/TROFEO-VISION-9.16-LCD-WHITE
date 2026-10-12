"""projectM drawing an audio visual off-screen, on Linux.

projectM is a MilkDrop engine: fed sound, it draws a preset with OpenGL.  It
draws its finished picture into framebuffer 0 -- "the surface's own buffer"
-- so it needs a surface with one and no window: an EGL *pbuffer*.  Each
picture is read back with ``glReadPixels`` and handed on as RGB24, top row
first, like every other frame TRCC sends.

libprojectM 4's C API is called through ``ctypes``; it has been stable since
4.0 (``docs``: "the API will remain stable ... until the next major
release").  Every call comes from one thread: an OpenGL context belongs to
the thread that made it current.

**Credit.**  projectM (https://github.com/projectM-visualizer/projectm,
LGPL-2.1-or-later), by the projectM team (see its ``AUTHORS.txt``) -- a
reimplementation of Ryan Geiss's MilkDrop.  TRCC links to it as a library and
does not modify it.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import logging
from pathlib import Path

from ...core._safe import is_under
from ...core.logs import per_frame
from ...core.models import (
    SOUND_CHANNELS,
    VISUAL_FPS,
    RawFrame,
    VisualAdjustment,
    VisualInfo,
    VisualSpec,
)
from ._base import BaseVisual

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

#: A MilkDrop preset file.
PRESET_SUFFIX = ".milk"
#: libprojectM 4's names: what ``find_library`` resolves, then the soname.
_LIBRARY = ("projectM-4", "libprojectM-4.so.4")

_EGL_PBUFFER_BIT, _EGL_OPENGL_BIT, _EGL_OPENGL_API = 0x1, 0x8, 0x30A2
_EGL_SURFACE_TYPE, _EGL_RENDERABLE_TYPE = 0x3033, 0x3040
_EGL_RED, _EGL_GREEN, _EGL_BLUE, _EGL_ALPHA = 0x3024, 0x3023, 0x3022, 0x3021
_EGL_DEPTH, _EGL_WIDTH, _EGL_HEIGHT, _EGL_NONE = 0x3025, 0x3057, 0x3056, 0x3038
_EGL_MAJOR, _EGL_MINOR, _EGL_PROFILE, _EGL_CORE = 0x3098, 0x30FB, 0x30FD, 0x1
_GL_RGB, _GL_UNSIGNED_BYTE, _GL_PACK_ALIGNMENT = 0x1907, 0x1401, 0x0D05
_GL_READ_FRAMEBUFFER = 0x8CA8


def _ints(*values: int) -> ctypes.Array[ctypes.c_int]:
    log.debug("_ints: %d value(s)", len(values))
    return (ctypes.c_int * len(values))(*values)


def load_projectm() -> ctypes.CDLL:
    """libprojectM 4, or ``OSError`` saying it is not installed."""
    found = ctypes.util.find_library(_LIBRARY[0]) or _LIBRARY[1]
    log.info("load_projectm: %s", found)
    try:
        lib = ctypes.CDLL(found)
    except OSError as e:
        raise OSError("projectM is not installed (libprojectM 4) -- it is "
                      f"what draws audio visuals ({e})") from e
    lib.projectm_create.restype = ctypes.c_void_p
    for name, args in (
            ("projectm_destroy", [ctypes.c_void_p]),
            ("projectm_set_window_size",
             [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_size_t]),
            ("projectm_set_fps", [ctypes.c_void_p, ctypes.c_int32]),
            ("projectm_set_preset_locked", [ctypes.c_void_p, ctypes.c_bool]),
            ("projectm_set_beat_sensitivity", [ctypes.c_void_p, ctypes.c_float]),
            ("projectm_load_preset_file",
             [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_bool]),
            ("projectm_pcm_add_float",
             [ctypes.c_void_p, ctypes.POINTER(ctypes.c_float), ctypes.c_uint,
              ctypes.c_int]),
            ("projectm_opengl_render_frame", [ctypes.c_void_p])):
        getattr(lib, name).argtypes = args
    return lib


class _EglSurface:
    """An invisible OpenGL 3.3 core surface: a pbuffer, current on the
    thread that made it."""

    def __init__(self, width: int, height: int) -> None:
        log.info("_EglSurface: %dx%d", width, height)
        try:
            egl = ctypes.CDLL("libEGL.so.1")
            self.gl = ctypes.CDLL("libOpenGL.so.0")
        except OSError as e:
            raise OSError(f"OpenGL (EGL) is not available: {e}") from e
        for name in ("eglGetDisplay", "eglCreatePbufferSurface",
                     "eglCreateContext"):
            getattr(egl, name).restype = ctypes.c_void_p
        egl.eglGetDisplay.argtypes = [ctypes.c_void_p]
        egl.eglInitialize.argtypes = [ctypes.c_void_p] + [ctypes.c_void_p] * 2
        egl.eglChooseConfig.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_void_p), ctypes.c_int,
            ctypes.POINTER(ctypes.c_int)]
        egl.eglCreatePbufferSurface.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
        egl.eglCreateContext.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_int)]
        egl.eglMakeCurrent.argtypes = [ctypes.c_void_p] * 4
        egl.eglDestroySurface.argtypes = [ctypes.c_void_p] * 2
        egl.eglDestroyContext.argtypes = [ctypes.c_void_p] * 2
        egl.eglTerminate.argtypes = [ctypes.c_void_p]
        self._egl = egl
        self._display = egl.eglGetDisplay(None)
        config, count = ctypes.c_void_p(), ctypes.c_int()
        if not (self._display and egl.eglInitialize(self._display, None, None)
                and egl.eglChooseConfig(
                    self._display,
                    _ints(_EGL_SURFACE_TYPE, _EGL_PBUFFER_BIT,
                          _EGL_RENDERABLE_TYPE, _EGL_OPENGL_BIT, _EGL_RED, 8,
                          _EGL_GREEN, 8, _EGL_BLUE, 8, _EGL_ALPHA, 8,
                          _EGL_DEPTH, 24, _EGL_NONE),
                    ctypes.byref(config), 1, ctypes.byref(count))
                and count.value and egl.eglBindAPI(_EGL_OPENGL_API)):
            raise OSError("OpenGL (EGL) found no off-screen surface to draw on")
        self._surface = egl.eglCreatePbufferSurface(
            self._display, config,
            _ints(_EGL_WIDTH, width, _EGL_HEIGHT, height, _EGL_NONE))
        self._context = egl.eglCreateContext(
            self._display, config, None,
            _ints(_EGL_MAJOR, 3, _EGL_MINOR, 3, _EGL_PROFILE, _EGL_CORE,
                  _EGL_NONE))
        if not (self._surface and self._context and egl.eglMakeCurrent(
                self._display, self._surface, self._surface, self._context)):
            self.close()
            raise OSError("OpenGL 3.3 is not available to draw audio visuals")
        self.gl.glGetString.restype = ctypes.c_char_p
        log.info("_EglSurface: %s, %s",
                 (self.gl.glGetString(0x1F01) or b"?").decode(errors="replace"),
                 (self.gl.glGetString(0x1F02) or b"?").decode(errors="replace"))

    def close(self) -> None:
        log.debug("_EglSurface.close")
        egl, display = self._egl, self._display
        egl.eglMakeCurrent(display, None, None, None)
        if self._context:
            egl.eglDestroyContext(display, self._context)
        if self._surface:
            egl.eglDestroySurface(display, self._surface)
        egl.eglTerminate(display)
        self._context = self._surface = None


class MilkdropVisual(BaseVisual, key="milkdrop"):
    """A MilkDrop preset drawn by libprojectM on an EGL pbuffer; pictures
    read back as RGB24."""

    INFO = VisualInfo(
        kind="milkdrop", title="MilkDrop", category="MilkDrop",
        uses_presets=True,
        adjustments=(VisualAdjustment("beat_sensitivity", "Beat sensitivity",
                                      0.0, 5.0, 1.0),))

    @classmethod
    def available(cls) -> bool:
        """Only where libprojectM 4 is installed."""
        found = ctypes.util.find_library(_LIBRARY[0]) is not None
        log.debug("MilkdropVisual.available: %s", found)
        return found

    @classmethod
    def presets(cls, folder: Path) -> tuple[str, ...]:
        """Every ``.milk`` file under *folder*, relative to it."""
        found = (tuple(sorted(str(p.relative_to(folder))
                              for p in folder.rglob(f"*{PRESET_SUFFIX}")))
                 if folder.is_dir() else ())
        log.info("MilkdropVisual.presets: %d under %s", len(found), folder)
        return found

    @classmethod
    def preset(cls, folder: Path, name: str) -> Path | None:
        """*name* under *folder* -- a ``.milk`` file that is there and does
        not climb out of it -- or None."""
        path = (folder / name).resolve()
        ok = (bool(name) and path.suffix == PRESET_SUFFIX and path.is_file()
              and is_under(path, folder))
        log.debug("MilkdropVisual.preset: %r -> %s", name, path if ok else None)
        return path if ok else None

    def __init__(self) -> None:
        log.debug("MilkdropVisual.__init__")
        self._lib: ctypes.CDLL | None = None
        self._surface: _EglSurface | None = None
        self._handle: int | None = None
        self._size = (0, 0)

    def open(self, spec: VisualSpec) -> None:
        preset, width, height = spec.preset, spec.width, spec.height
        log.info("MilkdropVisual.open: %s at %dx%d", preset, width, height)
        if preset is None:
            raise OSError("a MilkDrop visual needs a preset file")
        self._lib = lib = load_projectm()
        self._surface = _EglSurface(width, height)
        self._handle = lib.projectm_create()
        if not self._handle:
            self.close()
            raise OSError("projectM could not start")
        self._size = (width, height)
        lib.projectm_set_window_size(self._handle, width, height)
        lib.projectm_set_fps(self._handle, VISUAL_FPS)
        lib.projectm_set_preset_locked(self._handle, True)
        lib.projectm_set_beat_sensitivity(
            self._handle, float(spec.settings.get("beat_sensitivity", 1.0)))
        lib.projectm_load_preset_file(self._handle, str(preset).encode(), False)

    def feed(self, pcm: bytes) -> None:
        if self._lib is None or not self._handle or not pcm:
            frame_log.debug("MilkdropVisual.feed: nothing to feed")
            return
        frames = len(pcm) // (4 * SOUND_CHANNELS)
        samples = (ctypes.c_float * (frames * SOUND_CHANNELS)).from_buffer_copy(
            pcm[:frames * 4 * SOUND_CHANNELS])
        frame_log.debug("MilkdropVisual.feed: %d frame(s)", frames)
        self._lib.projectm_pcm_add_float(self._handle, samples, frames,
                                         SOUND_CHANNELS)

    def render(self) -> RawFrame:
        if self._lib is None or self._surface is None or not self._handle:
            raise OSError("projectM is not open")
        width, height = self._size
        self._lib.projectm_opengl_render_frame(self._handle)
        gl = self._surface.gl
        # projectM binds only the DRAW framebuffer to 0 for its finished
        # picture, and leaves READ on its own buffer -- the image before the
        # preset's final shader.  Reading that showed every preset without
        # its composite pass (a red test shader came back black).
        gl.glBindFramebuffer(_GL_READ_FRAMEBUFFER, 0)
        gl.glPixelStorei(_GL_PACK_ALIGNMENT, 1)
        rows = ctypes.create_string_buffer(width * height * 3)
        gl.glReadPixels(0, 0, width, height, _GL_RGB, _GL_UNSIGNED_BYTE, rows)
        stride = width * 3
        raw = rows.raw
        # OpenGL's first row is the bottom one; a panel's is the top.
        data = b"".join(raw[y * stride:(y + 1) * stride]
                        for y in range(height - 1, -1, -1))
        frame_log.debug("MilkdropVisual.render: %dx%d", width, height)
        return RawFrame(data=data, width=width, height=height)

    def close(self) -> None:
        log.info("MilkdropVisual.close: open=%s", self._handle is not None)
        if self._lib is not None and self._handle:
            self._lib.projectm_destroy(self._handle)
        self._handle = None
        if self._surface is not None:
            self._surface.close()
            self._surface = None
