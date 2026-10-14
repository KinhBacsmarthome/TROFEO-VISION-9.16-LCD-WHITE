"""Đẩy ảnh lên màn hình qua trcc (lệnh ``SendImage``).

* ``python``: gọi thẳng ``trcc._boot.trcc().dispatch(SendImage(...))`` — chỉ dùng
  được khi chạy dashboard bằng Python có cài trcc (vd. venv của pipx). Mỗi lần
  gửi chỉ là một vòng hỏi-đáp qua socket tới tiến trình trcc nền.
* ``cli``: chạy ``trcc display send-image <key> <file>`` — dùng được với mọi
  cách cài (.deb, legacy .deb, pipx) nhưng tốn một tiến trình Python mỗi lần.
* ``auto``: thử ``python``, không được thì dùng ``cli``.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class SendError(RuntimeError):
    pass


class Sender:
    def __init__(self, key: str, mode: str = "auto", trcc_command: str = "trcc") -> None:
        self.key = key
        self.trcc_command = trcc_command
        self._bus: Any = None
        self._send_image: Any = None
        if mode not in ("auto", "python", "cli"):
            raise ValueError(f"display.sender phải là auto/python/cli, không phải {mode!r}")
        if mode in ("auto", "python"):
            try:
                from trcc._boot import trcc  # pyright: ignore[reportMissingImports]
                from trcc.core.commands import SendImage  # pyright: ignore[reportMissingImports]
            except ImportError as e:
                if mode == "python":
                    raise SendError(f"không import được trcc: {e}") from e
                log.info("sender: không import được trcc (%s) — dùng lệnh CLI", e)
            else:
                self._bus = trcc()
                self._send_image = SendImage
                log.info("sender: gửi trực tiếp qua trcc Python API")
        if self._bus is None:
            exe = shutil.which(trcc_command)
            if exe is None:
                raise SendError(f"không tìm thấy lệnh {trcc_command!r} trong PATH")
            self.trcc_command = exe
            log.info("sender: gửi qua %s display send-image", exe)

    def send(self, path: Path) -> None:
        if self._bus is not None:
            result = self._bus.dispatch(self._send_image(key=self.key, path=path))
            if not result.ok:
                raise SendError(result.message)
            log.debug("sender: %s", result.message)
            return
        proc = subprocess.run(
            [self.trcc_command, "display", "send-image", self.key, str(path)],
            capture_output=True, text=True, timeout=60, check=False,
        )
        if proc.returncode != 0:
            raise SendError((proc.stderr or proc.stdout).strip() or
                            f"trcc thoát với mã {proc.returncode}")
        log.debug("sender: %s", proc.stdout.strip())
