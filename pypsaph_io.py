"""
PyPSA-PH input/output module.

Reads the PyPSA-PH input workbooks, builds a PyPSA network at a chosen spatial and
temporal resolution, prepares the hand-off files for the next planning year, and
writes summary results.

Laboratory of Electrochemical Engineering (LEE), Department of Chemical Engineering,
University of the Philippines Diliman.
"""

import calendar
import gc
import os

import geopandas as gpd
import numpy as np
import pandas as pd

PYPSAPH_BASE_YEAR = 2030

# --------------------------------------------------------------------------------------
# Carrier definitions
# --------------------------------------------------------------------------------------

GENERATOR_CARRIERS = [
    "OCGT", "CCGT", "Diesel", "Bunker Fuel", "Coal - CFB", "Coal - SubC", "Coal - SuperC",
    "Offshore Wind", "Onshore Wind", "Ground-Mounted Solar PV", "Rooftop Solar PV",
    "Floating Solar PV", "Impounding Hydro", "Run-of-River Hydro", "Biomass", "Biogas",
    "Landfill Gas", "Geothermal",
]

STORAGE_CARRIERS = ["1-H Battery", "1.5-H Battery", "4-H Battery", "6-H Battery", "8-H Battery", "Pump Hydro"]

# Maps PyPSA-PH carrier names to the technology names used in PYPSAPH_COST.xlsx
TECH_MAP = {
    "OCGT": "OCGT", "CCGT": "CCGT",
    "Ground-Mounted Solar PV": "solar-utility", "Rooftop Solar PV": "solar-rooftop commercial",
    "Floating Solar PV": "solar-floating", "Onshore Wind": "onwind", "Offshore Wind": "offwind",
    "Diesel": "oil", "Bunker Fuel": "bunker",
    "Coal - SubC": "coal - subC", "Coal - CFB": "coal - CFB", "Coal - SuperC": "coal - superC",
    "Impounding Hydro": "hydro", "Run-of-River Hydro": "ror", "Geothermal": "geothermal",
    "Biogas": "biogas", "Landfill Gas": "landfill", "Biomass": "biomass",
    "1-H Battery": "1-h battery storage", "1.5-H Battery": "1.5-h battery storage",
    "4-H Battery": "4-h battery storage", "6-H Battery": "6-h battery storage",
    "8-H Battery": "8-h battery storage", "Pump Hydro": "Pumped-Storage-Hydro-store",
}

CARRIER_COLORS = {
    "Biomass": "#127E2A", "Biogas": "#496d8a", "Landfill Gas": "#192e40",
    "Impounding Hydro": "#1E81D4", "Run-of-River Hydro": "#8ab2d4",
    "Coal - CFB": "#342C27", "Coal - SubC": "#806250", "Coal - SuperC": "#a8685b",
    "Ground-Mounted Solar PV": "#FDB324", "Rooftop Solar PV": "#FDB324", "Floating Solar PV": "#FDB3FF",
    "Onshore Wind": "#3BBFE5", "Offshore Wind": "#3BBFFF",
    "Diesel": "#B41E25", "Bunker Fuel": "#B41EFF", "OCGT": "#564A41", "CCGT": "#564AFF",
    "Geothermal": "#E6622D", "load": "#FF0000",
    "1-H Battery": "#814AD4", "1.5-H Battery": "#A44AD4", "4-H Battery": "#9C34A3",
    "6-H Battery": "#FFD700", "8-H Battery": "#1E90FF", "Pump Hydro": "#104775",
}


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------

def remove_decimal_point(number):
    """13.8 -> '13_8', 230.0 -> '230'. Used to build bus names like 'Visayas_230'."""
    number_str = str(number)
    if "." not in number_str:
        return number_str
    integer_part, decimal_part = number_str.split(".", 1)
    if not decimal_part or all(c == "0" for c in decimal_part):
        return integer_part
    return f"{integer_part}_{decimal_part}"


