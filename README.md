# Australian Public Toilets — a SQL analytics project

A small, self-contained analytics project built on the **National Public Toilet Map**:
25,563 public toilets across Australia, loaded into SQLite, interrogated with 14
analytical SQL queries, and plotted on an interactive map.

It answers questions like *which state has the most wheelchair-accessible toilets*,
*where can you find a free 24-hour shower on a road trip*, and *which towns have
plenty of public toilets but almost no baby-change tables* — and it is small enough
to read end to end in one sitting.

---

## The dataset

| | |
|---|---|
| **Source** | [National Public Toilet Map](https://toiletmap.gov.au) (Australian Government, Department of Health and Aged Care) |
| **Export used** | `toiletmapexport_260901_074429.csv` (12 MB) |
| **Rows** | 25,563 facilities |
| **Columns** | 47 |
| **Coverage** | 8 states/territories, 6,793 towns, 20 facility types |
| **Licence** | Check the current terms on toiletmap.gov.au before redistributing the raw data |

The raw CSV is **not** committed (see `.gitignore`) — download your own export from
the site and drop it in the project root.

### Shape of the data

Each row is one facility, with three kinds of column:

* **Identity and location** — `FacilityID`, `Name`, `FacilityType`, `Address1`,
  `Town`, `State`, `Latitude`, `Longitude`, `URL`.
* **Boolean amenity flags** stored in the CSV as the text `"True"` / `"False"` —
  29 of them, covering accessibility (`Accessible`, `Ambulant`, `LHTransfer`,
  `RHTransfer`, `ChangingPlaces`, `ParkingAccessible`), parenting (`BabyChange`,
  `BabyCareRoom`, `AdultChange`), travel (`Shower`, `DumpPoint`, `DPWashout`,
  `DrinkingWater`, `Parking`), access control (`KeyRequired`, `MLAK24`,
  `MLAKAfterHours`, `PaymentRequired`) and hygiene (`SanitaryDisposal`,
  `SharpsDisposal`, `MensPadDisposal`).
* **Free text** — `OpeningHours` (canonical values like `OPEN: 24 hours`,
  `OPEN: Daylight hours`, `Currently closed`), plus a `*Note` column for most
  feature groups.

`load_data.py` converts every one of those `"True"`/`"False"` columns into a real
`INTEGER` 0/1 column, which is what makes the SQL readable: `SUM(Accessible)` is a
count and `AVG(Accessible)` is a rate, with no `CASE` wrapper needed.

---

## Setup

Requires Python 3.9+ (3.10+ for the type hints as written).

```bash
git clone <your-fork-url>
cd toilet-project

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Put your toiletmapexport_*.csv in the project root, then:
python load_data.py              # CSV  -> toilets.db
python run_queries.py            # runs all 14 queries, prints tables
python map.py                    # -> toilets_map.html
```

Each script takes optional arguments:

```bash
python load_data.py path/to/export.csv custom.db
python run_queries.py queries.sql toilets.db --max-rows 50
python map.py --state QLD --sample 5000 --out qld.html
python map.py --where "BabyChange=1 AND Accessible=1"   # plot a SQL condition
python map.py --no-markers            # heatmap only (no filter panel)
```

---

## The scripts

### `load_data.py`

CSV → SQLite. It is deliberately defensive, because government data exports move:

* **Encoding fallback** — tries `utf-8`, falls back to `latin-1` on a
  `UnicodeDecodeError`.
* **Column-name normalisation** — `"Parking / Accessible (note)"` becomes
  `Parking_Accessible_note`; whitespace and slashes become underscores, brackets
  and punctuation are dropped, duplicates get a numeric suffix. (This export's
  names were already clean, so the script reports "column names were already
  clean" — it still runs, so a messier export loads without edits.)
* **Coordinate coercion** — any `Latitude`/`Longitude`/`lat`/`lon` column becomes
  a `REAL`, with unparseable values as `NULL` and a count reported.
* **Boolean detection** — a column is converted to `INTEGER` 0/1 when *every*
  non-null value is a recognised true/false token (`true/false/t/f/yes/no/1/0`,
  any casing). Nothing is hard-coded, so a new flag column in a future export is
  picked up automatically. This export: **29 columns converted**.
* **Indexes** on `State`, `Town` and `FacilityType`.
* Prints the row count and the full typed column list on completion.

### `queries.sql`

14 commented queries — see *Questions answered* below. Each is introduced by a
`-- QUERY n: Title` comment, which `run_queries.py` uses as the table label.

### `run_queries.py`

Runs every statement and prints a labelled, aligned table with thousands
separators and right-aligned numerics. Two details worth knowing:

* **One bad query cannot halt the run.** Each statement is executed in its own
  `try`, and a failure prints `SKIPPED — <error>` and moves on. The script exits
  `1` if anything failed, so it doubles as a CI smoke test.
* **The statement splitter is not a naive `text.split(";")`.** The explanatory
  comments in `queries.sql` contain semicolons, and a bare split shattered two
  queries mid-statement. The splitter walks the text instead, skipping over
  `--` comments and quoted strings, and only breaks on a semicolon at statement
  level.

### `map.py`

Reads coordinates from the database and writes `toilets_map.html` with two
switchable layers: **clustered markers** (colour-coded green = wheelchair
accessible, orange = payment required, blue = neither; click for an amenity
popup and a link back to toiletmap.gov.au) and a **heatmap** for continent-scale
density.

There are two ways to narrow what's on the map, and they're the reason the map
exists alongside the SQL rather than just duplicating toiletmap.gov.au:

**In the browser.** A filter panel lets you search by name or town, pick a
state, and tick any combination of twelve amenities — wheelchair accessible,
baby change, shower, drinking water, RV dump point, open 24 hours, free, no key
required, and so on. Filters combine with **AND**, which the official site
doesn't offer: "shower *and* dump point *and* 24-hour *and* free" is one click
each. The whole thing runs client-side; every marker was registered with its
packed row when the page loaded, so a filter change re-evaluates 25,000 rows in
memory and swaps the cluster's layer set and the heatmap's points. No server,
no reload, works from a double-clicked file.

**At build time.** `--where` takes any SQL condition over the `toilets` table
and plots only the matching rows, which turns a query from `queries.sql` into a
shareable map in one command:

```bash
python map.py --where "Shower=1 AND PaymentRequired=0 AND OpeningHours LIKE '%24 hours%'" --out roadtrip.html
```

That produces a 0.1 MB file of the 907 free 24-hour showers, with the condition
shown in the panel so the reader knows what was excluded. A malformed condition
is reported with the SQLite error rather than a traceback.

All 25,563 facilities fit in a **3.9 MB** file. Rendering 25,000 popups in Python
produced a 48 MB page instead, so each facility is shipped to the browser as a
compact array — `[lat, lon, name, town, state, type, hours, id, amenity_bitmask]` —
and the popup HTML is built on demand in JavaScript by `FastMarkerCluster`. The
same bitmask is what the filter panel tests against.

**The basemap.** OpenStreetMap's standard tiles are a general-purpose
reference map: every footpath, railway and POI icon in saturated colour, with
place names that only appear at high zoom. For "where am I, roughly, and where
is the nearest toilet" that is noise. A raster tile can't be restyled, so the
map uses [OpenFreeMap](https://openfreemap.org)'s **vector** tiles (free, no API
key) rendered by MapLibre GL, starting from their Positron style and recolouring
it in the browser to a Google-Maps-like palette:

* three soft colours with two shades each — land / built-up, water, park /
  woodland — plus roads in **exactly two greys** (minor, major);
* railways hidden; footpaths and building footprints only from street zoom;
* place labels in dark grey with a white halo from the country level down, so
  you always know roughly where you are, and nothing below 12px;
* the whole palette lives in one dict (`BASEMAP` in `map.py`) and is applied
  by layer id, so changing a colour is a one-line edit.

If WebGL is unavailable or the style can't be fetched, the page falls back to
Esri's Light Gray raster tiles automatically, so there is always a map under
the pins. (CARTO's Positron/Voyager rasters would have been the easy answer,
but they now watermark tiles served without an API key.)

**Visual hierarchy.** With the basemap deliberately soft and low-contrast, the
data owns the hue channel:

* **Pins** are 14px with a white halo *and* a dark outline, so they read on
  light, dark, green or blue ground alike, and they scale up on hover. The three
  category hues (`#1baf7a` accessible, `#2a78d6` standard, `#eb6834` paid) are
  the first three slots of a categorical palette pre-validated for
  colour-vision-deficiency separation across every pair.
* **Clusters** are dark bubbles sized in three steps by count, with abbreviated
  labels (`8.9k`), so they read as "a group" rather than "a big pin". Hovering
  one outlines the area its facilities span.
* The **heatmap** uses a single blue ramp, light to dark — density is a
  magnitude, and a rainbow gradient would imply categories that aren't there.
* **Text** is one sans-serif family (Source Sans 3, falling back to the system
  UI font), nothing below 12px, with section headings and popup kickers in
  letter-spaced grey capitals so the facility name is the only thing that
  shouts. Tooltips are dark on light so they win over basemap labels.
* A custom dot cursor (ring over anything clickable), because the default arrow
  vanished against the pale tiles; Safari doesn't support SVG cursors and falls
  back to the standard ones.

#### Why not just use toiletmap.gov.au?

For finding the nearest toilet right now, you should — it has directions, a
mobile UI and live data. What it can't do is answer questions about the dataset
as a whole (that's what `queries.sql` is for), or combine several filters at
once (that's what the panel is for). The map here is the bridge between the two:
a query's result set, on a map, with the filters still adjustable.

---

## Questions answered

| # | Question | Technique on show |
|---|---|---|
| 1 | How big is the dataset, and how complete are the key fields? | `COUNT(DISTINCT …)`, conditional aggregation for null counts |
| 2 | How are facilities distributed across the states? | `GROUP BY` + scalar subquery for the national denominator |
| 3 | How common are pay-to-use toilets nationally? | `SUM`/`AVG` over a 0/1 flag |
| 4 | Which states charge most often, and who holds the most pay toilets? | `HAVING`, scalar subquery, two percentages with different denominators |
| 5 | How does accessibility provision vary by state? | Four `AVG(flag)` rates side by side, `HAVING COUNT(*) >= 100` |
| 6 | Which towns are best equipped for parents? | `GROUP BY Town, State` with a `HAVING` minimum-size filter |
| 7 | Which towns have many toilets but few baby-change tables? | `HAVING` on both a count and an aggregate rate |
| 8 | Where can a road-tripper find a free 24-hour shower? | Multi-condition `WHERE` across flags + a `LIKE` on free text |
| 9 | Which specific facilities have shower + dump point + water, free, 24h? | The same filter returning rows rather than aggregates |
| 10 | What do opening-hours patterns look like per state? | `SUM(CASE WHEN … LIKE … THEN 1 ELSE 0 END)` cross-tab |
| 11 | What is each state's geographic bounding box and centroid? | `MIN`/`MAX`/`AVG` over coordinates, computed spans |
| 12 | Which facility types are best equipped? | CTE to normalise inconsistent label casing, then `HAVING` |
| 13 | Which states beat the national accessibility average? | Two CTEs + `CROSS JOIN` to compare against a benchmark |
| 14 | Which are the best-equipped individual facilities? | CTE computing a derived score from summed flags |

### A few of the findings

* **Pay toilets are vanishingly rare.** 163 of 25,563 — **0.64%**. Even the
  most-charging jurisdiction (NT, 1.52%) is barely above one in a hundred.
  NSW alone holds a third of all the pay toilets in the country.
* **Roughly 3 in 5 facilities are wheelchair accessible** (59.7% nationally),
  ranging from NSW at 62.1% down to NT at 49.4%.
* **Opening hours vary enormously by state.** 62% of Tasmanian facilities are
  open 24 hours; in the ACT it is 17%, with a quarter of ACT facilities
  requiring a key.
* **907 facilities are free, open 24 hours and have a shower** — the road-tripper's
  shortlist. 86 of those also have an RV dump point, and 35 have a dump point
  *and* drinking water.
* **Facility type predicts amenities more strongly than state does.** Shopping
  centres are 87% accessible and 60% have baby change; service stations are 27%
  and 5%. Swimming pools are the most likely to charge (15.5%).
* **The bounding-box query doubles as a data-quality check.** Victoria's
  northern extent comes out at latitude −16.9, which is in far north Queensland.
  Following that up finds exactly **4 rows** whose coordinates sit in a different
  state from their `State` label (a Victorian community house plotted in Cairns,
  a Queensland foreshore plotted on the Mornington Peninsula, and two others) —
  a good reminder to sanity-check geography before mapping it.

---

## SQL techniques demonstrated

* `GROUP BY` on single and composite keys (`GROUP BY Town, State`)
* `HAVING` to filter on aggregates — on counts, on rates, and on both at once
* **Conditional aggregation** — `SUM(CASE WHEN … THEN 1 ELSE 0 END)` to build
  cross-tabs, and `AVG(flag)` as a rate over 0/1 columns
* **Scalar subqueries** in the `SELECT` list to supply a whole-table denominator
  without a join (queries 2 and 4)
* **CTEs** (`WITH`) — for pre-normalising dirty labels (12), for computing a
  benchmark and comparing against it (13), and for naming a derived score so it
  can be reused in `WHERE` and `ORDER BY` (14)
* `CROSS JOIN` against a one-row CTE — the idiomatic way to attach a global
  benchmark to every group
* **Free-text pattern matching** with `LIKE` on the `OpeningHours` column
* **Aggregate geometry** — `MIN`/`MAX`/`AVG` over coordinates for bounding boxes,
  centroids and spans
* `COALESCE` for null-safe grouping labels, `ROUND` for presentation
* Multi-condition boolean filters combining flag columns and text patterns
* Indexing the columns that the analytical queries group and filter on

---

## Possible next steps

* **Join to population data.** Toilets per 100,000 residents by LGA would turn
  raw counts into a provision measure. ABS Census data keyed on postcode or LGA
  would do it, and would make the "which towns are underserved" question
  defensible rather than suggestive.
* **Add real spatial queries.** Load the coordinates into SpatiaLite or PostGIS
  and ask distance questions: nearest accessible toilet to any point, coverage
  gaps along highways, how far apart the 24-hour facilities are on a given route.
* **Nearest-neighbour desert analysis.** Even without PostGIS, a haversine
  calculation would identify the longest stretch of highway with no facility —
  the single most useful output for a traveller.
* **Track changes over time.** The site publishes fresh exports; loading several
  into date-stamped tables would show which councils are adding accessible
  facilities and which are closing toilets.
* **Clean the geography.** Reverse-geocode the coordinates and flag every row
  whose `State` disagrees, rather than catching only the four obvious outliers.
* **Normalise the schema.** Split the 29 flags into a tidy
  `facility_id / feature / value` table, which makes "top N features by state"
  a single `GROUP BY` instead of 29 hand-written columns.
* **Mine the note columns.** `AccessNote`, `ToiletNote` and `OpeningHoursNote`
  hold thousands of rows of free text about temporary closures and access
  quirks — a keyword pass would surface facilities that are listed as open but
  described as closed.
* **Make the map fully queryable.** The panel covers fixed filters; a free-form
  SQL box in the browser would need the database reachable from the page —
  either a small FastAPI layer over `toilets.db`, or sql.js loading the SQLite
  file directly in the browser with no server at all.

---

## Project layout

```
.
├── load_data.py        CSV -> SQLite, with type coercion and boolean detection
├── queries.sql         14 commented analytical queries
├── run_queries.py      Runs them all, prints labelled tables, skips failures
├── map.py              Interactive folium map (clusters + heatmap + filter panel, --where)
├── requirements.txt    pandas, folium
├── .gitignore          Excludes the database, generated HTML and the raw CSV
└── README.md
```

Generated at runtime and intentionally untracked: `toilets.db`, `toilets_map.html`,
and the raw `toiletmapexport_*.csv`.
