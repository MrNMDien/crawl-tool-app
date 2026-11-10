# Đóng gói CrawlTool.exe (1 file, đã kèm uv.exe, giao diện ui.html và base code). Chạy: powershell -ExecutionPolicy Bypass -File build.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$uv = (Get-Command uv).Source
python test_crawl_tool.py
if ($LASTEXITCODE -ne 0) { Write-Host "Test thất bại, dừng build."; exit 1 }
# Gói base code (bản đã commit của ..\MediaCrawler) vào exe: người dùng không cần token hay tải gì từ repo riêng
$base = Join-Path $PSScriptRoot "..\MediaCrawler"
# Nhiều nơi cùng sửa code: build từ bản cũ hơn GitHub sẽ làm mất bản sửa của nơi khác (vd. base cũ cài thư viện lỗi 403)
foreach ($repo in @($PSScriptRoot, $base)) {
    git -C $repo fetch -q
    $behind = git -C $repo rev-list --count "HEAD..@{u}"
    if ($LASTEXITCODE -ne 0 -or [int]$behind -gt 0) { Write-Host "$repo chậm hơn GitHub $behind commit: git pull rồi build lại."; exit 1 }
}
if (git -C $base status --porcelain) { Write-Host "Base code còn thay đổi chưa commit, dừng build."; exit 1 }
git -C $base archive --format=zip --prefix=MediaCrawler/ -o (Join-Path $PSScriptRoot "base.zip") HEAD
if ($LASTEXITCODE -ne 0) { Write-Host "Không gói được base code, dừng build."; exit 1 }
[IO.File]::WriteAllText("$PSScriptRoot/base.sha", (git -C $base rev-parse HEAD).Trim())

# Đồng bộ: project Supabase của chủ app (url + publishable key) gói vào exe, người dùng chỉ đăng nhập
if (-not (Test-Path supabase.json)) { Write-Host "Thiếu supabase.json (url + publishable key), dừng build."; exit 1 }

# Logo: logo.png (vuông) -> icon exe + ảnh nhỏ cho thanh tiêu đề trong app
$icon = @()
if (Test-Path logo.png) {
    uv run --no-project --with pillow python -c "from PIL import Image; im = Image.open('logo.png').convert('RGBA'); im.save('logo.ico', sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)]); im.resize((64, 64), Image.LANCZOS).save('logo_64.png')"
    if ($LASTEXITCODE -ne 0) { Write-Host "Không tạo được icon, dừng build."; exit 1 }
    $icon = @("--icon", "logo.ico", "--add-data", "logo_64.png;.")
}

uvx --python 3.11 --from pyinstaller --with pywebview pyinstaller --noconfirm --clean --onefile --windowed `
    --name CrawlTool --add-binary "$uv;." --add-data "ui.html;." --add-data "base.zip;." --add-data "base.sha;." --add-data "supabase.json;." --collect-all webview @icon crawl_tool.py
if ($LASTEXITCODE -ne 0) { Write-Host "PyInstaller lỗi, dừng build."; exit 1 }

Write-Host "Xong: $PSScriptRoot\dist\CrawlTool.exe"
