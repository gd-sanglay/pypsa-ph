"""
PyPSA-PH: capacity expansion (CP) followed by economic dispatch (ED) of the Philippine grid.

For each planning year:
  1. Build a network at the CP resolution and optimise investment (capacity expansion).
  2. Build a network at the ED resolution, fix capacities to the CP optimum and run
     dispatch over rolling horizons.
  3. Write the optimised fleet as fixed assets for the next planning year (year + 10).

Usage
-----
    python run_pypsaph.py                          # 2030 -> 2040 -> 2050 with the settings below
    python run_pypsaph.py --years 2030             # a single year
    python run_pypsaph.py --years 2040 --handoff-dir results/<previous run>/handoff
    python run_pypsaph.py --solver highs --spatial-level 1 --temporal-level 24h   # quick test

Laboratory of Electrochemical Engineering (LEE), Department of Chemical Engineering,
University of the Philippines Diliman.
"""

import argparse
import datetime
import gc
import os

import pandas as pd
import pypsa

import pypsaph_constraints
import pypsaph_io

# ======================================================================================
# CONFIGURATION
# ======================================================================================

CONFIG = {
    # paths
    "input_dir": "data",
    "shapefile": "data/external/PH_ADMIN/phl_admbnda_adm4_psa_namria_20231106.shp",
    "output_dir": "results",

    # planning years, run in sequence (each year's optimum is the next year's existing fleet)
    "years": [2030, 2040, 2050],

    # spatial level: 1 = national, 2 = 6 regions, 3 = 52 zones, 4 = 120 zones (provinces/islands)
    "spatial_level_CP": 3,
    "spatial_level_ED": 3,
    # pandas frequency strings, e.g. "1h", "2h", "24h"
    "temporal_level_CP": "2h",
    "temporal_level_ED": "2h",

    # rolling horizon for economic dispatch, in snapshots
    "ED_horizon": 8760,
    "ED_overlap": 0,

    # economic parameters
    "load_growth_per_year": 0.04,   # applied to load-shedding capacity relative to 2030
    "td_marginal_cost": 7,          # T&D wheeling cost per MWh of branch flow

    # solver
    "solver": "gurobi",             # "gurobi" or "highs"
    "threads": 64,
}

SOLVER_OPTIONS = {
    "gurobi": {"Method": 2, "ScaleFlag": 2, "Crossover": 0, "ObjScale": -0.5,
               "BarHomogeneous": 1, "NumericFocus": 3},
    "highs": {"solver": "ipm"},
}


# ======================================================================================
# INPUTS
# ======================================================================================

def read_timeseries(path, sheet):
    df = pd.read_excel(path, sheet_name=sheet, index_col=0, parse_dates=True, date_format="%m/%d/%Y %H:%M")
    df.index = pd.to_datetime(df.index)
    return df


def load_inputs(cfg, year, handoff_dir):
    """Read every input table needed for one planning year."""
    md = cfg["input_dir"]
    path = lambda f: os.path.join(md, f)
    d = {}

    d["area"] = pd.read_excel(path("PYPSAPH_SPATIAL_RESOLUTION_BARANGAY.xlsx"), sheet_name="PYPSAPH_spatial_resolution")
    d["buses"] = pd.read_excel(path("PYPSAPH_POWER_NETWORK.xlsx"), sheet_name=f"BUS_{year}", index_col="PYPSAPH_INDEX")
    d["transformers"] = pd.read_excel(path("PYPSAPH_POWER_NETWORK.xlsx"), sheet_name=f"TF_EXP_{year}", index_col="PYPSAPH_INDEX")
    d["lines"] = pd.read_excel(path("PYPSAPH_POWER_NETWORK.xlsx"), sheet_name=f"LN_EXP_{year}", index_col="PYPSAPH_INDEX")

    d["loads_meta"] = pd.read_excel(path("PYPSAPH_LOAD_TIMESERIES.xlsx"), sheet_name="loads_t", index_col="bus")
    d["loads_t"] = read_timeseries(path("PYPSAPH_LOAD_TIMESERIES.xlsx"), f"loads_t_{year}")

    d["load_shedding"] = pd.read_excel(path("PYPSAPH_LOADSHEDDING.xlsx"), sheet_name="load_shedding")
    d["load_shedding"]["p_nom"] *= (1 + cfg["load_growth_per_year"]) ** (year - pypsaph_io.PYPSAPH_BASE_YEAR)

    d["gen_ext"] = pd.read_excel(path("PYPSAPH_EXT_ENERGYASSETS.xlsx"), sheet_name=f"gen_ext_{year}")
    d["sto_ext"] = pd.read_excel(path("PYPSAPH_EXT_ENERGYASSETS.xlsx"), sheet_name=f"sto_ext_{year}")
    d["vre_ext_t"] = read_timeseries(path("PYPSAPH_VRE_GENERATOR_EXT_TIMESERIES.xlsx"), f"generators_t_VRE_ext_{year}")

    if year <= pypsaph_io.PYPSAPH_BASE_YEAR:
        # existing fleet
        d["gen_base"] = pd.read_excel(path("PYPSAPH_BASE_ENERGYASSETS.xlsx"), sheet_name="gen")
        d["sto_base"] = pd.read_excel(path("PYPSAPH_BASE_ENERGYASSETS.xlsx"), sheet_name="sto")
        d["vre_base_t"] = read_timeseries(path("PYPSAPH_VRE_GENERATOR_BASE_TIMESERIES.xlsx"), "generators_t_VRE")
    else:
        # fleet from the previous planning year's optimum
        if handoff_dir is None:
            raise ValueError(f"{year} needs the previous year's results: pass --handoff-dir or run the years in sequence.")
        opt_assets = os.path.join(handoff_dir, "PYPSAPH_OPT_ENERGYASSETS.xlsx")
        opt_vre = os.path.join(handoff_dir, "PYPSAPH_OPT_VRE_GENERATOR_EXT_TIMESERIES.xlsx")
        for f in [opt_assets, opt_vre]:
            if not os.path.exists(f):
                raise FileNotFoundError(f"Missing hand-off file {f}")
        d["gen_base"] = pd.read_excel(opt_assets, sheet_name=f"gen_opt_{year}")
        d["sto_base"] = pd.read_excel(opt_assets, sheet_name=f"sto_opt_{year}")
        d["vre_base_t"] = read_timeseries(opt_vre, f"gen_t_VRE_{year}")

    d["costs"] = pypsaph_io.get_costs_data(md, year)
    return d


