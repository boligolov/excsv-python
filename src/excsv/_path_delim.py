import os

from ._document import Header


def delim_name_for_path(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".tsv":
        return "tab"
    if ext == ".csv":
        return "comma"
    return ""


def header_for_data_path(sidecar_header: Header, data_path: str) -> Header:
    import copy

    h = copy.copy(sidecar_header)
    ext = os.path.splitext(data_path)[1].lower()
    if ext == ".tsv":
        h.delim_name = "tab"
        h.delim = "\t"
    elif ext == ".csv":
        if not h.delim_name:
            h.delim_name = "comma"
            h.delim = ","
    return h
