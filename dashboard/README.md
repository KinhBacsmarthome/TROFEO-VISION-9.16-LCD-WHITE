# Dashboard cho Trofeo Vision 9.16 LCD

Hiển thị đồng hồ, trạng thái **Home Assistant**, **NAS Synology**, nhiệt độ, tốc độ mạng
và thời tiết lên màn hình **Thermalright Trofeo Vision 9.16 LCD** (1920×462).

![Xem trước](preview.png)

*(Ảnh trên được vẽ bằng `--demo`, tức là số liệu giả.)*

## Cách hoạt động

```
Home Assistant (REST API) ─┐
Synology DSM (Web API)  ───┤
Máy này (psutil, hwmon)  ──┼─► lcd_dashboard ─► ảnh PNG 1920×462 ─► trcc SendImage ─► màn hình
Open-Meteo (thời tiết)  ───┘        (Pillow)
```

Cứ mỗi `interval` giây (mặc định 5 giây), chương trình lấy số liệu, vẽ lại ảnh rồi
gửi lên màn hình qua trcc. Ảnh giữ trên màn hình cho tới lần gửi sau; trcc tự gửi lại
ảnh (keepalive) nên màn hình không bị tắt. Mỗi nguồn số liệu chạy độc lập: nguồn nào
lỗi thì ô của nó hiện `--` hoặc **Mất kết nối**, các ô khác vẫn chạy bình thường.

