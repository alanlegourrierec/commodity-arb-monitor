"""Static assumptions: conversion factors, vessels, voyage legs and tracked routes.

Anything here can be overridden from the freight / assumptions CSV files
(see data/freight_assumptions.csv), so treat these as fallbacks.
"""

GAL_PER_BBL = 42.0
BBL_PER_TONNE = {
    "wti": 7.6,       # WTI Midland, ~42 API
    "murban": 7.6,    # ~40 API
    "gasoline": 8.45,
    "diesel": 7.45,
}
BBL_PER_TONNE_GENERIC = 7.33
MMBTU_PER_M3_LNG = 22.6
LNG_FILL = 0.985
MWH_PER_MMBTU = 0.29307

# key: (default, unit, label). Values whose unit starts with "%" are entered
# in percent in the CSV files and stored as fractions here.
PARAMS = {
    "FINANCING_RATE": (0.05, "%/yr", "Cargo financing rate"),
    "CARGO_INSURANCE": (0.0005, "% of value", "Cargo insurance"),
    "LOSSES_CRUDE": (0.002, "% of volume", "Transit losses, crude"),
    "LOSSES_PRODUCTS": (0.001, "% of volume", "Transit losses, products"),
    "CRUDE_DOCK_FEE": (0.35, "$/bbl", "Terminal and dock fees to load a ship in Houston"),
    "MISC_CRUDE": (0.05, "$/bbl", "Inspection, agency and misc, crude"),
    "MISC_PRODUCTS": (1.0, "$/t", "Inspection, agency and misc, products"),
    "VLSFO_BRENT_RATIO": (1.0, "x", "Bunker proxy, VLSFO $/t = Brent $/bbl x 7.33 x ratio"),
    "HH_MULTIPLIER": (1.15, "x", "US LNG feedgas formula, 1.15 x Henry Hub"),
    "LIQUEFACTION_FEE": (2.50, "$/MMBtu", "Tolling fee (sunk in the short run)"),
    "REGAS_EUROPE": (0.40, "$/MMBtu", "Regas and DES NWE discount to TTF"),
    "LNG_BOIL_OFF": (0.0010, "%/day", "LNG carrier boil-off rate"),
    "WAR_GULF": (0.08, "% of hull", "Hormuz transit war-risk premium (reference)"),
    "US_GASOLINE_DUTY": (0.525, "$/bbl", "US import duty on motor gasoline"),
    "US_DISTILLATE_DUTY": (0.105, "$/bbl", "US import duty on distillate fuel oil"),
}
PARAM_SOURCES = {
    "WAR_GULF": "Lloyd's List via Insurance Business, 9 Nov 2026 (7-9% quoted)",
    "CRUDE_DOCK_FEE": "Assumption. The Houston future is priced in the terminal, not on the ship",
    "US_GASOLINE_DUTY": "US tariff schedule, heading 2710.12.15",
    "US_DISTILLATE_DUTY": "US tariff schedule, heading 2710.19.11",
}

# Delivery terms, from the least to the most the seller takes on. A sale on one term leaves the
# seller with every cost up to that term and the buyer with the rest.
TERMS = ["FOB", "CFR", "CIF", "DAP", "DDP"]

# US Renewable Fuel Standard, share of each gallon of gasoline or diesel an obligated party covers
# with RINs. Refiners and importers are obligated, exported barrels are not. Final rule of 1 April 2026.
RFS_STANDARDS = {
    2026: dict(total=0.1550, advanced=0.0642, bbd=0.0524, cellulosic=0.0079),
    2027: dict(total=0.1578, advanced=0.0661, bbd=0.0537, cellulosic=0.0084),
}

# Assumptions that a public market price can replace: key -> (price key, factor to the assumption's unit).
FEEDS = {
    "ULSD_USGC_DIFF": ("ULSD_USGC_UPDOWN", 100.0),       # $/gal -> c/gal
    "ALU_ORIGIN_PREMIUM": ("ALU_DUP_RDM", 1.0),
}

