# Debtors-Knell


I would like to resurrect a website from the internet archive.  I would like to scrape the contents of the internet archive "building on a budget", located here and get a full page screenshot of each deck from the following page with all the images rendered: 

https://web.archive.org/web/20090601202842/https://www.wizards.com/Magic/Magazine/Archive.aspx?tag=Building%20on%20a%20Budget&description=Building%20on%20a%20Budget

Please save them as YYYYMMDD_DeckName folder and please also extract the actual decklist in MTGO format.

It looks like there are 13 pages of 25 decks each.

Please add a log.md to keep track of the status of each deck scraped.

==================

I would not like to make a flask application that aggregates all of this data 

## Site configuration

### Google Analytics

The webapp and the frozen GitHub Pages build both read the Google Analytics 4
measurement ID from the `GA_MEASUREMENT_ID` environment variable. When it is
unset (a local dev run, or a fork that hasn't configured one) no analytics
snippet and no request to Google go into the pages at all.

Locally:

```bash
GA_MEASUREMENT_ID=G-XXXXXXXXXX python3 webapp/app.py
```

For GitHub Pages, set it once under **Settings -> Secrets and variables ->
Actions -> Variables** as a repository variable named `GA_MEASUREMENT_ID`
(a secret of the same name also works, if you'd rather it stay out of the
Actions logs). `.github/workflows/deploy-pages.yml` passes it to
`staticsite/freeze.py`, which bakes the tag into every frozen page.
