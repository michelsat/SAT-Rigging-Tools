"""Qt dialogs for saved per-tool help text and web links."""

import html


def _load_qt():
  try:
    from PySide6 import QtCore, QtGui, QtWidgets
    from shiboken6 import wrapInstance
  except ImportError:
    from PySide2 import QtCore, QtGui, QtWidgets
    from shiboken2 import wrapInstance
  from maya import OpenMayaUI as omui

  return QtCore, QtGui, QtWidgets, wrapInstance, omui


def _maya_parent(QtWidgets, wrapInstance, omui):
  try:
    pointer = omui.MQtUtil.mainWindow()
    return wrapInstance(int(pointer), QtWidgets.QWidget) if pointer else None
  except Exception:
    return None


def _run_dialog(dialog):
  execute = getattr(dialog, "exec", None) or getattr(dialog, "exec_", None)
  if callable(execute):
    return execute()
  return None


def edit_tool_help(module_name, existing_help=None):
  """Open an editor dialog to add, edit, or delete help info (description & video/web links).

  Returns:
      (changed: bool, help_data: dict or None)
  """
  try:
    QtCore, QtGui, QtWidgets, wrapInstance, omui = _load_qt()
  except Exception as err:
    print(f"Failed to load Qt for edit_tool_help: {err}")
    return False, existing_help

  if not existing_help or not isinstance(existing_help, dict):
    existing_help = {}

  parent_widget = _maya_parent(QtWidgets, wrapInstance, omui)
  dialog = QtWidgets.QDialog(parent_widget)
  title_name = module_name.replace("_", " ").title()
  dialog.setWindowTitle(f"Edit Tool Help - {title_name}")
  dialog.setMinimumSize(540, 440)
  dialog.resize(580, 480)

  layout = QtWidgets.QVBoxLayout(dialog)
  layout.setSpacing(8)
  layout.setContentsMargins(12, 12, 12, 12)

  # Title header
  header_label = QtWidgets.QLabel(
      f"<h3 style='margin: 0; padding: 0;'>Help & Video Settings: <span"
      f" style='color: #48c9b0;'>{html.escape(title_name)}</span></h3>"
  )
  layout.addWidget(header_label)

  # Description section
  desc_label = QtWidgets.QLabel(
      "<b>Tool Description:</b> (Explain what the tool does, how to use it, and"
      " tips)"
  )
  layout.addWidget(desc_label)

  description = QtWidgets.QTextEdit()
  description.setPlaceholderText(
      "Describe tool functionality, prerequisites, workflow steps, etc."
  )
  description.setPlainText(existing_help.get("description", ""))
  layout.addWidget(description, 1)

  # Links section
  links_label = QtWidgets.QLabel(
      "<b>Help & Video Links:</b> (One URL per line - YouTube tutorials, Vimeo,"
      " documentation, etc.)"
  )
  layout.addWidget(links_label)

  links = QtWidgets.QPlainTextEdit()
  links.setPlaceholderText(
      "https://www.youtube.com/watch?v=...\nhttps://vimeo.com/...\nhttps://example.com/docs"
  )
  links.setMaximumHeight(90)
  links.setPlainText("\n".join(existing_help.get("links", [])))
  layout.addWidget(links)

  # Action Buttons
  btn_box = QtWidgets.QDialogButtonBox()
  save_button = btn_box.addButton(
      "Save Help", QtWidgets.QDialogButtonBox.AcceptRole
  )
  delete_button = btn_box.addButton(
      "Delete Help", QtWidgets.QDialogButtonBox.DestructiveRole
  )
  cancel_button = btn_box.addButton(QtWidgets.QDialogButtonBox.Cancel)
  layout.addWidget(btn_box)

  dialog.saved_help = existing_help
  dialog.changed = False

  def save_help():
    raw_links = [
        line.strip()
        for line in links.toPlainText().splitlines()
        if line.strip()
    ]
    formatted_links = []
    for l in raw_links:
      if not l.lower().startswith(("http://", "https://")):
        l = "https://" + l
      formatted_links.append(l)

    text = description.toPlainText().strip()
    dialog.saved_help = {"description": text, "links": formatted_links}
    dialog.changed = True
    dialog.accept()

  def delete_help():
    confirm = QtWidgets.QMessageBox.question(
        dialog,
        "Confirm Delete Help",
        f"Are you sure you want to delete all help and links for '{title_name}'?",
        QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        QtWidgets.QMessageBox.No,
    )
    if confirm == QtWidgets.QMessageBox.Yes:
      dialog.saved_help = None
      dialog.changed = True
      dialog.accept()

  save_button.clicked.connect(save_help)
  delete_button.clicked.connect(delete_help)
  cancel_button.clicked.connect(dialog.reject)

  _run_dialog(dialog)
  return dialog.changed, dialog.saved_help


