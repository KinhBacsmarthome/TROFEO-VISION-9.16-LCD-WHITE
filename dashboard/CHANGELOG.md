# Lịch sử thay đổi — TRCC Dashboard

## 1.1.0 — 2026-10-14

- **Trang web cấu hình** (`--web`, `--web-only`):
  - Chọn entity Home Assistant từ danh sách thật, kèm giá trị hiện tại.
  - Tự dò entity System Monitor và Synology DSM.
  - Kiểm tra kết nối NAS.
  - Sửa nhiệt độ, mạng, thời tiết (có sẵn danh sách thành phố).
  - Xem trước ảnh màn hình.
- Lưu cấu hình xong thì vòng gửi ảnh **tự nạp lại**, không cần khởi động lại.
- **`install.sh` / `uninstall.sh`**:
  - Cài gói hệ thống, tạo lệnh `trcc-dashboard`, cài dịch vụ systemd.
  - Chạy lại `install.sh` là cập nhật, cấu hình được giữ.
- Thêm `--version`. File cấu hình mặc định: `~/.config/trcc-dashboard/config.toml`.
- Dòng nhiệt độ chọn biểu tượng theo nguồn (CPU, GPU, ổ cứng, nhiệt kế).
- Tự tính phần đã dùng khi chỉ có % và dung lượng tổng (tích hợp Synology DSM của HA).
- Sửa: ô Home Assistant / NAS báo "Đang hoạt động" khi chưa cấu hình.
- Bảo mật:
  - Token và mật khẩu không gửi về trình duyệt.
  - Chặn yêu cầu khác nguồn và Host lạ.
  - `trcc_command` / `state_dir` / font chỉ sửa được trong file.
- Thêm 39 test tự động (unittest); đã chạy trên Python 3.11 + Pillow 9.4 và Python 3.13 + Pillow 12.

## 1.0.0 — 2026-10-14

- Bản đầu tiên:
  - Đồng hồ, Home Assistant, NAS Synology (DSM Web API), nhiệt độ, mạng, thời tiết Open-Meteo.
  - Vẽ 1920×462 bằng Pillow, gửi qua trcc `SendImage`.
  - Cấu hình bằng file TOML, có dịch vụ systemd.
