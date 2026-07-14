# deploy.ps1 — Deploy the HA integration to custom_components/daikin_madoka
# Usage: .\deploy.ps1
# Works from any git branch; copies the whole integration folder.

$src = Join-Path $PSScriptRoot "custom_components\daikin_madoka"
$dst = "H:\custom_components\daikin_madoka"

Write-Host "Active branch: $(git -C $PSScriptRoot rev-parse --abbrev-ref HEAD)" -ForegroundColor Cyan

if (-not (Test-Path $src)) {
    Write-Error "Source not found: $src"
    exit 1
}

New-Item -ItemType Directory -Force -Path $dst | Out-Null

# Mirror the integration folder (removes files deleted on the branch)
Copy-Item "$src\*" $dst -Recurse -Force

Get-ChildItem $dst -File | ForEach-Object { Write-Host "  OK  $($_.Name)" }
Get-ChildItem "$dst\translations" -File | ForEach-Object { Write-Host "  OK  translations/$($_.Name)" }

Write-Host ""
Write-Host "Deployed to: $dst" -ForegroundColor Green
Write-Host "=> Restart Home Assistant to pick up the changes." -ForegroundColor Yellow
