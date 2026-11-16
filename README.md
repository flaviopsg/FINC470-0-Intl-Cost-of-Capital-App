# International Cost of Capital App

A Streamlit app that estimates a project's **cost of equity and WACC in USD and in the currency of a
selected country**. Every intermediate step (D/E, relevered beta, each cost component, the
interest-rate differential and the local BBB spread) is shown with its formula and numeric substitution so
each number can be traced and checked by hand.

## What the app does

1. Fetches live data (cached for 6 hours; **🔄 Refresh data** clears the cache and refetches):
   unlevered industry betas, statutory corporate tax rates, the USD BBB spread, and 10-year government yields.
2. Lets you choose the industry and target country, set the equity risk premium and target debt share,
   choose the project tax rate (U.S. or selected country) with a switch, and override any fetched input.
3. Computes USD figures with CAPM and Hamada, converts them to the local currency with the yield
   differential, and shows the two currencies side by side.
4. Shows a step-by-step calculation table, an inputs table with the source of each value
   (fetched / manual override / fallback), an all-industry comparison (table + chart), and an
   "Assumptions & methodology" tab with the formulas and data sources with dates.
5. Downloads: current results (CSV), the step-by-step calculation (CSV), the all-industry table (CSV).

**Defaults on opening:** Software (System & Application), United States, U.S. tax rate,
ERP 6.5%, D/V 30%.

## Formulas (see `compute_cost_of_capital()` in `app.py`, tagged [F1]–[F12])

| ID | Item | Formula |
|----|------|---------|
| F1 | Debt-to-equity | D/E = (D/V) / (1 − D/V) |
| F2 | Levered beta (Hamada) | βL = βU × [1 + (1 − t) × D/E] |
| F3 | USD cost of equity | Ke$ = rf_US + βL × ERP |
| F4 | USD pre-tax cost of debt | Kd$ = rf_US + s_USD |
| F5 | USD after-tax cost of debt | Kd$ × (1 − t) |
| F6 | USD WACC | (1 − D/V) × Ke$ + D/V × Kd$ × (1 − t) |
| F7 | Rate differential | X = (1 + rf_FC) / (1 + rf_US) |
| F8 | Local cost of equity | Ke_FC = (1 + Ke$) × X − 1 |
| F9 | Local BBB spread (automatic) | s_FC = (1 + Kd$) × X − 1 − rf_FC = s_USD × X |
| F10 | Local pre-tax cost of debt | Kd_FC = rf_FC + s_FC |
| F11 | Local after-tax cost of debt | Kd_FC × (1 − t) |
| F12 | Local WACC | (1 − D/V) × Ke_FC + D/V × Kd_FC × (1 − t) |

* With the automatic spread, Kd_FC = (1 + Kd$) × X − 1, so the local pre-tax cost of debt equals the converted
  USD cost of debt exactly.
* The same t, D/V and ERP apply to both currencies; the ERP enters the local cost of equity through the
  conversion of Ke$.
* F12 differs from directly converting the USD WACC, (1 + WACC$) × X − 1, by D/V × t × (1 − X), because the tax
  shield applies to the whole local nominal rate. Both numbers and the gap are shown as memo rows.
* When the United States is selected, X = 1 and the two currencies coincide exactly (the local yield, tax rate
  and spread are tied to the U.S. values).

### Alternative conventions (not the default)

* **Additive spread:** s_FC = s_USD (the same spread in percentage points). It differs from F9 by
  s_USD × (X − 1), which is small unless the yield gap is large. You can apply it with the local-spread override.
* **Additive cost of equity:** Ke_FC = rf_FC + βL × ERP. It is shown as a memo row in the step table but is not
  used in the results. The default multiplicative (Fisher) conversion keeps the local and USD costs of equity
  consistent in real terms.
* Some courses use expected inflation (e.g. IMF forecasts) instead of 10-year yields for X. With the overrides
  you can enter yields that reproduce any inflation differential you want.

## Data sources

| Input | Source | Notes |
|---|---|---|
| Unlevered beta | Damodaran, `betas.xls` — https://pages.stern.nyu.edu/~adamodar/pc/datasets/betas.xls | The sheet and header row are found programmatically ("Industry Name" + "Unlevered beta"; the *cash-corrected* column is deliberately not used). The update date is read from the file's title rows. "Total Market" rows are excluded. |
| Corporate tax rate | OECD Corporate Tax Statistics 2024, statutory CIT rates — the URL in the brief | The page has no HTML table (the rates are shown as charts). The app first tries any HTML table, then downloads the figure's **StatLink** Excel file (Figure 2.1) and parses it. It prefers a "Combined" column, then the latest year column, then central + sub-central components. Country names and ISO3 codes are harmonised. |
| USD BBB spread | Damodaran, `ratings.html` | The Baa2/BBB row from the "large non-financial service firms" block (1.11% in the January 2026 table). The "as of" date is read from the page. |
| 10-year yields | Trading Economics, https://tradingeconomics.com/bonds | Rows whose link ends in `/<country>/government-bond-yield`; the yield is the `td#p` cell, and the quote date is the last cell. The country list is the intersection of the yield and tax-rate countries. |

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

## Deploy on Streamlit Community Cloud

1. Push `app.py`, `requirements.txt` and `README.md` to a GitHub repository.
2. Go to https://share.streamlit.io → **Create app** → pick the repository and branch, and set the main file to `app.py`.
3. Deploy. No secrets or API keys are needed.

## Known limitations

* **Scraping is fragile.** Any source can change its layout or block automated requests. Every fetch is wrapped
  in error handling. On failure the app shows a warning that names the source, switches to clearly labelled
  fallback values, and stays usable through the manual overrides. The "Data sources" panel shows each URL,
  the "as of" date, the fetch time and any error.
* **Trading Economics** builds its bond table in the browser and serves the rows from a bot-protected endpoint
  (`/bonds?partial=markets` returns an empty HTTP 202 without a browser token). From a cloud server, the live
  scrape will therefore often fail. In that case the app uses:
  * a dated snapshot of the 10-year yields read from tradingeconomics.com on **2026-11-16**, and
  * a live U.S. 10-year yield from FRED (series DGS10, no key needed), if FRED is reachable.

  Use the yield overrides for current local yields.
* **OECD StatLink layout** could not be inspected from the build environment, so its parser is heuristic.
  If it fails, approximate 2024 combined statutory rates are used and labelled as fallback.
* Fallback betas are illustrative only, for a handful of industries. The fallback BBB spread is Damodaran's
  January 2026 value (1.11%).
* Data dates differ by source: Damodaran updates annually (January), the OECD 2024 edition reports 2024 rates,
  and yields are daily quotes.
* Local 10-year yields of countries with sovereign default risk include a default spread; this is not stripped out.
* Hamada assumes zero debt beta; at very high D/V it produces implausible betas (the app warns above 80%).