# Inputs that come from licensed data in the private build. The public build uses a public
# substitute (model, futures, press) and says so on the page.
PRIVATE_NOTE = "Not allowed to share private data to public"
# Supramax legs are priced off the Baltic 10TC FFA (current month), or the S10 route for Kalimantan.
# A leg LSEG does not return falls back to market fixtures, and the private build says so.
SUPRAMAX_LEGS = {"RB_INDIA", "KALI_INDIA", "USG_INDIA", "SCRAP_US_TR", "SCRAP_US_IN", "SCRAP_EU_TR", "SCRAP_EU_IN"}
LICENCE_NOTE = "Supramax rate not returned by this LSEG package, market fixtures used"
LICENSED = {
    "TD25", "TD22", "TD3C", "TC14", "TC2", "LNG_ATL", "C5", "C3", "C4", "RB_JAPAN", "NEWC_JAPAN", "HP_CHINA",
    "HP_INDIA", "RB_INDIA", "KALI_INDIA", "USG_INDIA", "SCRAP_US_TR", "SCRAP_US_IN", "SCRAP_EU_TR", "SCRAP_EU_IN",
    "PETCOKE_USG",
}

ADJUSTMENTS = {
    "WTI_MIDLAND_CIF_DIFF": (0.0, "$/bbl", "WTI Midland CIF Rotterdam vs Brent"),
    "MURBAN_QUALITY_DIFF": (0.0, "$/bbl", "Quality adjustment WTI vs Murban delivered China"),
    "ULSD_USGC_DIFF": (-10.0, "c/gal", "Gulf Coast ULSD vs NYMEX HO"),
    "GASOIL_CARGO_DIFF": (0.0, "$/t", "ULSD cargo CIF NWE vs ICE gasoil"),
    "RBOB_QUALITY_ADJ": (0.0, "$/t", "Eurobob to RBOB blending cost"),
}

VESSELS = {
    "Aframax": dict(cargo_t=70_000, speed_kn=13.0, fuel_t_day=38.0, port_days=4.0,
                    port_costs=180_000, default_tce=40_000, hull_value=75e6),
    "VLCC": dict(cargo_t=270_000, speed_kn=13.0, fuel_t_day=60.0, port_days=5.0,
                 port_costs=300_000, default_tce=55_000, hull_value=140e6),
    "MR": dict(cargo_t=37_000, speed_kn=13.0, fuel_t_day=25.0, port_days=4.0,
               port_costs=120_000, default_tce=25_000, hull_value=50e6),
    "LNG carrier 174k m3": dict(cargo_m3=174_000, speed_kn=17.5, fuel_t_day=0.0, port_days=3.0,
                                port_costs=350_000, default_tce=40_000, hull_value=230e6),
    "Capesize": dict(cargo_t=170_000, speed_kn=12.5, fuel_t_day=35.0, port_days=6.0,
                     port_costs=250_000, default_tce=25_000, hull_value=70e6),
    "Panamax": dict(cargo_t=80_000, speed_kn=12.5, fuel_t_day=25.0, port_days=6.0,
                    port_costs=150_000, default_tce=15_000, hull_value=35e6),
    "Supramax": dict(cargo_t=58_000, speed_kn=12.5, fuel_t_day=24.0, port_days=6.0,
                     port_costs=120_000, default_tce=20_000, hull_value=32e6),
    "Handysize": dict(cargo_t=35_000, speed_kn=12.0, fuel_t_day=19.0, port_days=5.0,
                      port_costs=100_000, default_tce=14_000, hull_value=24e6),
}
HULL_SOURCES = {"VLCC": "AGBI, Sep 2026"}