def show_tool_help(module_name, help_data=None, on_edit_callback=None):
  """Show a readable help window with safe, clickable web/video links and an Edit button."""
  try:
    QtCore, QtGui, QtWidgets, wrapInstance, omui = _load_qt()
  except Exception as err:
    print(f"Failed to load Qt for show_tool_help: {err}")
    return False

  if not help_data or not isinstance(help_data, dict):
    help_data = {}

  parent_widget = _maya_parent(QtWidgets, wrapInstance, omui)
  dialog = QtWidgets.QDialog(parent_widget)
  title_name = module_name.replace("_", " ").title()
  dialog.setWindowTitle(f"Help - {title_name}")
  dialog.setMinimumSize(540, 380)
  dialog.resize(560, 420)

  layout = QtWidgets.QVBoxLayout(dialog)
  layout.setSpacing(10)
  layout.setContentsMargins(12, 12, 12, 12)

  browser = QtWidgets.QTextBrowser()
  browser.setOpenExternalLinks(True)

  raw_desc = help_data.get("description", "").strip()
  description_html = (
      html.escape(raw_desc).replace("\n", "<br>")
      if raw_desc
      else (
          "<i>No description has been added yet. Click <b>'Edit Help...'</b>"
          " below to add a description and video tutorial links.</i>"
      )
  )

  links = help_data.get("links", [])
  link_items = []
  for link in links:
    safe_link = str(link).strip()
    if not safe_link:
      continue
    escaped_link = html.escape(safe_link)
    lower = safe_link.lower()
    is_video = any(
        vid_domain in lower
        for vid_domain in (
            "youtube.com",
            "youtu.be",
            "vimeo.com",
            ".mp4",
            "bilibili.com",
            "loom.com",
        )
    )

    if is_video:
      prefix = "<span style='color: #e74c3c; font-weight: bold;'>[Video Tutorial]</span> "
    else:
      prefix = "<span style='color: #3498db; font-weight: bold;'>[Web Link]</span> "

    href = (
        escaped_link
        if lower.startswith(("http://", "https://"))
        else f"https://{escaped_link}"
    )
    link_items.append(
        f"<li>{prefix}<a href='{href}' style='color: #5dade2;"
        f" text-decoration: underline;'>{escaped_link}</a></li>"
    )

  html_content = (
      f"<h2 style='color: #1abc9c; margin-top: 0; margin-bottom:"
      f" 8px;'>{html.escape(title_name)}</h2>"
  )
  html_content += (
      "<div style='font-size: 11pt; line-height: 1.5; color:"
      f" #ecf0f1;'>{description_html}</div>"
  )

  if link_items:
    html_content += (
        "<h3 style='color: #f39c12; margin-top: 18px; margin-bottom: 6px;'>Help"
        " & Tutorial Video Links:</h3>"
    )
    html_content += (
        f"<ul style='line-height: 1.8; margin-top: 4px;'>{''.join(link_items)}</ul>"
    )

  browser.setHtml(html_content)
  layout.addWidget(browser, 1)

  # Bottom buttons row
  btn_row = QtWidgets.QHBoxLayout()

  edit_btn = QtWidgets.QPushButton("Edit Help...")
  edit_btn.setToolTip("Open editor to change description and links")

  close_btn = QtWidgets.QPushButton("Close")
  close_btn.setDefault(True)

  btn_row.addWidget(edit_btn)
  btn_row.addStretch()
  btn_row.addWidget(close_btn)
  layout.addLayout(btn_row)

  dialog.edit_requested = False

  def on_edit():
    dialog.edit_requested = True
    dialog.accept()

  edit_btn.clicked.connect(on_edit)
  close_btn.clicked.connect(dialog.accept)

  _run_dialog(dialog)

  if dialog.edit_requested and callable(on_edit_callback):
    on_edit_callback(module_name)

  return True