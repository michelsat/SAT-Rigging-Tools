import colorsys
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import json
import types
import shutil
import maya.cmds as cmds

from .config import load_config as load_collection_config
from .config import save_config as save_collection_config
from .help import edit_tool_help, show_tool_help

TOKEN_OPTION_VAR = "rtc_github_token"
TOKEN_ENV_VARS = ("RTC_GITHUB_TOKEN", "GITHUB_TOKEN")
TOKEN_FILE_NAME = "github_token.txt"
RAW_URL_PATTERN = re.compile(
    r"^https://raw\.githubusercontent\.com/([^/]+)/([^/]+)/(?:refs/heads/)?([^/]+)/(.+)$")
SPLIT_OPTION_VAR = "rtc_settings_split"
# Access key server (Cloudflare Worker, see server/README.md in the private repo).
# Users who sign in with an access key (sat_...) load the tools through it and
# never get GitHub access. Can be overridden per computer with the optionVar.
SERVER_URL = "https://sat-tools.michelsatishss.workers.dev"
SERVER_OPTION_VAR = "rtc_server_url"
ACCESS_KEY_PREFIX = "sat_"


def is_access_key(credential):
  return bool(credential) and credential.startswith(ACCESS_KEY_PREFIX)


# ---------------------------------------------------------------- button styling
try:
  from shiboken6 import wrapInstance
  from PySide6 import QtWidgets
except ImportError:
  try:
    from shiboken2 import wrapInstance
    from PySide2 import QtWidgets
  except ImportError:
    wrapInstance = QtWidgets = None
try:
  import maya.OpenMayaUI as omui
except ImportError:
  omui = None


def rgb_to_hex(rgb):
  return "#{:02X}{:02X}{:02X}".format(*[int(round(max(0.0, min(1.0, c)) * 255)) for c in rgb[:3]])


