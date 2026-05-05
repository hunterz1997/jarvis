"""
Tool router for small/local LLMs — filters the full 55-tool registry down
to only the relevant tools for a given user message.

Small models (≤3B params) can't handle 55 tools in context (that's ~18k tokens
just for the schemas). By sending only 5-10 relevant tools we cut context by 90%,
making responses ~10x faster.
"""

from typing import Any


# ── Keyword → tool name mapping ───────────────────────────────────────────────

_ROUTE_MAP: list[tuple[set[str], set[str]]] = [
    # Computer / files
    ({"file", "folder", "read", "write", "document", "open", "desktop", "downloads", "drive c", "drive d"},
     {"read_file", "write_file", "list_directory", "search_files", "file_operation"}),

    ({"run", "execute", "command", "cmd", "powershell", "terminal", "script", "batch"},
     {"run_command"}),

    ({"python", "code", "calculate", "compute", "analyze data", "pandas", "numpy", "excel formula"},
     {"run_python", "read_file", "write_file"}),

    ({"launch", "start", "open app", "open chrome", "open excel", "open vscode"},
     {"launch_application"}),

    ({"screenshot", "screen", "what's on", "see my screen"},
     {"take_screenshot"}),

    ({"ram", "cpu", "memory", "disk", "storage", "system", "process", "performance", "speed", "uptime", "network"},
     {"system_info"}),

    ({"clipboard", "copy", "paste"},
     {"clipboard"}),

    # Web / search
    ({"search", "google", "news", "latest", "current", "today", "weather", "price", "stock", "rate", "who is",
      "what is", "when did", "how to", "best", "top", "find out", "look up"},
     {"web_search", "fetch_url"}),

    ({"url", "website", "webpage", "article", "blog", "read page", "visit", "fetch"},
     {"fetch_url"}),

    # YouTube
    ({"youtube", "video", "watch", "channel", "transcript", "subtitle", "views", "subscribe"},
     {"youtube_search", "youtube_get_video_info", "youtube_get_transcript",
      "youtube_get_channel_info", "youtube_analyze_video", "youtube_research_topic",
      "youtube_get_comments"}),

    ({"my video", "my channel", "upload", "channel analytics", "video analytics", "my youtube"},
     {"youtube_list_my_videos", "youtube_get_channel_analytics", "youtube_get_video_analytics",
      "youtube_update_video", "youtube_post_comment"}),

    # LinkedIn — own account
    ({"linkedin", "post", "publish", "network", "connection", "profile pic", "my profile"},
     {"linkedin_get_profile", "linkedin_get_dashboard", "linkedin_list_posts",
      "linkedin_create_post", "linkedin_get_all_post_analytics", "linkedin_get_network_size",
      "linkedin_search_people", "linkedin_search_content", "linkedin_scrape_profile",
      "linkedin_get_comments", "linkedin_comment_on_post"}),

    ({"linkedin analytics", "post analytics", "engagement", "impressions", "reach"},
     {"linkedin_get_all_post_analytics", "linkedin_get_post_analytics", "linkedin_list_posts"}),

    # LinkedIn — searching for OTHER people / their posts / content
    ({"someone's", "his linkedin", "her linkedin", "their linkedin", "his post", "her post",
      "their post", "his profile", "her profile", "find on linkedin", "search linkedin",
      "linkedin posts about", "people on linkedin", "trending on linkedin", "what's", "latest",
      "show me", "what did", "recent posts of", "posts by", "from his", "from her"},
     {"linkedin_search_people", "linkedin_search_content", "linkedin_scrape_profile",
      "linkedin_scrape_my_posts", "linkedin_get_comments"}),

    # Zomato / food
    ({"food", "eat", "hungry", "order", "restaurant", "biryani", "pizza", "dinner", "lunch",
      "breakfast", "zomato", "swiggy", "delivery", "menu"},
     {"zomato_search_restaurants", "zomato_get_menu_listing", "zomato_get_menu_by_category",
      "zomato_get_addresses", "zomato_create_cart", "zomato_add_to_cart",
      "zomato_view_cart", "zomato_checkout"}),

    ({"order history", "track", "tracking", "where is my order", "delivery status", "reorder"},
     {"zomato_order_history", "zomato_track_order", "zomato_reorder"}),

    # WhatsApp
    ({"whatsapp", "message", "chat", "send message", "text"},
     {"whatsapp_list_chats", "whatsapp_read_messages", "whatsapp_send_message"}),

    # Scheduler / reminders
    ({"schedule", "every hour", "every day", "every minute", "recurring", "remind me", "automatically",
      "keep me updated", "notify me", "run every", "set up alert", "reminder", "remind",
      "at 4pm", "at 9am", "tomorrow morning", "next friday"},
     {"schedule_task", "list_schedules", "cancel_schedule"}),

    ({"scheduled task", "my schedules", "what's scheduled", "cancel schedule"},
     {"list_schedules", "cancel_schedule"}),

    # Google Calendar
    ({"calendar", "meeting", "event", "schedule meeting", "google meet", "meet link",
      "create event", "book a slot", "appointment", "upcoming events", "my calendar"},
     {"calendar_create_event", "calendar_list_events", "calendar_get_event",
      "calendar_update_event", "calendar_delete_event"}),

    # Gmail
    ({"email", "gmail", "mail", "inbox", "send email", "reply", "draft",
      "unread", "read my mail", "check mail", "write an email"},
     {"gmail_send_email", "gmail_read_inbox", "gmail_search_emails",
      "gmail_create_draft", "gmail_reply_email"}),

    # Memory
    ({"remember", "recall", "save this", "store", "preference", "forget", "memorize"},
     {"remember", "recall"}),
]

# Tools always included regardless of query (tiny schemas, near-zero cost)
_ALWAYS_INCLUDE = {"web_search", "remember", "recall"}

# Absolute cap — never send more than this many tools to a small model
_MAX_TOOLS = 12


def filter_tools(all_tools: list[dict[str, Any]], user_message: str) -> list[dict[str, Any]]:
    """
    Return a filtered subset of tools relevant to the user message.
    Keeps context under ~3000 tokens so small models respond quickly.
    """
    msg = user_message.lower()
    relevant_names: set[str] = set(_ALWAYS_INCLUDE)

    for keywords, tools in _ROUTE_MAP:
        if any(kw in msg for kw in keywords):
            relevant_names |= tools

    # Fallback: if only the always-include set matched, add system_info + fetch_url
    if relevant_names == _ALWAYS_INCLUDE:
        relevant_names |= {"system_info", "fetch_url"}

    # Build filtered list preserving original order, capped at _MAX_TOOLS
    filtered = [t for t in all_tools if t["name"] in relevant_names]
    return filtered[:_MAX_TOOLS]
