# Đóng gói CrawlTool.exe (1 file, đã kèm uv.exe). Chạy: powershell -ExecutionPolicy Bypass -File build.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$uv = (Get-Command uv).Source
python test_crawl_tool.py
uvx --python 3.11 --from pyinstaller pyinstaller --noconfirm --clean --onefile --windowed `
    --name CrawlTool --add-binary "$uv;." crawl_tool.py

Write-Host "Xong: $PSScriptRoot\dist\CrawlTool.exe"
