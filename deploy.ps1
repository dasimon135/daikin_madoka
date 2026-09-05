# deploy.ps1 — Deploy the HA integration to custom_components/daikin_madoka
# Usage: .\deploy.ps1
# Works from any git branch; copies the whole integration folder.

$ErrorActionPreference = "Stop"

$src     = Join-Path $PSScriptRoot "custom_components\daikin_madoka"
$dst     = "H:\custom_components\daikin_madoka"
$staging = "H:\_deploy_staging\daikin_madoka"
$backup  = "H:\_backups\custom_components\daikin_madoka-$(Get-Date -Format 'yyyyMMdd-HHmmss')"

Write-Host "Active branch: $(git -C $PSScriptRoot rev-parse --abbrev-ref HEAD)" -ForegroundColor Cyan

if (-not (Test-Path $src)) {
    Write-Error "Source not found: $src"
    exit 1
}

# Stage the copy outside custom_components/, then swap it in by rename. The
# slow part never touches the live tree, so the window in which HA could see a
# missing or half-copied integration is the gap between the two renames below,
# not the whole copy. A restart landing mid-deploy — yours, a HACS update,
# another session — then cannot load a partial integration.
#
# Neither the staging folder nor the backup may sit under custom_components/:
# HA scans every subdirectory there and maps it by the domain in its
# manifest.json, so a copy declaring the same domain gets loaded instead of the
# real integration, and dots in the directory name break the import outright.
if (Test-Path $staging) { Remove-Item $staging -Recurse -Force }
New-Item -ItemType Directory -Path (Split-Path $staging) -Force | Out-Null
Copy-Item $src (Split-Path $staging) -Recurse -Force
Get-ChildItem $staging -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force

# Both moves are same-volume renames, so each is near-instant.
New-Item -ItemType Directory -Path (Split-Path $backup) -Force | Out-Null
if (Test-Path $dst) { Move-Item $dst $backup }
try {
    Move-Item $staging $dst
} catch {
    # Put the previous version back rather than leave HA with no integration.
    if (Test-Path $backup) { Move-Item $backup $dst }
    throw
}

# Keep the five most recent backups; they are full copies and H: is the HA
# config share.
Get-ChildItem (Split-Path $backup) -Directory -Filter "daikin_madoka-*" |
    Sort-Object Name -Descending |
    Select-Object -Skip 5 |
    Remove-Item -Recurse -Force

Get-ChildItem $dst -Recurse -File | ForEach-Object {
    Write-Host "  OK  $($_.FullName.Substring($dst.Length + 1))"
}

Write-Host ""
Write-Host "Deployed to: $dst" -ForegroundColor Green
Write-Host "Previous version kept at: $backup" -ForegroundColor DarkGray
Write-Host "=> Restart Home Assistant to pick up the changes." -ForegroundColor Yellow