# One-way distances in nautical miles, computed with the searoute package.
LEGS = {
    "TD25": dict(name="Houston → Rotterdam", vessel="Aframax", distance_nm=5015, canal=0, eu_ets=0.5),
    "TD22": dict(name="Houston → Ningbo (Cape)", vessel="VLCC", distance_nm=15041, canal=0),
    "TD3C": dict(name="Fujairah → Ningbo", vessel="VLCC", distance_nm=5442, canal=0,
                 war_zone="Gulf of Oman, JWC listed area"),
    "TC14": dict(name="Houston → Rotterdam", vessel="MR", distance_nm=5015, canal=0, eu_ets=0.5),
    "TC2": dict(name="Rotterdam → New York", vessel="MR", distance_nm=3295, canal=0, eu_ets=0.5),
    "LNG_NWE": dict(name="Sabine Pass → Rotterdam", vessel="LNG carrier 174k m3", distance_nm=4974, canal=0,
                    eu_ets=0.5),
    "LNG_PANAMA": dict(name="Sabine Pass → Tokyo (Panama)", vessel="LNG carrier 174k m3",
                       distance_nm=9328, canal=800_000),
    "LNG_CAPE": dict(name="Sabine Pass → Tokyo (Cape)", vessel="LNG carrier 174k m3",
                     distance_nm=15847, canal=0),
    "C5": dict(name="Port Hedland → Qingdao", vessel="Capesize", distance_nm=3531, canal=0),
    "C3": dict(name="Tubarão → Qingdao", vessel="Capesize", distance_nm=11137, canal=0),
    "C4": dict(name="Richards Bay → Rotterdam", vessel="Capesize", distance_nm=7136, canal=0, cargo_t=150_000,
               eu_ets=0.5),
    "RB_JAPAN": dict(name="Richards Bay → Kashima", vessel="Capesize", distance_nm=7862, canal=0, cargo_t=150_000),
    "NEWC_JAPAN": dict(name="Newcastle → Kashima", vessel="Panamax", distance_nm=4356, canal=0),
    "HP_CHINA": dict(name="Hay Point → Jingtang", vessel="Capesize", distance_nm=4431, canal=0, cargo_t=150_000),
    "HP_INDIA": dict(name="Hay Point → Paradip", vessel="Panamax", distance_nm=4899, canal=0, cargo_t=75_000),
    "RB_INDIA": dict(name="Richards Bay → Mundra", vessel="Supramax", distance_nm=3891, canal=0, cargo_t=55_000,
                     trip=True),
    "KALI_INDIA": dict(name="Taboneo → Mundra", vessel="Supramax", distance_nm=3651, canal=0, cargo_t=55_000),
    # Petcoke to India is fixed on a trip basis (delivery US Gulf, redelivery India), so no ballast leg.
    "USG_INDIA": dict(name="Port Arthur → Mundra (Cape)", vessel="Supramax", distance_nm=12275, canal=0,
                      cargo_t=60_000, trip=True),
    "SCRAP_US_TR": dict(name="New York → Iskenderun", vessel="Supramax", distance_nm=5233, canal=0, cargo_t=45_000,
                        trip=True),
    "SCRAP_US_IN": dict(name="New York → Nhava Sheva (Cape)", vessel="Supramax", distance_nm=11457, canal=0,
                        cargo_t=45_000, trip=True),
    "SCRAP_EU_TR": dict(name="Rotterdam → Iskenderun", vessel="Supramax", distance_nm=3419, canal=0,
                        cargo_t=45_000, eu_ets=0.5, trip=True),
    "SCRAP_EU_IN": dict(name="Rotterdam → Nhava Sheva (Cape)", vessel="Supramax", distance_nm=10830, canal=0,
                        cargo_t=45_000, eu_ets=0.5, trip=True),
}

# The three US LNG legs share one Atlantic spot rate.
FREIGHT_KEY = {"LNG_NWE": "LNG_ATL", "LNG_PANAMA": "LNG_ATL", "LNG_CAPE": "LNG_ATL"}

# EU ETS for shipping: from 2026 owners surrender allowances for 100% of emissions on voyages between
# EU ports and 50% on voyages into or out of the EU. `eu_ets` on a leg is that share. Charterparties
# pass the cost on, so it is added to freight.
# Bunkers: Rotterdam VLSFO for Atlantic legs, Singapore for legs that start east of Suez.
EAST_LEGS = {"TD3C", "C5", "C3", "NEWC_JAPAN", "HP_CHINA", "HP_INDIA", "KALI_INDIA", "RB_JAPAN", "RB_INDIA"}

CO2_PER_T_FUEL = 3.114      # t CO2 per t of VLSFO/HFO burnt
CO2_PER_T_LNG = 2.75        # t CO2 per t of LNG burnt (boil-off as fuel), methane slip ignored
LNG_T_PER_M3 = 0.45

