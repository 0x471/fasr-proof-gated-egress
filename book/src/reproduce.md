# Reproduce the results

All commands run from the repository root.

## Tests

```sh
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python "ezkl==23.0.5" onnx numpy pytest
.venv/bin/python -m pytest -q
```

The verification tests need the pinned setup file (SRS). They use `prover/artifacts/kzg.srs` or `$PGE_SRS` if present, otherwise download it with EZKL and check its hash. Linux-only tests (the address-space limit, the device script) are skipped on macOS; CI runs them on Linux with EZKL's real CLI.

## Run the endpoint yourself

Start the AI server, then the endpoint. The endpoint calls EZKL's command-line tool `ezkl` (version 23.0.5); it has been tested on Linux, and on macOS the memory cap is skipped because macOS doesn't support it.

```sh
.venv/bin/python prover/server.py --listen 127.0.0.1:8701
python3 -I -c 'import sys; sys.path.insert(0, "."); from pge.service import main; main()' \
  --listen OUTSIDE_IP:8700 --server 127.0.0.1:8701 --bundle BUNDLE_DIR --pins device/tiny.sha256 \
  --cli ./ezkl --domain tests/fixtures/tiny/domain.json --proof-bytes 3072 \
  [--release arrival|immediate|fixed] [--deadline SECONDS] [--spin-ms MS] [--log endpoint.log]
```

`OUTSIDE_IP` is the outside interface's own address; don't use `0.0.0.0`, which listens on every interface. `BUNDLE_DIR` holds `settings.json`, `vk.key` and `kzg.srs`. Send questions with `observer/client.py`.

## The ordering channel on one machine

```sh
.venv/bin/python observer/order_experiment.py
```

Runs the real EZKL prover in `order_bits` mode against the endpoint on localhost, for k = 2, 3, 4 concurrent requests, with immediate and arrival release. Prints one JSON line per setting.

## On a Raspberry Pi

The Pi scripts expect `ssh p01` to reach the Pi, the repository copied to `~/p01-v1` on it with a bundle directory `~/p01-v1/bundle` (`settings.json`, `vk.key`, `kzg.srs` matching `device/tiny.sha256`), EZKL's aarch64 CLI 23.0.5 at `~/p01-v0/ezkl`, this machine at `192.168.2.1` and the Pi at `192.168.2.2`. Paths and addresses are those of the lab setup; change them in the scripts for another one.

```sh
./device/verify_on_pi.sh                     # device checks: accept and reject cases, 20 timed verifies
ssh p01 'cd ~/p01-v1 && python3 -I device/verify_cost.py --cli ~/p01-v0/ezkl --srs ~/p01-v0/kzg.srs --runs 20 --sweep 20'
OUT=runs/matrix device/pi_matrix.sh          # release policies x channels, 4 s budget
python3 observer/analyze_matrix.py runs/matrix
```

`device/pi_scenario.sh NAME RELEASE N BATCH -- SERVER_ARGS` runs one scenario: it starts the prover in the given mode on this machine and the endpoint on the Pi, sends `N` requests (in batches of `BATCH`, or one per connection per round with `CLIENTS`), and writes the traces and both logs to `OUT/NAME.*`. `DEADLINE` sets the endpoint's deadline, which with `fixed` release is also the release time; `SPIN_MS` sets the spin. It refuses to start if the checkout has uncommitted changes, if the Pi's endpoint code differs from it, or if the Pi's pins or domain file differ.

The residual-timing test (128 random bits plus an honest control; add `SPIN_MS=5` to both scenario commands for the spin version). Every scenario writes a manifest (`OUT/NAME.manifest.json`: the commit taken before the run, the settings, the hashes of what ran on the Pi (endpoint code, bundle, pins, domain, EZKL CLI, Python), the server's arguments, exit status, row count, whether the endpoint logs were copied) and exits non-zero if the observer failed, got fewer rows than asked for, or the logs weren't copied. The analysis scripts refuse a trace whose rows don't match the bits one for one. The pre-registered spin repeat and its exact commands are in `docs/EXPERIMENT_PROTOCOL.md`.

```sh
B=$(python3 -c "import random; r=random.Random(20261019); print(''.join(r.choice('01') for _ in range(128)))")
OUT=runs/fixed_long DEADLINE=4 device/pi_scenario.sh timing_fixed_long fixed 128 1 -- --mode timing_bits --bits $B --slot 1.0
OUT=runs/fixed_long DEADLINE=4 device/pi_scenario.sh honest_fixed_long fixed 128 1 -- --mode honest
python3 observer/jitter_test.py runs/fixed_long/timing_fixed_long.trace.jsonl $B --control runs/fixed_long/honest_fixed_long.trace.jsonl --heldout 2000
python3 observer/estimate.py runs/fixed_long/timing_fixed_long.trace.jsonl $B --control runs/fixed_long/honest_fixed_long.trace.jsonl
python3 observer/estimate.py --selftest      # the estimator on synthetic channels with known values
```

## Server modes

`prover/server.py --mode` makes the AI server misbehave: `honest`, `drop`, `delay`, `tamper_output`, `replay`, `duplicate`, `wrong_seq`, `oversize`, `garbage`, and the leakage encoders `timing_bits`, `order_bits` and `fail_position` (`--bits`, `--slot`, `--batch`). See the docstring in `prover/server.py`.
