# README

## Setup

Please ensure you have installed the Carla simulator and the Python API.

```shell
git clone https://github.com/Justin900429/carla_diffusion.git
conda env create -f environment.yml
conda activate cat
# setup the python path
export PYTHONPATH=$PYTHONPATH:${pwd}
```

Modify the `carla_sh_path` in `config/train_rl.yaml` to yours.

## Data collection

```shell
python data_collection.py --save-path {PLACE_TO_SAVE_DATA} --save-num {NUM_OF_DATA}

# Concrete example
python data_collection.py --save-path data/ --save-num 5000
```

If you would like to collect data under `off-screen` mode, please add the flag `--off-screen`.