# ======================================================================================
# NETWORK
# ======================================================================================

def build_network(d, year, spatial_level, temporal_level, shapefile):
    io = pypsaph_io
    n = pypsa.Network()
    io.set_snapshot(n, year, temporal_level)
    io.add_carriers(n, d["costs"])

    zones = io.aggregate_buses(shapefile, d["buses"], d["area"], spatial_level)
    io.add_buses(n, zones)
    io.add_lines(n, zones, d["lines"])
    io.add_transformers(n, zones, d["transformers"])
    io.add_generators(n, zones, d["gen_base"], year, d["costs"])
    io.add_generators(n, zones, d["gen_ext"], year, d["costs"])
    io.add_load_shedding(n, zones, d["load_shedding"])
    io.add_storage_units(n, zones, d["sto_base"], year, d["costs"])
    io.add_storage_units(n, zones, d["sto_ext"], year, d["costs"])

    loads_t = io.change_temporal_resolution(d["loads_t"], temporal_level, year)
    io.add_loads(n, d["loads_meta"], loads_t, zones)

    io.retire_generators(n, year)
    io.retire_storage_units(n, year)

    vre = pd.concat([io.change_temporal_resolution(d["vre_base_t"], temporal_level, year),
                     io.change_temporal_resolution(d["vre_ext_t"], temporal_level, year)], axis=1)
    n.generators_t.p_max_pu = vre.loc[:, vre.columns.intersection(n.generators.index)].round(3).astype("float16")

    io.downcast_network_data(n)
    del zones, loads_t, vre
    gc.collect()
    return n


def solve(n, cfg, extra_functionality, label, outdir, **kwargs):
    options = dict(SOLVER_OPTIONS[cfg["solver"]])
    options["Threads" if cfg["solver"] == "gurobi" else "threads"] = cfg["threads"]

    status, condition = n.optimize(transmission_losses=2, extra_functionality=extra_functionality,
                                   solver_name=cfg["solver"], solver_options=options, **kwargs)
    print(f"{label}: status={status}, condition={condition}", flush=True)

    failed = any(k in str(condition) for k in ["infeasible", "unbounded"]) or n.objective is None
    if status != "ok" and not failed:
        print(f"WARNING: {label} returned status '{status}' but a solution was loaded; check the solver log.", flush=True)
    if failed:
        os.makedirs(outdir, exist_ok=True)
        lp = os.path.join(outdir, f"failed_{label}.lp")
        if n.model is not None:
            n.model.to_file(lp)
        raise RuntimeError(f"{label} optimisation failed ({condition}); model written to {lp}")


def run_dispatch(n, cfg, extra_functionality, outdir):
    """Economic dispatch over rolling horizons, carrying storage state between windows.

    Line flows are collected per window and written back at the end, as a workaround
    for lines_t being reset between windows when transmission losses are enabled.
    """
    snapshots = n.snapshots
    horizon, overlap = cfg["ED_horizon"], cfg["ED_overlap"]
    starts = range(0, len(snapshots), horizon - overlap)

    p0 = pd.DataFrame(index=snapshots, columns=n.lines.index, dtype=float)
    p1 = p0.copy()
    loss = p0.copy()

    for i, start in enumerate(starts):
        sns = snapshots[start:min(len(snapshots), start + horizon)]
        print(f"ED window {i + 1}/{len(starts)}: {sns[0]} to {sns[-1]}", flush=True)

        if i:
            prev = snapshots[start - 1]
            if not n.stores.empty:
                n.stores.e_initial = n.stores_t.e.loc[prev]
            if not n.storage_units.empty:
                n.storage_units.state_of_charge_initial = n.storage_units_t.state_of_charge.loc[prev]

        solve(n, cfg, extra_functionality, f"ED_window{i + 1}", outdir, snapshots=sns)

        p0.loc[sns] = n.lines_t.p0.loc[sns]
        p1.loc[sns] = n.lines_t.p1.loc[sns]
        if "loss" in n.lines_t:
            loss.loc[sns] = n.lines_t.loss.loc[sns]

    n.lines_t.p0 = p0.fillna(0)
    n.lines_t.p1 = p1.fillna(0)
    n.lines_t.loss = loss.fillna(0)


