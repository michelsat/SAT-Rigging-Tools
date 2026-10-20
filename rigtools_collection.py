"""Compatibility launcher for the Rigging Tools Collection.

Keep importing this module from Maya exactly as before.  The implementation
now lives in sattools.rigtools_collection, alongside the tools it manages.
"""

import importlib

from sattools.rigtools_collection import ui as _ui

# Reloading this launcher during Maya development also refreshes the UI module.
_ui = importlib.reload(_ui)
RiggingToolsCollection = _ui.RiggingToolsCollection


def launch_rigging_tools_collection():
    return _ui.launch_rigging_tools_collection()


if __name__ == "__main__":
    launch_rigging_tools_collection()
