$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$root = Split-Path -Parent $PSScriptRoot
$rawDir = Join-Path $root 'data\raw'
$processedDir = Join-Path $root 'data\processed'

if (-not (Test-Path $rawDir)) {
    throw "Raw data directory not found: $rawDir"
}

New-Item -ItemType Directory -Force -Path $processedDir | Out-Null

$paths = @{
    gtfs = Join-Path $processedDir 'gtfs'
    historical = Join-Path $processedDir 'historical_paths'
    network = Join-Path $processedDir 'network'
    boundaries = Join-Path $processedDir 'boundaries'
    terrain = Join-Path $processedDir 'terrain'
    population = Join-Path $processedDir 'population'
}

foreach ($path in $paths.Values) {
    New-Item -ItemType Directory -Force -Path $path | Out-Null
}

function Expand-ZipToDirectory {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ZipPath,

        [Parameter(Mandatory = $true)]
        [string]$DestinationPath,

        [Parameter(Mandatory = $true)]
        [string]$MarkerName
    )

    if (-not (Test-Path $ZipPath)) {
        throw "Required archive not found: $ZipPath"
    }

    $markerPath = Join-Path $DestinationPath $MarkerName
    if (Test-Path $markerPath) {
        Write-Host "Skipping extraction for $ZipPath (already extracted)..."
        return
    }

    Write-Host "Extracting $(Split-Path -Leaf $ZipPath)..."
    Expand-Archive -Path $ZipPath -DestinationPath $DestinationPath -Force
}

function Copy-IfMissing {
    param(
        [Parameter(Mandatory = $true)]
        [string]$SourcePath,

        [Parameter(Mandatory = $true)]
        [string]$DestinationPath
    )

    if (-not (Test-Path $SourcePath)) {
        throw "Required source file not found: $SourcePath"
    }

    if (Test-Path $DestinationPath) {
        Write-Host "Skipping $(Split-Path -Leaf $DestinationPath) (already staged)..."
        return
    }

    Write-Host "Staging $(Split-Path -Leaf $DestinationPath)..."
    Copy-Item -Path $SourcePath -Destination $DestinationPath
}

Expand-ZipToDirectory -ZipPath (Join-Path $rawDir 'pid_gtfs.zip') -DestinationPath $paths.gtfs -MarkerName 'stops.txt'
Expand-ZipToDirectory -ZipPath (Join-Path $rawDir 'zanikle_cesty_praha.zip') -DestinationPath $paths.historical -MarkerName 'URK_CestyZanikle_l.shp'

$stageFiles = @(
    @{
        Source = Join-Path $rawDir 'praha-latest.osm.pbf'
        Destination = Join-Path $paths.network 'praha-latest.osm.pbf'
    },
    @{
        Source = Join-Path $rawDir 'praha_cycling_routes.geojson'
        Destination = Join-Path $paths.network 'praha_cycling_routes.geojson'
    },
    @{
        Source = Join-Path $rawDir 'praha_cycling_generel.geojson'
        Destination = Join-Path $paths.network 'praha_cycling_generel.geojson'
    },
    @{
        Source = Join-Path $rawDir 'pid_lines_wgs84.geojson'
        Destination = Join-Path $paths.network 'pid_lines_wgs84.geojson'
    },
    @{
        Source = Join-Path $rawDir 'praha_metro_entrances.geojson'
        Destination = Join-Path $paths.network 'praha_metro_entrances.geojson'
    },
    @{
        Source = Join-Path $rawDir 'praha_streets_ruian.geojson'
        Destination = Join-Path $paths.network 'praha_streets_ruian.geojson'
    },
    @{
        Source = Join-Path $rawDir 'praha_mestske_casti.geojson'
        Destination = Join-Path $paths.boundaries 'praha_mestske_casti.geojson'
    },
    @{
        Source = Join-Path $rawDir 'praha_zsj.geojson'
        Destination = Join-Path $paths.boundaries 'praha_zsj.geojson'
    },
    @{
        Source = Join-Path $rawDir 'praha_buc.geojson'
        Destination = Join-Path $paths.boundaries 'praha_buc.geojson'
    },
    @{
        Source = Join-Path $rawDir 'praha_dmr5g_8m.tif'
        Destination = Join-Path $paths.terrain 'praha_dmr5g_8m.tif'
    },
    @{
        Source = Join-Path $rawDir 'SLD21053-MC-OB.xlsx'
        Destination = Join-Path $paths.population 'praha_mestske_casti_population_sldb2021.xlsx'
    }
)

foreach ($file in $stageFiles) {
    Copy-IfMissing -SourcePath $file.Source -DestinationPath $file.Destination
}

$manifestPath = Join-Path $processedDir 'README.txt'
$manifest = @(
    'Processed data layout',
    '',
    'gtfs/',
    '  Extracted PID GTFS feed',
    '',
    'historical_paths/',
    '  Extracted vanished-path shapefile from IPR archive',
    '',
    'network/',
    '  OSM extract, cycling routes, cycling generel, and RUIAN streets',
    '',
    'boundaries/',
    '  Prague city-part, ZSJ, and BUC boundaries',
    '',
    'terrain/',
    '  Prague DMR5G raster export',
    '',
    'population/',
    '  Prague city-part population workbook from SLDB 2021',
    '',
    'Raw files remain canonical under data/raw/; processed folders provide a stable working layout.'
)

Set-Content -Path $manifestPath -Encoding UTF8 -Value $manifest

Get-ChildItem -Path $processedDir -Recurse | Select-Object FullName, Length, LastWriteTime
