$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$root = Split-Path -Parent $PSScriptRoot
$rawDir = Join-Path $root 'data\raw'

New-Item -ItemType Directory -Force -Path $rawDir | Out-Null

function Invoke-FileDownload {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,

        [Parameter(Mandatory = $true)]
        [string]$Url
    )

    $target = Join-Path $rawDir $Name
    if (Test-Path $target) {
        Write-Host "Skipping $Name (already exists)..."
        return
    }

    Write-Host "Downloading $Name..."
    Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $target
}

function Invoke-ArcGisGeoJsonDownload {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,

        [Parameter(Mandatory = $true)]
        [string]$ServiceUrl,

        [int]$PageSize = 2000
    )

    $target = Join-Path $rawDir $Name
    if (Test-Path $target) {
        Write-Host "Skipping $Name (already exists)..."
        return
    }

    Write-Host "Downloading $Name..."
    $allFeatures = New-Object System.Collections.Generic.List[object]
    $offset = 0

    do {
        $queryUrl = "$ServiceUrl/query?where=1%3D1&outFields=*&returnGeometry=true&f=geojson&outSR=4326&resultOffset=$offset&resultRecordCount=$PageSize"
        $response = Invoke-RestMethod -Uri $queryUrl
        $pageFeatures = @($response.features)

        foreach ($feature in $pageFeatures) {
            [void]$allFeatures.Add($feature)
        }

        $count = $pageFeatures.Count
        $offset += $PageSize
        $hasMore = ($response.exceededTransferLimit -eq $true) -or ($count -eq $PageSize)
    } while ($count -gt 0 -and $hasMore)

    $featureCollection = [ordered]@{
        type = 'FeatureCollection'
        features = [object[]]$allFeatures.ToArray()
    }

    $featureCollection | ConvertTo-Json -Depth 100 | Set-Content -Encoding UTF8 -Path $target
}

$fileDownloads = @(
    @{
        Name = 'zanikle_cesty_praha.zip'
        Url = 'https://mp.iprpraha.cz/portal/sharing/rest/content/items/80207f689893414b9429dbc032c96d79/data'
    },
    @{
        Name = 'pid_gtfs.zip'
        Url = 'https://data.pid.cz/PID_GTFS.zip'
    },
    @{
        Name = 'praha-latest.osm.pbf'
        Url = 'https://download.geofabrik.de/europe/czech-republic/praha-latest.osm.pbf'
    },
    @{
        Name = 'praha_cycling_routes.geojson'
        Url = 'https://lkod-iprpraha.hub.arcgis.com/api/download/v1/items/45063acce89d4b37afc6d51f03f3ad49/geojson?layers=0'
    },
    @{
        Name = 'praha_cycling_generel.geojson'
        Url = 'https://lkod-iprpraha.hub.arcgis.com/api/download/v1/items/999e4a28af374bc5a87021bce082c392/geojson?layers=0'
    },
    @{
        Name = 'praha_dmr5g_8m.tif'
        Url = 'https://ags.cuzk.cz/arcgis/rest/services/3D/dmr5g/ImageServer/exportImage?bbox=-756000,-1059000,-722000,-1034000&size=4000,3000&bboxSR=5514&imageSR=5514&format=tiff&f=image'
    },
    @{
        Name = 'pid_lines_wgs84.geojson'
        Url = 'https://data.pid.cz/geodata/Linky_WGS84.json'
    },
    @{
        Name = 'praha_metro_entrances.geojson'
        Url = 'https://lkod-iprpraha.hub.arcgis.com/api/download/v1/items/62106cae6acf4507b988c0e745f55bba/geojson?layers=0'
    }
)

foreach ($download in $fileDownloads) {
    Invoke-FileDownload -Name $download.Name -Url $download.Url
}

$serviceDownloads = @(
    @{
        Name = 'praha_streets_ruian.geojson'
        Url = 'https://mp.iprpraha.cz/arcgis/rest/services/ReferencedServices/RUIAN_Ulice_l/MapServer/0'
    },
    @{
        Name = 'praha_mestske_casti.geojson'
        Url = 'https://mp.iprpraha.cz/arcgis/rest/services/Hosted/MAP_CUR_MAP_MESTSKECASTI_P/FeatureServer/0'
    },
    @{
        Name = 'praha_zsj.geojson'
        Url = 'https://mp.iprpraha.cz/arcgis/rest/services/ReferencedServices/RUIAN_Zsj_p/MapServer/0'
    },
    @{
        Name = 'praha_buc.geojson'
        Url = 'https://mp.iprpraha.cz/arcgis/rest/services/Hosted/URK_CUR_URK_BUC_P/FeatureServer/0'
    }
)

foreach ($download in $serviceDownloads) {
    Invoke-ArcGisGeoJsonDownload -Name $download.Name -ServiceUrl $download.Url
}

Get-ChildItem -Path $rawDir | Select-Object Name, Length, LastWriteTime
