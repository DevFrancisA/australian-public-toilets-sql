-- ============================================================================
--  queries.sql -- Analytical queries over the National Public Toilet Map
--  Database : toilets.db     Table : toilets     Dialect : SQLite
--
--  Conventions
--    * Boolean flags are stored as INTEGER 0/1 by load_data.py, so SUM(flag)
--      counts the "true" rows and AVG(flag) is the rate directly.
--    * Every query is separated by a semicolon and introduced by a
--      "-- QUERY n: Title" comment. run_queries.py uses that comment as the
--      label for the result table, so keep the format if you add queries.
--    * "24-hour" facilities are identified from the free-text OpeningHours
--      column, whose canonical value for round-the-clock access is
--      'OPEN: 24 hours'.
-- ============================================================================


-- QUERY 1: National totals and data coverage
-- A one-row sanity check: how big is the dataset, how many distinct places
-- does it span, and are the coordinates complete enough to map?
SELECT
    COUNT(*)                                             AS total_facilities,
    COUNT(DISTINCT State)                                AS states_covered,
    COUNT(DISTINCT Town)                                 AS towns_covered,
    COUNT(DISTINCT FacilityType)                         AS facility_types,
    SUM(CASE WHEN Latitude IS NULL
              OR Longitude IS NULL THEN 1 ELSE 0 END)    AS missing_coordinates,
    SUM(CASE WHEN State IS NULL THEN 1 ELSE 0 END)       AS missing_state
FROM toilets;


-- QUERY 2: Facilities by state, with each state's share of the national total
-- A scalar subquery in the SELECT list supplies the national denominator, so
-- the percentages are computed in one pass without a join.
SELECT
    COALESCE(State, '(unknown)')                         AS state,
    COUNT(*)                                             AS facilities,
    ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM toilets), 2)
                                                         AS pct_of_national
FROM toilets
GROUP BY State
ORDER BY facilities DESC;


-- QUERY 3: Pay-to-use toilets -- national count and rate
-- Conditional aggregation over the whole table: one row summarising how rare
-- paid facilities are in Australia.
SELECT
    COUNT(*)                                             AS total_facilities,
    SUM(PaymentRequired)                                 AS pay_toilets,
    COUNT(*) - SUM(PaymentRequired)                      AS free_toilets,
    ROUND(100.0 * AVG(PaymentRequired), 3)               AS pct_pay_toilets
FROM toilets;


-- QUERY 4: Pay-toilet hotspots -- per-state count, rate and national share
-- HAVING keeps only the states that charge anywhere, and a scalar subquery
-- gives each state's slice of the national pay-toilet population.
SELECT
    State                                                AS state,
    COUNT(*)                                             AS facilities,
    SUM(PaymentRequired)                                 AS pay_toilets,
    ROUND(100.0 * AVG(PaymentRequired), 2)               AS pct_of_state_paid,
    ROUND(100.0 * SUM(PaymentRequired)
          / (SELECT SUM(PaymentRequired) FROM toilets), 2)
                                                         AS share_of_all_pay_toilets
FROM toilets
WHERE State IS NOT NULL
GROUP BY State
HAVING SUM(PaymentRequired) > 0
ORDER BY pct_of_state_paid DESC;


-- QUERY 5: Accessibility rate by state
-- Four accessibility dimensions side by side. Accessible = wheelchair
-- accessible cubicle; Ambulant = grab rails for people who walk with
-- difficulty; ChangingPlaces = full adult-change facility with a hoist.
SELECT
    State                                                AS state,
    COUNT(*)                                             AS facilities,
    SUM(Accessible)                                      AS wheelchair_accessible,
    ROUND(100.0 * AVG(Accessible), 1)                    AS pct_accessible,
    ROUND(100.0 * AVG(Ambulant), 1)                      AS pct_ambulant,
    ROUND(100.0 * AVG(ParkingAccessible), 1)             AS pct_accessible_parking,
    ROUND(100.0 * AVG(ChangingPlaces), 1)                AS pct_changing_places
FROM toilets
WHERE State IS NOT NULL
GROUP BY State
HAVING COUNT(*) >= 100          -- ignore any thinly covered jurisdiction
ORDER BY pct_accessible DESC;