# Forward curves. `basis` says where a contract sits in time:
#   delivery: a monthly index or swap, pinned to the middle of its month (JKM, TTF, coal, iron ore)
#   expiry:   a future that meets the spot price when it expires (crude, products, LME and COMEX metals)
# `follows` names the liquid curve a thin swap is moved with on a day it did not trade.
CURVES = {
    "BRENT": dict(exchange="ICEEUR", root="BRN", basis="expiry", unit="$/bbl", label="Brent"),
    "WTI": dict(exchange="NYMEX", root="CL", basis="expiry", unit="$/bbl", label="WTI"),
    "HO": dict(exchange="NYMEX", root="HO", basis="expiry", unit="$/gal", label="NY Harbor ULSD"),
    "RBOB": dict(exchange="NYMEX", root="RB", basis="expiry", unit="$/gal", label="RBOB"),
    "TTF": dict(exchange="ICEENDEX", root="TFM", basis="delivery", unit="EUR/MWh", label="TTF"),
    "JKM": dict(exchange="NYMEX", root="JKM", basis="delivery", unit="$/MMBtu", label="JKM"),
    "HENRY_HUB": dict(exchange="NYMEX", root="NG", basis="delivery", unit="$/MMBtu", label="Henry Hub"),
    "API2": dict(exchange="ICEEUR", root="ATW", basis="delivery", unit="$/t", label="API2"),
    "API4": dict(exchange="ICEEUR", root="AFR", basis="delivery", unit="$/t", label="API4"),
    "NEWC": dict(exchange="ICEEUR", root="NCF", basis="delivery", unit="$/t", label="Newcastle"),
    "IRON_ORE_62": dict(exchange="COMEX", root="TIO", basis="delivery", unit="$/dmt", label="Iron ore 62%"),
    "COKING_COAL": dict(exchange="SGX", root="ACF", basis="delivery", unit="$/t", label="PLV coking coal"),
    "VLSFO_SG": dict(exchange="NYMEX", root="S5F", basis="delivery", unit="$/t", label="VLSFO Singapore",
                     follows="BRENT"),
    "EUA": dict(exchange="ICEENDEX", root="ECF", basis="expiry", unit="EUR/t", label="EUA"),
    "WTI_HOUSTON": dict(exchange="ICEEUR", root="HOU", basis="expiry", unit="$/bbl", label="WTI Houston"),
    "DATED_BRENT": dict(exchange="NYMEX", root="AUB", basis="delivery", unit="$/bbl", label="Dated Brent",
                        follows="BRENT"),
    "MURBAN": dict(exchange="ICEAD", root="ADM", basis="delivery", unit="$/bbl", label="Murban"),
    "GASOIL_ICE": dict(exchange="ICEEUR", root="ULS", basis="expiry", unit="$/t", label="ICE gasoil"),
    "EUROBOB": dict(exchange="NYMEX", root="B7H", basis="delivery", unit="$/t", label="Eurobob", follows="BRENT"),
    "ULSD_USGC_UPDOWN": dict(exchange="NYMEX", root="LT", basis="delivery", unit="$/gal", label="Gulf Coast ULSD up-down",
                             hidden=True),
    "HRC_US": dict(exchange="COMEX", root="HRC", basis="delivery", unit="$/st", label="US Midwest HRC"),
    # Read for a cargo's dates only, and left off the curve tables.
    "HRC_NWE": dict(exchange="LME", root="HN", basis="delivery", unit="$/t", label="HRC NW Europe", hidden=True),
    "HRC_CHINA": dict(exchange="LME", root="HC", basis="delivery", unit="$/t", label="HRC FOB China", hidden=True),
    "LME_COPPER": dict(exchange="LME", root="CA", basis="expiry", unit="$/t", label="LME copper", hidden=True),
    "COMEX_COPPER": dict(exchange="COMEX", root="HG", basis="expiry", unit="$/lb", label="COMEX copper", hidden=True),
    "LME_ALU": dict(exchange="LME", root="AH", basis="expiry", unit="$/t", label="LME aluminium", hidden=True),
    "MIDWEST_PREMIUM": dict(exchange="COMEX", root="AUP", basis="delivery", unit="$/lb",
                            label="US Midwest aluminium premium", hidden=True),
    "JAPAN_PREMIUM": dict(exchange="COMEX", root="MJP", basis="delivery", unit="$/t",
                          label="Japan aluminium premium", hidden=True),
    "ALU_DUP_RDM": dict(exchange="COMEX", root="AEP", basis="delivery", unit="$/t",
                        label="Rotterdam duty-unpaid aluminium premium", hidden=True),
    "INDO_4200": dict(exchange="SGX", root="M42F", basis="delivery", unit="$/t", label="Indonesian 4,200 GAR", hidden=True),
    "DE_POWER": dict(exchange="EEX", root="DEBM", basis="delivery", unit="EUR/MWh", label="German power base", hidden=True),
    "VLSFO_RDM": dict(exchange="NYMEX", root="R5F", basis="delivery", unit="$/t", label="VLSFO Rotterdam", hidden=True,
                      follows="BRENT"),
}
CURVE_DEPTH = 7     # contracts fetched per curve
# Curve that prices a benchmark, when its key differs from the curve's.
PRICE_CURVE = {"WTI_CUSHING": "WTI", "ULSD_NYH": "HO", "TTF_EUR": "TTF"}

