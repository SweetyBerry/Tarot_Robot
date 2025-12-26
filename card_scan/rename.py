from pathlib import Path

# 設定圖片資料夾（用 "." 表示目前資料夾）
img_dir = Path(".")

# 讀取所有 jpg 檔（不分大小寫），並依檔名排序
images = sorted(
    list(img_dir.glob("*.jpg"))
)

# 安全檢查：確認是不是 78 張
if len(images) != 78:
    raise ValueError(f"Expected 78 images, but found {len(images)}")

# 先暫時改名，避免覆蓋
temp_names = []
for i, img in enumerate(images):
    tmp = img.with_name(f"__tmp_{i}.jpg")
    img.rename(tmp)
    temp_names.append(tmp)

# 再正式改成 0.jpg ~ 77.jpg
for i, tmp in enumerate(temp_names):
    tmp.rename(img_dir / f"{i}.jpg")

print("✅ Renaming completed: 0.jpg ~ 77.jpg")