-- QUERY 6: Best-equipped towns for parents -- baby-change facilities
-- Restricted to towns with a meaningful number of facilities so the
-- percentage is not dominated by a town with two toilets and one table.
SELECT
    Town                                                 AS town,
    State                                                AS state,
    COUNT(*)                                             AS facilities,
    SUM(BabyChange)                                      AS with_baby_change,
    SUM(BabyCareRoom)                                    AS with_baby_care_room,
    ROUND(100.0 * AVG(BabyChange), 1)                    AS pct_baby_change
FROM toilets
WHERE Town IS NOT NULL
GROUP BY Town, State
HAVING COUNT(*) >= 25
ORDER BY pct_baby_change DESC, facilities DESC
LIMIT 20;


-- QUERY 7: Towns with the widest baby-change gap
-- The mirror image of QUERY 6, and the more actionable half: plenty of
-- facilities, but few of them usable with a baby in tow.
SELECT
    Town                                                 AS town,
    State                                                AS state,
    COUNT(*)                                             AS facilities,
    SUM(BabyChange)                                      AS with_baby_change,
    ROUND(100.0 * AVG(BabyChange), 1)                    AS pct_baby_change
FROM toilets
WHERE Town IS NOT NULL
GROUP BY Town, State
HAVING COUNT(*) >= 25
   AND AVG(BabyChange) < 0.25
ORDER BY facilities DESC
LIMIT 20;


-- QUERY 8: Road-trip filter -- showers AND open 24 hours AND free
-- A multi-condition feature filter of the kind a van-life traveller actually
-- runs, aggregated by state so the result stays readable.
SELECT
    State                                                AS state,
    COUNT(*)                                             AS shower_24h_free,
    SUM(DrinkingWater)                                   AS also_drinking_water,
    SUM(Parking)                                         AS also_parking,
    SUM(DumpPoint)                                       AS also_rv_dump_point
FROM toilets
WHERE Shower = 1
  AND PaymentRequired = 0
  AND OpeningHours LIKE '%24 hours%'
GROUP BY State
ORDER BY shower_24h_free DESC;


-- QUERY 9: The full road-trip shortlist -- individual facilities
-- Same idea as QUERY 8 but returning named facilities, with an RV dump point
-- and drinking water added to the filter.
SELECT
    Name                                                 AS name,
    Town                                                 AS town,
    State                                                AS state,
    FacilityType                                         AS facility_type,
    ROUND(Latitude, 5)                                   AS latitude,
    ROUND(Longitude, 5)                                  AS longitude
FROM toilets
WHERE Shower = 1
  AND DumpPoint = 1
  AND DrinkingWater = 1
  AND PaymentRequired = 0
  AND OpeningHours LIKE '%24 hours%'
ORDER BY State, Town
LIMIT 25;


-- QUERY 10: Opening-hours profile by state
-- Conditional aggregation turns one messy free-text column into a tidy
-- cross-tab of access patterns.
SELECT
    State                                                AS state,
    COUNT(*)                                             AS facilities,
    SUM(CASE WHEN OpeningHours LIKE '%24 hours%' THEN 1 ELSE 0 END)  AS open_24h,
    ROUND(100.0 * AVG(CASE WHEN OpeningHours LIKE '%24 hours%'
                           THEN 1 ELSE 0 END), 1)        AS pct_open_24h,
    SUM(CASE WHEN OpeningHours LIKE '%Daylight%' THEN 1 ELSE 0 END)  AS daylight_only,
    SUM(CASE WHEN OpeningHours LIKE '%Variable%' THEN 1 ELSE 0 END)  AS variable_hours,
    SUM(CASE WHEN OpeningHours LIKE '%closed%'   THEN 1 ELSE 0 END)  AS currently_closed,
    SUM(KeyRequired)                                     AS key_required,
    SUM(MLAK24)                                          AS mlak_24h_key
FROM toilets
WHERE State IS NOT NULL
GROUP BY State
ORDER BY pct_open_24h DESC;


