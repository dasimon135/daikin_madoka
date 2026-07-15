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

# Mirror the integration folder: replace the destination wholesale so files
# deleted on the branch do not linger on the HA side. Copying the folder
# itself (not "$src\*") keeps subdirectories intact.
if (Test-Path $dst) { Remove-Item $dst -Recurse -Force }
Copy-Item $src (Split-Path $dst) -Recurse -Force
Get-ChildItem $dst -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force

Get-ChildItem $dst -Recurse -File | ForEach-Object {
    Write-Host "  OK  $($_.FullName.Substring($dst.Length + 1))"
}

Write-Host ""
Write-Host "Deployed to: $dst" -ForegroundColor Green
Write-Host "=> Restart Home Assistant to pick up the changes." -ForegroundColor Yellow