# A cargo bought today loads in about a month. Every price is read off its forward curve for the day
# the cargo loads or lands, so two prices always refer to the same moment and nothing jumps when a
# front contract expires.
LOAD_LEAD_DAYS = 30

ROUTES = [
    dict(id="CRUDE_USGC_NWE", family="Crude", leg="TD25", density="wti", curve="DATED_BRENT",
         name="WTI Houston → Rotterdam", unit="$/bbl", threshold=0.50,
         buy="In terminal, Houston", sell=("CIF", "Rotterdam")),
    dict(id="CRUDE_USGC_ASIA", family="Crude", leg="TD22", density="wti", curve="MURBAN",
         name="WTI Houston → Ningbo", unit="$/bbl", threshold=0.50,
         buy="In terminal, Houston", sell=("DAP", "Ningbo")),
    dict(id="DIESEL_USGC_NWE", family="Products", leg="TC14", density="diesel", curve="GASOIL_ICE",
         name="ULSD Houston → Rotterdam", unit="$/t", threshold=5.0,
         buy="FOB Houston, export cargo", sell=("CIF", "Rotterdam"), rvo="export",
         whose="for a refiner that exports"),
    dict(id="DIESEL_NWE_USAC", family="Products", leg="TC2", density="diesel", curve="HO",
         name="Gasoil Rotterdam → New York", unit="$/t", threshold=5.0,
         buy="FOB Rotterdam", sell=("DDP", "New York Harbor"), rvo="import", duty="US_DISTILLATE_DUTY"),
    dict(id="GASOLINE_NWE_USAC", family="Products", leg="TC2", density="gasoline", curve="RBOB",
         name="Eurobob Rotterdam → New York", unit="$/t", threshold=5.0,
         buy="FOB Rotterdam", sell=("DDP", "New York Harbor"), rvo="import", duty="US_GASOLINE_DUTY"),
    dict(id="LNG_US_NWE", family="LNG", leg="LNG_NWE", curve="TTF",
         name="US LNG → Europe", unit="$/MMBtu", threshold=0.30, buy="FOB Sabine Pass", sell=("DAP", "Rotterdam"),
         whose="for a tolling offtaker"),
    dict(id="LNG_US_ASIA_PANAMA", family="LNG", leg="LNG_PANAMA", curve="JKM",
         name="US LNG → Asia (Panama)", unit="$/MMBtu", threshold=0.30, buy="FOB Sabine Pass", sell=("DAP", "Tokyo Bay"),
         whose="for a tolling offtaker"),
    dict(id="LNG_US_ASIA_CAPE", family="LNG", leg="LNG_CAPE", curve="JKM",
         name="US LNG → Asia (Cape)", unit="$/MMBtu", threshold=0.30, buy="FOB Sabine Pass", sell=("DAP", "Tokyo Bay"),
         whose="for a tolling offtaker"),
]

# Base metals move in containers or breakbulk, so freight is a flat $/t assumption, not a voyage model.
METAL_LEGS = {
    "METAL_US": dict(name="Rotterdam → Baltimore", distance_nm=3615, transit_days=14),
    "METAL_JAPAN": dict(name="Rotterdam → Tokyo (Cape)", distance_nm=14609, transit_days=45),
}