-- QUERY 11: Geographic bounding box and centroid per state
-- MIN/MAX over the coordinate columns gives each state's extent; the span
-- columns are a quick plausibility check against the real map of Australia.
SELECT
    State                                                AS state,
    COUNT(*)                                             AS mapped_facilities,
    ROUND(MIN(Latitude),  4)                             AS south_lat,
    ROUND(MAX(Latitude),  4)                             AS north_lat,
    ROUND(MIN(Longitude), 4)                             AS west_lon,
    ROUND(MAX(Longitude), 4)                             AS east_lon,
    ROUND(AVG(Latitude),  4)                             AS centroid_lat,
    ROUND(AVG(Longitude), 4)                             AS centroid_lon,
    ROUND(MAX(Latitude)  - MIN(Latitude),  2)            AS lat_span_deg,
    ROUND(MAX(Longitude) - MIN(Longitude), 2)            AS lon_span_deg
FROM toilets
WHERE State IS NOT NULL
  AND Latitude IS NOT NULL
  AND Longitude IS NOT NULL
GROUP BY State
ORDER BY mapped_facilities DESC;


-- QUERY 12: Facility types ranked by how well equipped they are
-- Facility-type labels arrive with inconsistent casing ('Park or reserve' vs
-- 'Park or Reserve'), so a CTE folds them together before aggregating.
WITH typed AS (
    SELECT
        UPPER(SUBSTR(TRIM(FacilityType), 1, 1)) ||
        LOWER(SUBSTR(TRIM(FacilityType), 2))             AS facility_type,
        Accessible, BabyChange, Shower, DrinkingWater, Parking, PaymentRequired
    FROM toilets
    WHERE FacilityType IS NOT NULL
)
SELECT
    facility_type,
    COUNT(*)                                             AS facilities,
    ROUND(100.0 * AVG(Accessible),      1)               AS pct_accessible,
    ROUND(100.0 * AVG(BabyChange),      1)               AS pct_baby_change,
    ROUND(100.0 * AVG(Shower),          1)               AS pct_shower,
    ROUND(100.0 * AVG(DrinkingWater),   1)               AS pct_drinking_water,
    ROUND(100.0 * AVG(Parking),         1)               AS pct_parking,
    ROUND(100.0 * AVG(PaymentRequired), 1)               AS pct_paid
FROM typed
GROUP BY facility_type
HAVING COUNT(*) >= 50
ORDER BY facilities DESC;


-- QUERY 13: States that beat the national accessibility average
-- Two CTEs: one computes the national benchmark, the other the per-state rate.
-- The final SELECT joins them and reports the gap in percentage points.
WITH national AS (
    SELECT AVG(Accessible) AS national_rate
    FROM toilets
),
by_state AS (
    SELECT
        State                 AS state,
        COUNT(*)              AS facilities,
        AVG(Accessible)       AS state_rate
    FROM toilets
    WHERE State IS NOT NULL
    GROUP BY State
)
SELECT
    by_state.state,
    by_state.facilities,
    ROUND(100.0 * by_state.state_rate, 1)                AS pct_accessible,
    ROUND(100.0 * national.national_rate, 1)             AS national_pct,
    ROUND(100.0 * (by_state.state_rate - national.national_rate), 1)
                                                         AS gap_pct_points
FROM by_state
CROSS JOIN national
WHERE by_state.state_rate > national.national_rate
ORDER BY gap_pct_points DESC;


-- QUERY 14: An amenity score -- the best-equipped facilities in the country
-- Adding the boolean flags together scores each facility out of 8; a CTE keeps
-- the scoring expression in one place instead of repeating it in the ORDER BY.
WITH scored AS (
    SELECT
        Name, Town, State, FacilityType,
        (Accessible + BabyChange + Shower + DrinkingWater
         + Parking + ParkingAccessible + SanitaryDisposal + SharpsDisposal)
                                                         AS amenity_score,
        CASE WHEN OpeningHours LIKE '%24 hours%' THEN 'yes' ELSE 'no' END
                                                         AS open_24h
    FROM toilets
)
SELECT
    Name                                                 AS name,
    Town                                                 AS town,
    State                                                AS state,
    FacilityType                                         AS facility_type,
    amenity_score                                        AS score_out_of_8,
    open_24h
FROM scored
WHERE amenity_score >= 7
ORDER BY amenity_score DESC, State, Town
LIMIT 25;
