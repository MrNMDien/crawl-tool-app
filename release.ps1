# Phát hành bản app mới để app đã cài tự cập nhật: build, tính sha256, đăng lên GitHub Releases của repo public.
# Trước khi chạy: tăng APP_VERSION trong crawl_tool.py.
# Chạy: powershell -ExecutionPolicy Bypass -File release.ps1 -Notes "Mô tả ngắn thay đổi"
param([Parameter(Mandatory = $true)][string]$Notes)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$repo = "MrNMDien/CrawlTool"  # repo public, app đọc Releases ở đây (trùng APP_REPO trong crawl_tool.py)
$version = (Select-String -Path crawl_tool.py -Pattern '^APP_VERSION = "(\d+\.\d+\.\d+)"').Matches[0].Groups[1].Value
$tag = "app-v$version"
cmd /c "gh release view $tag --repo $repo >nul 2>&1"
if ($LASTEXITCODE -eq 0) { throw "Đã có bản $tag. Hãy tăng APP_VERSION trong crawl_tool.py." }

powershell -ExecutionPolicy Bypass -File build.ps1
if ($LASTEXITCODE -ne 0) { throw "Build thất bại, không phát hành." }

$hash = (Get-FileHash dist\CrawlTool.exe -Algorithm SHA256).Hash.ToLower()
gh release create $tag dist\CrawlTool.exe --repo $repo --title "CrawlTool v$version" --notes "$Notes`n`nsha256: $hash"
if ($LASTEXITCODE -ne 0) { throw "Đăng release thất bại." }
Write-Host "Đã phát hành $tag. App đã cài sẽ báo cập nhật ở lần mở tới (hoặc trong vòng 6 giờ nếu đang mở)."
