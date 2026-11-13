# Commodity Arbitrage Monitor

Every weekday morning this checks whether moving a cargo from one region to another still pays once the ship, the allowances, the duties and the forward curve are paid for. It is built the way a physical desk looks at its book, in four pages.

| Page | What it answers |
|---|---|
| Energy | Does US crude pay into Europe or China, do products cross the Atlantic, and should a US LNG cargo go to Europe or Asia |
| Dry bulk & metals | Where a Richards Bay coal cargo should go, the cheapest delivered energy into India, coking coal, the coal-to-gas switch in German power, iron ore and scrap netbacks, imported coil against local prices, copper and aluminium out of LME Rotterdam |
| Freight | Chokepoint traffic from IMF PortWatch, which trades can pay more for ships, what a day at sea costs, curve structure and floating storage |
| Trade ideas | One physical trade per desk, laid out from the purchase to the sale, with the P&L per cargo today, if it works and if it goes wrong |

The live page is at https://alanlegourrierec.github.io/commodity-arb-monitor/

For each route it computes the margin, the break-even freight, the break-even TCE in $/day, the P&L for a cargo, what the cargo costs at each delivery term and what a day of delay costs. Every page keeps a daily history.

## How it works

```
prices.py    Yahoo Finance, TradingView scanner, manual inputs, forward curves  ->  one price table with sources
inputs.py    freight / assumption overrides (CSV)
calc.py      voyage model, EU ETS, RINs, curve time spread, energy routes
bulk.py      coal, India fuel, coking coal, carbon, iron ore, scrap, steel, base metals
terms.py     what each benchmark includes, and the cost of a cargo from FOB to duty paid
shipping.py  PortWatch chokepoints, vessel day costs, curve table, storage check, freight headroom
ideas.py     trade ideas, a live deal sheet behind dated text
tools/       LSEG pull for the private build, Windows scheduling
history.py   one row per route per run (data/history.csv)
report.py    static HTML pages with Plotly (templates/)
run.py       ties it together
```

Freight comes from a rate override when there is one, as a Worldscale rate with its flat rate, $/t, a lump sum or a TCE in $/day. Otherwise it comes from a voyage model.

```
voyage cost = TCE x days + fuel x sea days x VLSFO + port costs + canal (+ EU allowances)
```

Round voyages pay the ballast leg. Trip charters, such as petcoke and scrap to India, do not. Distances and map lanes come from `searoute`. Vessel, route and cost assumptions are in `arb/config.py`.

### Delivery terms

A physical price only means something with its term, so every benchmark carries its basis and every route is sold on a term.

| Term | What the seller has paid |
|---|---|
| FOB | The cargo on board at the load port |
| CFR | FOB plus freight, with EU allowances and any war-risk premium |
| CIF | CFR plus cargo insurance |
| DAP | CIF plus what is lost on the way, since the cargo is sold on what arrives |
| Duty paid | DAP plus import duties, tariffs, CBAM and, in the US, the RINs an importer owes |

A route only pays the costs up to the term it is sold on. API2 is CIF, so a coal seller into Rotterdam insures the cargo. Iron ore and scrap are CFR, so the buyer does. LNG is sold delivered, so the boil-off is the seller's. Products into New York are sold duty paid. Costs past the sale term are shown but stay out of the margin. Financing and inspection stay with the seller on every term.

Some benchmarks sit below FOB. The Houston crude future is priced in the terminal, and the European coil index is ex-works. The model adds the cost of reaching the ship in both cases, as a labelled assumption.

### Modelling choices worth knowing

- **The curve.** A cargo is bought at today's price and sold at the price for its arrival. JKM, TTF, coal and iron ore are monthly indices, so the contract of the arrival month is used. For crude and products the front-month slope is applied over the laden days. In steep backwardation this alone can shut a long-haul arb.
- **EU ETS.** From 2026 shipping surrenders allowances for 50% of emissions on voyages into or out of the EU. The cost is added to freight on European legs.
- **RINs.** US refiners and importers retire RINs for each gallon of gasoline or diesel sold at home, about 32 c/gal at the 2026 standards and October RIN prices. Exported barrels carry none. A cargo landed in New York pays them. The Gulf Coast export route shows what a refiner gains over a domestic sale, and is labelled as the refiner's margin.
- **Whose margin.** A spot cargo costs what its next best outlet pays. The LNG margin over Henry Hub plus the tolling fee belongs to a long-term offtaker, and the page says so. The LNG trade idea buys the cargo at its European netback and earns the gap to Asia.
- **Bunkers.** VLSFO futures for Rotterdam and Singapore, by where the leg starts.
- **Crude.** WTI delivered Rotterdam is valued off Dated Brent, since WTI Midland sits in the Dated basket. Into China, WTI competes with Murban delivered.
- **Coal.** Richards Bay coal is paid at API2 in Europe. In Japan it competes with Newcastle coal plus the Newcastle to Japan freight.
- **India fuel.** Fuels are compared per million kcal, landed and duty paid. A fuel's margin is how much it could rise before the next cheapest one takes the buyer.
- **Metals.** The US aluminium tariff is charged on the metal value, and shipping metal out of Rotterdam gives up the local duty-unpaid premium.
- **Steel.** Imported coil pays the US Section 232 tariff on its FOB value, or EU anti-dumping and out-of-quota duties on its CIF value. An import also sells below domestic coil, which is an assumption.

