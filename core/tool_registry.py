"""All Jarvis tools registered in Anthropic tool-use format.

Tools:
  Web         : web_search, fetch_url
  Computer    : read_file, write_file, list_directory, search_files,
                file_operation, run_command, launch_application,
                take_screenshot, system_info, clipboard, run_python
  WhatsApp    : whatsapp_list_chats, whatsapp_read_messages, whatsapp_send_message
  YouTube     : 15 tools (public + channel OAuth)
  LinkedIn    : 11 tools (profile, posts, analytics, scraping)
  Zomato      : 9 tools (search, menu, order, track, history)
  Memory      : remember, recall
"""

from typing import Any


TOOLS: list[dict[str, Any]] = [

    # ─────────────────────────────────────────────────────────────────────────
    # WEB
    # ─────────────────────────────────────────────────────────────────────────
    {
        "name": "web_search",
        "description": (
            "Search the web in real-time for any topic: news, prices, weather, events, "
            "companies, people, sports scores, research papers, anything current. "
            "Returns titles, URLs, and snippets."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "max_results": {"type": "integer", "description": "Number of results (1–20)", "default": 5},
            },
            "required": ["query"],
        },
    },
    {
        "name": "fetch_url",
        "description": (
            "Fetch and read the full content of any URL or webpage. "
            "Use for reading articles, documentation, reports, public data."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to fetch"},
                "extract_text_only": {"type": "boolean", "description": "Return clean text (no HTML)", "default": True},
            },
            "required": ["url"],
        },
    },

    # ─────────────────────────────────────────────────────────────────────────
    # COMPUTER — FILES & SYSTEM
    # ─────────────────────────────────────────────────────────────────────────
    {
        "name": "read_file",
        "description": (
            "Read any file on the computer. Supports PDF, DOCX, XLSX, PPTX, CSV, JSON, "
            "XML, TXT, images (OCR), and all text-based files. Works across all drives."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Full file path, e.g. C:\\Users\\premj\\Documents\\report.pdf"},
                "sheet_name": {"type": "string", "description": "For Excel: specific sheet name (optional)"},
                "page_range": {"type": "string", "description": "For PDFs: e.g. '1-5' (optional)"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write or create a file. Creates parent directories if needed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Full file path to write"},
                "content": {"type": "string", "description": "Content to write"},
                "mode": {"type": "string", "description": "'overwrite' (default) or 'append'", "default": "overwrite"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "list_directory",
        "description": "List files and folders in a directory with sizes and dates.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path"},
                "recursive": {"type": "boolean", "description": "Include subdirectories", "default": False},
                "pattern": {"type": "string", "description": "Glob filter, e.g. '*.pdf'"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "search_files",
        "description": "Search for files by name, extension, date, or content keyword across drives.",
        "input_schema": {
            "type": "object",
            "properties": {
                "search_path": {"type": "string", "description": "Root path to search from", "default": "C:\\"},
                "name_pattern": {"type": "string", "description": "Filename pattern, e.g. '*.xlsx'"},
                "content_keyword": {"type": "string", "description": "Search inside file contents"},
                "modified_after": {"type": "string", "description": "ISO date, e.g. '2024-01-01'"},
                "max_results": {"type": "integer", "default": 50},
            },
        },
    },
    {
        "name": "file_operation",
        "description": "File system operations: copy, move, rename, delete, mkdir, zip, unzip.",
        "input_schema": {
            "type": "object",
            "properties": {
                "operation": {"type": "string", "description": "copy | move | rename | delete | mkdir | zip | unzip"},
                "source": {"type": "string", "description": "Source path"},
                "destination": {"type": "string", "description": "Destination path"},
            },
            "required": ["operation", "source"],
        },
    },
    {
        "name": "run_command",
        "description": (
            "Run a shell command in PowerShell or CMD. "
            "Always show the command to the user and confirm before running anything destructive."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The command to run"},
                "shell": {"type": "string", "description": "'powershell' or 'cmd'", "default": "powershell"},
                "working_dir": {"type": "string", "description": "Working directory (optional)"},
                "timeout": {"type": "integer", "description": "Timeout seconds", "default": 30},
            },
            "required": ["command"],
        },
    },
    {
        "name": "launch_application",
        "description": "Launch an installed application by name (Excel, Chrome, VS Code, etc.).",
        "input_schema": {
            "type": "object",
            "properties": {
                "app_name": {"type": "string", "description": "Application name or executable"},
                "args": {"type": "string", "description": "Arguments or file path to open"},
            },
            "required": ["app_name"],
        },
    },
    {
        "name": "take_screenshot",
        "description": "Take a screenshot of the current screen and describe what's visible.",
        "input_schema": {
            "type": "object",
            "properties": {
                "save_path": {"type": "string", "description": "Optional path to save the image"},
            },
        },
    },
    {
        "name": "system_info",
        "description": "Get system diagnostics: RAM, CPU, disk space, processes, network, uptime.",
        "input_schema": {
            "type": "object",
            "properties": {
                "info_type": {
                    "type": "string",
                    "description": "all | ram | cpu | disk | processes | network | uptime",
                    "default": "all",
                },
            },
        },
    },
    {
        "name": "clipboard",
        "description": "Read from or write to the Windows clipboard.",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "'read' or 'write'"},
                "text": {"type": "string", "description": "Text to write (only for 'write')"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "run_python",
        "description": (
            "Write and execute a Python script. Perfect for data analysis, "
            "file processing, automation, calculations, and generating reports."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python code to execute"},
                "save_as": {"type": "string", "description": "Optional: save script to this path"},
                "timeout": {"type": "integer", "description": "Timeout seconds", "default": 60},
            },
            "required": ["code"],
        },
    },

    # ─────────────────────────────────────────────────────────────────────────
    # WHATSAPP
    # ─────────────────────────────────────────────────────────────────────────
    {
        "name": "whatsapp_list_chats",
        "description": "List recent WhatsApp chats with contact names and last message preview.",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Number of chats", "default": 20},
            },
        },
    },
    {
        "name": "whatsapp_read_messages",
        "description": "Read messages from a WhatsApp chat by contact name or phone number.",
        "input_schema": {
            "type": "object",
            "properties": {
                "contact": {"type": "string", "description": "Contact name or phone number"},
                "limit": {"type": "integer", "description": "Number of messages", "default": 20},
            },
            "required": ["contact"],
        },
    },
    {
        "name": "whatsapp_send_message",
        "description": (
            "Send a WhatsApp message. STRICT 3-phase flow you MUST follow:\n"
            "  1) Call with confirmed=false → returns a draft preview. Show the user "
            "the message + recipient, ASK 'Should I send this?'. Wait for explicit yes.\n"
            "  2) Call again with confirmed=true. If the contact name is ambiguous, "
            "the tool returns needs_disambiguation=true with a candidates list. Show "
            "the user (each item has chat_id, name, isGroup) and ask which one is "
            "correct.\n"
            "  3) Call again with chat_id=<chosen> and confirmed=true. If the chat "
            "is a group, the tool will refuse unless you also pass allow_group=true "
            "— ALWAYS confirm with the user before sending to a group.\n"
            "Never send without an explicit user yes. Never assume which chat is "
            "correct when multiple match — always disambiguate."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "contact": {
                    "type": "string",
                    "description": "Contact name or phone number (used for initial lookup).",
                },
                "message": {"type": "string", "description": "Exact message text to send."},
                "confirmed": {
                    "type": "boolean",
                    "description": "Set true ONLY after user has explicitly approved the draft.",
                    "default": False,
                },
                "chat_id": {
                    "type": "string",
                    "description": (
                        "Specific WhatsApp chat ID (e.g. '91xxxxxxxxxx@c.us' or "
                        "'<id>@g.us' for groups). Pass this after the user has picked "
                        "from a disambiguation list."
                    ),
                },
                "allow_group": {
                    "type": "boolean",
                    "description": (
                        "Required to send to a group chat (chat_id ends in '@g.us'). "
                        "Set true only after the user has explicitly confirmed they "
                        "want to message the group, not an individual."
                    ),
                    "default": False,
                },
            },
            "required": ["contact", "message"],
        },
    },

    # ─────────────────────────────────────────────────────────────────────────
    # YOUTUBE — Public tools (API key)
    # ─────────────────────────────────────────────────────────────────────────
    {
        "name": "youtube_get_video_info",
        "description": "Get metadata, stats, description and tags for any YouTube video.",
        "input_schema": {
            "type": "object",
            "properties": {
                "video_url_or_id": {"type": "string", "description": "YouTube URL or 11-char video ID"},
                "response_format": {"type": "string", "default": "markdown"},
            },
            "required": ["video_url_or_id"],
        },
    },
    {
        "name": "youtube_get_transcript",
        "description": "Get the full transcript/subtitles of a YouTube video.",
        "input_schema": {
            "type": "object",
            "properties": {
                "video_url_or_id": {"type": "string", "description": "YouTube URL or video ID"},
                "language": {"type": "string", "description": "Language code: en, hi, es…", "default": "en"},
                "include_timestamps": {"type": "boolean", "default": False},
                "response_format": {"type": "string", "default": "markdown"},
            },
            "required": ["video_url_or_id"],
        },
    },
    {
        "name": "youtube_get_comments",
        "description": "Get top comments for a YouTube video.",
        "input_schema": {
            "type": "object",
            "properties": {
                "video_url_or_id": {"type": "string", "description": "YouTube URL or video ID"},
                "max_results": {"type": "integer", "description": "Number of comments (1–100)", "default": 50},
                "order": {"type": "string", "description": "relevance | time", "default": "relevance"},
                "response_format": {"type": "string", "default": "markdown"},
            },
            "required": ["video_url_or_id"],
        },
    },
    {
        "name": "youtube_search",
        "description": "Search YouTube for videos. Returns titles, channels, views, duration, URLs.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "max_results": {"type": "integer", "description": "Results to return (1–50)", "default": 10},
                "order": {"type": "string", "description": "relevance | date | viewCount | rating", "default": "relevance"},
                "video_duration": {"type": "string", "description": "any | short | medium | long", "default": "any"},
                "response_format": {"type": "string", "default": "markdown"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "youtube_get_channel_info",
        "description": "Get stats and info for any YouTube channel by @handle, ID, or URL.",
        "input_schema": {
            "type": "object",
            "properties": {
                "channel_url_or_id": {"type": "string", "description": "@handle, channel ID, or full URL"},
                "response_format": {"type": "string", "default": "markdown"},
            },
            "required": ["channel_url_or_id"],
        },
    },
    {
        "name": "youtube_analyze_video",
        "description": (
            "Deep analysis of a YouTube video: transcript, key concepts, "
            "steps, code, sentiment, top comments. Great for learning from videos."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "video_url_or_id": {"type": "string", "description": "YouTube URL or video ID"},
                "focus": {"type": "string", "description": "steps | code | concepts | summary | general", "default": "general"},
                "include_comments": {"type": "boolean", "default": True},
                "response_format": {"type": "string", "default": "markdown"},
            },
            "required": ["video_url_or_id"],
        },
    },
    {
        "name": "youtube_research_topic",
        "description": "Research a topic across multiple YouTube videos — aggregates insights from several videos.",
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "Topic to research"},
                "num_videos": {"type": "integer", "description": "Videos to analyze (1–10)", "default": 5},
                "include_comments": {"type": "boolean", "default": True},
                "response_format": {"type": "string", "default": "markdown"},
            },
            "required": ["topic"],
        },
    },

    # ─────────────────────────────────────────────────────────────────────────
    # YOUTUBE — Channel management (OAuth)
    # ─────────────────────────────────────────────────────────────────────────
    {
        "name": "youtube_list_my_videos",
        "description": "List videos on your YouTube channel with views, likes, and privacy status.",
        "input_schema": {
            "type": "object",
            "properties": {
                "max_results": {"type": "integer", "default": 20},
                "response_format": {"type": "string", "default": "markdown"},
            },
        },
    },
    {
        "name": "youtube_get_channel_analytics",
        "description": "Get your channel analytics: views, watch time, subscribers, likes by day/month.",
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "YYYY-MM-DD (default: 30 days ago)"},
                "end_date": {"type": "string", "description": "YYYY-MM-DD (default: today)"},
                "dimension": {"type": "string", "description": "day | month", "default": "day"},
                "response_format": {"type": "string", "default": "markdown"},
            },
        },
    },
    {
        "name": "youtube_get_video_analytics",
        "description": "Get detailed analytics for a specific video: views, watch time, traffic sources.",
        "input_schema": {
            "type": "object",
            "properties": {
                "video_url_or_id": {"type": "string", "description": "YouTube URL or video ID"},
                "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "YYYY-MM-DD"},
                "response_format": {"type": "string", "default": "markdown"},
            },
            "required": ["video_url_or_id"],
        },
    },
    {
        "name": "youtube_post_comment",
        "description": "Post a top-level comment on a YouTube video.",
        "input_schema": {
            "type": "object",
            "properties": {
                "video_url_or_id": {"type": "string", "description": "YouTube URL or video ID"},
                "comment_text": {"type": "string", "description": "Comment to post"},
            },
            "required": ["video_url_or_id", "comment_text"],
        },
    },
    {
        "name": "youtube_reply_to_comment",
        "description": "Reply to an existing YouTube comment by parent comment ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "parent_comment_id": {"type": "string", "description": "Parent comment ID (starts with Ugw…)"},
                "reply_text": {"type": "string", "description": "Reply text"},
            },
            "required": ["parent_comment_id", "reply_text"],
        },
    },
    {
        "name": "youtube_update_video",
        "description": "Update title, description, tags, or privacy of one of your videos.",
        "input_schema": {
            "type": "object",
            "properties": {
                "video_url_or_id": {"type": "string", "description": "YouTube URL or video ID"},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "privacy_status": {"type": "string", "description": "private | unlisted | public"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["video_url_or_id"],
        },
    },
    {
        "name": "youtube_delete_video",
        "description": "Permanently delete one of your YouTube videos. This is IRREVERSIBLE.",
        "input_schema": {
            "type": "object",
            "properties": {
                "video_url_or_id": {"type": "string", "description": "YouTube URL or video ID"},
            },
            "required": ["video_url_or_id"],
        },
    },

    # ─────────────────────────────────────────────────────────────────────────
    # LINKEDIN
    # ─────────────────────────────────────────────────────────────────────────
    {
        "name": "linkedin_get_profile",
        "description": "Get your LinkedIn profile: name, email, URN, avatar.",
        "input_schema": {
            "type": "object",
            "properties": {
                "response_format": {"type": "string", "default": "markdown"},
            },
        },
    },
    {
        "name": "linkedin_get_dashboard",
        "description": "Full LinkedIn overview: profile summary, network size, and recent posts.",
        "input_schema": {
            "type": "object",
            "properties": {
                "post_count": {"type": "integer", "description": "Recent posts to include", "default": 5},
                "response_format": {"type": "string", "default": "markdown"},
            },
        },
    },
    {
        "name": "linkedin_list_posts",
        "description": "List your recent LinkedIn posts with engagement metrics.",
        "input_schema": {
            "type": "object",
            "properties": {
                "count": {"type": "integer", "description": "Number of posts to return", "default": 10},
                "response_format": {"type": "string", "default": "markdown"},
            },
        },
    },
    {
        "name": "linkedin_create_post",
        "description": (
            "Create and publish a LinkedIn post. "
            "REQUIRES explicit user confirmation. Always preview and confirm before posting."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Post content (max 3000 chars)"},
                "visibility": {"type": "string", "description": "PUBLIC | CONNECTIONS | LOGGED_IN", "default": "PUBLIC"},
                "confirmed": {"type": "boolean", "description": "Set true only after explicit user confirmation", "default": False},
            },
            "required": ["text"],
        },
    },
    {
        "name": "linkedin_delete_post",
        "description": "Delete one of your LinkedIn posts. REQUIRES explicit user confirmation.",
        "input_schema": {
            "type": "object",
            "properties": {
                "post_urn": {"type": "string", "description": "Post URN from list_posts"},
                "confirmed": {"type": "boolean", "description": "Set true only after explicit user confirmation", "default": False},
            },
            "required": ["post_urn"],
        },
    },
    {
        "name": "linkedin_get_post_analytics",
        "description": "Get engagement analytics for a specific LinkedIn post.",
        "input_schema": {
            "type": "object",
            "properties": {
                "post_urn": {"type": "string", "description": "Post URN"},
                "response_format": {"type": "string", "default": "markdown"},
            },
            "required": ["post_urn"],
        },
    },
    {
        "name": "linkedin_get_all_post_analytics",
        "description": "Get analytics for all recent LinkedIn posts in one call.",
        "input_schema": {
            "type": "object",
            "properties": {
                "count": {"type": "integer", "description": "Number of recent posts to analyze", "default": 5},
                "response_format": {"type": "string", "default": "markdown"},
            },
        },
    },
    {
        "name": "linkedin_get_network_size",
        "description": "Get your LinkedIn 1st-degree connection count.",
        "input_schema": {
            "type": "object",
            "properties": {
                "response_format": {"type": "string", "default": "markdown"},
            },
        },
    },
    {
        "name": "linkedin_scrape_my_posts",
        "description": "Get the recent posts from any LinkedIn profile, including other people's profiles. Pass `vanity` (the slug from their profile URL — e.g. for linkedin.com/in/vaibhavsisinty pass 'vaibhavsisinty'). Omit `vanity` for your own posts. Returns full post text, engagement metrics, and timestamps. THIS IS THE TOOL TO USE when someone asks 'show me X's latest posts' or 'what did X post'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "vanity": {"type": "string", "description": "LinkedIn profile slug (the part after /in/ in their URL). Omit for own posts. Examples: 'vaibhavsisinty', 'satyanadella', 'billgates'."},
                "count": {"type": "integer", "default": 10, "description": "Number of recent posts to return (1-30)."},
                "response_format": {"type": "string", "default": "markdown"},
            },
        },
    },
    {
        "name": "linkedin_scrape_profile",
        "description": "Get LinkedIn profile stats (connections, followers, headline, etc.) for ANY user. Pass `vanity` (the slug from their LinkedIn URL, e.g. for linkedin.com/in/vaibhavsisinty pass 'vaibhavsisinty'). Omit `vanity` for own profile.",
        "input_schema": {
            "type": "object",
            "properties": {
                "vanity": {"type": "string", "description": "LinkedIn profile slug (the part after /in/ in the URL). Omit for own profile."},
                "response_format": {"type": "string", "default": "markdown"},
            },
        },
    },
    {
        "name": "linkedin_update_cache",
        "description": "Refresh the LinkedIn local cache (profile + posts). Run this if data seems stale.",
        "input_schema": {
            "type": "object",
            "properties": {
                "response_format": {"type": "string", "default": "markdown"},
            },
        },
    },
    {
        "name": "linkedin_search_people",
        "description": "Search LinkedIn for people by keyword (name, title, company, location, or any combination). Returns name, headline, location, connection degree, and profile URL for each result. Use this to find specific people or potential connections.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search keywords e.g. 'Vaibhav Sisinty' or 'CFO India FMCG'"},
                "count": {"type": "integer", "default": 10, "description": "Results to return (1-25)"},
                "response_format": {"type": "string", "default": "markdown"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "linkedin_search_content",
        "description": "Search LinkedIn POSTS by topic/keyword. Returns posts MENTIONING the query (could be by anyone). Use this for topic research like 'AI in audit' or 'CFO trends'. DO NOT use this to fetch a specific person's own posts — for that use `linkedin_scrape_my_posts` with their `vanity` slug instead.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Topic or keywords to search for in post content"},
                "count": {"type": "integer", "default": 10, "description": "Posts to return (1-25)"},
                "response_format": {"type": "string", "default": "markdown"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "linkedin_get_comments",
        "description": "Fetch all comments on a specific LinkedIn post. Returns commenter name, headline, comment text, timestamp, and reaction count.",
        "input_schema": {
            "type": "object",
            "properties": {
                "post_urn": {"type": "string", "description": "Post URN starting with 'urn:li:' e.g. 'urn:li:activity:7454071968481468416'"},
                "count": {"type": "integer", "default": 30, "description": "Max comments (1-50)"},
                "response_format": {"type": "string", "default": "markdown"},
            },
            "required": ["post_urn"],
        },
    },
    {
        "name": "linkedin_comment_on_post",
        "description": "Post a comment on a LinkedIn post. Requires confirmation before posting.",
        "input_schema": {
            "type": "object",
            "properties": {
                "post_urn": {"type": "string", "description": "Target post URN starting with 'urn:li:'"},
                "text": {"type": "string", "description": "Comment text (max 1250 chars)"},
                "person_urn": {"type": "string", "description": "Author URN — auto-resolved if omitted"},
                "confirmed": {"type": "boolean", "description": "Set true after user confirms the comment text"},
            },
            "required": ["post_urn", "text"],
        },
    },

    # ─────────────────────────────────────────────────────────────────────────
    # ZOMATO  — 20 tools, proxied to local MCP server on 127.0.0.1:8765
    # ─────────────────────────────────────────────────────────────────────────

    # Auth
    {
        "name": "zomato_login_start",
        "description": (
            "Start Zomato OTP login. Tries silent token recovery first; "
            "set force_otp=true to always send a fresh OTP. "
            "Use this if Zomato returns 'authentication required'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "phone":      {"type": "string", "description": "Phone with country code, e.g. +919876543210"},
                "otp_pref":   {"type": "string", "description": "OTP delivery: 'sms', 'whatsapp', or 'call'", "default": "sms"},
                "force_otp":  {"type": "boolean", "description": "Set true to force a new OTP even if already logged in", "default": False},
            },
            "required": ["phone"],
        },
    },
    {
        "name": "zomato_login_verify",
        "description": "Verify the Zomato OTP sent by zomato_login_start. Stores the new token on success.",
        "input_schema": {
            "type": "object",
            "properties": {
                "otp": {"type": "string", "description": "6-digit OTP from SMS / WhatsApp"},
            },
            "required": ["otp"],
        },
    },
    {
        "name": "zomato_logout",
        "description": "Clear the stored Zomato access token from Windows Credential Manager.",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },

    # Discovery
    {
        "name": "zomato_search_restaurants",
        "description": (
            "Search Zomato for restaurants by cuisine, dish, or restaurant name. "
            "Returns a list with restaurant IDs, names, ratings, and cuisine types."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "e.g. 'biryani', 'pizza', \"McDonald's\""},
                "lat":   {"type": "number", "description": "Delivery latitude  (default: your saved location)"},
                "lon":   {"type": "number", "description": "Delivery longitude (default: your saved location)"},
                "limit": {"type": "integer", "description": "Max results (1–50)", "default": 20},
            },
            "required": ["query"],
        },
    },
    {
        "name": "zomato_get_menu_listing",
        "description": (
            "Get a quick flat listing of all menu items (name, price, item_id) for a restaurant. "
            "Use this first to discover items before adding to cart."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "restaurant_id": {"type": "string", "description": "Restaurant ID from zomato_search_restaurants"},
            },
            "required": ["restaurant_id"],
        },
    },
    {
        "name": "zomato_get_menu_by_category",
        "description": (
            "Get the full menu for a restaurant organized by category, including variants and add-ons. "
            "Pass a category name to filter, or omit for the complete menu."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "restaurant_id": {"type": "string", "description": "Restaurant ID from search results"},
                "category":      {"type": "string", "description": "Category name to filter (optional, e.g. 'Starters')"},
            },
            "required": ["restaurant_id"],
        },
    },

    # Addresses
    {
        "name": "zomato_get_addresses",
        "description": "Get all saved delivery addresses from the Zomato account.",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "zomato_add_address",
        "description": "Add a new delivery address to the Zomato account (uses browser automation).",
        "input_schema": {
            "type": "object",
            "properties": {
                "label":        {"type": "string", "description": "Label: 'Home', 'Work', etc."},
                "full_address": {"type": "string", "description": "Complete street address"},
                "lat":          {"type": "number", "description": "Latitude of the address"},
                "lon":          {"type": "number", "description": "Longitude of the address"},
            },
            "required": ["label", "full_address", "lat", "lon"],
        },
    },
    {
        "name": "zomato_edit_address",
        "description": "Edit an existing saved address. Only pass the fields you want to change.",
        "input_schema": {
            "type": "object",
            "properties": {
                "address_id":   {"type": "string", "description": "Address ID from zomato_get_addresses"},
                "label":        {"type": "string", "description": "New label"},
                "full_address": {"type": "string", "description": "New street address"},
                "lat":          {"type": "number", "description": "New latitude"},
                "lon":          {"type": "number", "description": "New longitude"},
            },
            "required": ["address_id"],
        },
    },
    {
        "name": "zomato_delete_address",
        "description": (
            "Delete a saved delivery address. "
            "REQUIRES explicit user confirmation — always confirm before calling."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "address_id": {"type": "string", "description": "Address ID from zomato_get_addresses"},
                "confirmed":  {"type": "boolean", "description": "Set true only after user explicitly confirms", "default": False},
            },
            "required": ["address_id"],
        },
    },

    # Cart
    {
        "name": "zomato_create_cart",
        "description": (
            "Create a new Zomato cart with initial items. "
            "Get item_id values from zomato_get_menu_listing first."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "restaurant_id": {"type": "string", "description": "Restaurant ID"},
                "items": {
                    "type": "array",
                    "description": "Items to add",
                    "items": {
                        "type": "object",
                        "properties": {
                            "item_id":  {"type": "string"},
                            "quantity": {"type": "integer", "default": 1},
                        },
                        "required": ["item_id"],
                    },
                },
                "address_id": {"type": "string", "description": "Delivery address ID from zomato_get_addresses (optional)"},
            },
            "required": ["restaurant_id", "items"],
        },
    },
    {
        "name": "zomato_add_to_cart",
        "description": "Add an item (or increase quantity) in an existing Zomato cart.",
        "input_schema": {
            "type": "object",
            "properties": {
                "cart_id":     {"type": "string", "description": "Cart ID from zomato_create_cart"},
                "item_id":     {"type": "string", "description": "Item ID from menu listing"},
                "quantity":    {"type": "integer", "description": "Quantity to add", "default": 1},
                "variant_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Variant IDs for size/type selection (required if item has variants)",
                },
            },
            "required": ["cart_id", "item_id"],
        },
    },
    {
        "name": "zomato_view_cart",
        "description": "View current cart contents, item prices, and estimated total.",
        "input_schema": {
            "type": "object",
            "properties": {
                "cart_id": {"type": "string", "description": "Cart ID from zomato_create_cart"},
            },
            "required": ["cart_id"],
        },
    },
    {
        "name": "zomato_remove_from_cart",
        "description": "Remove an item from the Zomato cart.",
        "input_schema": {
            "type": "object",
            "properties": {
                "cart_id": {"type": "string", "description": "Cart ID"},
                "item_id": {"type": "string", "description": "Item ID to remove"},
            },
            "required": ["cart_id", "item_id"],
        },
    },
    {
        "name": "zomato_get_cart_offers",
        "description": "Get available discount codes and promo offers applicable to the current cart.",
        "input_schema": {
            "type": "object",
            "properties": {
                "cart_id": {"type": "string", "description": "Cart ID from zomato_create_cart"},
            },
            "required": ["cart_id"],
        },
    },

    # Orders
    {
        "name": "zomato_checkout",
        "description": (
            "Place a Zomato food order. "
            "MANDATORY: Always call zomato_view_cart first, show the full summary and total cost, "
            "then ask the user to confirm before calling this tool with confirmed=true."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "cart_id":        {"type": "string", "description": "Cart ID from zomato_create_cart"},
                "payment_method": {
                    "type": "string",
                    "description": "pay_later | upi_qr | card (default: pay_later — no payment details needed)",
                    "default": "pay_later",
                },
                "offer_id": {"type": "string", "description": "Promo/offer ID from zomato_get_cart_offers (optional)"},
                "confirmed": {"type": "boolean", "description": "Set true only after explicit user confirmation", "default": False},
            },
            "required": ["cart_id"],
        },
    },
    {
        "name": "zomato_track_order",
        "description": (
            "Track the live delivery status of a Zomato order. "
            "Omit order_id to track the most recent active order."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "Order ID (omit for most recent active order)"},
            },
        },
    },
    {
        "name": "zomato_order_history",
        "description": "Get recent Zomato order history with restaurant names, items ordered, and totals.",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Number of past orders to return (default: 10)", "default": 10},
            },
        },
    },
    {
        "name": "zomato_reorder",
        "description": "Reorder from a previous Zomato order (recreates the same cart).",
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "Order ID from zomato_order_history"},
            },
            "required": ["order_id"],
        },
    },

    # Table booking
    {
        "name": "zomato_book_table",
        "description": "Book a table at a Zomato restaurant for dine-in (uses browser automation).",
        "input_schema": {
            "type": "object",
            "properties": {
                "restaurant_id": {"type": "string", "description": "Restaurant ID from zomato_search_restaurants"},
                "date":          {"type": "string", "description": "Date in YYYY-MM-DD format, e.g. '2026-05-10'"},
                "time":          {"type": "string", "description": "Time in HH:MM format, e.g. '19:30'"},
                "guests":        {"type": "integer", "description": "Number of guests (default: 2)", "default": 2},
            },
            "required": ["restaurant_id", "date", "time"],
        },
    },

    # ─────────────────────────────────────────────────────────────────────────
    # SCHEDULER — recurring tasks + one-time reminders
    # ─────────────────────────────────────────────────────────────────────────
    {
        "name": "schedule_task",
        "description": (
            "Schedule a task in two modes:\n"
            "1. RECURRING (provide interval_minutes): runs every N minutes automatically. "
            "Use for 'every hour', 'daily update', 'keep me posted on...'.\n"
            "2. ONE-TIME REMINDER (provide run_at): fires exactly once at the specified datetime, "
            "then auto-disables. Use for 'remind me at 4pm', 'notify me on 27th April', "
            "'send me a WhatsApp at 9am tomorrow'.\n"
            "For reminders that should send a WhatsApp: write the prompt as "
            "'Send a WhatsApp message to [contact name or +91-number] saying: [message text]'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Short friendly name, e.g. 'Call John Reminder' or 'GST News Update'",
                },
                "prompt": {
                    "type": "string",
                    "description": (
                        "What to execute when the task fires. For reminders: "
                        "'Send a WhatsApp message to +91-XXXXXXXXXX saying: Time to call John!'. "
                        "For recurring tasks: 'Search for latest GST news and summarize top 3 stories.'"
                    ),
                },
                "interval_minutes": {
                    "type": "integer",
                    "description": "For RECURRING tasks: how often to run in minutes (e.g. 60 = hourly, 1440 = daily). Omit for one-time reminders.",
                },
                "run_at": {
                    "type": "string",
                    "description": "For ONE-TIME reminders: exact datetime in ISO 8601 format, e.g. '2026-04-27T16:00:00'. Parse natural language times before calling. Omit for recurring tasks.",
                },
            },
            "required": ["name", "prompt"],
        },
    },
    {
        "name": "list_schedules",
        "description": "List all scheduled tasks (recurring + one-time reminders) — name, type, next run, last run, enabled status.",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "cancel_schedule",
        "description": "Cancel / disable a scheduled task by its ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "integer",
                    "description": "Task ID from list_schedules",
                },
            },
            "required": ["task_id"],
        },
    },

    # ─────────────────────────────────────────────────────────────────────────
    # GOOGLE CALENDAR
    # ─────────────────────────────────────────────────────────────────────────
    {
        "name": "calendar_create_event",
        "description": (
            "Create a Google Calendar event. Set add_meet_link=true to generate a Google Meet link. "
            "Specify attendees as a list of email addresses to invite them. "
            "Always confirm the event details with the user before creating."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Event title"},
                "start_time": {"type": "string", "description": "Start time in ISO 8601, e.g. '2026-04-27T15:00:00'"},
                "end_time": {"type": "string", "description": "End time in ISO 8601, e.g. '2026-04-27T16:00:00'"},
                "description": {"type": "string", "description": "Event description or agenda (optional)"},
                "attendees": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of attendee email addresses (optional)",
                },
                "add_meet_link": {
                    "type": "boolean",
                    "description": "Set true to auto-generate a Google Meet video call link",
                    "default": False,
                },
                "location": {"type": "string", "description": "Physical location (optional)"},
            },
            "required": ["title", "start_time", "end_time"],
        },
    },
    {
        "name": "calendar_list_events",
        "description": "List upcoming Google Calendar events. Filter by date range.",
        "input_schema": {
            "type": "object",
            "properties": {
                "time_min": {"type": "string", "description": "Start of date range, ISO 8601 (default: now)"},
                "time_max": {"type": "string", "description": "End of date range, ISO 8601 (default: 7 days from now)"},
                "max_results": {"type": "integer", "description": "Max events to return (default: 10)", "default": 10},
            },
        },
    },
    {
        "name": "calendar_get_event",
        "description": "Get full details of a specific Google Calendar event by event ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string", "description": "Google Calendar event ID"},
            },
            "required": ["event_id"],
        },
    },
    {
        "name": "calendar_update_event",
        "description": "Update an existing Google Calendar event. Only provide fields you want to change.",
        "input_schema": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string", "description": "Event ID to update"},
                "title": {"type": "string"},
                "start_time": {"type": "string", "description": "ISO 8601"},
                "end_time": {"type": "string", "description": "ISO 8601"},
                "description": {"type": "string"},
                "attendees": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["event_id"],
        },
    },
    {
        "name": "calendar_delete_event",
        "description": "Delete a Google Calendar event. Always confirm with user before deleting.",
        "input_schema": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string", "description": "Event ID to delete"},
                "confirmed": {"type": "boolean", "description": "Set true only after user explicitly confirms", "default": False},
            },
            "required": ["event_id"],
        },
    },

    # ─────────────────────────────────────────────────────────────────────────
    # GMAIL
    # ─────────────────────────────────────────────────────────────────────────
    {
        "name": "gmail_send_email",
        "description": (
            "Send an email via Gmail. Always confirm recipient, subject, and body with user before sending. "
            "Show a preview of what will be sent."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient email address"},
                "subject": {"type": "string", "description": "Email subject"},
                "body": {"type": "string", "description": "Email body (plain text or HTML)"},
                "cc": {"type": "string", "description": "CC email addresses, comma-separated (optional)"},
                "confirmed": {"type": "boolean", "description": "Set true only after user explicitly confirms", "default": False},
            },
            "required": ["to", "subject", "body"],
        },
    },
    {
        "name": "gmail_read_inbox",
        "description": "Read recent emails from Gmail inbox. Returns sender, subject, date, and snippet.",
        "input_schema": {
            "type": "object",
            "properties": {
                "max_results": {"type": "integer", "description": "Number of emails to return (default: 10)", "default": 10},
                "unread_only": {"type": "boolean", "description": "Only return unread emails", "default": False},
            },
        },
    },
    {
        "name": "gmail_search_emails",
        "description": "Search Gmail using Gmail search syntax. E.g. 'from:boss@company.com', 'subject:GST', 'is:unread after:2026/01/01'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Gmail search query"},
                "max_results": {"type": "integer", "description": "Max results (default: 10)", "default": 10},
            },
            "required": ["query"],
        },
    },
    {
        "name": "gmail_create_draft",
        "description": "Save an email as a draft without sending it.",
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient email address"},
                "subject": {"type": "string", "description": "Email subject"},
                "body": {"type": "string", "description": "Email body"},
                "cc": {"type": "string", "description": "CC addresses (optional)"},
            },
            "required": ["to", "subject", "body"],
        },
    },
    {
        "name": "gmail_reply_email",
        "description": "Reply to an email thread. Always confirm the reply content before sending.",
        "input_schema": {
            "type": "object",
            "properties": {
                "thread_id": {"type": "string", "description": "Gmail thread ID to reply to"},
                "body": {"type": "string", "description": "Reply text"},
                "confirmed": {"type": "boolean", "description": "Set true only after user explicitly confirms", "default": False},
            },
            "required": ["thread_id", "body"],
        },
    },

    # ─────────────────────────────────────────────────────────────────────────
    # GOOGLE DRIVE
    # ─────────────────────────────────────────────────────────────────────────
    {
        "name": "drive_search",
        "description": (
            "Search Google Drive for files by name or content. Use for: 'find the audit report', "
            "'search for GST files', 'find documents about Q4'. Returns file names, types, links, and IDs."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query":     {"type": "string", "description": "What to search for — file name, keyword, or topic"},
                "file_type": {"type": "string", "description": "Optional filter: pdf, doc, sheet, slide, excel, word"},
                "limit":     {"type": "integer", "description": "Max results (default 10)", "default": 10},
            },
            "required": ["query"],
        },
    },
    {
        "name": "drive_list",
        "description": (
            "List files in a Google Drive folder. Use for: 'show my Drive files', "
            "'list files in the Audit folder', 'what's in my Documents'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "folder": {"type": "string", "description": "Folder name or 'root' for top level", "default": "root"},
                "limit":  {"type": "integer", "description": "Max files to return (default 20)", "default": 20},
            },
        },
    },
    {
        "name": "drive_read",
        "description": (
            "Read the text content of a Google Drive file. Works for Google Docs, Sheets (as CSV), "
            "and plain text files. For binary/Office files, use drive_email_file to send instead."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_name": {"type": "string", "description": "File name or partial name to search for"},
                "file_id":   {"type": "string", "description": "Google Drive file ID (if known)"},
            },
        },
    },
    {
        "name": "drive_email_file",
        "description": (
            "Find a file in Google Drive and email it as an attachment via Gmail. "
            "Use for: 'email the audit report to Rahul', 'send the Q4 sheet to manager@company.com'. "
            "Always confirm before sending."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_name": {"type": "string", "description": "File name or partial name to find"},
                "file_id":   {"type": "string", "description": "Google Drive file ID (if already known)"},
                "to":        {"type": "string", "description": "Recipient email address"},
                "subject":   {"type": "string", "description": "Email subject"},
                "body":      {"type": "string", "description": "Email body text"},
                "confirmed": {"type": "boolean", "description": "Set true only after user explicitly confirms", "default": False},
            },
            "required": ["to"],
        },
    },

    # ─────────────────────────────────────────────────────────────────────────
    # ONEDRIVE
    # ─────────────────────────────────────────────────────────────────────────
    {
        "name": "onedrive_search",
        "description": (
            "Search OneDrive for files by name or content. Use for: 'find the audit report in OneDrive', "
            "'search my OneDrive for GST files', 'find Excel files about Q4'. Returns file names, paths, links."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query":     {"type": "string", "description": "What to search for — file name, keyword, or topic"},
                "file_type": {"type": "string", "description": "Optional file extension filter e.g. xlsx, pdf, docx"},
                "limit":     {"type": "integer", "description": "Max results (default 10)", "default": 10},
            },
            "required": ["query"],
        },
    },
    {
        "name": "onedrive_list",
        "description": (
            "List files in a OneDrive folder. Use for: 'show my OneDrive files', "
            "'list files in OneDrive/Documents', 'what's in my OneDrive root'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "folder": {"type": "string", "description": "Folder path e.g. 'Documents', 'Work/Audit' or '/' for root", "default": "/"},
                "limit":  {"type": "integer", "description": "Max files to return (default 20)", "default": 20},
            },
        },
    },
    {
        "name": "onedrive_read",
        "description": (
            "Read the text content of a OneDrive file. Works for .txt, .csv, .json, .md, .log files. "
            "For Word/Excel/PDF use onedrive_email_file to send as attachment."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_name":  {"type": "string", "description": "File name or partial name to search for"},
                "file_path":  {"type": "string", "description": "Full OneDrive path e.g. 'Documents/report.txt'"},
                "item_id":    {"type": "string", "description": "OneDrive item ID (if known)"},
            },
        },
    },
    {
        "name": "onedrive_email_file",
        "description": (
            "Find a file in OneDrive and email it as an attachment via Gmail. "
            "Use for: 'email the audit report from OneDrive to Rahul', 'send my Q4 Excel to manager@rsm.com'. "
            "Works with any file type. Always confirm before sending."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_name":  {"type": "string", "description": "File name or partial name to find"},
                "file_path":  {"type": "string", "description": "Full OneDrive path e.g. 'Documents/Q4 Report.xlsx'"},
                "item_id":    {"type": "string", "description": "OneDrive item ID (if known)"},
                "to":         {"type": "string", "description": "Recipient email address"},
                "subject":    {"type": "string", "description": "Email subject"},
                "body":       {"type": "string", "description": "Email body text"},
                "confirmed":  {"type": "boolean", "description": "Set true only after user explicitly confirms", "default": False},
            },
            "required": ["to"],
        },
    },

    # ─────────────────────────────────────────────────────────────────────────
    # MEMORY
    # ─────────────────────────────────────────────────────────────────────────
    {
        "name": "remember",
        "description": "Save a preference or fact to persistent memory across sessions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Memory key (snake_case)"},
                "value": {"type": "string", "description": "Value to store"},
            },
            "required": ["key", "value"],
        },
    },
    {
        "name": "recall",
        "description": "Retrieve a previously remembered preference or fact.",
        "input_schema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Memory key to look up"},
            },
            "required": ["key"],
        },
    },
]


def get_tools() -> list[dict[str, Any]]:
    return TOOLS


def get_tool_names() -> set[str]:
    return {t["name"] for t in TOOLS}