# ======================================================================================
# MAIN
# ======================================================================================

def run_year(cfg, year, run_dir, handoff_dir_in):
    next_year = year + 10
    year_dir = os.path.join(run_dir, str(year))
    handoff_dir_out = os.path.join(run_dir, "handoff")
    os.makedirs(year_dir, exist_ok=True)
    os.makedirs(handoff_dir_out, exist_ok=True)
    extra = pypsaph_constraints.make_extra_functionality(cfg["td_marginal_cost"])

    print(f"\n===== {year} =====", flush=True)
    d = load_inputs(cfg, year, handoff_dir_in)

    # ---- capacity expansion
    t0 = datetime.datetime.now()
    n_cp = build_network(d, year, cfg["spatial_level_CP"], cfg["temporal_level_CP"], cfg["shapefile"])
    vre_hourly = pd.concat([d["vre_base_t"], d["vre_ext_t"]], axis=1)
    vre_hourly = vre_hourly.loc[:, vre_hourly.columns.intersection(n_cp.generators.index)].round(3).astype("float16")

    solve(n_cp, cfg, extra, "CP", year_dir)
    print(f"CP time: {datetime.datetime.now() - t0}", flush=True)

    pypsaph_io.write_handoff_vre_timeseries(n_cp, vre_hourly, next_year, handoff_dir_out)
    pypsaph_io.write_handoff_generators(n_cp, next_year, handoff_dir_out)
    pypsaph_io.write_handoff_storage_units(n_cp, next_year, handoff_dir_out)
    pypsaph_io.write_summary(n_cp, os.path.join(year_dir, "capacity_expansion"), cfg["td_marginal_cost"])

    # ---- economic dispatch
    t1 = datetime.datetime.now()
    n_ed = build_network(d, year, cfg["spatial_level_ED"], cfg["temporal_level_ED"], cfg["shapefile"])
    del d, vre_hourly
    gc.collect()

    pypsaph_io.transfer_optimal_capacities(n_cp, n_ed)
    n_cp.model = None
    del n_cp
    gc.collect()

    saved = pypsaph_io.fix_optimal_capacities(n_ed)
    run_dispatch(n_ed, cfg, extra, year_dir)
    pypsaph_io.restore_original_capacities(n_ed, saved)
    n_ed.model = None
    print(f"ED time: {datetime.datetime.now() - t1}", flush=True)

    totals = pypsaph_io.write_summary(n_ed, os.path.join(year_dir, "economic_dispatch"), cfg["td_marginal_cost"])
    pypsaph_io.export_network(n_ed, os.path.join(year_dir, f"network_ED_{year}.nc"))
    print(totals.to_string(), flush=True)
    return handoff_dir_out


def parse_args():
    p = argparse.ArgumentParser(description="Run PyPSA-PH capacity expansion and economic dispatch.")
    p.add_argument("--years", type=int, nargs="+")
    p.add_argument("--handoff-dir", help="Hand-off folder from a previous run (needed if the first year is after 2030)")
    p.add_argument("--input-dir")
    p.add_argument("--shapefile")
    p.add_argument("--output-dir")
    p.add_argument("--solver", choices=["gurobi", "highs"])
    p.add_argument("--threads", type=int)
    p.add_argument("--spatial-level", type=int, help="Sets both CP and ED spatial level")
    p.add_argument("--temporal-level", help="Sets both CP and ED temporal level, e.g. 2h")
    return p.parse_args()


def main():
    args = parse_args()
    cfg = dict(CONFIG)
    for key in ["years", "input_dir", "shapefile", "output_dir", "solver", "threads"]:
        if getattr(args, key) is not None:
            cfg[key] = getattr(args, key)
    if args.spatial_level is not None:
        cfg["spatial_level_CP"] = cfg["spatial_level_ED"] = args.spatial_level
    if args.temporal_level is not None:
        cfg["temporal_level_CP"] = cfg["temporal_level_ED"] = args.temporal_level

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    job = os.environ.get("SLURM_JOB_ID", "local")
    run_name = (f"{stamp}_{job}_CP{cfg['spatial_level_CP']}-{cfg['temporal_level_CP']}"
                f"_ED{cfg['spatial_level_ED']}-{cfg['temporal_level_ED']}")
    run_dir = os.path.join(cfg["output_dir"], run_name)
    os.makedirs(run_dir, exist_ok=True)
    pd.Series(cfg).to_csv(os.path.join(run_dir, "config_used.csv"), header=["value"])
    print(f"Run folder: {run_dir}", flush=True)

    handoff = args.handoff_dir
    for year in sorted(cfg["years"]):
        handoff = run_year(cfg, year, run_dir, handoff)


if __name__ == "__main__":
    main()