def calc_annuity(r, n):
    """Capital recovery factor for discount rate r over n years."""
    if np.isnan(r) or np.isnan(n) or n == 0:
        print(f"WARNING: bad annuity inputs r={r}, n={n}")
        return np.nan
    if r == 0:
        return 1 / n
    denom = 1.0 - 1.0 / (1.0 + r) ** n
    if denom == 0 or np.isnan(denom):
        return np.nan
    return r / denom


def calc_discountfactor(r, n):
    return (1.0 + r) ** (-n)


def _bus_mapping(grouped_buses):
    """Original bus name -> aggregated zone name."""
    return {old: new for new, group in grouped_buses for old in group["name"]}


def _bus_id(mapping, bus, v_nom):
    return f"{mapping.get(bus, bus)}_{remove_decimal_point(v_nom)}"


def _write_sheet(df, file_path, sheet_name):
    """Write df to sheet_name, appending to file_path if it already exists."""
    if os.path.exists(file_path):
        with pd.ExcelWriter(file_path, engine="openpyxl", mode="a", if_sheet_exists="replace") as w:
            df.to_excel(w, sheet_name=sheet_name)
    else:
        with pd.ExcelWriter(file_path, engine="openpyxl") as w:
            df.to_excel(w, sheet_name=sheet_name)


# --------------------------------------------------------------------------------------
# Costs, time and space
# --------------------------------------------------------------------------------------

def get_costs_data(input_dir, year):
    """Read PYPSAPH_COST.xlsx (sheet pypsaph_cost_{year}) and derive annualised and marginal costs."""
    costs = pd.read_excel(os.path.join(input_dir, "PYPSAPH_COST.xlsx"), sheet_name=f"pypsaph_cost_{year}")
    costs.loc[costs.unit.str.contains("/kW"), "value"] *= 1e3
    costs.unit = costs.unit.str.replace("/kW", "/MW")
    costs = costs.set_index(["technology", "parameter"])

    defaults = {
        "FOM": 0, "VOM": 0, "efficiency": 1, "fuel": 0, "investment": 0,
        "lifetime": 25, "CO2 intensity": 0, "nom_discount_rate": 0.07, "heating_value": 0,
    }
    costs = costs.value.unstack().fillna(defaults)

    # Proxy technologies without their own techno-economic data
    for tech, proxy in [("bunker", "oil"), ("landfill", "OCGT"), ("biogas", "OCGT")]:
        for p in ["FOM", "VOM", "efficiency", "investment", "lifetime"]:
            costs.at[tech, p] = costs.at[proxy, p]

    # Fuel properties shared between technologies
    for p in ["CO2 intensity", "fuel", "heating_value"]:
        costs.at["biomass", p] = costs.at["solid biomass", p]
        costs.at["OCGT", p] = costs.at["gas", p]
        costs.at["CCGT", p] = costs.at["gas", p]

    annuity = costs.apply(lambda x: calc_annuity(x["nom_discount_rate"], x["lifetime"]), axis=1)
    costs["annualized_capital_cost"] = annuity * costs["investment"]
    costs["marginal_cost"] = costs["VOM"] + costs["fuel"] / costs["efficiency"]

    # Storage: add inverter / bicharger cost and efficiency
    # (applied after annualisation, as in the original model)
    for tech in ["1-h", "1.5-h", "4-h", "6-h", "8-h"]:
        name = f"{tech} battery storage"
        costs.at[name, "investment"] += costs.at["battery inverter", "investment"]
        costs.at[name, "efficiency"] = costs.at["battery inverter", "efficiency"]
    costs.at["Pumped-Storage-Hydro-store", "investment"] += costs.at["Pumped-Storage-Hydro-bicharger", "investment"]
    costs.at["Pumped-Storage-Hydro-store", "efficiency"] = costs.at["Pumped-Storage-Hydro-bicharger", "efficiency"]

    return costs


