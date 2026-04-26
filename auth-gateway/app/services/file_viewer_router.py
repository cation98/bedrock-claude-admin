"""File viewer type routing — maps filename extensions to viewer backends."""

import os
from enum import Enum


class ViewerType(str, Enum):
    ONLYOFFICE = "onlyoffice"
    CODE = "code"
    IMAGE = "image"
    UNSUPPORTED = "unsupported"


_ONLYOFFICE_EXTS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx",
    ".ppt", ".pptx", ".odt", ".ods", ".odp",
    ".txt", ".csv", ".rtf",
}

_CODE_EXTS = {
    ".py", ".js", ".ts", ".jsx", ".tsx",
    ".sh", ".bash", ".zsh", ".fish",
    ".yaml", ".yml", ".toml", ".json", ".env",
    ".go", ".rs", ".java", ".kt", ".swift",
    ".c", ".cpp", ".h", ".hpp", ".cs",
    ".rb", ".php", ".r", ".sql",
    ".md", ".html", ".css", ".scss", ".vue",
    ".tf", ".hcl", ".dockerfile",
}

_IMAGE_EXTS = {
    ".jpg", ".jpeg", ".png", ".gif",
    ".svg", ".webp", ".bmp", ".ico",
}


def get_viewer_type(filename: str) -> ViewerType:
    """Return the viewer type for the given filename based on its extension."""
    ext = os.path.splitext(filename)[1].lower()
    if not ext:
        return ViewerType.UNSUPPORTED
    if ext in _ONLYOFFICE_EXTS:
        return ViewerType.ONLYOFFICE
    if ext in _CODE_EXTS:
        return ViewerType.CODE
    if ext in _IMAGE_EXTS:
        return ViewerType.IMAGE
    return ViewerType.UNSUPPORTED
