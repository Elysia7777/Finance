#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stable, local bill import duplicate detection."""

import hashlib
import json


def fingerprint(record, occurrence=0):
    source_ref = str(record.get("sourceRef") or "").strip()
    raw = record.get("raw") or ""
    try:
        raw_row = json.loads(raw).get("row", raw)
    except (TypeError, ValueError, AttributeError):
        raw_row = raw
    identity = {
        "source": record.get("source", ""),
        "date": record.get("date", ""),
        "amount": round(float(record.get("amount", 0)), 2),
        "direction": record.get("direction", ""),
        "reference": source_ref,
        "raw": raw_row,
        "occurrence": occurrence,
    }
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def dedup(records, existing=None):
    """Return (new records, duplicates), keeping identical rows within one file distinct."""
    known = {item.get("fingerprint") for item in (existing or []) if item.get("fingerprint")}
    occurrences = {}
    added = []
    duplicates = []
    for record in records:
        try:
            base = json.loads(record.get("raw") or "{}").get("row")
        except (TypeError, ValueError, AttributeError):
            base = record.get("raw", "")
        occurrence_key = json.dumps(
            [record.get("source"), record.get("date"), record.get("amount"),
             record.get("direction"), record.get("sourceRef") or "", base],
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )
        occurrence = occurrences.get(occurrence_key, 0)
        occurrences[occurrence_key] = occurrence + 1
        item = dict(record)
        item["fingerprint"] = fingerprint(item, occurrence)
        if item["fingerprint"] in known:
            duplicates.append(item)
        else:
            known.add(item["fingerprint"])
            added.append(item)
    return added, duplicates
