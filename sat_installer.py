"""Installer for the SAT Rigging Tools Collection launcher.

Run by dragging install.mel into the Maya viewport. It:
  1. copies the launcher into Documents/maya/scripts
     (rigtools_collection.py, sattools/rigtools_collection/, sattools/sat_banner.png)
  2. adds a "SAT" button to the current shelf
  3. opens the tools window

Your settings (sattools/rigging_tools_config.json) and saved sign-in are
kept when you install an update over an older version.
"""

import os
import shutil
import sys

import maya.cmds as cmds
import maya.mel as mel

SHELF_LABEL = "SAT Tools"
LAUNCH_CODE = (
    "import importlib\n"
    "import rigtools_collection as rtc\n"
    "importlib.reload(rtc)\n"
    "rtc.launch_rigging_tools_collection()\n"
)


def scripts_directory():
    """Documents/maya/scripts (shared by every Maya version)."""
    return os.path.join(cmds.internalVar(userAppDir=True), "scripts")


def _copy_files(source, target):
    os.makedirs(os.path.join(target, "sattools"), exist_ok=True)
    shutil.copy2(os.path.join(source, "rigtools_collection.py"), target)

    package_src = os.path.join(source, "sattools", "rigtools_collection")
    package_dst = os.path.join(target, "sattools", "rigtools_collection")
    if os.path.isdir(package_dst):
        shutil.rmtree(package_dst)
    shutil.copytree(package_src, package_dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

    banner = os.path.join(source, "sattools", "sat_banner.png")
    if os.path.exists(banner):
        shutil.copy2(banner, os.path.join(target, "sattools"))


def _forget_old_modules(target):
    if target not in sys.path:
        sys.path.insert(0, target)
    for name in list(sys.modules):
        if name == "rigtools_collection" or name.startswith("sattools.rigtools_collection"):
            del sys.modules[name]


def _add_shelf_button():
    shelf_top = mel.eval("$tmp = $gShelfTopLevel")
    shelf = cmds.tabLayout(shelf_top, query=True, selectTab=True)
    for child in cmds.shelfLayout(shelf, query=True, childArray=True) or []:
        if cmds.objectTypeUI(child) == "shelfButton" and \
                cmds.shelfButton(child, query=True, label=True) == SHELF_LABEL:
            cmds.deleteUI(child)
    cmds.shelfButton(parent=shelf, label=SHELF_LABEL, annotation="Open the SAT Rigging Tools Collection",
                     image="pythonFamily.png", imageOverlayLabel="SAT",
                     sourceType="python", command=LAUNCH_CODE)
    return shelf


def install(source):
    target = scripts_directory()
    try:
        _copy_files(source, target)
    except Exception as e:
        cmds.confirmDialog(title="SAT Rigging Tools", button=["OK"],
                           message="Install failed:\n{}\n\nTarget folder: {}".format(e, target))
        raise
    _forget_old_modules(target)

    try:
        shelf = _add_shelf_button()
        shelf_msg = "A 'SAT' button was added to your '{}' shelf.".format(shelf)
    except Exception as e:
        shelf_msg = "Could not add a shelf button ({}).".format(e)

    print("[SAT Rigging Tools] Installed into " + target)
    cmds.confirmDialog(
        title="SAT Rigging Tools",
        message=("Installed into:\n{}\n\n{}\n\nClick 'Sign In...' at the bottom of the window and paste "
                 "your GitHub token to load the tools.").format(target, shelf_msg),
        button=["OK"])

    import rigtools_collection
    rigtools_collection.launch_rigging_tools_collection()


def onMayaDroppedPythonFile(*args):
    """Lets the installer also work by dragging sat_installer.py into the viewport."""
    install(os.path.dirname(os.path.abspath(__file__)))
