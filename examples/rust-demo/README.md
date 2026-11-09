# rust-demo

Example project for the agentwork Rust support. The end-to-end test
`tests/test_e2e_rust.py` installs the harness into a copy of this folder and runs one
work package (WP01) through the full cycle: seal → run (fake agent) → gate → merge → FINAL gate.
