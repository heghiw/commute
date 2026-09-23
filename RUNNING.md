# Running the analysis

The [notebook](reports/project_analysis.ipynb) is a published, executed report. Open it to read the results; it is not a setup guide and the commands below do not regenerate it. The scripts produce local data layers, analysis tables and the interactive map.

## Requirements

- Windows PowerShell and Python 3.11 or newer.
- Internet access for source downloads and map tiles.
- Enough free disk space for the OSM extract, GTFS archive, terrain raster and derived GeoPackages.

From the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

If PowerShell blocks activation, use `.\.venv\Scripts\python.exe` in place of `python` for Python commands. Do not change machine-wide execution policy just for this project.

## 1. Download

```powershell
.\scripts\download.ps1
```

This downloads the historical paths (IPR Prague), PID GTFS and route-line geodata, IPR metro entrances, Geofabrik OSM extract, current and planned cycling-route layers, Prague street and administrative-boundary services, and the ČÚZK terrain image into `data/raw/`. Existing files are skipped. The exact endpoints and filenames are in [`scripts/download.ps1`](scripts/download.ps1); provider availability and licensing should be checked before reuse.

The script does **not** download the SLDB 2021 city-part population workbook. Obtain `SLD21053-MC-OB.xlsx` from the [Czech Statistical Office's Prague census publication](https://csu.gov.cz/produkty/scitani-lidu-domu-a-bytu-hl-m-praha-analyza-vysledku-2021) and place it at `data/raw/SLD21053-MC-OB.xlsx` before preparing data.

## 2. Prepare

```powershell
.\scripts\prepare.ps1
```

This extracts archives and copies downloaded inputs into `data/processed/`. It checks for the population workbook and stops if it is missing. Existing staged files are skipped. The cleaned GeoPackages and terrain raster are created in the next step.

## 3. Analyze

```powershell
.\scripts\analyze.ps1
```

This runs cleaning, graph construction, metro/train accessibility, corridor scoring, unit tests and the map export. Metro entrances and Prague train-served GTFS stops are snapped to street nodes within 200 m; PID route lines remain descriptive context. [`scripts/analyze.ps1`](scripts/analyze.ps1) lists the stages in execution order. The run can be lengthy and needs the bulk inputs downloaded above.

Main local outputs are under `data/cleaned/` and `data/derived/`. Generated summaries are written under `reports/`; the browser map is `reports/prague_graph_interactive.html`. Bulk data and intermediate summaries are ignored by Git. The published notebook is left unchanged, so a fresh local run may produce data newer than the notebook's committed snapshot.

## Check or open results

```powershell
python -m unittest discover -s tests -q
python scripts/check.py
```

`check.py` validates the committed notebook and its saved outputs **without executing or changing it**. Open [the map](reports/prague_graph_interactive.html) in a browser or, once Pages is enabled, use the link in the main README. The optional cadastral overlay and basemap need internet access.

The entry points are `download.ps1`, `prepare.ps1`, `analyze.ps1`, and `check.py`. Python modules use short names matching their role in the pipeline.
