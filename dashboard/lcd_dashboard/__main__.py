"""Chạy dashboard: ``trcc-dashboard [-c config.toml] [--web]`` (= ``python3 -m lcd_dashboard``)."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from pathlib import Path
from typing import Any

from . import __version__
from .config import DEFAULT_PATH, Config, ConfigError, load_config
from .demo import demo_snapshot
from .render import render

log = logging.getLogger("lcd_dashboard")


class Loop:
    """Vòng lặp vẽ + gửi; tự nạp lại khi file cấu hình thay đổi."""

    def __init__(self, config_path: Path, demo: bool, runtime: Any = None) -> None:
        self.config_path = config_path
        self.demo = demo
        self.runtime = runtime
        self.cfg = Config()
        self.collector: Any = None
        self.sender: Any = None
        self._mtime: float | None = -1.0

    def _mtime_now(self) -> float | None:
        try:
            return self.config_path.stat().st_mtime
        except FileNotFoundError:
            return None

    def reload_if_changed(self) -> None:
        mtime = self._mtime_now()
        if mtime == self._mtime:
            return
        first = self._mtime == -1.0
        self._mtime = mtime
        try:
            cfg = load_config(self.config_path)
        except (ConfigError, ValueError, OSError) as e:
            log.error("cấu hình lỗi, giữ cấu hình cũ: %s", e)
            return
        if not first:
            log.info("cấu hình đã thay đổi — nạp lại")
        self.cfg = cfg
        self.collector = None
        self.sender = None

    def ensure_sender(self) -> None:
        if self.sender is not None:
            return
        from .sender import Sender
        d = self.cfg.display
        self.sender = Sender(d.device_key, d.sender, d.trcc_command)
        if self.runtime is not None:
            self.runtime.sender_mode = self.sender.mode

    def frame(self) -> Any:
        if self.demo:
            snap = demo_snapshot(self.cfg)
        else:
            if self.collector is None:
                from .sources import Collector
                self.collector = Collector(self.cfg)
            snap = self.collector.collect()
        d = self.cfg.display
        return render(snap, d.width, d.height, font_regular=d.font_regular,
                      font_bold=d.font_bold, background=d.background_image)

    def send_once(self) -> bool:
        self.reload_if_changed()
        img = self.frame()
        path = self.cfg.state_dir / "frame.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        img.save(path)
        try:
            self.ensure_sender()
            self.sender.send(path)
        except Exception as e:  # lỗi gửi không được làm chết vòng lặp
            log.warning("gửi ảnh lỗi: %s", e)
            self.sender = None   # lần sau tạo lại (vd. trcc vừa được cài / khởi động lại)
            if self.runtime is not None:
                self.runtime.report(False, str(e))
            return False
        if self.runtime is not None:
            self.runtime.report(True, "OK")
        return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="trcc-dashboard",
                                 description="Dashboard cho Thermalright Trofeo Vision 9.16 LCD")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    ap.add_argument("-c", "--config", type=Path, default=DEFAULT_PATH,
                    help=f"file cấu hình TOML (mặc định {DEFAULT_PATH})")
    ap.add_argument("--once", action="store_true", help="vẽ và gửi một lần rồi thoát")
    ap.add_argument("--demo", action="store_true", help="dùng số liệu giả (xem thử giao diện)")
    ap.add_argument("--output", type=Path,
                    help="chỉ lưu ảnh ra file PNG này, không gửi lên màn hình")
    ap.add_argument("--web", action="store_true", help="mở trang web cấu hình")
    ap.add_argument("--web-only", action="store_true",
                    help="chỉ chạy trang web cấu hình, không gửi lên màn hình")
    ap.add_argument("--web-host", default="127.0.0.1",
                    help="địa chỉ nghe của trang web (mặc định 127.0.0.1 — chỉ máy này)")
    ap.add_argument("--web-port", type=int, default=8765)
    ap.add_argument("-v", "--verbose", action="count", default=0)
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose > 1 else logging.INFO if args.verbose else
        logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log.info("trcc-dashboard %s, cấu hình %s", __version__, args.config)
    loop = Loop(args.config, args.demo)
    loop.reload_if_changed()

    if args.output is not None:
        loop.frame().save(args.output)
        print(f"Đã lưu {args.output}")
        return 0

    web = None
    if args.web or args.web_only:
        from .webui import Runtime, WebUI
        runtime = Runtime(config_path=args.config, lcd_enabled=not args.web_only)
        loop.runtime = runtime
        try:
            web = WebUI(runtime, args.web_host, args.web_port)
        except OSError as e:
            log.error("không mở được trang web trên %s:%s — %s",
                      args.web_host, args.web_port, e)
            return 1
        web.start()
        print(f"Trang cấu hình: {web.url}", flush=True)

    stop = False

    def _stop(signum: int, _frame: object) -> None:
        nonlocal stop
        log.info("nhận tín hiệu %s, dừng", signum)
        stop = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    if args.once:
        return 0 if loop.send_once() else 1

    while not stop:
        started = time.monotonic()
        if not args.web_only:
            loop.send_once()
            log.info("khung hình xong trong %.2fs", time.monotonic() - started)
        while not stop and time.monotonic() - started < loop.cfg.display.interval:
            time.sleep(0.2)
    if web is not None:
        web.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
