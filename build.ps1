# Đóng gói CrawlTool.exe (1 file, đã kèm uv.exe và giao diện ui.html). Chạy: powershell -ExecutionPolicy Bypass -File build.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$uv = (Get-Command uv).Source
python test_crawl_tool.py
if ($LASTEXITCODE -ne 0) { Write-Host "Test thất bại, dừng build."; exit 1 }
uvx --python 3.11 --from pyinstaller --with pywebview pyinstaller --noconfirm --clean --onefile --windowed `
    --name CrawlTool --add-binary "$uv;." --add-data "ui.html;." --collect-all webview crawl_tool.py
if ($LASTEXITCODE -ne 0) { Write-Host "PyInstaller lỗi, dừng build."; exit 1 }

Write-Host "Xong: $PSScriptRoot\dist\CrawlTool.exe"
