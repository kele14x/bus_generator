#!/usr/bin/env bash

script_dir="$(dirname "$0")"
. $script_dir/venv/bin/activate
python $script_dir/bus_generator.py $1 -o .
