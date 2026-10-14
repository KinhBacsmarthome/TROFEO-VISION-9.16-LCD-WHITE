"""TRCC Pipes' painter: its tubes, balls, fire and picture, drawn by OpenGL.

TRCC Pipes works out *what* to draw each frame on the CPU -- where every
pipe reaches, how thick the beat makes it -- and hands this painter a few
dozen shapes.  Drawing them is the expensive part: on the CPU it was 88% of
a frame (measured 2026-10-13); here the graphics card does it.

Four passes a frame, all into framebuffers of their own:

1. **The shapes.**  Each tube and ball is one quad; its fragment shader
   lights the pixels it covers exactly as the CPU did -- a tube's normal
   from how far across it the pixel sits, a ball's from where on the sphere,
   the screen saver's light and materials, a depth test so the near pipes
   hide the far -- and, with the fire up, turns them toward embers.
2. **The fire** (only while it burns), at half size: heat seeded where the
   pipes and the picture are, risen, spread to its neighbours and cooled --
   the CPU ``Fire``'s rule, one frame per pass, between two buffers.
3. **The frame**: the pipes, and the flames' colours where there are none.
4. **The picture** on top, rocking and swelling where TRCC Pipes places it.

Then the frame is read back as RGB24, top row first.
"""
from __future__ import annotations

import ctypes
import logging
from typing import Any

import numpy as np

from ...core.logs import per_frame
from ._gl import (
    GL_ARRAY_BUFFER,
    GL_BLEND,
    GL_COLOR_BUFFER_BIT,
    GL_DEPTH_BUFFER_BIT,
    GL_DEPTH_TEST,
    GL_FLOAT,
    GL_LESS,
    GL_ONE_MINUS_SRC_ALPHA,
    GL_RGBA,
    GL_RGBA8,
    GL_SRC_ALPHA,
    GL_STREAM_DRAW,
    GL_TEXTURE0,
    GL_TEXTURE_2D,
    GL_TRIANGLES,
    GL_UNSIGNED_BYTE,
    Framebuffer,
    GlSurface,
    program,
    read_rgb,
    texture,
    uniforms,
)

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

#: A shape, as TRCC Pipes hands it over -- one row of floats:
#: start x, y, depth; end x, y, depth (a ball: its centre twice); radius on
#: screen at the start and the end, in pixels; radius in the scene; 1 for a
#: ball, 0 for a tube; material; then the box it covers on screen -- left,
#: top, right, bottom.  Screen pixels count right and down from the top left.
SHAPE_FIELDS = 15
_VERTEX_FLOATS = 2 + 11           # a corner, then the shape's first 11 fields
#: The quad's corners as two triangles, by the box's (x, y) column pairs.
_CORNERS = ((11, 12), (13, 12), (13, 14), (11, 12), (13, 14), (11, 14))

_SHAPES_VERTEX = """
in vec2 corner;
in vec3 start;
in vec3 finish;
in vec4 shape;
in float material;
uniform vec2 canvas;
out vec3 v_start;
out vec3 v_finish;
out vec4 v_shape;
flat out int v_material;
void main() {
    v_start = start;
    v_finish = finish;
    v_shape = shape;
    v_material = int(material + 0.5);
    // A screen pixel's centre, top row first, to OpenGL's -1..1, bottom first.
    gl_Position = vec4((corner.x + 0.5) / canvas.x * 2.0 - 1.0,
                       1.0 - (corner.y + 0.5) / canvas.y * 2.0, 0.0, 1.0);
}
"""

