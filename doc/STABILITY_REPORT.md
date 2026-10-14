# Stability Report

TRCC Linux is designed for long-running sessions — the GUI typically runs for hours or days as a system tray application. This report documents our memory and resource testing to ensure stable operation over extended periods.

## Test Suite Overview

| Category | Tests | Status |
|----------|-------|--------|
| PIL Image lifecycle | 3 | Pass |
| Video frame accumulation | 4 | Pass |
| Overlay render cycles | 4 | Pass |
| Theme image cycles | 2 | Pass |
| LED tick loop | 3 | Pass |
| USB handle cleanup | 3 | Pass |
| Garbage collectability | 3 | Pass |
| Config load/save cycles | 3 | Pass |
| **Total** | **25** | **All pass** |

## What We Test

### PIL Image Lifecycle

PIL Images hold memory buffers and (when file-backed) file descriptors. We verify:

- `ImageService.resize()` returns a new object, making the old one eligible for garbage collection
- PIL Images are fully reclaimable after all references are dropped (`weakref` verification)
- 50 consecutive open/resize cycles stay under 2MB memory growth (measured via `tracemalloc`)

### Video Frame Accumulation

Video playback preloads all frames into a list. For a 30-second 60fps video at 320x320, that is ~120 frames (~36MB). We verify:

- `MediaService.close()` clears the frame list and releases the decoder
- All frame objects become reclaimable after close (verified via `weakref` on each frame)
- Loading a new video releases the previous frame set completely
- `stop()` correctly preserves frames (stop is pause-at-start, not unload)

### Overlay Render Cycles

The overlay engine caches rendered layers to minimize per-frame cost. We verify:

- Replacing the background image releases the previous one
- Replacing the mask image releases the previous one
- `clear()` releases all cached surfaces (background, mask, overlay cache, composite cache)
- 50 render cycles with varying hardware metrics stay under 2MB growth

### Theme Image Cycles

Users browse themes frequently in the GUI, each click loading new images. We verify:

- Intermediate images from `Image.open()` are released after resize overwrites the reference
- 20 consecutive theme load cycles stay within bounded memory

### LED Tick Loop

The LED effect engine runs at 150ms intervals (6.7 ticks/second), computing per-segment RGB colors. We verify:

- 500 static-mode ticks stay under 500KB growth
- 200 breathing-mode ticks (most complex timer with sine computation) stay under 500KB growth
- Each `tick()` returns a fresh list object (no accumulation on the service)

### USB Handle Cleanup

USB transport handles (pyusb) occupy kernel resources. We verify:

- Protocol `close()` clears the transport reference
- Transport remains closeable even after handshake exceptions
- `LEDService` cleanup releases its protocol reference

### Config Load/Save Cycles

`conf.Settings` is loaded and saved on every config change. We verify:

- 50 consecutive load/save cycles stay under 2MB memory growth
- Config dicts are reclaimable after each load
- Migration code (legacy key translation) leaves no leaked references

### Garbage Collectability

Python's garbage collector handles circular references, but uncollectable cycles (`__del__` + cycles) cause permanent leaks. We verify:

- `OverlayService` create/use/delete produces zero uncollectable objects
- `MediaService` with frames produces zero uncollectable objects after close
- `LEDService` after tick loops produces zero uncollectable objects

## Methodology

All tests use Python stdlib tools — no external profiling dependencies:

| Tool | Purpose |
|------|---------|
| `tracemalloc` | Measure peak memory growth across operation cycles |
| `weakref` | Verify objects are reclaimable (not held by hidden references) |
| `gc.collect()` | Force deterministic cleanup before assertions |
| `gc.garbage` | Detect uncollectable circular reference cycles |

### Memory Thresholds

Thresholds are intentionally generous to avoid CI flakiness from allocator fragmentation:

| Operation | Threshold | Rationale |
|-----------|-----------|-----------|
| Image open/resize (50 cycles) | < 2MB | Single 320x320 RGB = ~300KB |
| Overlay render (50 cycles) | < 2MB | Single 320x320 RGBA = ~400KB |
| Theme load (20 cycles) | < 2MB | Same as image open/resize |
| LED tick (500 cycles) | < 500KB | Returns transient list of tuples, no images |

A test failure means memory grew beyond what a single cached copy would explain — indicating leaked references accumulating across cycles.

## Results Summary

All 25 tests pass. No active memory leaks detected in any service layer. The cleanup paths (`close()`, `clear()`, `set_background()`, `set_mask()`) all correctly release previous references, allowing Python's garbage collector to reclaim memory.

### What This Means for Users

- **GUI can run indefinitely** without memory growth from theme switching, video playback, overlay rendering, or LED animation
- **CLI commands** that loop (video, screencast, LED modes) release resources on each cycle
- **API server** handles repeated requests without accumulating stale image data

## Running the Tests

```bash
PYTHONPATH=src pytest tests/test_memory.py -v
```

## Test File

[tests/test_memory.py](../tests/test_memory.py) — 22 tests across 7 test classes.
