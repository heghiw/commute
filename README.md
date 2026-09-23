# Historical paths and cycling access in Prague

This project tests whether vanished paths could close gaps in Prague's cycling network. It combines historical alignments with a bicycle-access-screened street graph, terrain, population and destination data. Each corridor is evaluated as a possible addition to the current network, then screened for road connections, crossings and overlap with existing streets.

## Results

The 1,178 mapped path features form 717 continuous corridors. Of these, 144 have at least two modeled access points to existing roads. The model now scores improved access to metro entrances and train-served stops separately. Four corridors pass the current missing-link screen; twelve more have modeled benefit but require a major-road crossing review. These are research results, not approved construction projects.

- [Analysis notebook](reports/project_analysis.ipynb) — study design, data, graph construction, regional comparisons, sensitivity and results.
- [Interactive map](reports/prague_graph_interactive.html) — all corridors and the existing road graph. Download the HTML file to open it locally.
- [Corridor screening data](reports/corridor_screening_summary.csv), [city-part summary](reports/regional_analysis_summary.csv) and [transit proximity data](reports/transit_context_corridors.csv).

## Method

Street centerlines are split at mapped intersections and weighted by length, slope and existing cycling infrastructure. Historical fragments are merged into corridors. Endpoint, crossing and nearby-road access are recorded separately; parallel alignments and duplicated access points are screened out. For each candidate, the model compares shortest-path costs to metro, train, underserved areas and services with and without that corridor. The [notebook](reports/project_analysis.ipynb) gives the equations and results.

Geometric proximity does not establish a usable or buildable connection. Crossing safety, land access and project cost require separate review before investment decisions.

For data sources, setup and script commands, see [RUNNING.md](RUNNING.md).