def set_snapshot(n, year, temporal_level="1h"):
    """
    Set an 8760-hour (Feb 29 removed) snapshot index at the given resolution.

    Snapshot weightings are set to the number of hours per step so that energy,
    operating cost and storage state of charge are counted over the full year at any
    resolution (PyPSA defaults every weighting to 1 regardless of the step length).
    """
    if calendar.isleap(year):
        snapshots = pd.date_range(f"{year + 1}-01-01 00:00:00", f"{year + 1}-12-31 23:59:59", freq=temporal_level)
        snapshots -= pd.DateOffset(years=1)
    else:
        snapshots = pd.date_range(f"{year}-01-01 00:00:00", f"{year}-12-31 23:59:59", freq=temporal_level)
    n.set_snapshots(snapshots)
    n.snapshot_weightings.loc[:, :] = pd.Timedelta(temporal_level) / pd.Timedelta("1h")
    return n


def change_temporal_resolution(df, temporal_level, year):
    """Aggregate (mean) to a coarser step or interpolate to a finer one."""
    if df.empty:
        raise ValueError("Input DataFrame is empty.")

    out = df.copy()
    out.index = pd.to_datetime(out.index, errors="coerce")
    out = out[~out.index.isna()].sort_index()
    if out.index.has_duplicates:
        out = out.groupby(out.index).mean()
    if out.empty:
        raise ValueError("DataFrame has invalid or missing timestamps.")

    is_leap = calendar.isleap(year)
    if is_leap:
        out.index += pd.DateOffset(years=1)

    step = pd.to_timedelta(temporal_level)
    if step >= pd.Timedelta("1h"):
        result = out.resample(temporal_level).mean()
    else:
        desired_end = pd.Timestamp(f"{year}-12-31 23:59:59")
        if is_leap:
            desired_end += pd.DateOffset(years=1)
        extra = pd.date_range(out.index[-1] + step, desired_end, freq=step)
        if not extra.empty:
            pad = pd.concat([out.iloc[[-1]]] * len(extra), ignore_index=True)
            pad.index = extra
            out = pd.concat([out, pad])
        out = out[~out.index.duplicated(keep="first")]
        result = out.resample(temporal_level).interpolate(method="linear")
        result = result[result.index <= desired_end]

    if is_leap:
        result.index -= pd.DateOffset(years=1)

    return result.round(3).astype("float16")


def aggregate_buses(shapefile, buses_df, area_df, spatial_level):
    """
    Group network buses into zones.

    `area_df` (PYPSAPH_SPATIAL_RESOLUTION_BARANGAY.xlsx) assigns every barangay (ADM4)
    to a zone at each spatial level. Barangay polygons from the PSA/NAMRIA boundary
    shapefile are dissolved into zones and each bus is assigned to the zone it sits in.
    Returns a pandas GroupBy of buses keyed by zone name.
    """
    label = f"spatiallevel_{spatial_level}"
    shapes = gpd.read_file(shapefile).to_crs(epsg=4326)

    if "ADM4_PCODE" in shapes.columns and "ADM4_PCODE" in area_df.columns:
        shapes = shapes[["ADM4_PCODE", "geometry"]].merge(
            area_df[["ADM4_PCODE", label]], on="ADM4_PCODE", how="left", validate="one_to_one")
        if shapes[label].isna().any():
            raise ValueError(f"{shapes[label].isna().sum()} barangays in the shapefile have no {label} assignment.")
    else:
        # Fallback: rely on identical row order between shapefile and spreadsheet
        if len(shapes) != len(area_df):
            raise ValueError("Shapefile and spatial resolution sheet have different row counts.")
        shapes = shapes[["geometry"]].assign(**{label: area_df[label].values})

    zones = shapes.rename(columns={label: "zone"}).dissolve(by="zone", as_index=False)
    del shapes
    gc.collect()

    buses = gpd.GeoDataFrame(
        buses_df.rename(columns={"bus": "name"}),
        geometry=gpd.points_from_xy(buses_df.x, buses_df.y),
        crs="EPSG:4326",
    )
    buses = gpd.sjoin(buses, zones[["zone", "geometry"]], how="left", predicate="within")

    unassigned = buses.zone.isna().sum()
    if unassigned:
        print(f"WARNING: {unassigned} buses fall outside every zone and are dropped: "
              f"{buses.loc[buses.zone.isna(), 'name'].tolist()[:10]}")

    return buses.groupby("zone")


