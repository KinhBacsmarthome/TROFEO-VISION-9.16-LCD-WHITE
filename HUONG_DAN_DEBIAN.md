# Hướng dẫn sử dụng TRCC Linux trên Debian

Hướng dẫn cài đặt và sử dụng **TRCC Linux** (bản port Linux của Thermalright LCD
Control Center) trên Debian, dành cho màn hình **Thermalright Trofeo Vision 9.16 LCD (White)**.

> Nguồn gốc: [Lexonight1/thermalright-trcc-linux](https://github.com/Lexonight1/thermalright-trcc-linux)
> (GPL-3.0). Tài liệu gốc chi tiết bằng tiếng Anh nằm trong thư mục [`doc/`](doc/).

Trofeo Vision 9.16 LCD dùng giao thức **LY**, USB ID **`0416:5408`** — đã được
cộng đồng kiểm thử (xem [doc/REFERENCE_DEVICES.md](doc/REFERENCE_DEVICES.md)).

---

## 0. Chuẩn bị

1. Cắm cáp USB của màn hình vào header USB 2.0 trên bo mạch chủ.
2. Mở Terminal (`Ctrl+Alt+T`). Dán lệnh bằng `Ctrl+Shift+V`.
3. Kiểm tra phiên bản Debian:

   ```bash
   cat /etc/os-release | grep VERSION
   ```

4. Kiểm tra máy đã nhận thiết bị chưa:

   ```bash
   lsusb | grep -i 0416
   # Kết quả mong đợi, ví dụ: ID 0416:5408 Winbond Electronics Corp. ...
   ```

Chọn cách cài phù hợp với phiên bản Debian:

| Phiên bản | Cách cài |
|-----------|----------|
| Debian 13 (Trixie) trở lên | [Cách 1 — gói `.deb` chuẩn](#cách-1--debian-13-trixie-trở-lên-gói-deb-chuẩn) |
| Debian 12 (Bookworm) | [Cách 2 — gói Legacy `.deb`](#cách-2--debian-12-bookworm-gói-legacy-deb) |
| Debian 11 hoặc cũ hơn, hoặc muốn dùng pip | [Cách 3 — pipx](#cách-3--pipx-mọi-phiên-bản-kể-cả-debian-11) |
| Muốn chạy bản từ mã nguồn của repo này | [Cách 4 — từ mã nguồn](#cách-4--cài-từ-mã-nguồn-repo-này) |

---

## Cách 1 — Debian 13 (Trixie) trở lên: gói `.deb` chuẩn

```bash
cd ~/Downloads
curl -LO https://github.com/Lexonight1/thermalright-trcc-linux/releases/latest/download/trcc-linux-latest_all.deb
sudo apt install ./trcc-linux-latest_all.deb
```

> Dấu `./` là bắt buộc — thiếu nó, `apt` sẽ tìm gói trên kho thay vì file vừa tải.

Sau đó **khởi động lại máy** rồi chạy:

```bash
trcc gui
```

## Cách 2 — Debian 12 (Bookworm): gói Legacy `.deb`

Debian 12 không có sẵn PySide6 phù hợp, nên dùng bản *legacy* — nó tự cài môi trường
Python riêng vào `/opt/trcc-linux`, không ảnh hưởng Python hệ thống.

1. Vào trang [Releases mới nhất](https://github.com/Lexonight1/thermalright-trcc-linux/releases/latest),
   tải file có tên dạng `trcc-linux_*legacy*_all.deb`.
2. Cài đặt:

   ```bash
   cd ~/Downloads
   sudo apt install ./trcc-linux_*legacy*_all.deb
   ```

3. Chạy trình thiết lập (udev rules, shortcut trên desktop):

   ```bash
   trcc system setup
   ```

4. **Khởi động lại máy**, rồi chạy `trcc gui`.

Gỡ cài đặt: `sudo dpkg -r trcc-linux`.

## Cách 3 — pipx (mọi phiên bản, kể cả Debian 11)

```bash
# Bước 1: thư viện hệ thống
sudo apt update
sudo apt install pipx libusb-1.0-0 libportaudio2 sg3-utils p7zip-full libxcb-cursor0 ffmpeg

# Bước 2 (tuỳ chọn): cảm biến phần cứng, chụp màn hình Wayland
sudo apt install lm-sensors grim python3-gi python3-dbus python3-gst-1.0

# Bước 3: cài TRCC
pipx ensurepath
pipx install trcc-linux

# Bước 4: trình thiết lập
trcc system setup
```

Mở terminal mới (để `~/.local/bin` có trong `PATH`), **khởi động lại máy**, rồi chạy `trcc gui`.

- Cập nhật: `pipx upgrade trcc-linux`
- Có card NVIDIA và muốn đọc nhiệt độ GPU ngay: `pipx install "trcc-linux[nvidia]"`

## Cách 4 — Cài từ mã nguồn (repo này)

```bash
sudo apt install git
git clone https://github.com/KinhBacsmarthome/TROFEO-VISION-9.16-LCD-WHITE.git
cd TROFEO-VISION-9.16-LCD-WHITE
sudo ./install.sh
```

`install.sh` tự nhận diện Debian, cài gói hệ thống, thư viện Python, udev rules và
shortcut. Sau đó khởi động lại máy và chạy `trcc gui`.

Để lấy bản cập nhật mới từ repo gốc:

```bash
git remote add upstream https://github.com/Lexonight1/thermalright-trcc-linux.git  # chỉ làm 1 lần
git fetch upstream
git merge upstream/main
sudo ./install.sh
```

---

## Sử dụng

### Giao diện đồ hoạ (GUI)

```bash
trcc gui
```

Trong GUI bạn có thể:

- **Themes**: chọn theme có sẵn, hoặc mở **Cloud Themes** để tải theme đúng độ phân giải màn hình.
- **Media**: phát video/GIF, cắt ảnh, phát màn hình máy tính (screencast).
- **Overlay Editor**: thêm chữ, ngày giờ, thông số cảm biến (nhiệt độ CPU/GPU, tốc độ quạt, công suất…).
- **Xoay màn hình**: nếu lắp màn hình đứng, chọn góc **90°** (hoặc 270°), rồi tải theme dọc trong Cloud Themes.
- Độ sáng, ngôn ngữ (có tiếng Việt trong 38 ngôn ngữ).

### Dòng lệnh (CLI)

Các lệnh điều khiển thiết bị nhận **key** của thiết bị (dạng `VID:PID`) làm tham số đầu tiên.
Với Trofeo Vision 9.16 LCD là `0416:5408` — kiểm tra lại bằng `trcc detect`:

```bash
trcc detect                                    # Liệt kê thiết bị và key
trcc display send-image 0416:5408 anh.png      # Gửi ảnh lên LCD
trcc display color 0416:5408 "#ff0000"         # Tô màn hình một màu
trcc display load-video 0416:5408 clip.mp4     # Phát video
trcc display screencast 0416:5408              # Phát màn hình máy tính lên LCD
trcc display set-brightness 0416:5408 80       # Độ sáng 0–100%
trcc display set-orientation 0416:5408 90      # Xoay 0/90/180/270
trcc doctor                                    # Kiểm tra phụ thuộc hệ thống
trcc report                                    # Báo cáo chẩn đoán (dán vào issue khi cần hỗ trợ)
trcc --help                                    # Xem tất cả lệnh
```

Danh sách đầy đủ: [doc/REFERENCE_CLI.md](doc/REFERENCE_CLI.md).

### REST API (điều khiển từ xa / tự động hoá)

```bash
trcc serve                      # http://127.0.0.1:8080, tài liệu API tại /docs
curl http://127.0.0.1:8080/devices
curl -X POST http://127.0.0.1:8080/devices/0416:5408/display/color \
  -H "Content-Type: application/json" -d '{"r": 0, "g": 128, "b": 255}'
```

Muốn mở cho mạng LAN thì dùng `--host 0.0.0.0` **kèm** `--token MAT_KHAU` để bảo vệ.

### Dashboard Home Assistant / NAS / thời tiết

Thư mục [`dashboard/`](dashboard/) có chương trình hiển thị đồng hồ, Home Assistant,
NAS Synology, nhiệt độ, mạng và thời tiết lên màn hình, cấu hình bằng trang web. Cài đặt:

```bash
cd TROFEO-VISION-9.16-LCD-WHITE/dashboard
./install.sh                       # rồi mở http://127.0.0.1:8765
```

Hướng dẫn đầy đủ: [dashboard/README.md](dashboard/README.md).

---

## Xử lý sự cố thường gặp

| Hiện tượng | Cách khắc phục |
|-----------|----------------|
| `trcc: command not found` | Mở terminal mới, hoặc chạy `pipx ensurepath` rồi mở lại terminal. |
| Không tìm thấy thiết bị | Khởi động lại máy (để udev rules có hiệu lực). Kiểm tra `lsusb \| grep 0416`. Thử `trcc system setup` lần nữa. |
| `Permission denied` khi truy cập thiết bị | `trcc system setup`, rút/cắm lại cáp USB hoặc khởi động lại. |
| GUI không mở, `Segmentation fault` | Thiếu thư viện Qt: `sudo apt install libxcb-cursor0`. |
| Màn hình đen / không đổi hình | Rút cáp USB, chờ 5 giây, cắm lại rồi mở lại `trcc gui`. |
| Danh sách theme trống | Thiết bị chưa bắt tay (handshake) xong nên chưa biết độ phân giải — xử lý lỗi nhận thiết bị trước. |
| Không có nhiệt độ GPU NVIDIA | Kiểm tra `nvidia-smi` chạy được; nếu báo lệch phiên bản driver thì khởi động lại máy. |
| Công suất CPU hiện `--` | Bộ đếm RAPL chỉ cho root đọc — xem [doc/GUIDE_TROUBLESHOOTING.md](doc/GUIDE_TROUBLESHOOTING.md). |
| Đã cài bằng pipx rồi chuyển sang `.deb` mà vẫn chạy bản cũ | `pipx uninstall trcc-linux`, rồi `hash -r` và kiểm tra `which trcc` trả về `/usr/bin/trcc`. |

Hướng dẫn chi tiết hơn: [doc/GUIDE_TROUBLESHOOTING.md](doc/GUIDE_TROUBLESHOOTING.md).

Vẫn không được? Chạy `trcc report` và dán kết quả vào một
[issue ở repo gốc](https://github.com/Lexonight1/thermalright-trcc-linux/issues/new).

---

## Gỡ cài đặt

```bash
sudo apt remove trcc-linux        # nếu cài bằng .deb (Cách 1 / 2)
pipx uninstall trcc-linux         # nếu cài bằng pipx (Cách 3)
```
