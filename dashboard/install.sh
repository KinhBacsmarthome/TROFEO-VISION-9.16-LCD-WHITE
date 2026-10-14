#!/bin/sh
# Cài đặt / cập nhật TRCC Dashboard cho Thermalright Trofeo Vision 9.16 LCD.
#
#   ./install.sh               cài gói hệ thống, cài chương trình, bật dịch vụ systemd
#   ./install.sh --no-apt      bỏ qua bước cài gói bằng apt
#   ./install.sh --no-service  không cài / không bật dịch vụ systemd
#
# Chạy bằng tài khoản thường (KHÔNG dùng sudo). Script tự hỏi mật khẩu sudo khi cần
# cài gói. Chạy lại script bất cứ lúc nào để cập nhật lên bản mới — cấu hình được giữ.
set -eu

APP=trcc-dashboard
SRC_DIR=$(cd "$(dirname "$0")" && pwd)
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/$APP"
BIN_DIR="$HOME/.local/bin"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/$APP"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
PYTHON="${TRCC_DASHBOARD_PYTHON:-/usr/bin/python3}"
APT_PACKAGES="python3 python3-pil python3-psutil fonts-dejavu-core"

DO_APT=1
DO_SERVICE=1
for arg in "$@"; do
    case "$arg" in
        --no-apt) DO_APT=0 ;;
        --no-service) DO_SERVICE=0 ;;
        -h|--help) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Tham số lạ: $arg (xem --help)" >&2; exit 2 ;;
    esac
done

say()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m ✓\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m !\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m ✗\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -ne 0 ] || die "Đừng chạy bằng root/sudo. Chạy: ./install.sh (script tự hỏi sudo khi cần)."
[ -d "$SRC_DIR/lcd_dashboard" ] || die "Không thấy thư mục lcd_dashboard cạnh install.sh."

VERSION=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$SRC_DIR/lcd_dashboard/__init__.py")
say "Cài TRCC Dashboard $VERSION"

# 1. Gói hệ thống ----------------------------------------------------------------
if [ "$DO_APT" -eq 1 ]; then
    if command -v apt-get >/dev/null 2>&1; then
        say "Cài gói hệ thống: $APT_PACKAGES"
        sudo apt-get update -qq
        # shellcheck disable=SC2086
        sudo apt-get install -y $APT_PACKAGES
    else
        warn "Không có apt-get — hãy tự cài: Python ≥ 3.10, Pillow, psutil, font DejaVu."
    fi
fi

# 2. Kiểm tra Python -------------------------------------------------------------
[ -x "$PYTHON" ] || PYTHON=$(command -v python3 || true)
[ -n "$PYTHON" ] || die "Không tìm thấy python3."
"$PYTHON" - <<'EOF' || die "Thiếu thư viện hoặc Python quá cũ (cần ≥ 3.10, Pillow, psutil)."
import sys
if sys.version_info < (3, 10):
    sys.exit(f"Python {sys.version.split()[0]} quá cũ, cần ≥ 3.10 (Debian 12 trở lên)")
import PIL, psutil  # noqa: F401
EOF
ok "Python: $("$PYTHON" --version 2>&1)"

# 3. Chép chương trình -----------------------------------------------------------
say "Chép chương trình vào $DATA_DIR"
mkdir -p "$DATA_DIR"
rm -rf "$DATA_DIR/lcd_dashboard.new"
cp -r "$SRC_DIR/lcd_dashboard" "$DATA_DIR/lcd_dashboard.new"
find "$DATA_DIR/lcd_dashboard.new" -name __pycache__ -prune -exec rm -rf {} +
rm -rf "$DATA_DIR/lcd_dashboard"
mv "$DATA_DIR/lcd_dashboard.new" "$DATA_DIR/lcd_dashboard"
cp "$SRC_DIR/config.example.toml" "$SRC_DIR/README.md" "$DATA_DIR/"
ok "Đã chép bản $VERSION"

# 4. Lệnh trcc-dashboard ---------------------------------------------------------
mkdir -p "$BIN_DIR"
cat > "$BIN_DIR/$APP" <<EOF
#!/bin/sh
# Sinh bởi install.sh của TRCC Dashboard — chạy lại install.sh để cập nhật.
PYTHONPATH="$DATA_DIR\${PYTHONPATH:+:\$PYTHONPATH}"
export PYTHONPATH
exec "$PYTHON" -m lcd_dashboard "\$@"
EOF
chmod 755 "$BIN_DIR/$APP"
ok "Lệnh: $BIN_DIR/$APP"
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) warn "$BIN_DIR chưa có trong PATH — mở terminal mới (Debian tự thêm khi thư mục tồn tại)." ;;
esac

# 5. Cấu hình --------------------------------------------------------------------
mkdir -p "$CONFIG_DIR"
chmod 700 "$CONFIG_DIR"
if [ -f "$CONFIG_DIR/config.toml" ]; then
    chmod 600 "$CONFIG_DIR/config.toml"
    ok "Giữ nguyên cấu hình: $CONFIG_DIR/config.toml"
else
    ok "Chưa có cấu hình — sẽ tạo khi bạn bấm Lưu trên trang cấu hình"
fi

# 6. trcc ------------------------------------------------------------------------
if command -v trcc >/dev/null 2>&1; then
    ok "trcc: $(command -v trcc)"
else
    warn "Không thấy lệnh 'trcc' — cài trcc trước (xem HUONG_DAN_DEBIAN.md), nếu không màn hình sẽ không nhận ảnh."
fi

# 7. Dịch vụ systemd -------------------------------------------------------------
if [ "$DO_SERVICE" -eq 1 ]; then
    say "Cài dịch vụ systemd (người dùng)"
    mkdir -p "$UNIT_DIR"
    cp "$SRC_DIR/trcc-dashboard.service" "$UNIT_DIR/$APP.service"
    if systemctl --user daemon-reload 2>/dev/null; then
        systemctl --user enable "$APP.service" >/dev/null 2>&1
        systemctl --user restart "$APP.service"
        sleep 2
        if systemctl --user is-active --quiet "$APP.service"; then
            ok "Dịch vụ đang chạy (tự chạy lại khi đăng nhập)"
        else
            warn "Dịch vụ chưa chạy được — xem: journalctl --user -u $APP -n 50"
        fi
        if command -v loginctl >/dev/null 2>&1 && \
           [ "$(loginctl show-user "$(id -un)" -p Linger --value 2>/dev/null)" != "yes" ]; then
            warn "Muốn dashboard chạy cả khi chưa đăng nhập: sudo loginctl enable-linger $(id -un)"
        fi
    else
        warn "Không kết nối được systemd của người dùng (đang SSH chưa đăng nhập?)."
        warn "Đăng nhập trên máy rồi chạy: systemctl --user enable --now $APP"
    fi
fi

echo
say "Xong! Mở trang cấu hình: http://127.0.0.1:8765"
if [ "$DO_SERVICE" -eq 0 ]; then
    echo "    (chạy trước: $APP --web -v)"
fi
echo "    Gỡ cài đặt: $SRC_DIR/uninstall.sh"
