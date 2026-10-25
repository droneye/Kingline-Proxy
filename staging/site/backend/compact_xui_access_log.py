#!/usr/bin/env python3
import argparse
import json
import os
import re
import sys


RAW_RE = re.compile(
    r"^(?:\[(?P<server>[^\]]+)\]\s+)?"
    r"(?P<ts>\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2})(?:\.\d+)?\s+"
    r"from\s+(?P<src>\[[^\]]+\]|[^:\s]+)(?::\d+)?\s+"
    r"accepted\s+(?P<proto>tcp|udp):(?P<target>\[[^\]]+\]|[^:\s]+)"
    r"(?::(?P<port>\d+))?\s+\[[^\]]*\]\s+email:\s*(?P<email>.+)$",
    re.IGNORECASE,
)


def normalize_username(value):
    raw = str(value or "").strip()
    if "#" in raw:
        raw = raw.split("#", 1)[0].strip()
    return raw.replace("@", "").replace(" ", "").strip().lower()


def is_system_username(username):
    normalized = normalize_username(username)
    return normalized in {"", "api", "whitelist", "whitelisted", "system"} or normalized.startswith("whitelist")


def compact_ts(value):
    value = str(value or "").strip()
    if "/" in value:
        return value.replace("/", "-").replace(" ", "T") + "Z"
    if value.endswith("Z"):
        return value
    return value + "Z"


def normalize_record(record):
    username = normalize_username(record.get("u") or record.get("username") or record.get("email"))
    if is_system_username(username):
        return None
    domain = str(record.get("d") or record.get("domain") or record.get("target") or "").strip().strip("[]").strip(".").lower()
    ip = str(record.get("ip") or record.get("sourceIp") or record.get("source_ip") or "").strip().strip("[]")
    if not domain or not ip:
        return None
    try:
        count = max(1, int(record.get("c") or record.get("count") or 1))
    except Exception:
        count = 1
    try:
        port = int(record.get("p") or record.get("port") or 0)
    except Exception:
        port = 0
    return {
        "t": compact_ts(record.get("t") or record.get("ts") or record.get("time")),
        "s": str(record.get("s") or record.get("server") or "").strip() or "xui",
        "u": username,
        "ip": ip,
        "d": domain,
        "p": port,
        "c": count,
    }


def parse_line(line):
    line = line.strip()
    if not line:
        return None
    if line.startswith("{"):
        try:
            return normalize_record(json.loads(line))
        except Exception:
            return None
    match = RAW_RE.search(line)
    if not match:
        return None
    return normalize_record(
        {
            "t": match.group("ts"),
            "s": match.group("server") or "xui",
            "u": match.group("email"),
            "ip": match.group("src"),
            "d": match.group("target"),
            "p": match.group("port") or 0,
            "c": 1,
        }
    )


def flush(records, output):
    for record in records.values():
        output.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    records.clear()


def compact(input_path, output_path, chunk_records):
    parsed = skipped = written = 0
    records = {}
    with open(input_path, "r", encoding="utf-8", errors="ignore") as source, open(output_path, "w", encoding="utf-8") as output:
        for line in source:
            record = parse_line(line)
            if not record:
                skipped += 1
                continue
            parsed += 1
            key = (record["t"], record["s"], record["u"], record["ip"], record["d"], record["p"])
            if key in records:
                records[key]["c"] += record["c"]
            else:
                records[key] = record
            if len(records) >= chunk_records:
                written += len(records)
                flush(records, output)
        if records:
            written += len(records)
            flush(records, output)
    os.chmod(output_path, 0o600)
    return {"parsed": parsed, "skipped": skipped, "written": written}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input")
    parser.add_argument("output")
    parser.add_argument("--chunk-records", type=int, default=100000)
    args = parser.parse_args()
    stats = compact(args.input, args.output, args.chunk_records)
    print(json.dumps(stats, ensure_ascii=False))


if __name__ == "__main__":
    sys.exit(main())
