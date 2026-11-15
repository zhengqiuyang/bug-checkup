import json, os
value = json.load(open(os.environ["BUGCHECKUP_INPUT"], encoding="utf-8"))
def contains(node):
    if isinstance(node, dict):
        if node.get("boom") is True:
            return True
        return any(contains(v) for v in node.values())
    if isinstance(node, list):
        return any(contains(v) for v in node)
    return False
raise SystemExit(1 if contains(value) else 0)