"""issue #42 Phase 6: ``tools/`` package marker.

Makes ``tools/`` a regular package (PEP 328) rather than a namespace package
(PEP 420). Required so that ``pythonpath = ["python", "."]`` in
``pyproject.toml`` / ``pytest.ini`` resolves ``tools.lint.*`` predictably
without relying on namespace package semantics.

Note: This package contains the lint scripts and shell-based helpers under
``tools/`` (e.g., ``tools/lint/``, ``tools/secret_scan.sh``). Only Python
modules are exported as importable submodules.
"""
