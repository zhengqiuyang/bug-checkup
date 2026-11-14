"""Probe for multimodal conversion round-trips (v2/core/multimodal.py + provider encoders).

The provider encoder modules are loaded **by file path** so the probe never imports the provider
package ``__init__`` (which pulls the OpenAI SDK). That keeps the probe offline and fast while
still exercising the real conversion code.

Contracts:

1. an image built from bytes/base64/path reaches Anthropic as base64 whose decoded bytes equal the
   original file, with the same media type;
2. the same image reaches OpenAI as a ``data:`` URI that decodes to the same bytes, in both chat
   and Responses modes;
3. conversions are idempotent: calling twice yields identical blocks and does not re-encode data;
4. a local path PDF is read and base64-encoded by the Anthropic encoder;
5. a source with no inline data raises a clear error rather than emitting ``data: None``
   (Anthropic) / matches OpenAI's clear error.

Standalone script (no pytest); exit 0 PASS / 1 FAIL / 2 INCONCLUSIVE / 3 CHECKER BUG.
"""

from __future__ import annotations

import base64
import importlib.util
import json
import tempfile
from pathlib import Path

from instructor.v2.core.mode import Mode
from instructor.v2.core.multimodal import Audio, Image, PDF

PNG = bytes.fromhex("89504e470d0a1a0a") + b"\x00\x00\x00\rIHDR" + b"\x00" * 32
PNG_B64 = base64.b64encode(PNG).decode()


def _load(name: str, relative: str):
    import os

    root = Path(os.environ.get("PROBE_REPO_ROOT", r"D:\projects\instructor")) / "instructor"
    spec = importlib.util.spec_from_file_location(name, root / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ANTHROPIC = _load("probe_anthropic_mm", "v2/providers/anthropic/multimodal.py")
OPENAI = _load("probe_openai_mm", "v2/providers/openai/multimodal.py")


def _check_roundtrip(failures: list[str], image: Image, label: str) -> None:
    anthropic_block = ANTHROPIC.image_to_anthropic(image)
    source = anthropic_block["source"]
    if source.get("media_type") != image.media_type:
        failures.append(f"{label}: anthropic media_type {source.get('media_type')} != {image.media_type}")
    if source.get("data") is None:
        failures.append(f"{label}: anthropic encoder emitted data=None")
    elif base64.b64decode(source["data"]) != PNG:
        failures.append(f"{label}: anthropic data does not decode to the original bytes")

    openai_block = OPENAI.image_to_openai(image, Mode.TOOLS)
    url = openai_block["image_url"]["url"]
    prefix = f"data:{image.media_type};base64,"
    if not url.startswith(prefix):
        failures.append(f"{label}: openai url does not start with {prefix!r}: {url[:40]}")
    elif base64.b64decode(url[len(prefix):]) != PNG:
        failures.append(f"{label}: openai data does not decode to the original bytes")

    responses_block = OPENAI.image_to_openai(image, Mode.RESPONSES_TOOLS)
    if responses_block.get("type") != "input_image" or not isinstance(responses_block.get("image_url"), str):
        failures.append(f"{label}: responses mode shape is {responses_block}")


def check_sources(failures: list[str]) -> None:
    from_raw = Image.from_raw_base64(PNG_B64)
    _check_roundtrip(failures, from_raw, "from_raw_base64")

    from_uri = Image.from_base64(f"data:image/png;base64,{PNG_B64}")
    _check_roundtrip(failures, from_uri, "from_base64")

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
        handle.write(PNG)
        path = handle.name
    try:
        from_path = Image.from_path(path)
        _check_roundtrip(failures, from_path, "from_path")
    finally:
        Path(path).unlink()


def check_idempotence(failures: list[str]) -> None:
    image = Image.from_raw_base64(PNG_B64)
    before = image.data
    first = ANTHROPIC.image_to_anthropic(image)
    second = ANTHROPIC.image_to_anthropic(image)
    if first != second:
        failures.append("anthropic conversion is not idempotent")
    if image.data != before:
        failures.append("anthropic conversion re-encoded the stored data")
    openai_first = OPENAI.image_to_openai(image, Mode.TOOLS)
    openai_second = OPENAI.image_to_openai(image, Mode.TOOLS)
    if openai_first != openai_second:
        failures.append("openai conversion is not idempotent")


def check_pdf(failures: list[str]) -> None:
    pdf_bytes = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n%%EOF\n"
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
        handle.write(pdf_bytes)
        path = handle.name
    try:
        pdf = PDF.from_path(path)
        block = ANTHROPIC.pdf_to_anthropic(pdf)
        if block["source"].get("data") is None:
            failures.append("pdf: anthropic encoder emitted data=None")
        elif base64.b64decode(block["source"]["data"]) != pdf_bytes:
            failures.append("pdf: anthropic data does not decode to the file bytes")
    finally:
        Path(path).unlink()


def check_missing_data(failures: list[str]) -> None:
    # A data URI without inline data must hand its payload to Anthropic, not ``data: None``.
    uri = f"data:image/png;base64,{PNG_B64}"
    image = Image(source=uri, media_type="image/png")
    block = ANTHROPIC.image_to_anthropic(image)
    if block["source"].get("data") != PNG_B64:
        failures.append("data uri: anthropic encoder did not take the payload")
    # A source with no inline data and no way to load it must raise, matching OpenAI.
    missing = Image(source="missing-image.png", media_type="image/png")
    try:
        ANTHROPIC.image_to_anthropic(missing)
    except ValueError:
        pass
    else:
        failures.append("missing data: anthropic encoder emitted None instead of raising")


def main() -> int:
    if base64.b64decode(PNG_B64) != PNG:
        print("CHECKER BUG: fixture round-trip failed")
        return 3
    failures: list[str] = []
    for check in (check_sources, check_idempotence, check_pdf, check_missing_data):
        try:
            check(failures)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{check.__name__} raised {type(exc).__name__}: {exc}")
    if failures:
        print(f"FAIL ({len(failures)} issue(s))")
        for failure in failures:
            print(" -", failure)
        return 1
    print("PASS: anthropic/openai round-trips, responses shapes, idempotence, pdf, missing-data handling")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
