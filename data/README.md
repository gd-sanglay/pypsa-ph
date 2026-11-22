# PyPSA-PH input data

Licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Please credit the PyPSA-PH team when you reuse it.

All workbooks are read by `run_pypsaph.py`. Sheet names that end in a year (`_2030`, `_2040`, `_2050`) are read for that planning year.

## Included in the repository

| File | Sheets | Contents |
|---|---|---|
| `PYPSAPH_POWER_NETWORK.xlsx` | `BUS_{year}`, `LN_EXP_{year}`, `TF_EXP_{year}` | Buses (coordinates, voltage), transmission lines and transformers, including planned expansions |
| `PYPSAPH_BASE_ENERGYASSETS.xlsx` | `gen`, `sto` | Existing generators and storage (fleet before 2030) |
| `PYPSAPH_EXT_ENERGYASSETS.xlsx` | `gen_ext_{year}`, `sto_ext_{year}` | Committed and candidate (extendable) generators and storage per planning year |
| `PYPSAPH_COST.xlsx` | `pypsaph_cost_{year}` | Techno-economic parameters per technology, with sources |
| `PYPSAPH_LOADSHEDDING.xlsx` | `load_shedding` | Load-shedding pseudo-generators per load bus (value of lost load) |
| `PYPSAPH_SPATIAL_RESOLUTION_BARANGAY.xlsx` | `PYPSAPH_spatial_resolution` | Barangay (ADM4) to zone mapping for spatial levels 1–4 |
| `PYPSAPH_VRE_GENERATOR_BASE_TIMESERIES.xlsx` | `generators_t_VRE` | Hourly availability (p_max_pu) of existing VRE plants |

## Download separately

These are too large for the repository. Place them as shown:

| File | Location | Source |
|---|---|---|
| `PYPSAPH_LOAD_TIMESERIES.xlsx` (~50 MB) | `data/` | Zenodo: *DOI to be added* |
| `PYPSAPH_VRE_GENERATOR_EXT_TIMESERIES.xlsx` (~35 MB) | `data/` | Zenodo: *DOI to be added* |
| `phl_admbnda_adm4_psa_namria_20231106.shp` (+ `.dbf`, `.shx`, `.prj`) | `data/external/PH_ADMIN/` | PSA/NAMRIA barangay boundaries, via HDX: <https://data.humdata.org/dataset/cod-ab-phl> |

Expected structure of the two time-series workbooks:

- `PYPSAPH_LOAD_TIMESERIES.xlsx`: sheet `loads_t` (index `bus`, column `v_nom`), and sheets `loads_t_{year}` (hourly load in MW, one column per bus, datetime index).
- `PYPSAPH_VRE_GENERATOR_EXT_TIMESERIES.xlsx`: sheets `generators_t_VRE_ext_{year}` (hourly p_max_pu, one column per generator in `gen_ext_{year}`, datetime index).

## Generated during a run

Planning years after 2030 start from the optimised fleet of the previous year. The model writes this to `results/<run>/handoff/`:

- `PYPSAPH_OPT_ENERGYASSETS.xlsx` (`gen_opt_{year}`, `sto_opt_{year}`)
- `PYPSAPH_OPT_VRE_GENERATOR_EXT_TIMESERIES.xlsx` (`gen_t_VRE_{year}`)
