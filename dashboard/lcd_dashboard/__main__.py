"""Chạy dashboard: ``python3 -m lcd_dashboard -c config.toml``."""

from __future__ import annotations

import argparse
import logging
import math
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

from .config import Config, load_config
from .models import HostStats, NetStats, Snapshot, TempRow, Weather
from .render import render

log = logging.getLogger("lcd_dashboard")


def demo_snapshot(cfg: Config) -> Snapshot:
    """Số liệu giả giống ảnh mẫu — để xem thử giao diện khi chưa nối nguồn thật."""
    gb, tb = 2**30, 2**40
    wave = [30 + 12 * math.sin(i / 7) + 8 * math.sin(i / 2.3) for i in range(288)]
    net = [60 + 25 * math.sin(i / 4) + 15 * math.sin(i / 1.7) for i in range(60)]
    return Snapshot(
        now=datetime.now(),
        ha_title=cfg.home_assistant.title, nas_title=cfg.nas.title,
        ha=HostStats(True, 28, 52, 46, 3.7 * gb, 8 * gb, 62, 58 * gb, 93 * gb),
        nas=HostStats(True, 32, 48, 58, 4.6 * gb, 8 * gb, 71, 5.2 * tb, 7.3 * tb, 38),
        ha_hist=wave, nas_hist=[v + 4 for v in wave[::-1]],
        temps=[TempRow("CPU", 52), TempRow("GPU", 46), TempRow("NAS HDD", 38)],
        temp_max=cfg.temperature.max_temp,
        net=NetStats(92.4, 23.6, net, [v / 4 for v in net[::-1]]),
        weather=Weather(cfg.weather.city, 26, 78, 12, 2, True),
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="lcd_dashboard",
                                 description="Dashboard cho Thermalright Trofeo Vision 9.16 LCD")
    ap.add_argument("-c", "--config", type=Path, help="file cấu hình TOML")
    ap.add_argument("--once", action="store_true", help="vẽ và gửi một lần rồi thoát")
    ap.add_argument("--demo", action="store_true", help="dùng số liệu giả (xem thử giao diện)")
    ap.add_argument("--output", type=Path,
                    help="chỉ lưu ảnh ra file PNG này, không gửi lên màn hình")
    ap.add_argument("-v", "--verbose", action="count", default=0)
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose > 1 else logging.INFO if args.verbose else
        logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cfg = load_config(args.config)
    d = cfg.display

    collector = None
    if not args.demo:
        from .sources import Collector
        collector = Collector(cfg)

    sender = None
    if args.output is None:
        from .sender import Sender, SendError
        try:
            sender = Sender(d.device_key, d.sender, d.trcc_command)
        except SendError as e:
            log.error("%s", e)
            return 1

    frame = cfg.state_dir / "frame.png"
    frame.parent.mkdir(parents=True, exist_ok=True)

    stop = False

    def _stop(signum: int, _frame: object) -> None:
        nonlocal stop
        log.info("nhận tín hiệu %s, dừng", signum)
        stop = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    while not stop:
        started = time.monotonic()
        snap = demo_snapshot(cfg) if collector is None else collector.collect()
        img = render(snap, d.width, d.height, font_regular=d.font_regular,
                     font_bold=d.font_bold, background=d.background_image)
        if args.output is not None:
            img.save(args.output)
            print(f"Đã lưu {args.output}")
            return 0
        img.save(frame)
        try:
            sender.send(frame)  # type: ignore[union-attr]
        except Exception as e:  # lỗi gửi không được làm chết vòng lặp
            log.warning("gửi ảnh lỗi: %s", e)
        log.info("khung hình xong trong %.2fs", time.monotonic() - started)
        if args.once:
            break
        while not stop and time.monotonic() - started < d.interval:
            time.sleep(0.2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