BULK_PARAMS = {
    "IRON_ORE_MOISTURE": (0.08, "% of weight", "Iron ore moisture (freight is paid on wet tonnes)"),
    "COAL_MISC": (0.5, "$/t", "Coal inspection, agency and misc"),
    "COAL_SA_DISCOUNT_JAPAN": (5.0, "$/t", "Discount South African coal gives to Newcastle coal in Japan"),
    "LOSSES_BULK": (0.003, "% of volume", "Transit and handling loss, dry bulk"),
    "LME_LOADOUT": (50.0, "$/t", "LME warehouse load-out and delivery to port"),
    "COPPER_FREIGHT_US": (90.0, "$/t", "Cathode freight Rotterdam → US East Coast"),
    "ALU_FREIGHT_US": (120.0, "$/t", "Aluminium freight Rotterdam → US Midwest, sea + inland"),
    "ALU_FREIGHT_JAPAN": (140.0, "$/t", "Aluminium freight Rotterdam → Japan, via the Cape"),
    "COPPER_US_TARIFF": (0.0, "% of value", "US tariff on refined copper cathode"),
    "ALU_US_TARIFF": (0.50, "% of value", "US Section 232 tariff on aluminium"),
    "ALU_ORIGIN_PREMIUM": (500.0, "$/t", "Rotterdam duty-unpaid premium given up by shipping the metal out"),
    # India fuel: energy content (kcal/kg, NAR) and import duty
    "CV_API4": (6000.0, "kcal/kg", "South African coal, API4 basis"),
    "CV_INDO_4200": (3800.0, "kcal/kg", "Indonesian 4,200 GAR, net basis"),
    "CV_PETCOKE": (7900.0, "kcal/kg", "Petcoke 6.5% S, net basis"),
    "INDIA_COAL_DUTY": (0.0275, "% of value", "Indian import duty on steam coal"),
    "INDIA_PETCOKE_DUTY": (0.11, "% of value", "Indian import duty on petcoke"),
    # Steel
    "HRC_FOBBING_EU": (30.0, "$/t", "Coil from a Ruhr mill to FOB Antwerp, barge and port handling"),
    "HRC_FREIGHT_EU_US": (45.0, "$/t", "HRC freight Antwerp → US Gulf, breakbulk"),
    "HRC_FREIGHT_CN_EU": (55.0, "$/t", "HRC freight China → Antwerp, breakbulk"),
    "US_STEEL_TARIFF": (0.50, "% of value", "US Section 232 tariff on steel"),
    "US_IMPORT_DISCOUNT_HRC": (70.0, "$/t", "Discount imported coil gives US buyers, plus Houston port costs"),
    "EU_STEEL_DUTY": (0.50, "% of value", "EU out-of-quota steel duty"),
    "EU_AD_CHINA_HRC": (0.359, "% of value", "EU anti-dumping + countervailing duty on Chinese HRC, all other companies"),
    "CBAM_DEFAULT_HRC_CN": (3.187, "t CO2/t", "CBAM default emissions, hot-rolled flat (CN 7208) from China"),
    "CBAM_MARKUP": (0.10, "% of value", "CBAM mark-up on default values in 2026"),
    "CBAM_BENCHMARK_HRC": (1.370, "t CO2/t", "EU ETS benchmark for hot-rolled flat, BF-BOF route"),
    "CBAM_FACTOR": (0.975, "% of value", "CBAM factor in 2026 (free allocation still granted)"),
    "ORE_PER_T_HM": (1.6, "t", "Iron ore per tonne of hot metal"),
    "COAL_PER_T_HM": (0.55, "t", "Coking coal per tonne of hot metal"),
    "SCRAP_PER_T_REBAR": (1.1, "t", "Scrap per tonne of EAF rebar"),
    # Power and carbon (Germany)
    "COAL_PLANT_EFF": (0.38, "% of input", "Hard coal plant efficiency"),
    "CCGT_EFF": (0.50, "% of input", "Gas plant (CCGT) efficiency"),
    "MWH_PER_T_COAL": (6.978, "MWh/t", "Energy in a tonne of 6,000 kcal NAR coal"),
    "COAL_CO2": (0.341, "t/MWh", "CO2 per MWh of coal burnt (thermal)"),
    "GAS_CO2": (0.202, "t/MWh", "CO2 per MWh of gas burnt (thermal)"),
}
BULK_PARAM_SOURCES = {
    "LOSSES_BULK": "Assumption, inside the 0.5% weight tolerance usual in coal contracts",
    "COAL_SA_DISCOUNT_JAPAN": "Assumption. Japanese utilities are built around Australian coal and pay less for other origins",
    "HRC_FOBBING_EU": "Assumption. The European coil index is ex-works, not FOB",
    "COPPER_US_TARIFF": "Cathodes exempt from the Aug 2025 Section 232 copper tariff (check)",
    "ALU_US_TARIFF": "Section 232 rate since June 2025 (check)",
    "ALU_ORIGIN_PREMIUM": "Press level, used only if the CME duty-unpaid premium (AEP) is missing",
    "INDIA_COAL_DUTY": "Argus, June 2026 (NAPP coal 2.75%, coke 11%)",
    "INDIA_PETCOKE_DUTY": "Argus, June 2026",
    "EU_STEEL_DUTY": "Regulation 2026/1384, in force 1 July 2026 (quotas cut ~47%)",
    "US_STEEL_TARIFF": "Section 232 rate since June 2025 (check)",
    "US_IMPORT_DISCOUNT_HRC": "Assumption. An import has to sell under domestic coil to pay for 2-3 months of lead time",
    "EU_AD_CHINA_HRC": "Regs (EU) 2023/1122 and 2023/1123, in force to June 2028. Named mills pay 18.1% to 35.9%",
    "CBAM_DEFAULT_HRC_CN": "Implementing Regulation (EU) 2025/2621, Annex I",
    "CBAM_MARKUP": "Implementing Regulation (EU) 2025/2621",
    "CBAM_BENCHMARK_HRC": "Implementing Regulation (EU) 2025/2621, column B route C",
    "CBAM_FACTOR": "CBAM Regulation, 2026 phase-in",
}

