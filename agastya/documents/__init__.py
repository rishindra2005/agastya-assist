"""Documents module for Agastya Assist (Form 16, document store)."""

from agastya.documents.form16 import extract_form16, parse_indian_number
from agastya.documents.store import Document, DocumentStore

__all__ = [
    "Document",
    "DocumentStore",
    "extract_form16",
    "parse_indian_number",
]
