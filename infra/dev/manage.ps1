# Lance `manage.py` avec tout l'environnement de dev Vigil, depuis PowerShell :
#   .\infra\dev\manage.ps1 notify_security --digest
#   .\infra\dev\manage.ps1 projects_digest --dry-run
#
# Ce script : trouve Docker, démarre la base si besoin, charge infra/dev/dev.env et infra/dev/webhooks.env,
# active le mode dev et lit le jeton GitHub de `gh`. Il n'affiche JAMAIS les URL de webhook ni le jeton.

$ErrorActionPreference = "Continue"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $root

# Docker n'est pas toujours dans le PATH d'un terminal ouvert avant son installation.
$dockerBin = "C:\Program Files\Docker\Docker\resources\bin"
if ((Test-Path $dockerBin) -and ($env:Path -notlike "*$dockerBin*")) { $env:Path += ";$dockerBin" }

function Import-EnvFile([string]$path) {
    if (-not (Test-Path $path)) { return }
    foreach ($line in Get-Content $path) {
        $line = $line.Trim()
        if ($line -eq "" -or $line.StartsWith("#")) { continue }
        $name, $value = $line -split "=", 2
        if ($value -and $value.Trim() -ne "") { Set-Item -Path "Env:$($name.Trim())" -Value $value.Trim() }
    }
}

# Base PostgreSQL de dev : démarre le conteneur s'il ne tourne pas, puis attend qu'il soit prêt.
$running = (docker inspect -f "{{.State.Running}}" vigil_dev_postgres 2>$null)
if ($running -ne "true") {
    Write-Host "Démarrage de la base de dev..." -ForegroundColor Yellow
    docker compose -f infra/dev/docker-compose.yml up -d | Out-Null
}
for ($i = 0; $i -lt 40; $i++) {
    if ((docker inspect -f "{{.State.Health.Status}}" vigil_dev_postgres 2>$null) -eq "healthy") { break }
    Start-Sleep -Seconds 2
}
if ((docker inspect -f "{{.State.Health.Status}}" vigil_dev_postgres 2>$null) -ne "healthy") {
    Write-Host "La base de dev n'est pas prête. Docker Desktop est-il lancé ?" -ForegroundColor Red
    exit 1
}

Import-EnvFile "infra/dev/dev.env"
Import-EnvFile "infra/dev/webhooks.env"
$env:DJANGO_DEBUG = "1"
$env:PYTHONIOENCODING = "utf-8"
if (-not $env:GITHUB_TOKEN) { $env:GITHUB_TOKEN = (gh auth token) }

uv run python src/manage.py @args
exit $LASTEXITCODE
