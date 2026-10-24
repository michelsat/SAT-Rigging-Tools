# SAT Rigging Tools Collection

The Maya launcher for the SAT Rigging Tools Collection. This repository holds
only the window you open in Maya. The tools themselves are private and load
after you sign in with an **access key** from the owner.

Works with Maya 2022 and later (Python 3).

## 1. Get an access key

Ask the owner (**michelsat**) for an access key. It looks like `sat_Xy3...`.
Keep it to yourself: anyone with your key can use the tools as you, and the
owner can switch it off at any time.

## 2. Install

1. Click **Code → Download ZIP** on this page and unzip it anywhere.
2. Open Maya and drag **`install.mel`** into the viewport.

The installer copies the launcher into `Documents/maya/scripts`, adds a **SAT**
button to your current shelf, and opens the tools window.

## 3. Sign in

Click **Sign In...** at the bottom of the window, paste your access key and
click **Sign In**. The key is checked straight away, and your tools then
appear.

Next time, just click the **SAT** shelf button. You can also run this in the
Script Editor (Python tab):

```python
import importlib
import rigtools_collection as rtc
importlib.reload(rtc)
rtc.launch_rigging_tools_collection()
```

## Updating

- **Tools** update on their own. Click **Sync with Server** in the window.
- **The launcher window:** download the ZIP again and drag `install.mel` into
  Maya again. Your button colors, groups and sign-in are kept.

## Signing out / shared computers

Click **Sign Out** in the window. This removes your key and the offline copy
of the tools from that computer.

## Uninstall

Delete these from `Documents/maya/scripts`: `rigtools_collection.py`,
`sattools/rigtools_collection/`, and `sattools/sat_banner.png`.
`sattools/rigging_tools_config.json` holds your button colors and groups.
Remove the **SAT** shelf button with right-click → **Delete**.

## Troubleshooting

The Script Editor shows the exact reason when tools don't load:

| Message | Fix |
|---|---|
| Unknown access key | Check that you pasted the whole key (no spaces) |
| disabled by the owner / expired | Ask the owner to switch it on again or give you a new key |
| no tools server configured | Sign out, sign in again and enter the server address |
| Could not reach the tools server | Check your internet connection |