# --------------------------------------------------------------------------------------
# Network components
# --------------------------------------------------------------------------------------

def add_carriers(n, costs):
    for carrier in GENERATOR_CARRIERS:
        n.add("Carrier", carrier, co2_emissions=costs.at[TECH_MAP[carrier], "CO2 intensity"],
              color=CARRIER_COLORS.get(carrier), max_growth=np.inf, max_relative_growth=0.0)
    for carrier in ["AC", "DC"]:
        n.add("Carrier", carrier, co2_emissions=0, max_growth=np.inf, max_relative_growth=0.0)
    for carrier in ["load"] + STORAGE_CARRIERS:  # 'load' = load shedding
        n.add("Carrier", carrier, co2_emissions=0, color=CARRIER_COLORS.get(carrier),
              max_growth=np.inf, max_relative_growth=0.0)
    return n


def add_buses(n, grouped_buses):
    """One bus per (zone, voltage level), placed at the mean coordinate of its member buses."""
    for zone, group in grouped_buses:
        for voltage in group["voltage"].unique():
            n.add("Bus", f"{zone}_{remove_decimal_point(voltage)}",
                  x=group.geometry.x.mean(), y=group.geometry.y.mean(), v_nom=voltage)
    return n


def add_lines(n, grouped_buses, lines_df):
    """Add lines between zones. Lines internal to a zone are dropped."""
    mapping = _bus_mapping(grouped_buses)
    for row in lines_df.itertuples():
        bus0, bus1 = mapping.get(row.bus0, row.bus0), mapping.get(row.bus1, row.bus1)
        if bus0 == bus1:
            continue
        n.add("Line", f"{row.name}",
              bus0=f"{bus0}_{remove_decimal_point(row.voltage)}",
              bus1=f"{bus1}_{remove_decimal_point(row.voltage)}",
              v_nom=row.voltage, length=row.length, x=row.x, r=row.r,
              s_nom=row.s_nom_percable * int(row.cables),
              s_nom_extendable=row.s_nom_percable_extendable,
              s_nom_min=row.s_nom_percable_min,
              s_nom_max=row.s_nom_percable_max * int(row.cables),
              overwrite=True)
    return n


def add_transformers(n, grouped_buses, transformers_df):
    mapping = _bus_mapping(grouped_buses)
    for row in transformers_df.itertuples():
        n.add("Transformer", f"{row.name}",
              bus0=_bus_id(mapping, row.bus0, row.bus0_voltage),
              bus1=_bus_id(mapping, row.bus1, row.bus1_voltage),
              v_nom_0=row.bus0_voltage, v_nom_1=row.bus1_voltage,
              x=row.x, r=row.r, s_nom=row.s_nom,
              s_nom_extendable=row.s_nom_extendable,
              s_nom_min=row.s_nom_min, s_nom_max=row.s_nom_max,
              overwrite=True)
    return n