BULK_ROUTES = [
    dict(id="ORE_AUS_CHINA", family="Iron ore", leg="C5", name="Iron ore Port Hedland → Qingdao", unit="$/dmt",
         buy="FOB Port Hedland", sell=("CFR", "Qingdao")),
    dict(id="ORE_BRA_CHINA", family="Iron ore", leg="C3", name="Iron ore Tubarão → Qingdao", unit="$/dmt",
         buy="FOB Tubarão", sell=("CFR", "Qingdao")),
    dict(id="COAL_RB_NWE", family="Coal", leg="C4", name="Coal Richards Bay → Rotterdam", unit="$/t", threshold=2.0,
         buy="FOB Richards Bay", sell=("CIF", "Rotterdam")),
    dict(id="COAL_RB_JAPAN", family="Coal", leg="RB_JAPAN", name="Coal Richards Bay → Japan", unit="$/t", threshold=2.0,
         buy="FOB Richards Bay", sell=("CFR", "Kashima")),
    dict(id="COPPER_LME_COMEX", family="Metals", leg="METAL_US", name="Copper LME Rotterdam → COMEX", unit="$/t",
         threshold=20.0, buy="In LME warehouse, Rotterdam", sell=("DDP", "COMEX warehouse, Baltimore")),
    dict(id="ALU_LME_MIDWEST", family="Metals", leg="METAL_US", name="Aluminium LME → US Midwest", unit="$/t",
         threshold=20.0, buy="In LME warehouse, Rotterdam", sell=("DDP", "US Midwest")),
    dict(id="ALU_LME_JAPAN", family="Metals", leg="METAL_JAPAN", name="Aluminium LME → Japan", unit="$/t",
         threshold=20.0, buy="In LME warehouse, Rotterdam", sell=("CIF", "Yokohama")),
    dict(id="COKING_CHINA", family="Coking coal", leg="HP_CHINA", name="PLV coking coal Hay Point → China",
         unit="$/t", buy="FOB Hay Point", sell=("CIF", "Jingtang")),
    dict(id="COKING_INDIA", family="Coking coal", leg="HP_INDIA", name="PLV coking coal Hay Point → India",
         unit="$/t", buy="FOB Hay Point", sell=("CIF", "Paradip")),
    dict(id="INDIA_SA", family="India fuel", leg="RB_INDIA", fuel="API4", name="South African coal → India",
         unit="$/t", threshold=2.0, buy="FOB Richards Bay", sell=("DDP", "Mundra")),
    dict(id="INDIA_INDO", family="India fuel", leg="KALI_INDIA", fuel="INDO_4200",
         name="Indonesian coal → India", unit="$/t", threshold=2.0, buy="FOB Kalimantan", sell=("DDP", "Mundra")),
    dict(id="INDIA_PETCOKE", family="India fuel", leg="USG_INDIA", fuel="PETCOKE_USG", name="US petcoke → India",
         unit="$/t", threshold=2.0, buy="FOB US Gulf", sell=("DDP", "Mundra")),
    dict(id="SCRAP_US_TURKEY", family="Scrap", leg="SCRAP_US_TR", dest="SCRAP_TURKEY",
         name="Scrap New York → Turkey", unit="$/t", buy="FOB New York", sell=("CFR", "Iskenderun")),
    dict(id="SCRAP_US_INDIA", family="Scrap", leg="SCRAP_US_IN", dest="SCRAP_INDIA",
         name="Scrap New York → India", unit="$/t", buy="FOB New York", sell=("CFR", "Nhava Sheva")),
    dict(id="SCRAP_EU_TURKEY", family="Scrap", leg="SCRAP_EU_TR", dest="SCRAP_TURKEY",
         name="Scrap Rotterdam → Turkey", unit="$/t", buy="FOB Rotterdam", sell=("CFR", "Iskenderun")),
    dict(id="SCRAP_EU_INDIA", family="Scrap", leg="SCRAP_EU_IN", dest="SCRAP_INDIA",
         name="Scrap Rotterdam → India", unit="$/t", buy="FOB Rotterdam", sell=("CFR", "Nhava Sheva")),
    dict(id="HRC_EU_US", family="Steel", leg=None, name="EU HRC → US Gulf", unit="$/t", threshold=20.0,
         transit_days=18, buy="Ex-works, Ruhr", sell=("DDP", "Houston")),
    dict(id="HRC_CN_EU_QUOTA", family="Steel", leg=None, name="China HRC → EU, in quota", unit="$/t",
         threshold=20.0, transit_days=40, buy="FOB Tianjin", sell=("DDP", "Antwerp")),
    dict(id="HRC_CN_EU_OUT", family="Steel", leg=None, name="China HRC → EU, out of quota", unit="$/t",
         threshold=20.0, transit_days=40, buy="FOB Tianjin", sell=("DDP", "Antwerp")),
]
ST_PER_TONNE = 1.10231       # short tons per metric tonne

