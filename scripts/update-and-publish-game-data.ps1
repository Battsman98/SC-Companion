$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$snapshotRelative = "data/blueprints_snapshot.json"
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
$importerRelative = "scripts/update_game_data_from_p4k.py"
$gameArchive = "C:\StarCitizen\LIVE\Data.p4k"
$productionStatusUrl = "https://sccompanion.org/api/game-data/status"

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$Program,
        [Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments
    )
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Program failed with exit code $LASTEXITCODE."
    }
}

Write-Host ""
Write-Host "STAR CITIZEN MISSION + BLUEPRINT UPDATE" -ForegroundColor Cyan
Write-Host "This publishes data from your installed LIVE game files." -ForegroundColor DarkGray

foreach ($requiredPath in @($gameArchive, $pythonPath, (Join-Path $projectRoot $importerRelative))) {
    if (-not (Test-Path -LiteralPath $requiredPath)) {
        throw "Required file not found: $requiredPath"
    }
}

Push-Location $projectRoot
$worktreePath = $null
try {
    Write-Host ""
    Write-Host "1/5  Checking for the latest project version..."
    Invoke-Checked git fetch origin main

    # Build and publish from an isolated checkout. This leaves the user's current
    # branch and any unfinished work exactly as they are.
    $worktreePath = Join-Path ([System.IO.Path]::GetTempPath()) ("sc-game-data-update-" + [guid]::NewGuid().ToString("N"))
    Invoke-Checked git worktree add --detach $worktreePath origin/main
    Set-Location $worktreePath

    $snapshotPath = Join-Path $worktreePath $snapshotRelative
    $importerPath = Join-Path $worktreePath $importerRelative

    Write-Host ""
    Write-Host "2/5  Reading Data.p4k and rebuilding the database..."
    Invoke-Checked $pythonPath $importerPath

    & git diff --quiet -- $snapshotRelative
    if ($LASTEXITCODE -eq 0) {
        Write-Host ""
        Write-Host "The installed game data already matches the published snapshot." -ForegroundColor Green
        exit 0
    }
    if ($LASTEXITCODE -ne 1) {
        throw "The rebuilt snapshot could not be compared."
    }

    $snapshot = Get-Content -LiteralPath $snapshotPath -Raw | ConvertFrom-Json
    $version = [string]$snapshot.source.version
    if ([string]::IsNullOrWhiteSpace($version)) {
        throw "The rebuilt snapshot does not contain a game version."
    }

    Write-Host ""
    Write-Host "3/5  Running safety checks..."
    Invoke-Checked $pythonPath -m pytest -q

    Write-Host ""
    Write-Host "4/5  Publishing $version..."
    Invoke-Checked git add -- $snapshotRelative
    Invoke-Checked git commit -m "Update game data to $version" -- $snapshotRelative
    Invoke-Checked git push origin HEAD:main

    Write-Host ""
    Write-Host "5/5  Waiting for the hosted website to confirm the update..."
    $deadline = (Get-Date).AddMinutes(20)
    $deployed = $false
    while ((Get-Date) -lt $deadline) {
        try {
            $status = Invoke-RestMethod -Uri $productionStatusUrl -Method Get -TimeoutSec 20
            if ([string]$status.version -eq $version) {
                $deployed = $true
                Write-Host ""
                Write-Host "Update complete: $version" -ForegroundColor Green
                Write-Host "$($status.blueprints) blueprints and $($status.missions) missions are live."
                break
            }
        }
        catch {
            # The host can briefly be unavailable while the new release starts.
        }
        Start-Sleep -Seconds 10
    }
    if (-not $deployed) {
        throw "The data was pushed, but the hosted website did not confirm $version within 20 minutes."
    }
}
finally {
    Set-Location $projectRoot
    if ($null -ne $worktreePath -and (Test-Path -LiteralPath $worktreePath)) {
        & git worktree remove --force $worktreePath
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "The temporary checkout could not be removed: $worktreePath"
        }
    }
    Pop-Location
}