def _capital_cost(row, simulation_year, lifetime, FOM_default, investment_default, rate_default):
    """
    Annualised capital cost for a generator or storage unit.

    - Existing plants (fixed, built before the base year, base-year run): use the
      plant's own cost data if given; zero if none is given.
    - Fixed plants carried into a later planning year: use the values written in the
      hand-off file, falling back to the technology defaults.
    - Everything else (new or extendable): technology defaults from PYPSAPH_COST.xlsx.
    """
    fixed = not row.p_nom_extendable

    if fixed and simulation_year <= PYPSAPH_BASE_YEAR and row.build_year < PYPSAPH_BASE_YEAR:
        FOM = getattr(row, "FOM", None) or 0
        investment = getattr(row, "overnight_capital_cost", None) or 0
        rate = getattr(row, "nom_discount_rate", None) or 0
        zero_if_no_data = True
    elif fixed and simulation_year > PYPSAPH_BASE_YEAR:
        FOM = getattr(row, "FOM", FOM_default)
        investment = getattr(row, "overnight_capital_cost", investment_default)
        rate = getattr(row, "nom_discount_rate", rate_default)
        zero_if_no_data = False
    else:
        FOM, investment, rate = FOM_default, investment_default, rate_default
        annualized = investment * calc_annuity(rate, lifetime)
        capital_cost = annualized * calc_discountfactor(rate, simulation_year - row.build_year) + investment * FOM / 100
        return FOM, investment, rate, annualized, capital_cost

    ann = getattr(row, "annualized_capital_cost", None)
    annualized = ann if (ann is not None and ann > 0) else investment * calc_annuity(rate, lifetime)

    if zero_if_no_data and FOM == 0 and investment == 0 and rate == 0:
        capital_cost = 0
    else:
        capital_cost = annualized * calc_discountfactor(rate, simulation_year - row.build_year) + investment * FOM / 100
    return FOM, investment, rate, annualized, capital_cost


def add_generators(n, grouped_buses, generators_df, simulation_year, costs):
    mapping = _bus_mapping(grouped_buses)
    for row in generators_df.itertuples():
        bus_id = _bus_id(mapping, row.bus, row.v_nom)
        if bus_id not in n.buses.index:
            continue
        tech = TECH_MAP.get(row.carrier)
        lifetime = costs.at[tech, "lifetime"]
        FOM, investment, rate, annualized, capital_cost = _capital_cost(
            row, simulation_year, lifetime,
            costs.at[tech, "FOM"], costs.at[tech, "investment"], costs.at[tech, "nom_discount_rate"])

        n.add("Generator", row.name,
              bus=bus_id, v_nom=row.v_nom, facility_name=row.facility_name, original_bus=row.bus,
              p_nom=row.p_nom, carrier=row.carrier,
              ramp_limit_up=row.ramp_limit_up, ramp_limit_down=row.ramp_limit_down,
              efficiency=costs.at[tech, "efficiency"], build_year=row.build_year, lifetime=lifetime,
              FOM=FOM, overnight_capital_cost=investment, nom_discount_rate=rate,
              annualized_capital_cost=annualized, capital_cost=capital_cost,
              marginal_cost=costs.at[tech, "marginal_cost"], heating_value=costs.at[tech, "heating_value"],
              p_nom_extendable=row.p_nom_extendable, p_nom_min=row.p_nom_min, p_nom_max=row.p_nom_max,
              overwrite=True)
    return n


def add_storage_units(n, grouped_buses, storage_df, simulation_year, costs):
    mapping = _bus_mapping(grouped_buses)
    for row in storage_df.itertuples():
        bus_id = _bus_id(mapping, row.bus, row.v_nom)
        if bus_id not in n.buses.index:
            continue
        tech = TECH_MAP.get(row.carrier, row.carrier)
        lifetime = costs.at[tech, "lifetime"]
        eff = costs.at[tech, "efficiency"]
        FOM, investment, rate, annualized, capital_cost = _capital_cost(
            row, simulation_year, lifetime,
            costs.at[tech, "FOM"], costs.at[tech, "investment"], costs.at[tech, "nom_discount_rate"])

        n.add("StorageUnit", row.name,
              bus=bus_id, original_bus=row.bus, v_nom=row.v_nom, p_nom=row.p_nom, carrier=row.carrier,
              max_hours=row.max_hours, efficiency_dispatch=eff, efficiency_store=eff,
              state_of_charge_initial=row.state_of_charge_initial, standing_loss=row.standing_loss,
              build_year=row.build_year, lifetime=lifetime,
              FOM=FOM, overnight_capital_cost=investment, nom_discount_rate=rate,
              annualized_capital_cost=annualized, capital_cost=capital_cost,
              marginal_cost=costs.at[tech, "marginal_cost"],
              p_nom_extendable=row.p_nom_extendable, p_nom_min=row.p_nom_min,
              p_nom_max=getattr(row, "p_nom_max", None),
              overwrite=True)
    return n


