"""Persistent configuration for the Rigging Tools Collection."""

import json
import os


UNCATEGORIZED = "Uncategorized"


def load_config(config_file, available_tools, default_button_color):
    """Load and validate saved tool ordering, colors, and group assignments."""
    defaults = (sorted(available_tools), default_button_color.copy(), {}, [], {}, {})
    if not os.path.exists(config_file):
        return defaults

    try:
        with open(config_file, "r") as config_stream:
            config = json.load(config_stream)

        saved_order = config.get("button_order", [])
        button_order = [tool for tool in saved_order if tool in available_tools]
        button_order.extend(tool for tool in available_tools if tool not in button_order)

        current_color = config.get("button_color", default_button_color)
        button_colors = config.get("button_colors", {})
        stored_categories = config.get("categories", [])
        categories = []
        for category in stored_categories if isinstance(stored_categories, list) else []:
            category = str(category).strip()
            if category and category != UNCATEGORIZED and category not in categories:
                categories.append(category)

        stored_assignments = config.get("button_categories", {})
        assignments = {
            tool: category for tool, category in stored_assignments.items()
            if tool in available_tools and category in categories
        } if isinstance(stored_assignments, dict) else {}

        stored_help = config.get("tool_help", {})
        tool_help = {
            tool: {
                "description": str(help_data.get("description", "")),
                "links": [str(link) for link in help_data.get("links", []) if str(link).strip()],
            }
            for tool, help_data in stored_help.items()
            if tool in available_tools and isinstance(help_data, dict)
        } if isinstance(stored_help, dict) else {}

        return button_order, current_color, button_colors, categories, assignments, tool_help
    except Exception:
        return defaults


def save_config(config_file, button_order, current_button_color, button_colors,
                categories, button_categories, tool_help):
    """Write all collection customizations in one readable JSON file."""
    config = {
        "button_order": button_order,
        "button_color": current_button_color,
        "button_colors": button_colors,
        "categories": categories,
        "button_categories": button_categories,
        "tool_help": tool_help,
    }
    with open(config_file, "w") as config_stream:
        json.dump(config, config_stream, indent=4)
