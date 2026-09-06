"""Line-splitting shared by the header/meta scanner and the data-section builder."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Record:
    num: int
    text: str
    start: int
    end: int  # exclusive, content only


def split_records(data: bytes) -> list[Record]:
    text = data.decode("utf-8", errors="surrogateescape")
    out: list[Record] = []
    line_no = 1
    i = 0
    n = len(text)
    while i <= n:
        start = i
        j = i
        while j < n and text[j] != "\n" and text[j] != "\r":
            j += 1
        out.append(Record(num=line_no, text=text[start:j], start=start, end=j))
        line_no += 1
        if j >= n:
            break
        if text[j] == "\r":
            j += 1
        if j < n and text[j] == "\n":
            j += 1
        i = j
    return out


def trim_trailing_empty_records(records: list[Record]) -> list[Record]:
    while len(records) > 1 and records[-1].text == "":
        records = records[:-1]
    return records


def lines_from_records(records: list[Record]) -> tuple[list[str], list[int]]:
    records = trim_trailing_empty_records(records)
    return [r.text for r in records], [r.num for r in records]


def extract_data_section(full: str, records: list[Record], data_idx: int) -> str:
    if data_idx >= len(records):
        return ""
    start = records[data_idx].start
    end = len(full)
    if records:
        last = records[-1]
        end = last.end
        if end < len(full):
            if full[end] == "\r":
                if end + 1 < len(full) and full[end + 1] == "\n":
                    end += 2
                else:
                    end += 1
            elif full[end] == "\n":
                end += 1
    return full[start:end]


def first_line_bytes(data: bytes) -> str:
    i = data.find(b"\n")
    line = data if i < 0 else data[:i]
    if line.endswith(b"\r"):
        line = line[:-1]
    return line.decode("utf-8", errors="surrogateescape")