def add_load_shedding(n, grouped_buses, load_shedding_df):
    """Load shedding modelled as generators with carrier 'load' at the value of lost load."""
    mapping = _bus_mapping(grouped_buses)
    for row in load_shedding_df.itertuples():
        bus_id = _bus_id(mapping, row.bus, row.v_nom)
        if bus_id in n.buses.index:
            n.add("Generator", row.name, bus=bus_id, v_nom=row.v_nom, original_bus=row.bus,
                  p_nom=row.p_nom, p_nom_extendable=row.p_nom_extendable, carrier=row.carrier,
                  sign=row.sign, marginal_cost=row.marginal_cost, overwrite=True)
    return n


def add_loads(n, loads_meta_df, loads_t_df, grouped_buses):
    """Sum load time series of all original buses in the same zone and voltage level."""
    mapping = _bus_mapping(grouped_buses)
    aggregated = pd.DataFrame(index=loads_t_df.index)
    for _, group in grouped_buses:
        for bus in group["name"]:
            if bus in loads_t_df.columns:
                col = _bus_id(mapping, bus, loads_meta_df["v_nom"].loc[bus])
                if col not in aggregated.columns:
                    aggregated[col] = 0
                aggregated[col] += loads_t_df[bus]

    n.loads_t.p_set = aggregated.round(3).astype("float16")
    for name in n.loads_t.p_set.columns:
        n.add("Load", name, bus=name)
    return n


def retire_generators(n, year):
    for row in n.generators.itertuples():
        if row.build_year + row.lifetime < year:
            n.generators_t.p_max_pu = n.generators_t.p_max_pu.drop(row.Index, axis=1, errors="ignore")
            n.generators_t.p_min_pu = n.generators_t.p_min_pu.drop(row.Index, axis=1, errors="ignore")
            n.remove("Generator", row.Index)
    return n


def retire_storage_units(n, year):
    for row in n.storage_units.itertuples():
        if row.build_year + row.lifetime < year:
            n.remove("StorageUnit", row.Index)
    return n


def downcast_network_data(n):
    """Store time-varying inputs as sparse float16 to reduce memory on large runs."""
    for attr in [a for a in dir(n) if a.endswith("_t")]:
        df = getattr(n, attr, None)
        if isinstance(df, pd.DataFrame):
            for col in df.select_dtypes(include="float").columns:
                df[col] = df[col].astype(pd.SparseDtype("float16"))


# --------------------------------------------------------------------------------------
# Capacity expansion -> economic dispatch
# --------------------------------------------------------------------------------------

def transfer_optimal_capacities(n_cp, n_ed):
    """
    Copy optimised capacities from the capacity expansion network to the dispatch network.
    Indices are aligned by component name, so this also works when CP and ED use different
    spatial resolutions (components missing from CP keep their input capacity).
    """
    gens = n_cp.generators
    n_ed.generators.p_nom_opt = (
        gens.p_nom_opt.round(1).where((gens.p_nom_opt >= 0.1) & (gens.carrier != "load")).fillna(0)
        .reindex(n_ed.generators.index).fillna(n_ed.generators.p_nom))
    n_ed.storage_units.p_nom_opt = (
        n_cp.storage_units.p_nom_opt.round(1).where(n_cp.storage_units.p_nom_opt >= 0.1).fillna(0)
        .reindex(n_ed.storage_units.index).fillna(n_ed.storage_units.p_nom))
    for c, attr in [("lines", "s_nom"), ("transformers", "s_nom")]:
        df_ed, df_cp = getattr(n_ed, c), getattr(n_cp, c)
        df_ed[f"{attr}_opt"] = df_cp[f"{attr}_opt"].reindex(df_ed.index).fillna(df_ed[attr])


