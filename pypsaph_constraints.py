"""
PyPSA-PH additions to the PyPSA optimisation problem.

Only the terms used in the base model are kept here:

1. Auxiliary variables |s| for line and transformer flows (linearised absolute value).
2. A transmission & distribution (T&D) wheeling cost on every MWh that flows through a
   line or transformer, added to the objective.

Pass `extra_functionality` to `n.optimize(...)`.
"""

import xarray as xr


def add_absolute_flow_variables(n):
    """Add aux_abs_{Line,Transformer}_s >= |s| for every branch and snapshot."""
    for component in ["Line", "Transformer"]:
        name = f"{component}-s"
        if name not in n.model.variables:
            continue
        s = n.model[name]
        abs_s = n.model.add_variables(name=f"aux_abs_{component}_s", coords=s.coords, lower=0)
        n.model.add_constraints(abs_s >= s, name=f"abs_{component}_geq_pos")
        n.model.add_constraints(abs_s >= -s, name=f"abs_{component}_geq_neg")


def add_td_marginal_cost(n, snapshots, td_marginal_cost):
    """Add td_marginal_cost (currency/MWh) x total absolute branch flow (MWh) to the objective."""
    w = xr.DataArray(n.snapshot_weightings.objective.loc[snapshots].rename_axis("snapshot"))
    terms = [(n.model[v] * w).sum() for v in ["aux_abs_Line_s", "aux_abs_Transformer_s"] if v in n.model.variables]
    if terms:
        n.model.objective += sum(terms) * td_marginal_cost


def make_extra_functionality(td_marginal_cost):
    """Build the extra_functionality callback used for both capacity expansion and dispatch."""
    def extra_functionality(n, snapshots):
        add_absolute_flow_variables(n)
        add_td_marginal_cost(n, snapshots, td_marginal_cost)
    return extra_functionality