| Ô | Lấy số liệu từ |
|---|---|
| Đồng hồ, ngày | Giờ của máy |
| Home Assistant | Các entity của tích hợp **System Monitor** (CPU, RAM, ổ đĩa, nhiệt độ) |
| NAS Synology | Web API của DSM, hoặc tích hợp **Synology DSM** trong Home Assistant |
| CPU Load (24h) | Lịch sử do chương trình tự lưu (1 điểm / 5 phút) vào `~/.local/state/trcc-dashboard/history.json` |
| Nhiệt độ | CPU/GPU của máy này, ổ cứng NAS, hoặc entity HA bất kỳ |
| Mạng | Tốc độ card mạng của máy này, hoặc entity HA (vd. router) |
| Thời tiết | [Open-Meteo](https://open-meteo.com) — miễn phí, không cần API key |

## Cài đặt trên Debian

Yêu cầu: **Debian 12 trở lên** (Python ≥ 3.10), đã cài trcc và màn hình đã chạy được
(`trcc detect` thấy `0416:5408`).

```bash
# 1. Thư viện cần thiết
sudo apt install python3-pil python3-psutil fonts-dejavu-core

# 2. Lấy mã nguồn (nếu chưa có)
cd ~
git clone https://github.com/KinhBacsmarthome/TROFEO-VISION-9.16-LCD-WHITE.git
cd TROFEO-VISION-9.16-LCD-WHITE/dashboard

# 3. Tạo file cấu hình
mkdir -p ~/.config/trcc-dashboard
cp config.example.toml ~/.config/trcc-dashboard/config.toml
nano ~/.config/trcc-dashboard/config.toml
```

### Trang web cấu hình (khuyên dùng)

Thay vì sửa file TOML bằng tay, bạn có thể mở trang cấu hình trên trình duyệt:

```bash
cd ~/TROFEO-VISION-9.16-LCD-WHITE/dashboard
python3 -m lcd_dashboard --web -v          # vừa gửi lên màn hình, vừa mở trang cấu hình
# hoặc: python3 -m lcd_dashboard --web-only  # chỉ mở trang cấu hình, không gửi lên màn hình
```

Rồi mở **http://127.0.0.1:8765** trên trình duyệt của máy đó.

![Trang cấu hình](webui.png)

Những gì làm được trên trang:

- **Home Assistant**: nhập địa chỉ và token, bấm **Kết nối & tải danh sách entity**.
  Mỗi ô (CPU %, nhiệt độ CPU, RAM, ổ đĩa…) có danh sách gợi ý để gõ tìm entity. Giá trị
  hiện tại hiển thị ngay bên cạnh, nên bạn biết mình chọn đúng hay chưa.
  Nút **Tự dò System Monitor** tự điền các entity của tích hợp System Monitor.
- **NAS**: chọn **Synology DSM** (nhập tài khoản và bấm **Kiểm tra kết nối**) hoặc
  **Entity Home Assistant**, có nút **Tự dò tích hợp Synology DSM**.
- **Nhiệt độ**: thêm, xoá hoặc đổi tên dòng (tối đa 4). Mỗi dòng chọn nguồn là CPU/GPU
  của máy này, NAS, hoặc bất kỳ cảm biến nhiệt độ nào trong Home Assistant.
- **Mạng**, **Thời tiết** (có sẵn danh sách thành phố), thời gian cập nhật, ảnh nền.
- **Xem trước** ảnh màn hình với số liệu thật hoặc số liệu mẫu.
- Bấm **Lưu & áp dụng**: cấu hình được ghi vào `~/.config/trcc-dashboard/config.toml`
  (quyền 600). Vòng gửi ảnh **tự nạp lại** ở lần gửi kế tiếp, không cần khởi động lại.

Về an toàn:

- Trang chỉ nghe trên `127.0.0.1`, nên chỉ máy này mở được.
- Token và mật khẩu không bao giờ được gửi ngược về trình duyệt. Để trống ô token khi
  lưu nghĩa là giữ token cũ.
- Lệnh `trcc_command`, thư mục `state_dir` và đường dẫn font chỉ sửa được trong file.
- Muốn cấu hình từ máy khác trong LAN, an toàn nhất là dùng đường hầm SSH:
  `ssh -L 8765:127.0.0.1:8765 user@may-debian`, rồi mở http://127.0.0.1:8765 trên máy
  của bạn. Có thể dùng `--web-host 0.0.0.0`, nhưng khi đó **ai trong mạng LAN cũng sửa
  được cấu hình** (trang không có mật khẩu).

### Điền cấu hình bằng tay

1. **Home Assistant**
   - Cài tích hợp **System Monitor**: Cài đặt → Thiết bị & dịch vụ → Thêm tích hợp,
     rồi bật các cảm biến CPU, bộ nhớ, ổ đĩa và nhiệt độ bộ xử lý.
   - Tạo token: bấm tên người dùng ở góc trái dưới → **Bảo mật** →
     **Mã thông báo truy cập dài hạn** → Tạo, rồi dán vào `token`.
   - Kiểm tra tên entity tại **Công cụ nhà phát triển → Trạng thái** và sửa mục
     `[home_assistant.entities]` cho đúng. Đơn vị (MiB, GiB…) được đọc tự động.
2. **NAS Synology**
   - Nên tạo một tài khoản riêng trong DSM, thuộc nhóm `administrators` (API
     dung lượng ổ cần quyền quản trị) và **không bật xác thực 2 bước**.
   - Điền `url` (vd. `https://192.168.1.10:5001`), `username` và `password`.
   - Nếu đã dùng tích hợp Synology DSM trong Home Assistant, có thể đặt
     `source = "home_assistant"` và khai báo `[nas.entities]` thay cho tài khoản DSM.
3. **Thời tiết**: đổi `city`, `latitude`, `longitude` nếu không ở Hà Nội.

> File cấu hình chứa token và mật khẩu, nên hãy khoá quyền đọc:
> `chmod 600 ~/.config/trcc-dashboard/config.toml`

### Chạy thử

```bash
cd ~/TROFEO-VISION-9.16-LCD-WHITE/dashboard

# Xem giao diện với số liệu giả, lưu ra file ảnh (không đụng tới màn hình)
python3 -m lcd_dashboard --demo --output /tmp/thu.png

# Lấy số liệu thật, lưu ra file ảnh để kiểm tra
python3 -m lcd_dashboard -c ~/.config/trcc-dashboard/config.toml --output /tmp/that.png -v

# Mở trang cấu hình + chạy liên tục
python3 -m lcd_dashboard --web -v

# Gửi lên màn hình một lần
python3 -m lcd_dashboard -c ~/.config/trcc-dashboard/config.toml --once -v

# Chạy liên tục (Ctrl+C để dừng)
python3 -m lcd_dashboard -c ~/.config/trcc-dashboard/config.toml -v
```

Không truyền `-c` thì chương trình dùng `~/.config/trcc-dashboard/config.toml`.

`-v` hiện thông báo chính, `-vv` hiện chi tiết từng request (dùng khi dò lỗi).

### Tự chạy khi đăng nhập (systemd)

```bash
mkdir -p ~/.config/systemd/user
cp trcc-dashboard.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now trcc-dashboard

journalctl --user -u trcc-dashboard -f      # xem log
systemctl --user restart trcc-dashboard     # chạy lại sau khi sửa config
```

Nếu bạn để repo ở thư mục khác `~/TROFEO-VISION-9.16-LCD-WHITE`, hãy sửa
`WorkingDirectory` trong file `.service`.

Muốn dashboard chạy cả khi chưa đăng nhập: `sudo loginctl enable-linger $USER`.

## Cách gửi ảnh lên màn hình (`display.sender`)

| Giá trị | Cách gửi | Khi nào dùng |
|---|---|---|
| `auto` (mặc định) | Thử `python`, không được thì dùng `cli` | Hầu hết trường hợp |
| `python` | Gọi thẳng `SendImage` qua tiến trình trcc nền | Python đang chạy dashboard import được `trcc`. Với gói `.deb` chuẩn (Debian 13), `/usr/bin/python3` import được. Nhẹ nhất. |
| `cli` | Chạy `trcc display send-image <key> <file>` | Gói legacy `.deb` hoặc pipx: tốn một tiến trình Python mỗi lần gửi, nhưng chạy được với mọi cách cài |

Muốn dùng `python` với pipx: `pipx inject trcc-linux pillow`, rồi sửa `ExecStart` thành
`~/.local/share/pipx/venvs/trcc-linux/bin/python -m lcd_dashboard …`.

## Lưu ý

- **Hướng màn hình**: ảnh được vẽ nằm ngang 1920×462. Hãy để góc xoay của màn hình
  trong trcc là **0°**, hoặc **180°** nếu lắp ngược.
- **Nạp theme trong `trcc gui`** sẽ lấy lại màn hình. Dashboard sẽ chiếm lại ở lần
  gửi tiếp theo (sau tối đa `interval` giây). Nếu đang **screencast**, trcc sẽ từ chối
  nhận ảnh, nên hãy tắt screencast trước.
- **Nhiệt độ GPU**: card AMD đọc qua `amdgpu` hwmon, card NVIDIA đọc qua `nvidia-smi`.
  Không có thì ô GPU hiện `--`. Có thể thay bằng entity HA khác trong
  `[temperature] rows`.
- **Biểu đồ 24h** bắt đầu trống và đầy dần (1 điểm mỗi 5 phút). Lịch sử được lưu
  lại nên khởi động lại không bị mất.
- **Đổi ảnh nền** cho ô đồng hồ: đặt `background_image = "/đường/dẫn/anh.jpg"`.

## Sửa lỗi thường gặp

| Hiện tượng | Kiểm tra |
|---|---|
| `không tìm thấy lệnh 'trcc'` | Mở terminal mới, hoặc đặt `trcc_command` là đường dẫn đầy đủ (`which trcc`). |
| Ô Home Assistant hiện **Mất kết nối** | Chạy với `-vv` để xem lỗi. Hay gặp nhất: sai `url`, token hết hạn (401), sai tên entity (404). |
| NAS báo `đăng nhập thất bại, mã lỗi 400/403` | Sai mật khẩu, hoặc tài khoản đang bật 2FA. |
| NAS báo `lỗi mã 105` | Tài khoản không có quyền quản trị. |
| Ô thời tiết hiện `--` | Máy không ra được `api.open-meteo.com`. |
| `gửi ảnh lỗi: … screencast` | Tắt screencast trong trcc. |
| Trang cấu hình báo `token sai hoặc đã hết hạn` | Tạo lại token dài hạn trong Home Assistant. |
| `không mở được trang web … Address already in use` | Đã có một dashboard khác đang chạy, hoặc đổi cổng bằng `--web-port 8766`. |