## Data

Prices come from Yahoo Finance and TradingView's scanner, for front months and the first contracts of each curve. That covers crude with Dated Brent and the Houston price, products with Eurobob and the Gulf Coast ULSD up-down, gas, coal, coking coal, iron ore, LME and COMEX metals with the Rotterdam aluminium premium, coil, rebar and scrap, EUA, German power, marine fuels and the TC20 tanker future. Chokepoint transits come from IMF PortWatch and are kept in `data/chokepoints.json`.

A fetched price outside its sanity bounds is dropped and the manual level in `data/market_inputs.csv` takes over. Petcoke and RINs have no public feed and are typed in by hand. Any manual price older than ten days is flagged on the page.

Regulatory inputs are taken from the texts and computed, not guessed. That means the EU anti-dumping and countervailing duty on Chinese hot-rolled coil (Regulations 2023/1122 and 2023/1123), CBAM certificates per tonne (Implementing Regulation 2025/2621) priced at the live EUA, the US import duty on gasoline and distillate, and the EPA renewable fuel standards for 2026 and 2027.

### Public and private builds

Baltic freight rates, FFAs and Argus or Platts assessments are licensed, so they never go into this repo. The public page uses a public substitute, such as the voyage model, rates reported in the press with their date, or exchange futures. It marks those numbers with "Not allowed to share private data to public". The private build reads `private/freight.csv`, which has the same columns as `data/freight_assumptions.csv`. The folder is gitignored.

```
python run.py --private private      # writes build_private/
```

`private/trade_ideas.json`, if present, adds the freight trade ideas to that build. Commodity trade ideas live in `data/commodity_ideas.json`.

### Pulling the licensed rates from LSEG Workspace

`tools/lseg_pull.py` fills `private/freight.csv` from an open Workspace through the LSEG Data Library, in a desktop session, so the data stays on the machine under its licence. The codes are in `data/lseg_map.csv`. Monthly FFA codes roll by themselves, and a code that returns nothing keeps its last value and date.

On Windows, `tools\setup_windows.bat` does the whole setup in one go. It installs the Python packages, stores the app key from the App Key Generator in Workspace, pulls the rates once, registers the 07:30 weekday task and builds the private pages. By hand it looks like this.

```
pip install -r requirements-lseg.txt
setx LSEG_APP_KEY <key from the App Key Generator in Workspace>
python tools/lseg_pull.py --check                    # what each code returns, nothing written
python tools/lseg_pull.py --find "Baltic Supramax"   # look up a code
tools\update_private.bat --open                      # pull, build, open build_private/index.html
```

The codes in use are the Baltic tanker routes and C3/C5, the Capesize 5TC FFA, Panamax 82 5TC (.BPWT), the Supramax 58 10TC FFA for the current month (BASIFEM) on Atlantic and South Africa legs, the S10 route (.BASI) for Kalimantan, and the NYMEX future on the Baltic LNG route BLNG2-174. The Baltic LNG indices stop in December 2025 on this licence. Platts petcoke (CPAAF00) is not in the licence, so petcoke stays a typed-in level. A code the licence does not return keeps its last value, and a Supramax leg left without an LSEG rate is flagged on the page.

## Running it

```
pip install -r requirements-dev.txt
pytest
python run.py                        # fetch prices and PortWatch, update history, build site/
python run.py --prices-file tests/fixtures/prices_2026-11-12.json --no-history
```

`.github/workflows/daily.yml` does the same on weekdays at 06:40 UTC. It runs the tests, builds, commits the history and chokepoint data, and deploys to GitHub Pages.

## Limits

- Prices are front-month futures, delayed. Physical premiums and discounts from Platts or Argus are missing, and they are often what decides a cargo.
- There are no vessel positions, fixture lists or port line-ups, so nothing on how many ships are open in a loading area.
- Default TCEs are rough. In a stressed market like the 2026 Hormuz crisis they can be far off, which is what the rate overrides are for.
- Terminal fees, the cost of bringing coil to a ship, the discount on imported coil and the dry bulk handling loss are assumptions.
- Quality is not priced. That covers Brazilian 65% ore, coal sulphur and ash, and petcoke sulphur.
- Laytime and demurrage claims, letters of credit and payment terms are not modelled. The page only shows what one day of delay costs.
- RIN and petcoke prices are typed in by hand and go stale between updates.
- EU duties on Chinese coil use the "all other companies" rate. Named mills pay less.
- The TradingView endpoint is unofficial and can change.

## Next

- FFA curves for the main routes in the private build, to price freight for the loading month.
- Port line-ups and open tonnage counts from AIS, which needs a feed with a key.
- Guinea bauxite and Chilean copper concentrates.