_SHAPES_FRAGMENT = """
in vec3 v_start;
in vec3 v_finish;
in vec4 v_shape;
flat in int v_material;
uniform vec2 canvas;
uniform vec3 light;
uniform vec3 halfway;
uniform vec3 ambient[16];
uniform vec3 diffuse[16];
uniform vec3 specular[16];
uniform float shininess[16];
uniform float gain;
uniform float glow;
uniform float far;
void main() {
    // This pixel as the CPU counted it: column, then row from the top.
    vec2 s = vec2(gl_FragCoord.x - 0.5, canvas.y - gl_FragCoord.y - 0.5);
    vec3 n;
    float depth;
    if (v_shape.w > 0.5) {                       // a ball
        vec2 o = (s - v_start.xy) / (v_shape.x + 0.5);
        float d2 = dot(o, o);
        if (d2 > 1.0) discard;
        float nz = sqrt(max(1.0 - d2, 0.0));
        n = vec3(o, nz);
        depth = v_start.z - nz * v_shape.z;
    } else {                                     // a tube
        vec2 d = v_finish.xy - v_start.xy;
        float len = length(d);
        vec2 p = s - v_start.xy;
        float t = dot(p, d) / (len * len);
        float across = (p.y * d.x - p.x * d.y) / len
                       / (v_shape.x + (v_shape.y - v_shape.x) * t);
        if (t < 0.0 || t > 1.0 || abs(across) >= 1.0) discard;
        float nz = sqrt(clamp(1.0 - across * across, 0.0, 1.0));
        n = vec3(-d.y / len * across, d.x / len * across, nz);
        depth = v_start.z + (v_finish.z - v_start.z) * t - nz * v_shape.z;
    }
    int m = v_material;
    float nl = clamp(dot(n, light), 0.0, 1.0);
    float nh = clamp(dot(n, halfway), 0.0, 1.0);
    vec3 c = (ambient[m] + nl * diffuse[m]
              + pow(nh, shininess[m]) * specular[m]) * gain;
    if (glow > 0.0) {                            // _fire.burn
        float shine = (c.r + c.g + c.b) / 3.0 * 0.7 + 0.3;
        c = c * (1.0 - glow) + vec3(1.0, 0.22, 0.05) * shine * glow * 1.25;
    }
    gl_FragDepth = clamp(depth / far, 0.0, 1.0);
    FRAG_COLOUR = vec4(clamp(c, 0.0, 1.0), 1.0);
}
"""

#: A quad given in OpenGL's -1..1 already, with its picture coordinates.
_QUAD_VERTEX = """
in vec2 corner;
in vec2 uv;
out vec2 v_uv;
void main() {
    v_uv = uv;
    gl_Position = vec4(corner, 0.0, 1.0);
}
"""

_FIRE_FRAGMENT = """
uniform sampler2D heat;
uniform sampler2D pipes;
uniform sampler2D picture;
uniform vec2 canvas;
uniform ivec2 half_size;
uniform int frame;
uniform int lift;
uniform float bass;
uniform float pipe_heat;
uniform float cooling;
uniform vec4 placed;          // centre x, y; tilt; size -- size 0: none
float flicker(ivec2 q) {
    uint h = uint(q.x) * 73856093u ^ uint(q.y) * 19349663u
             ^ uint(frame) * 83492791u;
    h ^= h >> 13u;
    h *= 1274126177u;
    h ^= h >> 16u;
    return float(h & 65535u) / 65535.0;
}
float seed(ivec2 q) {
    float s = texelFetch(pipes, q * 2, 0).a * pipe_heat;
    if (placed.w > 0.0) {
        vec2 screen = vec2(2.0 * float(q.x) + 1.0,
                           canvas.y - 2.0 * float(q.y) - 1.0);
        vec2 uv = vec2((screen.x - placed.x) / placed.z, screen.y - placed.y)
                  / placed.w + 0.5;
        if (all(greaterThanEqual(uv, vec2(0.0))) && all(lessThanEqual(uv, vec2(1.0))))
            s = max(s, texture(picture, uv).a);
    }
    return s;
}
float heated(ivec2 q) {
    if (q.y < 0) return 0.0;
    q.x = (q.x + half_size.x) % half_size.x;
    float f = flicker(q);
    return max(texelFetch(heat, q, 0).r,
               seed(q) * (0.6 + 0.4 * f) * (0.7 + 0.5 * min(bass, 1.3)));
}
void main() {
    ivec2 p = ivec2(gl_FragCoord.xy);
    ivec2 below = p - ivec2(0, lift);
    float spread = heated(below) + heated(below - ivec2(1, 0))
                   + heated(below + ivec2(1, 0)) + heated(below - ivec2(0, 1));
    FRAG_COLOUR = vec4(spread / 4.0 * pow(0.955 - 0.03 * flicker(p), cooling),
                       0.0, 0.0, 1.0);
}
"""

_FRAME_FRAGMENT = """
uniform sampler2D pipes;
uniform sampler2D heat;
uniform float strength;
void main() {
    ivec2 p = ivec2(gl_FragCoord.xy);
    vec4 pipe = texelFetch(pipes, p, 0);
    vec3 c = pipe.rgb;
    if (pipe.a < 0.5 && strength > 0.0) {        // _fire's palette
        float level = floor(clamp(texelFetch(heat, p / 2, 0).r * 1.4 * strength,
                                  0.0, 1.0) * 255.0) / 255.0;
        c = clamp(vec3(level * 3.0, level * 3.0 - 1.0, level * 3.0 - 2.0),
                  0.0, 1.0);
    }
    FRAG_COLOUR = vec4(c, 1.0);
}
"""

