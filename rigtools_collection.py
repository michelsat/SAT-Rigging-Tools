"""Launcher for the Rigging Tools Collection.

Keep importing this module from Maya exactly as before:

    import importlib
    import rigtools_collection as rtc
    importlib.reload(rtc)
    rtc.launch_rigging_tools_collection()

The implementation lives in sattools/rigtools_collection next to this file.
It is always loaded fresh from that folder, so you see your latest changes,
and moving or reinstalling the tools never leaves Maya pointing at an old
location ("spec not found for the module 'sattools.rigtools_collection.ui'").
"""

import importlib
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load_ui():
  """Imports sattools.rigtools_collection.ui from the folder next to this file."""
  if _HERE in sys.path:
    sys.path.remove(_HERE)
  sys.path.insert(0, _HERE)

  # Forget any copy loaded earlier (possibly from an old or moved folder);
  # reloading it would fail if that folder no longer exists.
  for name in list(sys.modules):
    if name == "sattools" or name.startswith("sattools.rigtools_collection"):
      del sys.modules[name]
  importlib.invalidate_caches()

  ui = importlib.import_module("sattools.rigtools_collection.ui")
  expected = os.path.normcase(os.path.join(_HERE, "sattools"))
  if not os.path.normcase(os.path.abspath(ui.__file__)).startswith(expected):
    print(f"[Rigging Tools] Warning: UI loaded from {ui.__file__}, not from {_HERE}. "
          "Remove the older copy of the 'sattools' folder.")
  return ui


_ui = _load_ui()
RiggingToolsCollection = _ui.RiggingToolsCollection


def launch_rigging_tools_collection(scripts_directory=None):
  global _ui, RiggingToolsCollection
  _ui = _load_ui()
  RiggingToolsCollection = _ui.RiggingToolsCollection
  return _ui.launch_rigging_tools_collection(scripts_directory)


if __name__ == "__main__":
  launch_rigging_tools_collection()
