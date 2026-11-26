# NZ 2026 election special votes projection

Live at https://h-yd-n.github.io/nz-election-2026/

`index.html` is a built, self-contained page. Edit `template.html` (and `build.py` for data), then rebuild:

```bash
python3 build.py --static
```

This pulls the Electoral Commission feed (https://media.election.net.nz/xml/), embeds it with the 2023 comparison data, and writes `index.html`. Commit both the source and `index.html`.

`python3 build.py --serve` runs a local live-refreshing copy on http://localhost:8765/election.html.

## Official results

`prelim2026.json` is the frozen election-night data. When the feed's timestamp no longer matches it, `build.py` switches to final mode: the Parliament tab shows change from prelim, and the specials tab and electorate cards compare election night, the 2023 specials lean and the actual 2026 specials. On release day:

```bash
python3 build.py --static
```

Check the output says `FINAL MODE`. If the feed has moved, pass `--base <url>` (folder holding election.xml). If it 404s, it isn't up yet.

## Data files

- `results2023.json`, `specials2023.json`: 2023 results and special votes from archive.electionresults.govt.nz
- `vpcache.json`, `transfers.json`: cached voting-place results and the fitted party-to-candidate vote splits
- `prelim2026.json`: election-night snapshot, used for the final-vs-prelim comparison
- `history.json`, `nz.json`: election-night history and coastline (not shown on the page now)

## Share image

`og.png` is rendered from `og-card.html`: party colours sized by preliminary seats, typed in. The render command is in a comment at the top of that file.
