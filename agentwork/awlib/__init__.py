"""agentwork support library (portable: Windows, Linux, macOS).

Everything that used to live in bash scripts (verify_gate.sh, launch.sh, split.sh,
driver.sh, setup.sh, update-all.sh) is implemented here in plain Python 3.9+ with
no third-party dependencies. The `.sh` / `.cmd` files are thin launchers only.
"""

__all__ = ["__version__"]
__version__ = "2.1.0"
