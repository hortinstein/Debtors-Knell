# Price archive

Populated daily by `.github/workflows/fetch-prices.yml` running
`scripts/fetch_prices.py`. See `research/PRICE_DATA_SOURCES.md` for why these
two sources were picked and what their real historical depth is.

```
prices/
  daily/<YYYY-MM-DD>/
    goatbots/card-definitions.zip   GoatBots MTGO card id -> name/set/rarity/foil
    goatbots/price-history.zip      GoatBots MTGO per-card tix price, that day's snapshot
    mtgjson/AllPricesToday.json.bz2 MTGJSON paper+MTGO per-card price, that day's snapshot
  goatbots_yearly_archive/<YEAR>/<YYYY-MM-DD>.txt.gz
                                     GoatBots' own yearly bulk-history files, unpacked to one
                                     gzipped file per day (GitHub hard-rejects files over
                                     100MB, and the original per-year zips exceed that).
                                     Backfilled once per day per file - see script docstring.
```

A day's folder under `daily/` is never overwritten by a later run (each run's
target path is date-stamped); rerunning the script the same day is a no-op
unless `--force` is passed. The yearly-archive backfill only ever writes days
it doesn't already have a local copy of.