def hex_to_rgb(text):
  text = (text or "").strip().lstrip("#")
  if len(text) == 3:
    text = "".join(c * 2 for c in text)
  if not re.match(r"^[0-9a-fA-F]{6}$", text):
    return None
  return tuple(int(text[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def _shade(rgb, amount):
  """amount > 0 lightens towards white, < 0 darkens towards black."""
  if amount >= 0:
    return [c + (1.0 - c) * amount for c in rgb[:3]]
  return [c * (1.0 + amount) for c in rgb[:3]]


def _css(rgb):
  return "rgb({}, {}, {})".format(*[int(round(max(0.0, min(1.0, c)) * 255)) for c in rgb[:3]])


def button_css(rgb, selected=False, radius=8):
  """Rounded button: lighter on hover, darker when pressed, readable text."""
  luminance = 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]
  text = "#1E1E1E" if luminance > 0.6 else "#F2F2F2"
  border = "2px solid rgb(255, 176, 64)" if selected else "1px solid " + _css(_shade(rgb, 0.22))
  return (
      "QPushButton {{ background-color: {bg}; color: {text}; border: {border}; border-radius: {r}px;"
      " font-weight: bold; padding: 2px 8px; }}"
      " QPushButton:hover {{ background-color: {hover}; border-color: {hover_border}; }}"
      " QPushButton:pressed {{ background-color: {pressed}; }}"
      " QPushButton:disabled {{ background-color: {disabled}; color: #888888; }}"
  ).format(bg=_css(rgb), text=text, border=border, r=radius,
           hover=_css(_shade(rgb, 0.15)), hover_border=_css(_shade(rgb, 0.4)),
           pressed=_css(_shade(rgb, -0.25)), disabled=_css(_shade(rgb, -0.4)))


def style_button(control, rgb, selected=False, radius=8):
  """Applies the rounded style to a cmds.button; falls back to a plain
  background color when Qt is not available."""
  widget = None
  if wrapInstance is not None and omui is not None:
    try:
      ptr = omui.MQtUtil.findControl(control)
      if ptr:
        widget = wrapInstance(int(ptr), QtWidgets.QWidget)
    except Exception:
      widget = None
  if widget is not None:
    widget.setStyleSheet(button_css(rgb, selected, radius))
  elif cmds.button(control, exists=True):
    cmds.button(control, edit=True, backgroundColor=rgb[:3])


def _github_api_url(raw_url):
  """raw.githubusercontent.com URL -> GitHub contents API URL (works for private repos)."""
  match = RAW_URL_PATTERN.match(raw_url or "")
  if not match:
    return raw_url
  owner, repo, branch, path = match.groups()
  return f"https://api.github.com/repos/{owner}/{repo}/contents/{path}?ref={branch}"


def _clean_token(text):
  """Strips quotes, spaces and a pasted 'Bearer '/'token ' prefix."""
  token = (text or "").strip().strip('"').strip("'").strip()
  for prefix in ("Bearer ", "bearer ", "token ", "Token "):
    if token.startswith(prefix):
      token = token[len(prefix):].strip()
  return "".join(token.split())


def _api_get(url, token, timeout=10):
  """Returns (status_code, parsed_json_or_None)."""
  headers = {
      "Authorization": f"Bearer {token}",
      "Accept": "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "RiggingToolsCollection",
  }
  try:
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as response:
      return response.status, json.loads(response.read().decode("utf-8") or "null")
  except urllib.error.HTTPError as e:
    try:
      body = json.loads(e.read().decode("utf-8") or "null")
    except Exception:
      body = None
    return e.code, body


def check_access_key(key, server_url):
  """Asks the access key server who this key belongs to. Returns (ok, message, info)."""
  if not server_url:
    return False, "No access key server is configured.", None
  try:
    status, data = _api_get(server_url + "/whoami", key)
  except Exception as e:
    return False, f"Could not reach the tools server: {getattr(e, 'reason', e)}", None
  if status == 200 and isinstance(data, dict):
    cache = "allowed" if data.get("offline_cache") else "not allowed"
    return True, f"Access key OK (user: {data.get('user')}, offline cache {cache}).", data
  reason = (data or {}).get("error") if isinstance(data, dict) else None
  return False, reason or f"The tools server refused the key (HTTP {status}).", None


def check_github_token(token, manifest_url):
  """Tests a token step by step. Returns (ok, message)."""
  match = RAW_URL_PATTERN.match(manifest_url)
  if not match:
    return False, "Manifest URL is not a GitHub raw URL."
  owner, repo, branch, path = match.groups()
  try:
    status, user = _api_get("https://api.github.com/user", token)
    if status == 401:
      return False, ("GitHub rejected this token (401): it is wrong, incomplete or expired.\n"
                     "Copy the whole token again, or create a new one.")
    login = (user or {}).get("login", "?") if status == 200 else "?"

    status, _ = _api_get(f"https://api.github.com/repos/{owner}/{repo}", token)
    if status == 404:
      return False, (f"The token works (account: {login}) but it cannot see {owner}/{repo}.\n\n"
                     "Fix it on GitHub > Settings > Developer settings > Fine-grained tokens > edit token:\n"
                     f"  - Resource owner: {owner}\n"
                     f"  - Repository access: Only select repositories > {repo}\n"
                     "  - Repository permissions: Contents = Read-only\n"
                     + (f"\nThis token belongs to '{login}', not '{owner}'. That account must be a "
                        "collaborator on the repository." if login not in ("?", owner) else ""))
    if status not in (200, 403):
      return False, f"Unexpected reply from GitHub when checking the repository (HTTP {status})."

    status, _ = _api_get(f"https://api.github.com/repos/{owner}/{repo}/contents/{path}?ref={branch}", token)
    if status in (403, 404):
      return False, (f"The token can see {owner}/{repo} but cannot read its files (HTTP {status}).\n"
                     "Edit the token and set Repository permissions > Contents = Read-only.")
    if status != 200:
      return False, f"Unexpected reply from GitHub when reading {path} (HTTP {status})."
    return True, f"Token OK (account: {login}). Tools can be loaded from {owner}/{repo}."
  except Exception as e:
    return False, f"Could not reach GitHub to test the token: {getattr(e, 'reason', e)}"


def _describe_http_error(error, has_token, key_mode=False):
  if isinstance(error, urllib.error.HTTPError) and key_mode:
    try:
      reason = json.loads(error.read().decode("utf-8")).get("error")
    except Exception:
      reason = None
    if error.code in (401, 403):
      return f"HTTP {error.code}: {reason or 'the access key was refused'} Sign out, then sign in with a valid key."
    return f"HTTP {error.code}: {reason or error.reason}"
  if isinstance(error, urllib.error.HTTPError):
    if error.code == 404 and not has_token:
      return "HTTP 404: the repository is private. Click 'Sign In...' and paste a token."
    if error.code == 404:
      return "HTTP 404: file not found, or the token cannot see this repository."
    if error.code == 401:
      return "HTTP 401: the GitHub token is invalid or expired. Sign out, then 'Sign In...' with a new one."
    if error.code == 403:
      return ("HTTP 403: the token has no access. It needs read access to repository "
              "contents (fine-grained: Contents = Read-only; classic: 'repo' scope).")
    return f"HTTP {error.code}: {error.reason}"
  return str(getattr(error, "reason", error))

def resolve_permissions(login, policy):
  """Owner policy (access.json) -> {'allowed': bool, 'offline_cache': bool}.
  No policy file = old behaviour (everything allowed)."""
  if not isinstance(policy, dict):
    return {"allowed": True, "offline_cache": True}
  rules = dict({"allowed": True, "offline_cache": False}, **(policy.get("default") or {}))
  users = {str(k).lower(): v for k, v in (policy.get("users") or {}).items() if isinstance(v, dict)}
  if login and login.lower() in users:
    rules.update(users[login.lower()])
  return {"allowed": bool(rules.get("allowed")), "offline_cache": bool(rules.get("offline_cache"))}


class RiggingToolsCollection:
  def __init__(self, scripts_directory):
    self.scripts_directory = scripts_directory
    self.window_name = "RiggingToolsCollectionUI"
    self.content_layout = "toolsContentLayout"
    self.tool_scripts = {}
    
    # Strictly fixed grid settings
    self.columns = 4
    self.button_height = 40
    self.grid_spacing = 6
    self.window_width = 720
    self.window_height = 680
    self.button_widgets = {}
    
    self.config_file = os.path.join(scripts_directory, "rigging_tools_config.json")
    self.MANIFEST_URL = "https://raw.githubusercontent.com/michelsat/Rigging-Tools-Collection/main/manifest.json"
    # Leave empty: the token is read from the 'Sign In...' button (Maya prefs),
    # the RTC_GITHUB_TOKEN / GITHUB_TOKEN environment variable, or github_token.txt
    # in the scripts directory. Never commit a token to the repository.
    self.GITHUB_TOKEN = self._load_token()
    
    self.ACCESS_URL = "https://raw.githubusercontent.com/michelsat/Rigging-Tools-Collection/main/access.json"
    self.cache_dir = os.path.join(self.scripts_directory, ".cloud_cache")
    self.user_prefers_cache = cmds.optionVar(query="rtc_use_cache") if cmds.optionVar(exists="rtc_use_cache") else True
    self.login = None
    self.permissions = {"allowed": True, "offline_cache": False}
    self.permissions_online = False
    self.use_cache = False
    self.check_permissions()
    
    self.button_order = []
    self.categories = []
    self.button_categories = {}
    self.tool_help = {}
    self.uncategorized_label = "Uncategorized"
    
    self.default_button_color = [0.3, 0.3, 0.32, 1.0]
    self.current_button_color = self.default_button_color.copy()
    self.button_colors = {}
    
    self.selected_button = None
    self.drag_source_index = None
    self._grid_rebuild_pending = False

    self.fetch_cloud_tools()
    self.load_config()

  def _wipe_cache(self):
    if os.path.exists(self.cache_dir):
      shutil.rmtree(self.cache_dir, ignore_errors=True)

  @property
  def key_mode(self):
    return is_access_key(self.GITHUB_TOKEN)

  def server_url(self):
    url = cmds.optionVar(query=SERVER_OPTION_VAR) if cmds.optionVar(exists=SERVER_OPTION_VAR) else ""
    return (url or SERVER_URL or "").strip().rstrip("/")

  def _check_key_permissions(self):
    """Access key users: the server decides who may use the tools and the cache."""
    self.access_error = ""
    try:
      status, data = _api_get(self.server_url() + "/whoami", self.GITHUB_TOKEN)
    except Exception:
      return False                       # offline
    self.permissions_online = True
    if status == 200 and isinstance(data, dict):
      self.login = str(data.get("user") or "unknown")
      self.permissions = {"allowed": True, "offline_cache": bool(data.get("offline_cache"))}
    else:
      self.access_error = (data or {}).get("error", "") if isinstance(data, dict) else ""
      self.permissions = {"allowed": False, "offline_cache": False}
    return True

  def check_permissions(self):
    """Applies the owner's rules: who may use the tools and who may keep an
    offline copy on this computer. Access keys are checked by the server;
    GitHub tokens by access.json in the repository."""
    self.login = None
    policy = None
    self.permissions_online = False
    self.access_error = ""
    if self.key_mode:
      if not self._check_key_permissions():
        self.permissions = {"allowed": True, "offline_cache": os.path.isdir(self.cache_dir)}
      self._apply_cache_rules()
      return
    if self.GITHUB_TOKEN:
      try:
        status, user = _api_get("https://api.github.com/user", self.GITHUB_TOKEN)
        if status == 200:
          self.login = (user or {}).get("login")
      except Exception:
        pass
    try:
      policy = json.loads(self._download_text(self.ACCESS_URL, timeout=5))
      self.permissions_online = True
    except urllib.error.HTTPError as e:
      # GitHub answered, so we are online. A 404 with a working token means the
      # repo has no access.json (old behaviour); anything else gets the default rules.
      self.permissions_online = True
      policy = None if (e.code == 404 and self.login) else {}
    except Exception:
      pass

    if self.permissions_online:
      self.permissions = resolve_permissions(self.login, policy)
    else:
      # Offline: only keep using a cache that an earlier online check allowed
      # (a cache is wiped as soon as the owner disallows it).
      self.permissions = {"allowed": True, "offline_cache": os.path.isdir(self.cache_dir)}
    self._apply_cache_rules()

  def _apply_cache_rules(self):
    if not self.permissions["allowed"] or not self.permissions["offline_cache"]:
      self._wipe_cache()
    self.use_cache = bool(self.user_prefers_cache and self.permissions["offline_cache"])
    if self.use_cache and not os.path.exists(self.cache_dir):
      os.makedirs(self.cache_dir)
    self._update_access_ui()

  def _access_label(self):
    if not self.GITHUB_TOKEN:
      return "Signed out  |  click 'Sign In...' and paste your access key to load the tools"
    who = self.login or ("unknown user" if self.key_mode else "unknown account")
    kind = "User" if self.key_mode else "Account"
    if not self.permissions["allowed"]:
      if self.key_mode:
        return f"Access key refused  |  {self.access_error or 'ask the tools owner for a valid key'}"
      return f"Account: {who}  |  NOT APPROVED by the tools owner"
    cache = "allowed" if self.permissions["offline_cache"] else "disabled by owner"
    online = "" if self.permissions_online else "  |  offline"
    return f"{kind}: {who}  |  Offline cache: {cache}{online}"

  def _update_access_ui(self):
    if cmds.text("rtcAccessText", exists=True):
      cmds.text("rtcAccessText", edit=True, label=self._access_label())
    if cmds.button("rtcAccountButton", exists=True):
      signed_in = bool(self.GITHUB_TOKEN)
      cmds.button("rtcAccountButton", edit=True, label="Sign Out" if signed_in else "Sign In...",
                  annotation=(f"Sign out of {self.login or 'this account'}: removes the saved token and the offline copy of the tools"
                              if signed_in else "Paste your GitHub token to load the tools"))
      style_button("rtcAccountButton", [0.45, 0.3, 0.3] if signed_in else [0.25, 0.4, 0.6], radius=6)
    if cmds.checkBox("rtcCacheCheckBox", exists=True):
      allowed = self.permissions["offline_cache"]
      cmds.checkBox("rtcCacheCheckBox", edit=True, value=self.use_cache, enable=allowed,
                    label="Smart Offline Cache" if allowed else "Offline Cache (owner only)")

  def toggle_cache_mode(self, value, *args):
    if value and not self.permissions["offline_cache"]:
      cmds.checkBox("rtcCacheCheckBox", edit=True, value=False)
      return cmds.warning("Offline cache is not allowed for your account by the tools owner.")
    self.use_cache = value
    self.user_prefers_cache = value
    cmds.optionVar(intValue=("rtc_use_cache", self.use_cache))
    if not value:
      self._wipe_cache()
    
    if self.use_cache:
      if not os.path.exists(self.cache_dir):
        os.makedirs(self.cache_dir)
      cmds.inViewMessage(amg="Mode: Smart Offline Cache Enabled", pos="midCenter", fade=True)
    else:
      cmds.inViewMessage(amg="Mode: Pure Cloud Streaming Enabled", pos="midCenter", fade=True)

  def _load_token(self):
    if cmds.optionVar(exists=TOKEN_OPTION_VAR):
      token = _clean_token(cmds.optionVar(query=TOKEN_OPTION_VAR))
      if token:
        return token
    for env_var in TOKEN_ENV_VARS:
      token = _clean_token(os.environ.get(env_var, ""))
      if token:
        return token
    token_file = os.path.join(self.scripts_directory, TOKEN_FILE_NAME)
    if os.path.exists(token_file):
      try:
        with open(token_file, "r", encoding="utf-8") as f:
          return _clean_token(f.read())
      except Exception:
        pass
    return ""

  def _token_source(self):
    """Where the current token comes from: 'prefs', 'env', 'file' or None."""
    if cmds.optionVar(exists=TOKEN_OPTION_VAR) and _clean_token(cmds.optionVar(query=TOKEN_OPTION_VAR)):
      return "prefs"
    if any(_clean_token(os.environ.get(v, "")) for v in TOKEN_ENV_VARS):
      return "env"
    if os.path.exists(os.path.join(self.scripts_directory, TOKEN_FILE_NAME)):
      return "file"
    return None

  def account_button_clicked(self, *args):
    if self.GITHUB_TOKEN:
      self.sign_out()
    else:
      self.set_github_token()

  def sign_out(self, *args):
    who = self.login or "this account"
    answer = cmds.confirmDialog(
        title="Sign Out",
        message=(f"Sign out of {who}?\n\n"
                 "This removes the saved GitHub token from this computer and deletes the\n"
                 "offline copy of the tools. Your button colors, groups and help stay."),
        button=["Sign Out", "Cancel"], defaultButton="Cancel", cancelButton="Cancel", dismissString="Cancel")
    if answer != "Sign Out":
      return
    if cmds.optionVar(exists=TOKEN_OPTION_VAR):
      cmds.optionVar(remove=TOKEN_OPTION_VAR)
    self._wipe_cache()
    self.GITHUB_TOKEN = self._load_token()
    leftover = self._token_source()
    if self.GITHUB_TOKEN and leftover == "env":
      cmds.warning("Still signed in through the RTC_GITHUB_TOKEN / GITHUB_TOKEN environment variable. "
                   "Remove it from your system environment variables to sign out fully.")
    elif self.GITHUB_TOKEN and leftover == "file":
      cmds.warning(f"Still signed in through {os.path.join(self.scripts_directory, TOKEN_FILE_NAME)}. "
                   "Delete that file to sign out fully.")

    # Drop everything loaded with the old token, then reload with what is left (normally nothing)
    self.tool_scripts = {}
    self.check_permissions()
    self.fetch_cloud_tools()
    self.load_config()
    self._refresh_group_selector()
    self._refresh_help_selector()
    self._rebuild_grid()
    self._update_access_ui()
    if not self.GITHUB_TOKEN:
      cmds.inViewMessage(amg=f"Signed out of {who}", pos="midCenter", fade=True)

  def _ask_server_url(self):
    result = cmds.promptDialog(
        title="Tools Server",
        message="Paste the tools server address you got from the tools owner\n(for example https://sat-tools.yourname.workers.dev):",
        text=self.server_url(), button=["OK", "Cancel"], defaultButton="OK",
        cancelButton="Cancel", dismissString="Cancel")
    if result != "OK":
      return ""
    url = cmds.promptDialog(query=True, text=True).strip().rstrip("/")
    if url:
      cmds.optionVar(stringValue=(SERVER_OPTION_VAR, url))
    return url

  def set_github_token(self, *args):
    result = cmds.promptDialog(
        title="Sign In",
        message=("Paste the access key you got from the tools owner (starts with 'sat_').\n"
                 "The tools owner can also paste a GitHub token here.\n"
                 "It is stored in your Maya preferences on this computer."),
        text="", button=["Sign In", "Cancel"], defaultButton="Sign In",
        cancelButton="Cancel", dismissString="Cancel")
    if result != "Sign In":
      return
    token = _clean_token(cmds.promptDialog(query=True, text=True))
    if is_access_key(token):
      server = self.server_url() or self._ask_server_url()
      if not server:
        return cmds.warning("An access key needs the tools server address. Ask the tools owner for it.")
      ok, message, _ = check_access_key(token, server)
      print(f"[Rigging Tools] Access key check: {message}")
      if not ok:
        cmds.confirmDialog(title="Sign In Problem", message=message, button=["OK"])
        return
      cmds.optionVar(stringValue=(TOKEN_OPTION_VAR, token))
      cmds.inViewMessage(amg="Signed in", pos="midCenter", fade=True)
    elif token:
      ok, message = check_github_token(token, self.MANIFEST_URL)
      print(f"[Rigging Tools] Token check: {message}")
      if ok:
        cmds.optionVar(stringValue=(TOKEN_OPTION_VAR, token))
        cmds.inViewMessage(amg="GitHub token saved and verified", pos="midCenter", fade=True)
      else:
        answer = cmds.confirmDialog(
            title="GitHub Token Problem", message=message,
            button=["Save Anyway", "Cancel"], defaultButton="Cancel",
            cancelButton="Cancel", dismissString="Cancel")
        if answer != "Save Anyway":
          return
        cmds.optionVar(stringValue=(TOKEN_OPTION_VAR, token))
    else:
      cmds.optionVar(remove=TOKEN_OPTION_VAR)
      cmds.inViewMessage(amg="GitHub token removed", pos="midCenter", fade=True)
    self.GITHUB_TOKEN = self._load_token()
    self.refresh_content()

  def _download_text(self, url, timeout):
    """Downloads a file from the repo. With an access key it goes through the
    tools server; with a GitHub token through the GitHub API (private repos);
    without either, the raw URL."""
    if self.key_mode:
      match = RAW_URL_PATTERN.match(url or "")
      server = self.server_url()
      if not match or not server:
        raise urllib.error.URLError("no tools server configured for access keys")
      path = match.groups()[3]
      url = server + "/file/" + urllib.parse.quote(path)
      headers = {"Authorization": f"Bearer {self.GITHUB_TOKEN}", "User-Agent": "RiggingToolsCollection"}
    elif self.GITHUB_TOKEN:
      url = _github_api_url(url)
      headers = {
          "Authorization": f"Bearer {self.GITHUB_TOKEN}",
          "Accept": "application/vnd.github.raw",
          "X-GitHub-Api-Version": "2022-11-28",
          "User-Agent": "RiggingToolsCollection",
      }
    else:
      headers = {"User-Agent": "RiggingToolsCollection"}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as response:
      return response.read().decode("utf-8")

  def fetch_cloud_tools(self):
    """Loads the tool list. Returns True if it came from the server."""
    self.tool_scripts = {}
    if not self.permissions["allowed"]:
      if self.key_mode:
        cmds.warning(f"Your access key was refused: {self.access_error or 'unknown key'} "
                     "Ask the tools owner for a valid key.")
      else:
        cmds.warning(f"Your GitHub account ({self.login or 'unknown'}) is not approved to use these tools. "
                     "Ask the tools owner to add you to access.json.")
      return False
    manifest_cache = os.path.join(self.cache_dir, "manifest.json")
    data = {}
    from_server = False

    try:
      raw_text = self._download_text(self.MANIFEST_URL, timeout=5)
      data = json.loads(raw_text)
      from_server = True

      if self.use_cache:
        if not os.path.exists(self.cache_dir): 
          os.makedirs(self.cache_dir)
        with open(manifest_cache, "w", encoding="utf-8", newline="") as f:
          f.write(raw_text)

    except Exception as e:
      cmds.warning(f"Could not load the tool list from GitHub - {_describe_http_error(e, bool(self.GITHUB_TOKEN), self.key_mode)}")
      if self.use_cache and os.path.exists(manifest_cache):
        try:
          with open(manifest_cache, "r", encoding="utf-8") as f:
            data = json.load(f)
          cmds.inViewMessage(amg="Offline Mode Active (using cached tools)", pos="midCenter", fade=True)
        except Exception:
          cmds.warning("Failed to read cached manifest.")
          return False
      else:
        return False

    tools_data = data.get("tools", {})
    for tool_id, tool_info in tools_data.items():
      self.tool_scripts[tool_id] = tool_info.get("url")
      if tool_id not in self.tool_help and "help" in tool_info:
        self.tool_help[tool_id] = tool_info["help"]
    return from_server

  def load_config(self):
    cloud_help_backup = dict(self.tool_help)
    (
        self.button_order,
        self.current_button_color,
        self.button_colors,
        self.categories,
        self.button_categories,
        local_help,
    ) = load_collection_config(self.config_file, self.tool_scripts, self.default_button_color)

    self.tool_help = cloud_help_backup
    if local_help:
        for tool, help_data in local_help.items():
            if help_data and (help_data.get("description") or help_data.get("links")):
                self.tool_help[tool] = help_data

  def save_config(self):
    try:
      save_collection_config(
          self.config_file, self.button_order, self.current_button_color,
          self.button_colors, self.categories, self.button_categories, self.tool_help,
      )
    except Exception as e:
      cmds.warning(f"Error saving configuration: {str(e)}")

  def run_script(self, module_name, *args):
    remote_url = self.tool_scripts.get(module_name)
    cached_tool_path = os.path.join(self.cache_dir, f"{module_name}.py")
    mod = types.ModuleType(module_name)

    if self.use_cache:
      if not os.path.exists(cached_tool_path):
        if not remote_url: 
          return cmds.warning(f"No URL found for {module_name}")
        cmds.inViewMessage(amg=f"Caching {module_name}...", pos="midCenter", fade=True)
        try:
          source_code = self._download_text(remote_url, timeout=8)
          # newline="" keeps the file's own line endings; without it Windows
          # doubles CRLF endings and breaks lines ending in a backslash
          with open(cached_tool_path, "w", encoding="utf-8", newline="") as f:
            f.write(source_code)
        except Exception as e:
          return cmds.warning(f"Download failed for {module_name} - {_describe_http_error(e, bool(self.GITHUB_TOKEN), self.key_mode)}")

      try:
        with open(cached_tool_path, "r", encoding="utf-8") as f:
          source_code = f.read()
        mod.__file__ = cached_tool_path
        mod.__dict__["__name__"] = "__main__"
        exec(compile(source_code, cached_tool_path, "exec"), mod.__dict__)
      except Exception as e:
        return cmds.warning(f"Cache execution error: {e}")

    else:
      if not remote_url: 
        return cmds.warning(f"No URL found for {module_name}")
      cmds.inViewMessage(amg=f"Streaming {module_name}...", pos="midCenter", fade=True)
      try:
        source_code = self._download_text(remote_url, timeout=8)
        mod.__file__ = f"<cloud>/{module_name}.py"
        mod.__dict__["__name__"] = "__main__"
        exec(compile(source_code, f"<cloud>/{module_name}", "exec"), mod.__dict__)
      except Exception as e:
        return cmds.warning(f"Streaming failed for {module_name} - {_describe_http_error(e, bool(self.GITHUB_TOKEN), self.key_mode)}")

    for entry in ("main", "run", "show_ui"):
      fn = getattr(mod, entry, None)
      if callable(fn):
        fn()
        break

  def show_tool_help_dialog(self, module_name, *args):
    help_data = self.tool_help.get(module_name, {})
    show_tool_help(module_name, help_data, on_edit_callback=self.edit_tool_help_dialog)

  def edit_tool_help_dialog(self, module_name, *args):
    existing_help = self.tool_help.get(module_name, {})
    changed, updated_help = edit_tool_help(module_name, existing_help)
    if changed:
      if updated_help is None or (not updated_help.get("description") and not updated_help.get("links")):
        if module_name in self.tool_help: del self.tool_help[module_name]
        cmds.inViewMessage(amg=f"Help removed for {module_name.replace('_', ' ').title()}", pos="midCenter", fade=True)
      else:
        self.tool_help[module_name] = updated_help
        cmds.inViewMessage(amg=f"Help saved for {module_name.replace('_', ' ').title()}", pos="midCenter", fade=True)
      self.save_config()
      self._refresh_help_selector()
      self._rebuild_grid()

  def delete_tool_help(self, module_name, *args):
    if module_name in self.tool_help:
      tool_display = module_name.replace("_", " ").title()
      result = cmds.confirmDialog(
          title="Delete Tool Help", message=f"Delete help settings and links for '{tool_display}'?",
          button=["Delete", "Cancel"], defaultButton="Delete", cancelButton="Cancel", dismissString="Cancel"
      )
      if result == "Delete":
        del self.tool_help[module_name]
        self.save_config()
        self._refresh_help_selector()
        self._rebuild_grid()
        cmds.inViewMessage(amg=f"Deleted help for {tool_display}", pos="midCenter", fade=True)

  def _refresh_help_selector(self):
    if not cmds.optionMenu("helpToolSelector", exists=True): return
    menu_items = cmds.optionMenu("helpToolSelector", query=True, itemListLong=True) or []
    for item in menu_items: cmds.deleteUI(item)
    if self.button_order:
      for tool in self.button_order:
        has_help = bool(self.tool_help.get(tool, {}).get("description") or self.tool_help.get(tool, {}).get("links"))
        suffix = "  [*Help]" if has_help else ""
        cmds.menuItem(label=tool.replace("_", " ").title() + suffix, parent="helpToolSelector")
    else:
      cmds.menuItem(label="No tools available", enable=False, parent="helpToolSelector")
    self._update_selected_help_status()

  def _get_selected_help_module(self):
    if not cmds.optionMenu("helpToolSelector", exists=True): return None
    val = cmds.optionMenu("helpToolSelector", query=True, value=True) or ""
    cleaned = val.replace("  [*Help]", "").strip()
    for tool in self.button_order:
      if tool.replace("_", " ").title() == cleaned: return tool
    return None

  def _update_selected_help_status(self, *args):
    if not cmds.text("helpStatusText", exists=True): return
    module_name = self._get_selected_help_module()
    if not module_name:
      cmds.text("helpStatusText", edit=True, label="Status: No tool selected")
      return
    help_info = self.tool_help.get(module_name)
    if help_info and (help_info.get("description") or help_info.get("links")):
      links_count = len(help_info.get("links", []))
      desc_len = len(help_info.get("description", ""))
      status = f"Configured ({desc_len} chars, {links_count} link(s))"
    else: status = "No help or links added yet."
    cmds.text("helpStatusText", edit=True, label=f"Status: {status}")

  def _open_selected_tool_help(self, *args):
    module_name = self._get_selected_help_module()
    if module_name: self.show_tool_help_dialog(module_name)

  def _edit_selected_tool_help(self, *args):
    module_name = self._get_selected_help_module()
    if module_name: self.edit_tool_help_dialog(module_name)

  def _delete_selected_tool_help(self, *args):
    module_name = self._get_selected_help_module()
    if module_name: self.delete_tool_help(module_name)

  def create_help_settings(self, parent_layout):
    self.help_frame = cmds.frameLayout(
        label="Tool Help & Video Settings", collapsable=True, collapse=True,
        parent=parent_layout, marginWidth=5, marginHeight=5
    )
    help_layout = cmds.columnLayout(adjustableColumn=True, columnAttach=("both", 5), rowSpacing=4)
    cmds.text(label="(Tip: You can also right-click any tool button to view or edit help)", align="left", font="smallPlainLabelFont")
    cmds.rowLayout(numberOfColumns=2, adjustableColumn=2, columnWidth2=(70, 250), parent=help_layout)
    cmds.text(label="Select Tool:", align="left")
    cmds.optionMenu("helpToolSelector", changeCommand=self._update_selected_help_status)
    cmds.setParent(help_layout)
    cmds.text("helpStatusText", label="Status: Select a tool", align="left", font="smallBoldLabelFont")
    cmds.rowLayout(numberOfColumns=3, adjustableColumn=1, columnWidth3=(120, 130, 90), parent=help_layout)
    cmds.button(label="View Help", height=28, command=self._open_selected_tool_help)
    cmds.button(label="Add / Edit Help...", height=28, command=self._edit_selected_tool_help)
    cmds.button(label="Delete Help", height=28, command=self._delete_selected_tool_help)
    cmds.setParent(parent_layout)
    self._refresh_help_selector()

  def swap_buttons(self, source_index, target_index):
    if source_index == target_index: return
    if 0 <= source_index < len(self.button_order) and 0 <= target_index < len(self.button_order):
      self.button_order[source_index], self.button_order[target_index] = (self.button_order[target_index], self.button_order[source_index])
      self.save_config()
      self._rebuild_grid()
      cmds.inViewMessage(amg="Button positions updated", pos="midCenter", fade=True)

  def start_drag(self, button_index, *args):
    self.drag_source_index = button_index
    source_name = self.button_order[button_index].replace("_", " ").title()
    cmds.inViewMessage(amg=f"Marked <hl>{source_name}</hl>. Right-click a target and choose Drop Here.", pos="midCenter", fade=True)

  def drop_on_target(self, target_index, *args):
    source_index = self.drag_source_index
    self.drag_source_index = None
    if source_index is None:
      return cmds.inViewMessage(amg="Choose Start Drag on a tool first.", pos="midCenter", fade=True)
    self.swap_buttons(source_index, target_index)

  def drag_tool_button(self, module_name, *args):
    return ["RiggingToolsCollectionTool", module_name]

  def drop_tool_button(self, target_module_name, messages, *args):
    if len(messages) < 2 or messages[0] != "RiggingToolsCollectionTool": return
    source_module_name = messages[1]
    if source_module_name == target_module_name: return
    try:
      source_index = self.button_order.index(source_module_name)
      target_index = self.button_order.index(target_module_name)
      self.swap_buttons(source_index, target_index)
    except ValueError: pass

  def _refresh_group_selector(self):
    if not cmds.optionMenu("groupManagerSelector", exists=True): return
    menu_items = cmds.optionMenu("groupManagerSelector", query=True, itemListLong=True) or []
    for item in menu_items: cmds.deleteUI(item)
    if self.categories:
      for category in self.categories: cmds.menuItem(label=category, parent="groupManagerSelector")
    else: cmds.menuItem(label="No custom groups", enable=False, parent="groupManagerSelector")

  def add_category(self, *args):
    category = cmds.textField("newGroupNameField", query=True, text=True).strip()
    if not category or category == self.uncategorized_label or category in self.categories: return
    self.categories.append(category)
    cmds.textField("newGroupNameField", edit=True, text="")
    self.save_config()
    self._refresh_group_selector()
    self._rebuild_grid()

  def rename_selected_category(self, *args):
    if not self.categories: return
    old_name = cmds.optionMenu("groupManagerSelector", query=True, value=True)
    if old_name not in self.categories: return
    result = cmds.promptDialog(
        title="Rename Tool Group", message="New group name:", text=old_name,
        button=["Rename", "Cancel"], defaultButton="Rename", cancelButton="Cancel", dismissString="Cancel"
    )
    if result != "Rename": return
    new_name = cmds.promptDialog(query=True, text=True).strip()
    if not new_name or new_name == self.uncategorized_label or new_name in self.categories: return
    category_index = self.categories.index(old_name)
    self.categories[category_index] = new_name
    self.button_categories = {
        module: new_name if category == old_name else category
        for module, category in self.button_categories.items()
    }
    self.save_config()
    self._refresh_group_selector()
    cmds.optionMenu("groupManagerSelector", edit=True, value=new_name)
    self._rebuild_grid()

  def remove_selected_category(self, *args):
    if not self.categories: return
    category = cmds.optionMenu("groupManagerSelector", query=True, value=True)
    if category not in self.categories: return
    self.categories.remove(category)
    self.button_categories = {
        module: assigned_category for module, assigned_category in self.button_categories.items() if assigned_category != category
    }
    self.save_config()
    self._refresh_group_selector()
    self._rebuild_grid()

  def assign_button_category(self, module_name, category, *args):
    if category in self.categories: self.button_categories[module_name] = category
    else:
      self.button_categories.pop(module_name, None)
      category = self.uncategorized_label
    self.save_config()
    self._rebuild_grid()

  def create_group_settings(self, parent_layout):
    self.group_frame = cmds.frameLayout(
        label="Tool Groups", collapsable=True, collapse=True,
        parent=parent_layout, marginWidth=5, marginHeight=5
    )
    group_layout = cmds.columnLayout(adjustableColumn=True, columnAttach=("both", 5), rowSpacing=4)
    cmds.rowLayout(numberOfColumns=2, adjustableColumn=1)
    cmds.textField("newGroupNameField", placeholderText="New group name (e.g. Skinning)")
    cmds.button(label="Add Group", width=90, command=self.add_category)
    cmds.setParent(group_layout)
    cmds.rowLayout(numberOfColumns=3, adjustableColumn=1, columnWidth3=(250, 90, 90))
    cmds.optionMenu("groupManagerSelector")
    cmds.button(label="Rename", command=self.rename_selected_category)
    cmds.button(label="Remove", command=self.remove_selected_category)
    cmds.setParent(parent_layout)
    self._refresh_group_selector()

  def get_blended_bgc(self, color_values):
    r, g, b = color_values[0], color_values[1], color_values[2]
    a = color_values[3] if len(color_values) > 3 else 1.0
    bg = 0.25
    return [max(0.0, min(1.0, (r * a) + (bg * (1.0 - a)))), max(0.0, min(1.0, (g * a) + (bg * (1.0 - a)))), max(0.0, min(1.0, (b * a) + (bg * (1.0 - a))))]

  # ---- button painting
  def _paint_tool_button(self, module_name):
    path = self.button_widgets.get(module_name)
    if not path or not cmds.button(path, exists=True): return
    color = self.button_colors.get(module_name, self.default_button_color)
    style_button(path, self.get_blended_bgc(color), selected=(module_name == self.selected_button))

  def _paint_preview(self):
    if cmds.button("colorPreviewButton", exists=True):
      label = self.selected_button.replace("_", " ").title() if self.selected_button else "Preview"
      cmds.button("colorPreviewButton", edit=True, label=label)
      style_button("colorPreviewButton", self.get_blended_bgc(self.current_button_color))

  # ---- color editor state
  def _set_color_controls(self, rgba, skip=None):
    """Shows rgba in every color control (except the one being dragged)."""
    r, g, b = rgba[:3]
    a = rgba[3] if len(rgba) > 3 else 1.0
    if skip != "picker" and cmds.colorSliderGrp("colorPickerGrp", exists=True):
      cmds.colorSliderGrp("colorPickerGrp", edit=True, rgb=(r, g, b))
    if skip != "hex" and cmds.textFieldGrp("hexColorField", exists=True):
      cmds.textFieldGrp("hexColorField", edit=True, text=rgb_to_hex((r, g, b)))
    if skip != "hsv" and cmds.floatSliderGrp("hueSlider", exists=True):
      h, s, v = colorsys.rgb_to_hsv(r, g, b)
      cmds.floatSliderGrp("hueSlider", edit=True, value=h * 360.0)
      cmds.floatSliderGrp("satSlider", edit=True, value=s)
      cmds.floatSliderGrp("valSlider", edit=True, value=v)
    if skip != "alpha" and cmds.floatSliderGrp("alphaSlider", exists=True):
      cmds.floatSliderGrp("alphaSlider", edit=True, value=a)
    self._paint_preview()

  def _apply_color(self, rgba, save=True, skip=None):
    rgba = [max(0.0, min(1.0, float(c))) for c in list(rgba[:3]) + [rgba[3] if len(rgba) > 3 else 1.0]]
    self.current_button_color = rgba
    if self.selected_button is not None:
      self.button_colors[self.selected_button] = rgba.copy()
      self._paint_tool_button(self.selected_button)
      if save: self.save_config()
    self._set_color_controls(rgba, skip=skip)

  def _alpha(self):
    return cmds.floatSliderGrp("alphaSlider", query=True, value=True) if cmds.floatSliderGrp("alphaSlider", exists=True) else 1.0

  def _on_picker(self, save, *args):
    rgb = cmds.colorSliderGrp("colorPickerGrp", query=True, rgb=True)
    self._apply_color(list(rgb) + [self._alpha()], save=save, skip="picker")

  def _on_hex(self, *args):
    text = cmds.textFieldGrp("hexColorField", query=True, text=True)
    rgb = hex_to_rgb(text)
    if rgb is None:
      cmds.warning(f"'{text}' is not a hex color. Use #RRGGBB, e.g. #3A7BD5.")
      self._set_color_controls(self.current_button_color)
      return
    self._apply_color(list(rgb) + [self._alpha()], skip="hex")

  def _on_hsv(self, save, *args):
    h = cmds.floatSliderGrp("hueSlider", query=True, value=True) / 360.0
    s = cmds.floatSliderGrp("satSlider", query=True, value=True)
    v = cmds.floatSliderGrp("valSlider", query=True, value=True)
    self._apply_color(list(colorsys.hsv_to_rgb(h, s, v)) + [self._alpha()], save=save, skip="hsv")

  def _on_alpha(self, save, *args):
    self._apply_color(list(self.current_button_color[:3]) + [self._alpha()], save=save, skip="alpha")

  def open_color_wheel(self, *args):
    """Maya's full color editor (HSV wheel, eyedropper, numeric input)."""
    r, g, b = self.current_button_color[:3]
    cmds.colorEditor(rgbValue=(r, g, b), alpha=self._alpha())
    if cmds.colorEditor(query=True, result=True):
      rgb = cmds.colorEditor(query=True, rgb=True)
      alpha = cmds.colorEditor(query=True, alpha=True)
      self._apply_color(list(rgb) + [alpha])

  # ---- selection / presets / reset
  def update_button_color(self, *args):
    # kept for compatibility: apply whatever the controls currently show
    self._on_hsv(True)

  def select_button_for_editing(self, module_name, *args):
    previous = self.selected_button
    self.selected_button = module_name
    if previous: self._paint_tool_button(previous)
    self._paint_tool_button(module_name)
    self.current_button_color = list(self.button_colors.get(module_name, self.default_button_color))
    if len(self.current_button_color) < 4: self.current_button_color.append(1.0)
    if cmds.frameLayout(self.color_frame, exists=True):
      cmds.frameLayout(self.color_frame, edit=True, collapse=False)
    self._set_color_controls(self.current_button_color)
    self.update_selected_button_display()
    cmds.inViewMessage(amg=f"Editing color: {module_name.replace('_', ' ').title()}", pos="midCenter", fade=True)

  def clear_button_selection(self, *args):
    previous, self.selected_button = self.selected_button, None
    if previous: self._paint_tool_button(previous)
    self.update_selected_button_display()
    self._paint_preview()

  def update_selected_button_display(self):
    if cmds.text("selectedButtonText", exists=True):
      if self.selected_button:
        cmds.text("selectedButtonText", edit=True, label=f"Editing: {self.selected_button.replace('_', ' ').title()}")
      else: cmds.text("selectedButtonText", edit=True, label="No button selected - right-click a tool and choose 'Edit Color'")

  def reset_button_color(self, *args):
    if not self.selected_button: return
    self.button_colors.pop(self.selected_button, None)
    self.current_button_color = self.default_button_color.copy()
    self.save_config()
    self._paint_tool_button(self.selected_button)
    self._set_color_controls(self.current_button_color)

  def preset_color(self, color_values, *args):
    if len(color_values) == 3: color_values = list(color_values) + [1.0]
    self._apply_color(color_values)

  def create_color_settings(self, parent_layout):
    self.color_frame = cmds.frameLayout(
        label="Button Color Settings", collapsable=True, collapse=True,
        parent=parent_layout, marginWidth=5, marginHeight=5
    )
    color_layout = cmds.columnLayout(adjustableColumn=True, columnAttach=("both", 5), rowSpacing=5)
    cmds.text("selectedButtonText", label="", align="left", font="boldLabelFont")

    # live preview, styled exactly like the tool buttons
    cmds.rowLayout(numberOfColumns=2, adjustableColumn=1, columnAttach2=("both", "both"), columnOffset2=(0, 6))
    cmds.button("colorPreviewButton", label="Preview", height=self.button_height, command=lambda *_: None)
    cmds.button(label="Done", width=60, height=self.button_height, command=self.clear_button_selection,
                annotation="Stop editing this button")
    cmds.setParent(color_layout)

    r, g, b = self.current_button_color[:3]
    cmds.rowLayout(numberOfColumns=2, adjustableColumn=1, columnAttach2=("both", "both"), columnOffset2=(0, 6))
    cmds.colorSliderGrp("colorPickerGrp", label="Color", rgb=(r, g, b), columnWidth3=(70, 60, 150), adjustableColumn=3,
                        annotation="Click the swatch for Maya's color picker; drag the slider to change brightness",
                        dragCommand=lambda *a: self._on_picker(False), changeCommand=lambda *a: self._on_picker(True))
    cmds.button(label="Color Wheel...", width=100, command=self.open_color_wheel,
                annotation="Open Maya's full color editor (HSV wheel, eyedropper)")
    cmds.setParent(color_layout)

    cmds.textFieldGrp("hexColorField", label="Hex", text=rgb_to_hex((r, g, b)), columnWidth2=(70, 100),
                      annotation="Type a hex color like #3A7BD5 and press Enter", changeCommand=self._on_hex)

    for name, label, max_value in (("hueSlider", "Hue", 360.0), ("satSlider", "Saturation", 1.0),
                                   ("valSlider", "Brightness", 1.0), ("alphaSlider", "Opacity", 1.0)):
      handler = self._on_alpha if name == "alphaSlider" else self._on_hsv
      cmds.floatSliderGrp(
          name, label=label, field=True, minValue=0.0, maxValue=max_value,
          fieldMinValue=0.0, fieldMaxValue=max_value, value=0.0, columnWidth3=(70, 60, 150), adjustableColumn=3,
          dragCommand=lambda *a, h=handler: h(False), changeCommand=lambda *a, h=handler: h(True)
      )

    cmds.separator(height=8, style="in")
    cmds.text(label="Presets:", align="left")
    preset_layout = cmds.columnLayout(adjustableColumn=True, rowSpacing=3)
    presets = [
        ("Blue", [0.2, 0.3, 0.6]), ("Green", [0.2, 0.6, 0.3]), ("Red", [0.6, 0.2, 0.2]), ("Purple", [0.5, 0.2, 0.5]), ("Orange", [0.8, 0.4, 0.1]),
        ("Teal", [0.1, 0.6, 0.6]), ("Grey", [0.4, 0.4, 0.4]), ("Pink", [0.8, 0.3, 0.5]), ("Yellow", [0.8, 0.8, 0.2]), ("Dark", [0.2, 0.2, 0.2])
    ]
    for row_start in range(0, len(presets), 5):
      preset_row = cmds.formLayout(parent=preset_layout, height=26)
      row_presets = presets[row_start : row_start + 5]
      for column_index, (name, values) in enumerate(row_presets):
        preset_button = cmds.button(
            label=name, backgroundColor=values, command=lambda x, c=values: self.preset_color(c),
            height=26, recomputeSize=False, parent=preset_row
        )
        style_button(preset_button, values, radius=5)
        left_pos = int(round((100.0 / len(row_presets)) * column_index))
        right_pos = int(round((100.0 / len(row_presets)) * (column_index + 1)))
        attachments = [(preset_button, "top", 0), (preset_button, "bottom", 0)]
        if column_index == 0: attachments.append((preset_button, "left", 0))
        else: cmds.formLayout(preset_row, edit=True, attachPosition=(preset_button, "left", 3, left_pos))
        if column_index == len(row_presets) - 1: attachments.append((preset_button, "right", 0))
        else: cmds.formLayout(preset_row, edit=True, attachPosition=(preset_button, "right", 3, right_pos))
        cmds.formLayout(preset_row, edit=True, attachForm=attachments)

    cmds.setParent(color_layout)
    cmds.separator(height=5)
    cmds.button(label="Reset to Default", command=self.reset_button_color, height=28)
    cmds.setParent(parent_layout)
    self.update_selected_button_display()
    self._set_color_controls(self.current_button_color)

  def _clear_content(self):
    children = cmds.layout(self.content_layout, query=True, childArray=True) or []
    for child in children: cmds.deleteUI(child)

  def _create_fixed_row(self, parent_layout, row_modules):
    row = cmds.formLayout(parent=parent_layout, height=self.button_height)
    for column_index, module_name in enumerate(row_modules):
      button_index = self.button_order.index(module_name)
      display_name = module_name.replace("_", " ").title()
      button_name = f"button_{button_index}"
      button_color = self.button_colors.get(module_name, self.default_button_color)
      blended_color = self.get_blended_bgc(button_color)

      tool_help_data = self.tool_help.get(module_name, {})
      has_help = bool(tool_help_data.get("description") or tool_help_data.get("links"))
      desc = tool_help_data.get("description", "").strip()
      
      if desc:
        first_line = desc.split("\n")[0]
        if len(first_line) > 55: first_line = first_line[:52] + "..."
        button_annotation = f"{display_name}: {first_line}\n(Right-click for Help & options)"
      else: button_annotation = f"Run {display_name}\n(Right-click for Help & options)"

      button = cmds.button(
          button_name, label=display_name, command=lambda x, mn=module_name: self.run_script(mn),
          annotation=button_annotation, backgroundColor=blended_color, height=self.button_height, recomputeSize=False,
          dragCallback=(lambda drag_control, x, y, modifiers, mn=module_name: (self.drag_tool_button(mn))),
          dropCallback=(lambda drag_control, drop_control, messages, x, y, drag_type, mn=module_name: self.drop_tool_button(mn, messages)),
          parent=row
      )
      self.button_widgets[module_name] = button
      self._paint_tool_button(module_name)

      attachments = [(button, "top", 0), (button, "bottom", 0)]
      left_position = int(round((100.0 / self.columns) * column_index))
      right_position = int(round((100.0 / self.columns) * (column_index + 1)))

      if column_index == 0: attachments.append((button, "left", 0))
      else: cmds.formLayout(row, edit=True, attachPosition=(button, "left", self.grid_spacing, left_position))
      if column_index == len(row_modules) - 1: attachments.append((button, "right", 0))
      else: cmds.formLayout(row, edit=True, attachPosition=(button, "right", self.grid_spacing, right_position))
      cmds.formLayout(row, edit=True, attachForm=attachments)

      cmds.popupMenu(parent=button)
      cmds.menuItem(label="Help / Video Links", command=lambda x, mn=module_name: self.show_tool_help_dialog(mn))
      cmds.menuItem(label="Add / Edit Help...", command=lambda x, mn=module_name: self.edit_tool_help_dialog(mn))
      if has_help: cmds.menuItem(label="Delete Help", command=lambda x, mn=module_name: self.delete_tool_help(mn))
      cmds.menuItem(divider=True)
      cmds.menuItem(label="Start Drag (Menu Fallback)", command=lambda x, idx=button_index: self.start_drag(idx))
      cmds.menuItem(label="Drop Here (Menu Fallback)", command=lambda x, idx=button_index: self.drop_on_target(idx))
      cmds.menuItem(divider=True)
      cmds.menuItem(label="Assign to Group", subMenu=True)
      cmds.menuItem(label=self.uncategorized_label, command=lambda x, mn=module_name: self.assign_button_category(mn, None))
      for category in self.categories:
        cmds.menuItem(label=category, command=lambda x, mn=module_name, cat=category: (self.assign_button_category(mn, cat)))
      cmds.setParent("..", menu=True)
      cmds.menuItem(divider=True)
      cmds.menuItem(label="Edit Color", command=lambda x, mn=module_name: self.select_button_for_editing(mn))

  def create_grid_layout(self, parent_layout):
    if not self.button_order:
      return cmds.text(
          label=("No tools loaded from GitHub.\nIf the repository is private, click 'Sign In...' below "
                 "and paste a token.\nDetails are in the Script Editor."),
          align="center", parent=parent_layout)

    cmds.setParent(parent_layout)
    grid_layout = cmds.columnLayout(adjustableColumn=True, rowSpacing=8)
    group_names = list(self.categories)
    uncategorized_tools = [m for m in self.button_order if self.button_categories.get(m) not in self.categories]
    if uncategorized_tools or not group_names: group_names.append(self.uncategorized_label)

    for category in group_names:
      group_tools = uncategorized_tools if category == self.uncategorized_label else [m for m in self.button_order if self.button_categories.get(m) == category]
      group_frame = cmds.frameLayout(label=category, collapsable=True, collapse=False, marginWidth=6, marginHeight=6, parent=grid_layout)
      group_layout = cmds.columnLayout(adjustableColumn=True, rowSpacing=self.grid_spacing, parent=group_frame)

      if group_tools:
        for row_start in range(0, len(group_tools), self.columns):
          self._create_fixed_row(group_layout, group_tools[row_start : row_start + self.columns])
      else: cmds.text(label="No tools assigned to this group yet.", align="left", parent=group_layout)
      cmds.setParent(grid_layout)

  def _rebuild_grid(self):
    if not cmds.layout(self.content_layout, exists=True): return
    self._clear_content()
    self.button_widgets = {}
    cmds.setParent(self.content_layout)
    self.create_grid_layout(self.content_layout)

  def refresh_content(self, *args):
    # Fetch first: only drop the cached tools once the new list has arrived,
    # so a failed sync (no internet, bad token) keeps the tools you had.
    previous_tools = dict(self.tool_scripts)
    self.check_permissions()                       # the owner may have changed access.json
    if not self.permissions["allowed"]:
      previous_tools = {}
    if self.fetch_cloud_tools():
      if self.use_cache and os.path.exists(self.cache_dir):
        for name in os.listdir(self.cache_dir):
          if name.endswith(".py"):
            try: os.remove(os.path.join(self.cache_dir, name))
            except Exception as e: cmds.warning(f"Could not clear cached {name}: {e}")
      synced = True
    else:
      if not self.tool_scripts:
        self.tool_scripts = previous_tools
      synced = False
    self.load_config()
    self._refresh_group_selector()
    self._refresh_help_selector()

    if cmds.layout(self.content_layout, exists=True):
      self._rebuild_grid()
      if synced:
        cmds.inViewMessage(amg="Cache cleared & tools synced with server", pos="midCenter", fade=True)
      else:
        cmds.inViewMessage(amg="Sync failed - see the Script Editor", pos="midCenter", fade=True)
    else: self.show_ui()

  def _remember_split(self, *args):
    if cmds.paneLayout("rtcSplitPane", exists=True):
      sizes = cmds.paneLayout("rtcSplitPane", query=True, paneSize=True) or []
      if len(sizes) >= 2:
        cmds.optionVar(intValue=(SPLIT_OPTION_VAR, int(sizes[1])))

  def show_ui(self):
    if cmds.window(self.window_name, exists=True): cmds.deleteUI(self.window_name)
    self.button_widgets = {}

    # Width is fixed so the banner always fits; only the height can be dragged
    # (when Qt is not available the window is not resizable at all).
    can_lock_width = wrapInstance is not None and omui is not None
    window = cmds.window(
        self.window_name, title="SAT Rigging Tools Collection",
        widthHeight=(self.window_width, self.window_height), sizeable=can_lock_width, resizeToFitChildren=False,
    )

    main_form = cmds.formLayout(numberOfDivisions=100)
    header_column = cmds.columnLayout(adjustableColumn=True, columnAttach=("both", 0), parent=main_form)

    # banner next to the settings (sattools/) or one level up (scripts/)
    banner_path = next((p for p in (os.path.join(self.scripts_directory, "sat_banner.png"),
                                    os.path.join(os.path.dirname(self.scripts_directory), "sat_banner.png"))
                        if os.path.exists(p)), "")
    if banner_path:
        cmds.image(image=banner_path, width=720, height=60, parent=header_column)
    else:
        cmds.text(label="SAT Rigging Tools Collection", font="boldLabelFont", height=30)

    cmds.columnLayout(adjustableColumn=True, columnAttach=("both", 5), parent=header_column)
    cmds.text(label="(Drag to reorder; right-click for help, groups, and colors. Drag the bar below the settings to resize.)", font="smallPlainLabelFont", height=20, backgroundColor=[0.2, 0.2, 0.22])
    cmds.text("rtcAccessText", label=self._access_label(), font="smallPlainLabelFont", height=18, align="center")
    cmds.setParent(main_form)

    footer_layout = cmds.formLayout(parent=main_form, height=44, backgroundColor=[0.22, 0.22, 0.24])
    cache_checkbox = cmds.checkBox("rtcCacheCheckBox", label="Smart Offline Cache", value=self.use_cache, changeCommand=self.toggle_cache_mode, parent=footer_layout,
                                   annotation="Keep a local copy of the tools for offline use. Only available if the tools owner allows it for your account.")
    token_button = cmds.button("rtcAccountButton", label="Sign In...", command=self.account_button_clicked, height=30, width=110, parent=footer_layout)
    refresh_button = cmds.button(label="Sync with Server", command=self.refresh_content, height=30, width=140, backgroundColor=[0.2, 0.5, 0.35], parent=footer_layout)
    close_button = cmds.button(label="Close Window", command=lambda x: cmds.deleteUI(self.window_name), height=30, width=120, backgroundColor=[0.6, 0.25, 0.25], parent=footer_layout)
    style_button(refresh_button, [0.2, 0.5, 0.35], radius=6)
    style_button(close_button, [0.6, 0.25, 0.25], radius=6)

    cmds.formLayout(
        footer_layout, edit=True,
        attachForm=[
            (cache_checkbox, "left", 15), (cache_checkbox, "top", 14),
            (token_button, "top", 7), (token_button, "bottom", 7),
            (refresh_button, "top", 7), (refresh_button, "bottom", 7),
            (close_button, "top", 7), (close_button, "bottom", 7), (close_button, "right", 10)
        ],
        attachControl=[(refresh_button, "right", 10, close_button), (token_button, "right", 10, refresh_button)]
    )
    cmds.setParent(main_form)

    # Top pane: settings (own scroll bar)  |  bottom pane: tool buttons (own scroll bar)
    split = cmds.optionVar(query=SPLIT_OPTION_VAR) if cmds.optionVar(exists=SPLIT_OPTION_VAR) else 30
    split = max(10, min(90, int(split)))
    split_pane = cmds.paneLayout("rtcSplitPane", configuration="horizontal2", parent=main_form,
                                 paneSize=[(1, 100, split), (2, 100, 100 - split)],
                                 separatorThickness=6, separatorMovedCommand=self._remember_split)

    cmds.scrollLayout(childResizable=True, verticalScrollBarThickness=14, parent=split_pane)
    settings_column = cmds.columnLayout(adjustableColumn=True, columnAttach=("both", 5), rowSpacing=4)
    self.create_group_settings(settings_column)
    self.create_color_settings(settings_column)
    self.create_help_settings(settings_column)
    cmds.setParent(split_pane)

    cmds.scrollLayout(childResizable=True, verticalScrollBarThickness=16, backgroundColor=[0.25, 0.25, 0.25], parent=split_pane)
    cmds.columnLayout(self.content_layout, adjustableColumn=True, columnAttach=("both", 5))
    self.create_grid_layout(self.content_layout)

    cmds.formLayout(
        main_form, edit=True,
        attachForm=[
            (header_column, "top", 0), (header_column, "left", 0), (header_column, "right", 0),
            (footer_layout, "left", 0), (footer_layout, "right", 0), (footer_layout, "bottom", 0),
            (split_pane, "left", 0), (split_pane, "right", 0),
        ],
        attachControl=[(split_pane, "top", 4, header_column), (split_pane, "bottom", 4, footer_layout)],
    )
    self._update_access_ui()
    cmds.showWindow(window)
    self._lock_window_width(window)

  def _lock_window_width(self, window):
    if wrapInstance is None or omui is None: return
    try:
      ptr = omui.MQtUtil.findWindow(window)
      if ptr:
        widget = wrapInstance(int(ptr), QtWidgets.QWidget)
        widget.setFixedWidth(self.window_width)
        widget.setMinimumHeight(420)
    except Exception:
      pass

def default_data_directory():
  """The sattools folder this UI is installed in (.../maya/scripts/sattools),
  so the tools work on any computer without editing a path."""
  return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def launch_rigging_tools_collection(scripts_directory=None):
  data_directory = scripts_directory or default_data_directory()
  if not os.path.isdir(data_directory):
    os.makedirs(data_directory)
  tools_collection = RiggingToolsCollection(data_directory)
  tools_collection.show_ui()

if __name__ == "__main__":
  launch_rigging_tools_collection()
