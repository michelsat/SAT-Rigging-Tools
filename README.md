# SAT Rigging Tools Collection

The Maya launcher for the SAT Rigging Tools Collection. This repository holds
only the window you open in Maya. The tools themselves live in a **private**
repository and are loaded after you sign in, so you need an invitation from the
owner.

Works with Maya 2022 and later (Python 3).

## 1. Get access

1. Create a free GitHub account if you don't have one.
2. Ask the owner (**michelsat**) to invite your GitHub account to the tools
   repository. Accept the invitation from your GitHub notifications or email.
3. Create a token on GitHub: **Settings → Developer settings → Personal access
   tokens → Fine-grained tokens → Generate new token**
   - **Resource owner:** `michelsat`
   - **Repository access:** Only select repositories → `Rigging-Tools-Collection`
   - **Repository permissions → Contents:** Read-only
   - **Expiration:** your choice (for example 90 days)

   Copy the token. Never share it or paste it into chats or files.

## 2. Install

1. Click **Code → Download ZIP** on this page and unzip it anywhere.
2. Open Maya and drag **`install.mel`** into the viewport.

The installer copies the launcher into `Documents/maya/scripts`, adds a **SAT**
button to your current shelf, and opens the tools window.

## 3. Sign in

Click **Sign In...** at the bottom of the window, paste your token and click
**Save**. The token is checked straight away, and if something is wrong the
message says what to fix. Your tools then appear.

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

Click **Sign Out** in the window. This removes your token and the offline copy
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
| repository is private / click 'Sign In...' | Sign in with your token |
| HTTP 401 | The token is wrong or expired. Sign out and sign in with a new one |
| cannot see Rigging-Tools-Collection | Accept the invitation, and check the token's resource owner and repository |
| cannot read its files | Set the token's **Contents** permission to Read-only |
| NOT APPROVED by the tools owner | Ask the owner to allow your account |
