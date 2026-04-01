# deploy.ps1 — Déploie l'intégration HA vers custom_components/daikin_madoka
# Usage : .\deploy.ps1
# Fonctionne depuis n'importe quelle branche git.

$src = $PSScriptRoot
$dst = "H:\custom_components\daikin_madoka"

Write-Host "Branche active : $(git -C $src rev-parse --abbrev-ref HEAD)" -ForegroundColor Cyan

New-Item -ItemType Directory -Force -Path $dst | Out-Null

# Fichiers Python + config de l'intégration HA
$files = @(
    "__init__.py",
    "climate.py",
    "config_flow.py",
    "const.py",
    "sensor.py",
    "strings.json",
    "manifest.json"
)

# Fichiers optionnels présents sur feature/v2-native-ble
$optionalFiles = @(
    "madoka_protocol.py",
    "bluetooth.py",
    "coordinator.py",
    "binary_sensor.py",
    "button.py",
    "number.py"
)

foreach ($f in $files) {
    Copy-Item "$src\$f" "$dst\$f" -Force
    Write-Host "  OK  $f"
}

foreach ($f in $optionalFiles) {
    if (Test-Path "$src\$f") {
        Copy-Item "$src\$f" "$dst\$f" -Force
        Write-Host "  OK  $f (v2)"
    }
}

# Dossier translations
Copy-Item "$src\translations" "$dst\translations" -Recurse -Force
Write-Host "  OK  translations/"

Write-Host ""
Write-Host "Deploye dans : $dst" -ForegroundColor Green
Write-Host "=> Redemarrer Home Assistant pour prendre en compte les changements." -ForegroundColor Yellow
