import json
import logging
from datetime import datetime
from re import sub
from typing import Any, Dict, List, Optional, Tuple

import requests
from tabulate import tabulate


def slack_notify(webhook_url: str, body: dict) -> None:
    """
    Send a message to a slack webhook
    :param webhook_url: str
        Slack incoming webhook url e.g https://hooks.slack.com/services/T0JKJQKQS/B0JKJQKQS/XXXXXXXXXXXXXXXXXXXXXXXXXXXX
    :param body: dict
        Slack message payload
    :return: None
    """
    headers = {"Content-Type": "application/json"}

    # Validate message size before sending
    message_size = len(json.dumps(body))
    if message_size > 50000:  # Slack's limit is ~50KB for webhook messages
        logging.warning(f"Message size ({message_size} bytes) exceeds Slack limits. Truncating...")
        body = truncate_slack_message(body)

    response = requests.post(webhook_url, json=body, headers=headers)
    response.raise_for_status()


def truncate_slack_message(message_obj: dict) -> dict:
    """
    Truncate a Slack message to fit within size limits.
    """
    # Deep copy to avoid modifying original
    truncated = json.loads(json.dumps(message_obj))

    # Remove blocks that might be too large
    if "blocks" in truncated:
        # Keep only essential blocks (header, basic info)
        essential_blocks = []
        for block in truncated["blocks"]:
            if block.get("type") in ["header", "divider"]:
                essential_blocks.append(block)
            elif block.get("type") == "section":
                # Truncate section text if it's too long
                if "text" in block and "text" in block["text"]:
                    text = block["text"]["text"]
                    if len(text) > 2000:
                        block["text"]["text"] = text[:2000] + "... (truncated)"
                essential_blocks.append(block)
                # Only keep first few sections
                if len(essential_blocks) >= 5:
                    break

        truncated["blocks"] = essential_blocks

    return truncated


def format_alerts(alerts: list, limit=None) -> str:
    """
    Formats a list of alerts to a table.
    :param alerts:
        List of alerts exported from dbt-re-data.
    :return: str
        Formatted table.
    """
    table = []
    for alert in alerts:
        time = datetime.strptime(alert.get("time_window_end"), "%Y-%m-%d %H:%M:%S")
        time_formatted = time.strftime("%Y-%m-%d %H:%M")

        table.append(
            f"[{time_formatted}] {alert['message']}",
        )
    if limit:
        table = table[:limit]

    return "\n".join(table)


def add_footer(message_obj, subtitle):
    """
    Adds a footer to a slack message.
    """
    if subtitle:
        message_obj["blocks"].append({"type": "section", "text": {"type": "mrkdwn", "text": subtitle}})
    message_obj["blocks"].append(
        {
            "type": "context",
            "elements": [
                {
                    "type": "plain_text",
                    "text": "Generated at {}. Check re_data UI for more details".format(
                        datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    ),
                    "emoji": True,
                }
            ],
        }
    )

    return message_obj


def truncate_table_name(table_name: str, max_length: int = 200) -> str:
    """
    Truncate table name if it's too long for Slack display.
    """
    if len(table_name) <= max_length:
        return table_name

    # Try to keep the most important parts (project.dataset.table)
    parts = table_name.split(".")
    if len(parts) >= 3:
        # Keep project, truncate dataset, keep table name
        project = parts[0]
        table = parts[-1]
        dataset = ".".join(parts[1:-1])

        # Calculate available space
        available = max_length - len(project) - len(table) - 2  # 2 for dots
        if available > 10:  # Ensure we have some space for dataset
            truncated_dataset = dataset[:available] + "..."
            return f"{project}.{truncated_dataset}.{table}"

    # Fallback: just truncate the whole thing
    return table_name[: max_length - 3] + "..."


def generate_slack_message(model, details, owners, subtitle: str, selected_alert_types: set) -> dict:
    """
    Generates a slack message for a given model.
    """
    anomalies = details["anomalies"]
    schema_changes = details["schema_changes"]
    tests = details["tests"]
    slack_owners = [k[0] for k in owners]

    # Truncate table name if too long
    truncated_model = truncate_table_name(model)

    message_obj = {
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "Table: {}".format(truncated_model), "emoji": True},
            },
            {"type": "divider"},
            {"type": "section", "text": {"type": "mrkdwn", "text": "Owners: {}".format(", ".join(slack_owners))}},
            {"type": "divider"},
            {
                "type": "section",
                "fields": [
                    {"type": "plain_text", "text": ":warning: {} anomalies".format(len(anomalies)), "emoji": True},
                    {
                        "type": "plain_text",
                        "text": ":bulb: {} schema changes".format(len(schema_changes)),
                        "emoji": True,
                    },
                    {"type": "plain_text", "text": ":bangbang: {} failed tests".format(len(tests)), "emoji": True},
                ],
            },
        ]
    }

    # Add alert sections with size limits
    if anomalies and "anomaly" in selected_alert_types:
        alert_text = format_alerts(anomalies, limit=10)
        if len(alert_text) > 2000:
            alert_text = alert_text[:2000] + "... (truncated)"
        message_obj["blocks"].append(
            {"type": "section", "text": {"type": "mrkdwn", "text": "*Anomalies*\n ```{}```".format(alert_text)}}
        )

    if schema_changes and "schema_change" in selected_alert_types:
        alert_text = format_alerts(schema_changes, limit=10)
        if len(alert_text) > 2000:
            alert_text = alert_text[:2000] + "... (truncated)"
        message_obj["blocks"].append(
            {"type": "section", "text": {"type": "mrkdwn", "text": "*Schema Changes*\n ```{}```".format(alert_text)}}
        )

    if tests and "test" in selected_alert_types:
        alert_text = format_alerts(tests, limit=10)
        if len(alert_text) > 2000:
            alert_text = alert_text[:2000] + "... (truncated)"
        message_obj["blocks"].append(
            {"type": "section", "text": {"type": "mrkdwn", "text": "*Tests failures*\n ```{}```".format(alert_text)}}
        )

    add_footer(message_obj, subtitle)

    return message_obj


def generate_all_good_slack_message(subtitle: str) -> dict:
    """
    Generates a slack message for a given model.
    """
    message_obj = {
        "blocks": [
            {"type": "header", "text": {"type": "plain_text", "text": "All Good! :tada:", "emoji": True}},
            {"type": "divider"},
            {
                "type": "section",
                "text": {"type": "plain_text", "text": "re_data didn't find any alerts at the moment", "emoji": True},
            },
        ]
    }

    add_footer(message_obj, subtitle)

    return message_obj
