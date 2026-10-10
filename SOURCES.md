# Source research (checked 9 Oct 2026)

Honest summary: there is no free, stable, daily public page for most of these items.
What exists is either monthly official data, paid, or news articles with a new URL each day.
Only `usd_ngn_official` is enabled. Everything else below is a finding, not a working source.

| Item | Best source found | Frequency | Status |
|---|---|---|---|
| USD/NGN official | open.er-api.com JSON | daily | **Enabled** in sources.json |
| USD/NGN CBN | allratestoday.com CBN endpoint (free key, Bearer auth) | daily | Added, disabled: needs key, JSON key name unverified |
| Petrol (PMS) | NBS "Petrol Price Watch" (monthly national average) | monthly | Not automated yet (see below) |
| Diesel (AGO) | NBS "Diesel Price Watch" | monthly | Not automated yet |
| Rice, tomato, garri, beans, yam | NBS "Selected Food Prices Watch" (PDF + Excel per month) | monthly | Not automated yet |
| Bus / transport fares | NBS "Transport Fare Watch" | monthly | Not automated yet |
| Parallel USD rate | No clean public API found | n/a | Gap |

## What was checked and why it was not used
- **NBS (nigerianstat.gov.ng):** official and free. Each monthly report page has only a text summary
  plus a PDF and an Excel download (e.g. `/resource/selected_food_april_2024.xlsx`). No data table in the page,
  so a CSS selector will not work. Needs a small Excel importer. I could not open the Excel file in this
  session (it came back as binary), so I did not guess its layout.
- **globalpetrolprices.com/Nigeria:** shows weekly petrol/diesel in a table, but I could not confirm its
  terms on automated access and the date shown was stale (Jan 2026). Not used.
- **oilpriceapi.com (Nigeria gasoline):** free API key exists (`GASOLINE_RETAIL_NG_NGN`) but the page showed
  "Data unavailable" and says updated monthly. Not used.
- **Daily naira-rate news articles (techeconomy, primebusiness, etc.):** new URL every day and the text is
  copyrighted journalism. Not suitable for scraping.
- **Parallel rate:** the pages found are news articles or unclear aggregators. Safest options: a paid data
  feed, or log the Binance/other P2P USDT-NGN price through that platform's own documented API after
  reading its terms.

## Recommended next step
Build `import_nbs.py`: once a month, download the NBS Excel (a human downloads it, or a fixed URL pattern
if it proves stable), find the rows for petrol, diesel, rice, tomato and fares, and save them with the report
month. It gives you official, free, citable history for every item except the parallel rate. To do that
reliably, upload one recent NBS Excel file here so the importer can be written against the real layout.