LB_PER_TONNE = 2204.62

# Sanity bounds used to flag a bad price read.
PRICE_BOUNDS = {
    "WTI_CUSHING": (20, 250), "WTI_HOUSTON": (20, 250), "BRENT": (20, 250), "MURBAN": (20, 250),
    "RBOB": (0.8, 7.0), "ULSD_NYH": (0.8, 8.0), "GASOIL_ICE": (250, 2500), "HENRY_HUB": (0.5, 20),
    "TTF_USD": (2, 120), "JKM": (2, 120), "EUROBOB": (250, 2000),
    "IRON_ORE_62": (30, 300), "API2": (30, 500), "API4": (30, 500), "NEWC": (30, 500),
    "LME_COPPER": (3000, 30000), "COMEX_COPPER": (1.5, 15), "LME_ALU": (1000, 6000),
    "MIDWEST_PREMIUM": (0.01, 3.0), "JAPAN_PREMIUM": (0, 1500),
    "HOU_DIFF": (-10, 10), "DATED_BRENT": (20, 250), "ULSD_USGC_UPDOWN": (-1, 1), "ALU_DUP_RDM": (0, 2000),
    "TC20_FUT": (5, 500),
    "COKING_COAL": (80, 700), "INDO_4200": (20, 250), "PETCOKE_USG": (20, 400), "EUA": (5, 300),
    "DE_POWER": (-50, 1000), "VLSFO_SG": (200, 1500), "VLSFO_RDM": (200, 1500), "HSFO_SG": (150, 1200),
    "HRC_CHINA": (200, 1500), "HRC_NWE": (300, 2000), "HRC_US": (300, 3000), "SCRAP_TURKEY": (150, 900),
    "SCRAP_INDIA": (150, 900), "SCRAP_TAIWAN": (150, 900), "REBAR_TURKEY": (250, 1500),
    "RIN_D3": (0.01, 6), "RIN_D4": (0.01, 6), "RIN_D5": (0.01, 6), "RIN_D6": (0.01, 6),
}
STALE_DAYS = 10      # a manual price older than this is flagged