def fix_optimal_capacities(n):
    """Fix extendable capacities to their optimum. Returns what is needed to undo it."""
    saved = {}
    for c, attr in [("generators", "p_nom"), ("links", "p_nom"), ("storage_units", "p_nom"),
                    ("stores", "e_nom"), ("lines", "s_nom"), ("transformers", "s_nom")]:
        df = getattr(n, c)
        if df.empty:
            continue
        mask = df[f"{attr}_extendable"].copy()
        saved[c] = (attr, mask, df.loc[mask, attr].copy())
        df.loc[mask, attr] = df.loc[mask, f"{attr}_opt"]
        df.loc[mask, f"{attr}_extendable"] = False
    return saved


def restore_original_capacities(n, saved):
    for c, (attr, mask, values) in saved.items():
        df = getattr(n, c)
        df.loc[mask, attr] = values
        df.loc[mask, f"{attr}_extendable"] = True


# --------------------------------------------------------------------------------------
# Hand-off files for the next planning year
# --------------------------------------------------------------------------------------

def write_handoff_generators(n, next_year, handoff_dir):
    """Write optimised generator fleet as fixed assets for next_year (PYPSAPH_OPT_ENERGYASSETS.xlsx)."""
    cols = ["name", "v_nom", "facility_name", "bus", "p_nom", "connection", "p_nom_extendable",
            "p_nom_min", "p_nom_max", "p_min_pu", "p_max_pu", "ramp_limit_up", "ramp_limit_down",
            "carrier", "build_year", "FOM", "overnight_capital_cost", "nom_discount_rate",
            "annualized_capital_cost", "marginal_cost"]
    g = n.generators.copy()
    ext = g.p_nom_extendable & (g.p_nom_opt > 0)
    g.loc[ext, "p_nom"] = g.loc[ext, "p_nom_opt"].round(1)
    g["p_nom_extendable"] = False
    g["bus"] = g["original_bus"]
    g = g[[c for c in cols if c in g.columns]]
    g = g[(g.p_nom > 0.1) & (g.carrier != "load")]
    g.index.name = "name"
    _write_sheet(g, os.path.join(handoff_dir, "PYPSAPH_OPT_ENERGYASSETS.xlsx"), f"gen_opt_{next_year}")


def write_handoff_storage_units(n, next_year, handoff_dir):
    cols = ["name", "v_nom", "p_nom", "p_nom_extendable", "bus", "p_nom_min", "p_nom_max",
            "state_of_charge_initial", "standing_loss", "ramp_limit_down", "carrier", "build_year",
            "max_hours", "FOM", "overnight_capital_cost", "nom_discount_rate",
            "annualized_capital_cost", "marginal_cost"]
    s = n.storage_units.copy()
    ext = s.p_nom_extendable & (s.p_nom_opt > 0.1)
    s.loc[ext, "p_nom"] = s.loc[ext, "p_nom_opt"].round(1)
    s["p_nom_extendable"] = False
    s["bus"] = s["original_bus"]
    s = s[s.p_nom >= 0.1]
    s.index.name = "name"
    s = s[[c for c in cols if c in s.columns]]
    _write_sheet(s, os.path.join(handoff_dir, "PYPSAPH_OPT_ENERGYASSETS.xlsx"), f"sto_opt_{next_year}")


