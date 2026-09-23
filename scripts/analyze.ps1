$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $RepoRoot

$stages = @(
    'clean.py',
    'inventory.py',
    'network.py',
    'streets.py',
    'demand.py',
    'baseline.py',
    'shortlist.py',
    'portfolio.py',
    'sensitivity.py',
    'objectives.py',
    'selection.py',
    'corridors.py',
    'connections.py',
    'transit.py',
    'score.py'
)

foreach ($stage in $stages) {
    python (Join-Path $PSScriptRoot $stage)
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

python -m unittest discover -s tests -q
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

python (Join-Path $PSScriptRoot 'map.py')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host 'Analysis and map completed.'
