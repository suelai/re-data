import json
import logging
from datetime import datetime

import requests


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


def truncate_table_name(table_name: str, max_length: int = 150) -> str:
    """
    Truncate table name if it's too long for Slack display.
    Only truncates when necessary, prioritizing client name and meaningful table suffix.
    """

    if len(table_name) <= max_length:
        return table_name

    parts = table_name.split(".")
    if len(parts) >= 3:
        project = parts[0]
        schema = parts[1]
        table = parts[2]

        if len(table_name) < 200:
            schema_table = f"{schema}.{table}"
            if len(schema_table) <= max_length:
                return schema_table

        client_name = None
        if "anomaly_tracking_" in schema:
            client_name = schema.split("anomaly_tracking_")[-1]
        elif "_" in schema:
            schema_parts = schema.split("_")
            for part in reversed(schema_parts):
                if part not in [
                    "data",
                    "quality",
                    "europe",
                    "west2",
                    "west4",
                    "central1",
                    "west3",
                    "us",
                    "east1",
                    "west1",
                    "anomaly",
                    "tracking",
                    "re",
                ]:
                    client_name = part
                    break

        meaningful_table = table
        if table.startswith("acquisition_tracking_"):
            meaningful_table = table[21:]
        elif table.startswith("acquisition_"):
            meaningful_table = table[12:]

        unnecessary_keywords = ["simple_"]
        for keyword in unnecessary_keywords:
            if meaningful_table.startswith(keyword):
                meaningful_table = meaningful_table[len(keyword) :]
                break

        if client_name and meaningful_table:
            display_name = f"{client_name}.{meaningful_table}"
            if len(display_name) <= max_length:
                return display_name

            available_space = max_length - 6  # Reserve space for "..." + "..."
            client_space = min(len(client_name), available_space // 2)
            table_end_space = available_space - client_space

            table_parts = meaningful_table.split("_")
            table_end = "_".join(table_parts[-2:]) if len(table_parts) > 1 else meaningful_table
            if len(table_end) > table_end_space:
                table_end = table_end[-table_end_space:]

            return f"...{client_name[:client_space]}...{table_end}"

        if client_name:
            display_name = f"{client_name}.{table}"
            if len(display_name) <= max_length:
                return display_name

            available_space = max_length - 6  # Reserve space for "..." + "..."
            client_space = min(len(client_name), available_space // 2)
            table_end_space = available_space - client_space

            table_parts = table.split("_")
            table_end = "_".join(table_parts[-2:]) if len(table_parts) > 1 else table
            if len(table_end) > table_end_space:
                table_end = table_end[-table_end_space:]

            return f"...{client_name[:client_space]}...{table_end}"

        schema_table = f"{schema}.{table}"
        if len(schema_table) <= max_length:
            return schema_table

        if len(table) <= max_length:
            return table

        return table[: max_length - 3] + "..."

    # For 2-part names (project.table), try to extract client from project
    elif len(parts) == 2:
        project = parts[0]
        table = parts[1]

        if project.startswith("churney-") and len(project) > 8:
            client_name = project[8:]
            display_name = f"{client_name}.{table}"
            if len(display_name) <= max_length:
                return display_name

        if len(table_name) <= max_length:
            return table_name

    return table_name[: max_length - 3] + "..."


def generate_slack_message(model, details, owners, subtitle: str, selected_alert_types: set) -> dict:
    """
    Generates a slack message for a given model.
    """
    anomalies = details["anomalies"]
    schema_changes = details["schema_changes"]
    tests = details["tests"]
    slack_owners = [k[0] for k in owners]

    truncated_model = truncate_table_name(model)

    header_model = truncate_table_name(model, max_length=100)

    message_obj = {
        "text": "re_data alerts for table: {}".format(truncated_model),  # Fallback text required by Slack
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "🔍 {}".format(header_model), "emoji": True},
            },
            {"type": "divider"},
            {"type": "section", "text": {"type": "mrkdwn", "text": "Owners: {}".format(", ".join(slack_owners))}},
            {"type": "divider"},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": ":warning: *{}* anomalies  |  :bulb: *{}* schema changes  |  :bangbang: *{}* failed tests".format(
                        len(anomalies), len(schema_changes), len(tests)
                    ),
                },
            },
        ],
    }

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
        "text": "All Good! re_data didn't find any alerts at the moment",  # Fallback text required by Slack
        "blocks": [
            {"type": "header", "text": {"type": "plain_text", "text": "All Good! :tada:", "emoji": True}},
            {"type": "divider"},
            {
                "type": "section",
                "text": {"type": "plain_text", "text": "re_data didn't find any alerts at the moment", "emoji": True},
            },
        ],
    }

    add_footer(message_obj, subtitle)

    return message_obj