def write_handoff_vre_timeseries(n, vre_t, next_year, handoff_dir):
    """Write hourly p_max_pu of VRE plants that survive into next_year."""
    g = n.generators.copy()
    ext = g.p_nom_extendable & (g.p_nom_opt > 0)
    g.loc[ext, "p_nom"] = g.loc[ext, "p_nom_opt"].round(1)
    drop = g.index[(g.p_nom < 0.1) | (g.carrier == "load")]
    out = vre_t.drop(columns=drop, errors="ignore").copy()
    out.index = out.index + pd.DateOffset(year=next_year)
    _write_sheet(out.astype("float16"), os.path.join(handoff_dir, "PYPSAPH_OPT_VRE_GENERATOR_EXT_TIMESERIES.xlsx"),
                 f"gen_t_VRE_{next_year}")


# --------------------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------------------

def write_summary(n, outdir, td_marginal_cost=0):
    """National summary tables. Everything else can be derived from the exported network."""
    os.makedirs(outdir, exist_ok=True)
    gen, sto = n.generators, n.storage_units
    is_gen = gen.carrier != "load"

    capacity = pd.concat({
        "generator_MW": gen[is_gen].groupby("carrier").p_nom_opt.sum(),
        "storage_MW": sto.groupby("carrier").p_nom_opt.sum(),
        "storage_MWh": (sto.p_nom_opt * sto.max_hours).groupby(sto.carrier).sum(),
    }, axis=1)
    capacity.to_csv(os.path.join(outdir, "installed_capacity_per_carrier.csv"))

    w = n.snapshot_weightings.generators
    gen_energy = n.generators_t.p.mul(w, axis=0).sum().groupby(gen.carrier).sum()
    gen_energy.rename("MWh").to_csv(os.path.join(outdir, "generation_per_carrier.csv"))

    co2 = (n.generators_t.p.mul(w, axis=0).sum() / gen.efficiency * gen.carrier.map(n.carriers.co2_emissions)).sum()

    cost = pd.concat({"CAPEX": n.statistics.capex(groupby=["carrier"]),
                      "OPEX": n.statistics.opex(groupby=["carrier"])}, axis=1).fillna(0)
    cost.to_csv(os.path.join(outdir, "annual_cost_per_carrier.csv"))

    flows = sum((df_t.p0.clip(lower=0) + df_t.p1.clip(lower=0)).mul(w, axis=0).sum().sum()
                for df_t in [n.lines_t, n.transformers_t] if not df_t.p0.empty)
    demand = n.loads_t.p.mul(w, axis=0).sum().sum()
    load_shed = gen_energy.get("load", 0.0)

    totals = pd.Series({
        "total_system_cost": cost.sum().sum() + flows * td_marginal_cost,
        "transmission_marginal_cost": flows * td_marginal_cost,
        "total_generation_MWh": gen_energy.drop("load", errors="ignore").sum(),
        "total_demand_MWh": demand,
        "load_shed_MWh": load_shed,
        "total_CO2_t": co2,
    })
    totals.to_csv(os.path.join(outdir, "totals.csv"), header=["value"])

    n.buses_t.marginal_price.mean().rename("mean_LMP").to_csv(os.path.join(outdir, "mean_marginal_price_per_bus.csv"))
    n.statistics().to_csv(os.path.join(outdir, "network_statistics.csv"))
    return totals


def export_network(n, path):
    """Export to NetCDF. NetCDF has no float16, so sparse/float16 time series are converted to float64."""
    for attr in [a for a in dir(n) if a.endswith("_t")]:
        dfs = getattr(n, attr, None)
        if dfs is None or not hasattr(dfs, "items"):
            continue
        for key, df in list(dfs.items()):
            if isinstance(df, pd.DataFrame) and not df.empty:
                if any(isinstance(t, pd.SparseDtype) or t == "float16" for t in df.dtypes):
                    dense = df.apply(lambda s: s.sparse.to_dense() if isinstance(s.dtype, pd.SparseDtype) else s)
                    dfs[key] = dense.astype("float64")
    n.export_to_netcdf(path)
