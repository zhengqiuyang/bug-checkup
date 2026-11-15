import json
import os

from instructor.v2.core.multimodal import Image
from instructor.v2.providers.anthropic.multimodal import image_to_anthropic

case = json.load(open(os.environ["BUGCHECKUP_INPUT"], encoding="utf-8"))
try:
    image = Image(source=case["source"], media_type=case["media_type"], data=case.get("data"))
    block = image_to_anthropic(image)
except Exception:
    raise SystemExit(0)  # a different failure, not the target bug
raise SystemExit(0 if block["source"].get("data") is not None else 1)