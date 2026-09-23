# Data layout

The bulk data are deliberately not stored in Git. The pipeline creates:

- `raw/`: downloaded source files and manually supplied population workbook;
- `processed/`: staged files and archive extraction, including PID line and metro-entrance geodata;
- `cleaned/`: normalized GeoPackages, CSV and terrain raster;
- `derived/`: graph, candidate, and scoring outputs;
- `manual/`: local access-point reviews.

Run `scripts/download.ps1`, `scripts/prepare.ps1`, and then `scripts/analyze.ps1` to create local outputs. The population workbook `SLD21053-MC-OB.xlsx` is not downloaded by the script and must be supplied in `data/raw/` for the complete pipeline. See [RUNNING.md](../RUNNING.md) for the full guide.

The public GitHub presentation uses compact snapshots in `reports/`; source data licensing remains with the original providers. See the notebook source table and `scripts/download.ps1` for exact acquisition endpoints.
