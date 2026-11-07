# NZ 2026 election results

Live at https://h-yd-n.github.io/nz-election-2026/

`index.html` is a built, self-contained page. Edit `template.html` (and `build.py` for data), then rebuild:

```bash
python3 build.py --static
```

This pulls the Electoral Commission feed (https://media.election.net.nz/xml/), embeds it with the 2023 comparison data, and writes `index.html`. Commit both the source and `index.html`.

`python3 build.py --serve` runs a local live-refreshing copy on http://localhost:8765/election.html.

## Data files

- `results2023.json`, `specials2023.json`: 2023 results and special votes from archive.electionresults.govt.nz
- `vpcache.json`, `transfers.json`: cached voting-place results and the fitted party-to-candidate vote splits
- `history.json`, `nz.json`: election-night history and coastline (not shown on the page now)
