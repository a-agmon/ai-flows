"""OCR module node (placeholder implementation)."""

from __future__ import annotations


async def extract_text(inputs: dict, state: dict, config: dict) -> str:
    """Extract text from a document URL.

    Placeholder: a real implementation would call an OCR service or library.
    The contract is what matters here -- it returns a string that gets written
    to the node's ``output_key``.
    """
    file_url = inputs["file_url"]
    return f"[extracted text from {file_url}]"
