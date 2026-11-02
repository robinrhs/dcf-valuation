"""Smoke test: the package skeleton imports and exposes a version string."""

import dcf


def test_package_imports_and_has_version():
    assert isinstance(dcf.__version__, str)
    assert dcf.__version__
