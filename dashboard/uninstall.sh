#!/bin/sh
# Gỡ TRCC Dashboard.
#
#   ./uninstall.sh          gỡ chương trình + dịch vụ, GIỮ cấu hình và lịch sử
#   ./uninstall.sh --purge  gỡ sạch, xoá cả cấu hình (token, mật khẩu) và lịch sử
#
# Không đụng tới trcc và các gói hệ thống (python3-pil, python3-psutil…).
set -eu

APP=trcc-dashboard
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/$APP"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/$APP"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/$APP"
UNIT="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/$APP.service"

PURGE=0
case "${1:-}" in
    --purge) PURGE=1 ;;
    "") ;;
    -h|--help) sed -n '2,7p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Tham số lạ: $1 (xem --help)" >&2; exit 2 ;;
esac

[ "$(id -u)" -ne 0 ] || { echo "Đừng chạy bằng root/sudo." >&2; exit 1; }

if [ -f "$UNIT" ]; then
    systemctl --user disable --now "$APP.service" 2>/dev/null || true
    rm -f "$UNIT"
    systemctl --user daemon-reload 2>/dev/null || true
    echo " ✓ Đã gỡ dịch vụ systemd"
fi
rm -f "$HOME/.local/bin/$APP"
rm -rf "$DATA_DIR"
echo " ✓ Đã gỡ chương trình"

if [ "$PURGE" -eq 1 ]; then
    rm -rf "$CONFIG_DIR" "$STATE_DIR"
    echo " ✓ Đã xoá cấu hình và lịch sử"
else
    echo " • Giữ lại cấu hình: $CONFIG_DIR (xoá bằng ./uninstall.sh --purge)"
fi
echo "Lưu ý: ảnh cuối cùng vẫn nằm trên màn hình cho tới khi bạn nạp theme khác trong trcc."
