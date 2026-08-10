"""
Per-table field mappers, extracting only {prompt, response} and discarding
everything else immediately (see servicenow_Design.md §5 and §7 — data
minimization is a hard requirement, no intermediate full-row object is ever
persisted or logged).

sys_llm_log's field names are still an unconfirmed guess (no Now Assist
license was available to test against) — run discover_table_schema() before
trusting it, per servicenow_Design.md §5/§8. sys_cs_message's mapper below
IS confirmed against a real Virtual Agent conversation on a live instance.
"""

import json

TABLE_API_PATH = "/api/now/table/"


def _get(row: dict, *candidate_fields: str) -> str:
    for field in candidate_fields:
        val = row.get(field)
        if isinstance(val, dict):
            val = val.get("display_value") or val.get("value")
        if val:
            return str(val)
    return ""


def map_now_assist_row(row: dict) -> dict | None:
    prompt = _get(row, "prompt", "input", "user_input")
    response = _get(row, "response", "output", "generated_text")
    if not prompt and not response:
        return None
    return {"prompt": prompt, "response": response}


def map_virtual_agent_row(row: dict) -> dict | None:
    # sys_cs_message: confirmed against a real Virtual Agent conversation.
    # `payload` is JSON-encoded UI-widget data, not plain text, and
    # `is_bot_message` reads false on every row including the bot's own
    # replies -- `direction` is the real inbound(user)/outbound(bot) signal.
    #
    # Each row is only half a turn (either what the user typed OR what the
    # bot said back), not a combined prompt+response pair the way
    # u_ai_prompt_log's rows are -- sys_cs_message has no single field that
    # holds both. So this emits prompt-only and response-only records rather
    # than pairing them into one turn; pairing consecutive inbound/outbound
    # rows within the same conversation would need a real second pass over
    # the table, which isn't implemented here.
    try:
        payload = json.loads(row.get("payload") or "{}")
    except (ValueError, TypeError):
        return None

    direction = row.get("direction", "")
    if direction == "inbound":
        text = payload.get("searchText") or ""
        # An inbound row with no searchText is UI scaffolding (e.g. a
        # "Show me everything" topic-picker click), not something the user
        # actually typed -- skip it rather than log an empty prompt.
        if not text:
            return None
        return {"prompt": text, "response": ""}

    if direction == "outbound" and payload.get("uiType") == "OutputText":
        text = payload.get("plainTextMessage") or payload.get("value") or ""
        if not text:
            return None
        return {"prompt": "", "response": text}

    # Other uiTypes (TopicPickerControl prompts, ContextualAction menus,
    # etc.) are widget chrome, not conversational content.
    return None


def map_custom_ai_prompt_log_row(row: dict) -> dict | None:
    # u_ai_prompt_log: a plain custom table (u_prompt/u_response fields) set
    # up on a trial dev instance for end-to-end testing, since a blank
    # instance has neither Now Assist nor CSM/Virtual Agent activated and
    # therefore no sys_llm_log/cs_conversation to point at.
    prompt = _get(row, "u_prompt")
    response = _get(row, "u_response")
    if not prompt and not response:
        return None
    return {"prompt": prompt, "response": response}


MAPPERS = {
    "sys_llm_log": map_now_assist_row,
    "sys_cs_message": map_virtual_agent_row,
    "u_ai_prompt_log": map_custom_ai_prompt_log_row,
}

SUBTYPE_FOR_TABLE = {
    "sys_llm_log": "now_assist",
    "sys_cs_message": "virtual_agent",
    "u_ai_prompt_log": "custom_agent",
}

# Tables an admin can pick in the Connections UI, and what to label a
# custom-agent table added outside this fixed list.
KNOWN_TABLE_LABELS = {
    "sys_llm_log": "Now Assist (sys_llm_log)",
    "sys_cs_message": "Virtual Agent (sys_cs_message)",
    "u_ai_prompt_log": "Custom test table (u_ai_prompt_log)",
}


def discover_table_schema(connection: dict, table: str) -> dict:
    """Schema-discovery probe (design §5/§8): confirms a table exists and is
    readable before enabling it, and returns one sample row's field names so
    a mapper's guessed field names can be checked against reality."""
    from servicenow.http import get as sn_get

    resp = sn_get(connection, f"{TABLE_API_PATH}{table}", {"sysparm_limit": 1})
    if resp.status_code == 404:
        return {"exists": False, "fields": []}
    resp.raise_for_status()
    rows = resp.json().get("result", [])
    return {"exists": True, "fields": sorted(rows[0].keys()) if rows else []}
