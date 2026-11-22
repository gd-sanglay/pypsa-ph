# PyPSA-PH (LEE-PyPSA-Pilipinas)

An open capacity expansion and economic dispatch model of the Philippine power system, built on [PyPSA](https://pypsa.org).

PyPSA-PH represents the grid down to distribution-utility level (13.8 kV to 500 kV). It optimises generation, storage (1 to 8 hour batteries and pumped hydro) and dispatch for the 2030, 2040 and 2050 planning years. The network can be aggregated to four spatial levels and any temporal resolution.

Developed at the Laboratory of Electrochemical Engineering (LEE), Department of Chemical Engineering, University of the Philippines Diliman.

## Repository

```
pypsa-ph/
├── run_pypsaph.py          # configuration and run: capacity expansion -> economic dispatch -> next year
├── pypsaph_io.py           # reads inputs, builds the network, writes hand-off files and results
├── pypsaph_constraints.py  # additions to the PyPSA problem (T&D wheeling cost)
├── environment.yml
├── data/                   # input workbooks (see data/README.md)
└── hpc/run_pypsaph.slurm   # SLURM template
```

## Method

For each planning year:

1. **Capacity expansion (CP).** Builds the network at the CP resolution and co-optimises investment in extendable generators and storage with dispatch over the full year. Uses linearised transmission losses (`transmission_losses=2`).
2. **Economic dispatch (ED).** Builds the network at the ED resolution, fixes capacities to the CP optimum and solves dispatch over rolling horizons, carrying storage state of charge between windows.
3. **Hand-off.** Writes the optimised fleet as fixed assets for the next planning year (year + 10). Plants retire once `build_year + lifetime` is passed.

The objective is PyPSA's standard total system cost (annualised capital cost plus marginal cost) plus a transmission & distribution wheeling cost on every MWh of branch flow (`td_marginal_cost`). Load shedding is allowed at the value of lost load given in `PYPSAPH_LOADSHEDDING.xlsx`.

Snapshot weightings equal the hours per time step, so costs and energy always add up to a full 8,760-hour year whatever the temporal resolution.

**Spatial levels** (`PYPSAPH_SPATIAL_RESOLUTION_BARANGAY.xlsx`): 1 = national (single node), 2 = 6 regions, 3 = NGCP/IEMOP-based zones (52 defined; 28 are grid-connected under the 2023 TDP, as used in the baseline paper), 4 = 120 zones (provinces and island groups).

## Installation

```bash
conda env create -f environment.yml
conda activate pypsa-ph
```

Then download the barangay boundary shapefile listed in [`data/README.md`](data/README.md). All other inputs are included.

HiGHS is installed by default. Full-resolution runs (spatial level 3, 1–2 h) are large and were solved with Gurobi on an HPC cluster. Install `gurobi` and provide your own licence for these.

## Running

Settings are in the `CONFIG` block at the top of `run_pypsaph.py`. Common ones can be overridden on the command line:

```bash
# Quick check on a laptop: national node, daily resolution, HiGHS
python run_pypsaph.py --solver highs --spatial-level 1 --temporal-level 24h --years 2030

# Full chain 2030 -> 2040 -> 2050 with the settings in CONFIG
python run_pypsaph.py

# Start from 2040 using the hand-off of an earlier run
python run_pypsaph.py --years 2040 2050 --handoff-dir results/<run>/handoff

# On a SLURM cluster
mkdir -p Logs && sbatch hpc/run_pypsaph.slurm
```

## Outputs

Each run writes to `results/<timestamp>_<job>_CP<level>-<res>_ED<level>-<res>/`:

```
config_used.csv
handoff/                        # fleet passed on to the next planning year
<year>/
  capacity_expansion/           # summary tables from the CP solve
  economic_dispatch/            # summary tables from the ED solve
    installed_capacity_per_carrier.csv
    generation_per_carrier.csv
    annual_cost_per_carrier.csv
    mean_marginal_price_per_bus.csv
    network_statistics.csv
    totals.csv                  # system cost, generation, demand, load shed, CO2
  network_ED_<year>.nc          # full solved network
```

The `.nc` file holds every input and result. Load it with `pypsa.Network("network_ED_2030.nc")` for further analysis or plotting.

## Contributors

Eugene Esparcia Jr., Giancarlo Dominador Sanglay, Anthony John Santos, Genev Yesiree Garcia, Czarina Ann Espera, Kevin Lester Lobo, Sophia Bernadette Lunor, and Joey Ocon

Laboratory of Electrochemical Engineering, Department of Chemical Engineering, University of the Philippines Diliman

Maintained by Giancarlo Dominador D. Sanglay.

## Citation

If you use PyPSA-PH, please cite:

> Esparcia Jr., E., Sanglay, G.D., Santos, A.J., Garcia, G.Y., Espera, C.A., Lobo, K.L., Lunor, S.B., Ocon, J. *A high spatio-temporal electricity-only model of the Philippine energy system using Python for Power Systems Analysis (PyPSA).* (Under review; full citation and DOI to be added on publication.)

## License

Code: [MIT](LICENSE). Input data in `data/`: [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
