#!/bin/bash
# SLURM template for PyPSA-PH. Adjust partition, CPUs and time to your cluster.
# Submit from the repository root:  sbatch hpc/run_pypsaph.slurm
#SBATCH --job-name=pypsaph
#SBATCH --output=Logs/%j_%x.out
#SBATCH --error=Logs/%j_%x.err
#SBATCH --partition=normal
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=64
#SBATCH --exclusive
#SBATCH --time=10-00:00:00
#SBATCH --hint=nomultithread

export OMP_NUM_THREADS=$SLURM_CPUS_PER_TASK
export OPENBLAS_NUM_THREADS=$SLURM_CPUS_PER_TASK
export MKL_NUM_THREADS=$SLURM_CPUS_PER_TASK
export NUMEXPR_NUM_THREADS=$SLURM_CPUS_PER_TASK

# Gurobi: point to your own licence file (do not commit it)
# export GRB_LICENSE_FILE=$HOME/gurobi.lic

echo "Job $SLURM_JOB_ID on $(hostname), started $(date)"
module purge
ulimit -s unlimited
ulimit -n 65536

source ~/.bashrc
conda activate pypsa-ph

mkdir -p Logs
python -u run_pypsaph.py --solver gurobi --threads "$SLURM_CPUS_PER_TASK" "$@"

echo "Finished $(date)"
