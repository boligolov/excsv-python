"""ExCSV row-ZIP container (".excsv.zip"/".ecsv.zip").

Most callers should use `excsv.parse_file` / `excsv.Document` instead of this
module directly.
"""

from ._zip import (
    ExtractResult,
    InspectResult,
    ZipError,
    extract,
    extract_primary,
    extract_primary_with_password,
    extract_with_password,
    inspect,
    read_entries,
    wrap,
    wrap_with_password,
)

__all__ = [
    "ExtractResult",
    "InspectResult",
    "ZipError",
    "extract",
    "extract_primary",
    "extract_primary_with_password",
    "extract_with_password",
    "inspect",
    "read_entries",
    "wrap",
    "wrap_with_password",
]
