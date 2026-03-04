"""Basic tests to verify the package is importable and functional."""

import heimdall


def test_version():
    """Test that version is defined."""
    assert heimdall.__version__ == "0.1.0"


def test_exports():
    """Test that main exports are available."""
    assert hasattr(heimdall, "Agent")
    assert hasattr(heimdall, "BrowserSession")
    assert hasattr(heimdall, "DomService")
    assert hasattr(heimdall, "registry")
    assert hasattr(heimdall, "ActionResult")
