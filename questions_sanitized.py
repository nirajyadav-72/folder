# Auto-generated sanitized questions list
# This file imports the original QUIZ_LIST from questions.py and creates a sanitized
# version where each explanation is truncated to a short summary to avoid Telegram
# "message is too long" errors when used as a poll question + explanation.

from questions import QUIZ_LIST as ORIGINAL_QUIZ_LIST

def truncate(s, limit=120):
    if s is None:
        return ""
    s = str(s)
    if len(s) <= limit:
        return s
    # try to cut at last space to avoid breaking words
    cut = s.rfind(" ", 0, limit)
    if cut == -1:
        return s[:limit-3] + "..."
    return s[:cut] + "..."

SANITIZED_QUIZ_LIST = []
for item in ORIGINAL_QUIZ_LIST:
    new = dict(item)  # shallow copy
    # truncate explanation to 120 chars (adjustable)
    new['explanation'] = truncate(new.get('explanation', ''), 120)
    SANITIZED_QUIZ_LIST.append(new)

# Export same name for convenience
QUIZ_LIST = SANITIZED_QUIZ_LIST