_PICTURE_FRAGMENT = """
in vec2 v_uv;
uniform sampler2D picture;
void main() {
    FRAG_COLOUR = texture(picture, v_uv);
}
"""

#: The whole frame, as two triangles: corner, then picture coordinates.
_FULL = np.array([-1, -1, 0, 0, 1, -1, 1, 0, 1, 1, 1, 1,
                  -1, -1, 0, 0, 1, 1, 1, 1, -1, 1, 0, 1], dtype=np.float32)


class PipesPainter:
    """Draws TRCC Pipes' frames on *surface*, *width* x *height*, lit with
    *materials* (each ``(ambient, diffuse, specular, shininess)``) from
    *light* with *halfway* its half-vector; depths run to *far*.  The fire
    burns by *fire_rule*: rows risen a frame, cooling exponent, how hot a
    pipe burns."""

    def __init__(self, surface: GlSurface, width: int, height: int,
                 materials: list[tuple[Any, ...]], light: np.ndarray,
                 halfway: np.ndarray, far: float,
                 fire_rule: tuple[int, float, float]) -> None:
        log.info("PipesPainter: %dx%d, %d material(s)", width, height,
                 len(materials))
        self._gl = gl = surface.gl
        self._w, self._h = width, height
        self._half = ((width + 1) // 2, (height + 1) // 2)
        self._frame = 0
        self._shapes = program(gl, _SHAPES_VERTEX, _SHAPES_FRAGMENT,
                               ("corner", "start", "finish", "shape", "material"))
        self._fire = program(gl, _QUAD_VERTEX, _FIRE_FRAGMENT, ("corner", "uv"))
        self._compose = program(gl, _QUAD_VERTEX, _FRAME_FRAGMENT, ("corner", "uv"))
        self._paste = program(gl, _QUAD_VERTEX, _PICTURE_FRAGMENT, ("corner", "uv"))
        self._u = uniforms(gl, self._shapes, "canvas", "light", "halfway",
                           "ambient", "diffuse", "specular", "shininess",
                           "gain", "glow", "far")
        self._uf = uniforms(gl, self._fire, "heat", "pipes", "picture", "canvas",
                            "half_size", "frame", "lift", "bass", "pipe_heat",
                            "cooling", "placed")
        self._uc = uniforms(gl, self._compose, "pipes", "heat", "strength")
        self._up = uniforms(gl, self._paste, "picture")
        self._pipes = Framebuffer(gl, width, height, depth=True)
        self._heat = [Framebuffer(gl, *self._half, floats=True) for _ in range(2)]
        self._out = Framebuffer(gl, width, height)
        self._picture = texture(gl, 1, 1, GL_RGBA8, GL_RGBA, GL_UNSIGNED_BYTE,
                                bytes(4))
        self._picture_size = (1, 1)
        self._shape_vao, self._shape_vbo = self._vertex_array(
            (2, 3, 3, 4, 1))
        self._quad_vao, self._quad_vbo = self._vertex_array((2, 2))
        self._paste_vao, self._paste_vbo = self._vertex_array((2, 2))
        self._upload(self._quad_vbo, _FULL)
        self._lit(materials, light, halfway, far)
        lift, cooling, pipe_heat = fire_rule
        gl.glUseProgram(self._fire)
        gl.glUniform1i(self._uf["lift"], lift)
        gl.glUniform1f(self._uf["cooling"], cooling)
        gl.glUniform1f(self._uf["pipe_heat"], pipe_heat)
        gl.glUniform2i(self._uf["half_size"], *self._half)
        for heat in self._heat:
            heat.bind(gl)
            gl.glClearColor(0.0, 0.0, 0.0, 0.0)
            gl.glClear(GL_COLOR_BUFFER_BIT)

    def _vertex_array(self, sizes: tuple[int, ...]) -> tuple[int, int]:
        """A vertex array whose vertices are *sizes* floats after floats --
        attribute 0, 1, ... in order -- over a buffer of its own."""
        gl = self._gl
        vao, vbo = ctypes.c_uint(), ctypes.c_uint()
        gl.glGenVertexArrays(1, ctypes.byref(vao))
        gl.glBindVertexArray(vao.value)
        gl.glGenBuffers(1, ctypes.byref(vbo))
        gl.glBindBuffer(GL_ARRAY_BUFFER, vbo.value)
        stride, offset = sum(sizes) * 4, 0
        for location, size in enumerate(sizes):
            gl.glVertexAttribPointer(location, size, GL_FLOAT, 0, stride,
                                     ctypes.c_void_p(offset))
            gl.glEnableVertexAttribArray(location)
            offset += size * 4
        log.debug("PipesPainter._vertex_array: %s -> %d", sizes, vao.value)
        return vao.value, vbo.value

    def _upload(self, buffer: int, floats: np.ndarray) -> None:
        """*floats* into vertex buffer *buffer*."""
        frame_log.debug("PipesPainter._upload: %d float(s)", floats.size)
        gl = self._gl
        gl.glBindBuffer(GL_ARRAY_BUFFER, buffer)
        gl.glBufferData(GL_ARRAY_BUFFER, floats.nbytes, floats.ctypes.data,
                        GL_STREAM_DRAW)

    def _lit(self, materials: list[tuple[Any, ...]], light: np.ndarray,
             halfway: np.ndarray, far: float) -> None:
        """The shapes' light, materials and depth range -- fixed for good."""
        log.debug("PipesPainter._lit: far %.1f", far)
        gl, u = self._gl, self._u
        gl.glUseProgram(self._shapes)
        gl.glUniform2f(u["canvas"], float(self._w), float(self._h))
        for name, value in (("light", light), ("halfway", halfway)):
            gl.glUniform3fv(u[name], 1, _floats(value))
        for index, name in enumerate(("ambient", "diffuse", "specular")):
            gl.glUniform3fv(u[name], len(materials), _floats(
                np.concatenate([m[index] for m in materials])))
        gl.glUniform1fv(u["shininess"], len(materials),
                        _floats(np.array([m[3] for m in materials])))
        gl.glUniform1f(u["far"], far)
        gl.glUseProgram(self._fire)
        gl.glUniform2f(self._uf["canvas"], float(self._w), float(self._h))
        gl.glUniform1i(self._uf["heat"], 1)
        gl.glUniform1i(self._uf["pipes"], 0)
        gl.glUniform1i(self._uf["picture"], 2)
        gl.glUseProgram(self._compose)
        gl.glUniform1i(self._uc["pipes"], 0)
        gl.glUniform1i(self._uc["heat"], 1)
        gl.glUseProgram(self._paste)
        gl.glUniform1i(self._up["picture"], 2)

    def show_picture(self, rgba: bytes, width: int, height: int) -> None:
        """The centre picture from now on: RGBA bytes, top row first."""
        log.info("PipesPainter.show_picture: %dx%d", width, height)
        gl = self._gl
        gl.glDeleteTextures(1, ctypes.byref(ctypes.c_uint(self._picture)))
        self._picture = texture(gl, width, height, GL_RGBA8, GL_RGBA,
                                GL_UNSIGNED_BYTE, rgba, smooth=True)
        self._picture_size = (width, height)

    def paint(self, shapes: np.ndarray, gain: float, glow: float, fire: float,
              bass: float, placed: tuple[float, float] | None) -> bytes:
        """One frame: *shapes* (``SHAPE_FIELDS`` floats a row) lit and
        swollen by *gain*, turned *glow* toward embers; flames as hot as
        *fire*, fed by *bass*; the picture at *placed* (tilt, size), or
        none.  RGB24, top row first."""
        frame_log.debug("PipesPainter.paint: %d shape(s), fire %.2f",
                        len(shapes), fire)
        self._frame += 1
        self._draw_shapes(shapes, gain, glow)
        if fire > 0:
            self._burn(bass, placed)
        self._compose_frame(fire)
        if placed is not None:
            self._draw_picture(*placed)
        return read_rgb(self._gl, self._out.name, self._w, self._h)

    def _draw_shapes(self, shapes: np.ndarray, gain: float, glow: float) -> None:
        """Pass 1: every tube and ball, depth-tested, into ``_pipes``."""
        frame_log.debug("PipesPainter._draw_shapes: %d", len(shapes))
        gl = self._gl
        self._pipes.bind(gl)
        gl.glClearColor(0.0, 0.0, 0.0, 0.0)
        gl.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        if not len(shapes):
            return
        corners = np.concatenate([shapes[:, list(c)] for c in _CORNERS], axis=1)
        vertices = np.concatenate(
            [corners.reshape(-1, 2),
             np.repeat(shapes[:, :11], len(_CORNERS), axis=0)], axis=1)
        self._upload(self._shape_vbo, np.ascontiguousarray(vertices, np.float32))
        gl.glUseProgram(self._shapes)
        gl.glUniform1f(self._u["gain"], gain)
        gl.glUniform1f(self._u["glow"], glow)
        gl.glEnable(GL_DEPTH_TEST)
        gl.glDepthFunc(GL_LESS)
        gl.glBindVertexArray(self._shape_vao)
        gl.glDrawArrays(GL_TRIANGLES, 0, len(vertices))
        gl.glDisable(GL_DEPTH_TEST)

    def _burn(self, bass: float, placed: tuple[float, float] | None) -> None:
        """Pass 2: one frame of the fire, from one heat buffer to the other."""
        frame_log.debug("PipesPainter._burn: bass %.2f", bass)
        gl, u = self._gl, self._uf
        source, target = self._heat
        target.bind(gl)
        gl.glUseProgram(self._fire)
        gl.glUniform1i(u["frame"], self._frame)
        gl.glUniform1f(u["bass"], bass)
        tilt, size = placed if placed is not None else (1.0, 0.0)
        gl.glUniform4f(u["placed"], self._w / 2, self._h / 2, tilt, size)
        self._textures(self._pipes.colour, source.colour, self._picture)
        self._full_quad()
        self._heat.reverse()                        # the new heat is current

    def _compose_frame(self, fire: float) -> None:
        """Pass 3: the pipes, and the flames where there are none."""
        frame_log.debug("PipesPainter._compose_frame: fire %.2f", fire)
        gl = self._gl
        self._out.bind(gl)
        gl.glUseProgram(self._compose)
        gl.glUniform1f(self._uc["strength"], fire)
        self._textures(self._pipes.colour, self._heat[0].colour, self._picture)
        self._full_quad()

    def _draw_picture(self, tilt: float, size: float) -> None:
        """Pass 4: the picture, blended on top -- squeezed by *tilt* across,
        *size* pixels a side, in the middle."""
        frame_log.debug("PipesPainter._draw_picture: tilt %.2f size %.0f",
                        tilt, size)
        gl = self._gl
        half_x = tilt * size / 2 / self._w * 2          # in OpenGL's -1..1
        half_y = size / 2 / self._h * 2
        x0, x1, y0, y1 = -half_x, half_x, half_y, -half_y   # top is +1
        corners = np.array([x0, y0, 0, 0, x1, y0, 1, 0, x1, y1, 1, 1,
                            x0, y0, 0, 0, x1, y1, 1, 1, x0, y1, 0, 1],
                           dtype=np.float32)
        self._upload(self._paste_vbo, corners)
        gl.glUseProgram(self._paste)
        self._textures(self._pipes.colour, self._heat[0].colour, self._picture)
        gl.glEnable(GL_BLEND)
        gl.glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        gl.glBindVertexArray(self._paste_vao)
        gl.glDrawArrays(GL_TRIANGLES, 0, 6)
        gl.glDisable(GL_BLEND)

    def _textures(self, *names: int) -> None:
        """Texture units 0, 1, 2 ... hold *names*, in order."""
        frame_log.debug("PipesPainter._textures: %s", names)
        gl = self._gl
        for unit, name in enumerate(names):
            gl.glActiveTexture(GL_TEXTURE0 + unit)
            gl.glBindTexture(GL_TEXTURE_2D, name)
        gl.glActiveTexture(GL_TEXTURE0)

    def _full_quad(self) -> None:
        """Every pixel of the bound framebuffer, once."""
        frame_log.debug("PipesPainter._full_quad")
        self._gl.glBindVertexArray(self._quad_vao)
        self._gl.glDrawArrays(GL_TRIANGLES, 0, 6)

    def close(self) -> None:
        """Free what it drew with -- the surface stays the caller's."""
        log.info("PipesPainter.close")
        gl = self._gl
        for framebuffer in (self._pipes, self._out, *self._heat):
            framebuffer.delete(gl)
        gl.glDeleteTextures(1, ctypes.byref(ctypes.c_uint(self._picture)))
        for made in (self._shapes, self._fire, self._compose, self._paste):
            gl.glDeleteProgram(made)
        for vao, vbo in ((self._shape_vao, self._shape_vbo),
                         (self._quad_vao, self._quad_vbo),
                         (self._paste_vao, self._paste_vbo)):
            gl.glDeleteVertexArrays(1, ctypes.byref(ctypes.c_uint(vao)))
            gl.glDeleteBuffers(1, ctypes.byref(ctypes.c_uint(vbo)))


def _floats(values: np.ndarray) -> Any:
    """*values* as a C array of floats, for a ``glUniform*v``."""
    flat = np.ascontiguousarray(values, dtype=np.float32).ravel()
    log.debug("_floats: %d", flat.size)
    return (ctypes.c_float * flat.size)(*flat.tolist())
