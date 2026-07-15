"""Inserty HWP MCP server package.

Exposes the existing Inserty HWP-editing backend (``python/hwp_com_process.py``)
as Model Context Protocol tools so external MCP clients can read and edit the
user's open HWP document.

This package only *calls* the existing backend as a subprocess; it does not
modify the Inserty application.
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
