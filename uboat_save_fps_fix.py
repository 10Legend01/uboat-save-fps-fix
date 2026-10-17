#!/usr/bin/env python3
"""Create a repaired COPY of a supported UBOAT save, using only Python stdlib.

This deliberately narrow, fail-closed byte parser never runs game code. It
deactivates verified extra World-area skipper actors; it does not delete actors,
alter the crew, unlock achievements, or rewrite the serialization graph.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import math
import os
from pathlib import Path
import re
import struct
import sys
import tempfile
import zlib

__version__ = "0.1.0"
DEFAULT_MAX_COMPRESSED_BYTES = 256 * 1024 * 1024
DEFAULT_MAX_EXPANDED_BYTES = 512 * 1024 * 1024
CAREER = "UBOAT.Game.Sandbox.PlayerCareer"
CREW = "UBOAT.Game.Scene.Characters.PlayerCrew"
SKIPPER = "UBOAT.Game.Scene.Characters.Statuses.Skipper"
NAV = "UBOAT.Game.Scene.Characters.Navigation.NavMeshNavigator"
PLAYABLE = "UBOAT.Game.Scene.Characters.PlayableCharacter"
DATA = "UBOAT.Game.Scene.Characters.PlayableCharacterData"
TRANSFORM = "UnityEngine.Transform"
AREA = "UBOAT.Game.Scene.Characters.Navigation.CharacterArea"
SCENE_OBJECT = "UBOAT.Game.Scene.Entities.SceneObject"
ACHIEVEMENT_FIELD = "isPlaythroughLockedOutOfAchievements"
_ASCII = re.compile(rb"[\x20-\x7e]{5,}")
_FIELD_NAME = re.compile(r"[A-Za-z_<>][A-Za-z_0-9<>.$+`]*\Z")
_QUALIFIED = re.compile(r", Version=\d+\.\d+\.\d+\.\d+, Culture=[^,]+, PublicKeyToken=(?:null|[0-9a-f]{16})\Z")
_FMTS = {8: "B", 9: "b", 11: "h", 12: "H", 13: "i", 14: "I",
         15: "q", 16: "Q", 17: "f", 18: "d", 19: "B", 20: "2f",
         21: "3f", 22: "4f", 23: "4f", 24: "4B", 25: "4f"}
_SIZES = {k: struct.calcsize("<" + v) for k, v in _FMTS.items()}


class SaveFixError(Exception):
    """A refused operation, unsupported save, or I/O failure."""


class SaveFormatError(SaveFixError):
    """The save cannot be proved safe to edit by this version of the parser."""


def _need(condition, message):
    if not condition:
        raise SaveFormatError(message)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _i32(data, pos):
    _need(0 <= pos <= len(data) - 4, "Integer outside the save buffer")
    return struct.unpack_from("<i", data, pos)[0]


class _Reader:
    def __init__(self, data, pos=0, end=None):
        self.data, self.pos = data, pos
        self.end = len(data) if end is None else end
        _need(0 <= pos <= self.end <= len(data), "Invalid buffer bounds")

    def number(self, fmt="i"):
        size = struct.calcsize("<" + fmt)
        _need(self.pos + size <= self.end, "Truncated numeric value")
        value = struct.unpack_from("<" + fmt, self.data, self.pos)[0]
        self.pos += size
        return value

    def boolean(self):
        value = self.number("B")
        _need(value in (0, 1), "Invalid boolean encoding")
        return bool(value)

    def skip(self, size):
        _need(0 <= size <= self.end - self.pos, "Block exceeds its bounds")
        self.pos += size

    def string(self, maximum=1_000_000, nullable=True):
        if nullable and self.data[self.pos:self.pos + 4] == b"\xff" * 4:
            self.skip(4)
            return None
        size = 0
        for shift in range(0, 35, 7):
            byte = self.number("B")
            size |= (byte & 127) << shift
            if not byte & 128:
                break
        else:
            raise SaveFormatError("Invalid 7-bit string length")
        _need(0 <= size <= maximum and size <= self.end - self.pos, "String exceeds its bounds")
        start = self.pos
        self.pos += size
        try:
            return self.data[start:self.pos].decode("utf-8")
        except UnicodeError as exc:
            raise SaveFormatError("Invalid UTF-8 string") from exc


def _inflate(compressed, limit):
    _need(isinstance(limit, int) and limit > 0, "Expanded-size limit must be positive")
    try:
        dec = zlib.decompressobj(-15)
        raw = dec.decompress(compressed, limit + 1)
        _need(len(raw) <= limit and not dec.unconsumed_tail, "Expanded save exceeds the size limit")
        _need(dec.eof, "Truncated or incomplete raw DEFLATE stream")
        _need(not dec.unused_data, "Trailing bytes after the raw DEFLATE stream")
        tail = dec.flush()
        _need(len(raw) + len(tail) <= limit, "Expanded save exceeds the size limit")
        return raw + tail
    except zlib.error as exc:
        raise SaveFormatError("Invalid raw DEFLATE stream: " + str(exc)) from exc


def _main_start(raw):
    r = _Reader(raw)
    metadata = r.number()
    _need(0 < metadata <= 4 * 1024 * 1024, "Unsupported metadata buffer length")
    r.skip(metadata)
    thumbnail = r.number()
    _need(0 <= thumbnail <= 64 * 1024 * 1024, "Unsupported thumbnail length")
    r.skip(thumbnail)
    _need(bool(r.string(maximum=4096, nullable=False)), "Missing interior scene name")
    count = r.number()
    _need(0 <= count <= 256, "Unsupported scene count")
    for _ in range(count):
        r.string(maximum=4096, nullable=False)
    _need(r.pos < len(raw) - 4, "Missing serialized game state")
    return r.pos


def _descriptor(r, depth=0):
    _need(depth <= 8, "Descriptor nesting is too deep")
    code, count = r.number(), r.number()
    _need(0 <= code <= 29 and 0 <= count <= 4, "Unsupported type descriptor")
    return [code, [_descriptor(r, depth + 1) for _ in range(count)]]


def _schema_at(raw, prefix):
    class_id = _i32(raw, prefix - 4)
    _need(-2147483647 <= class_id < 0, "Invalid class definition ID")
    r = _Reader(raw, prefix)
    name = r.string(maximum=20000, nullable=False)
    _need(_QUALIFIED.search(name) is not None, "Not an assembly-qualified type")
    count = r.number()
    _need(0 <= count <= 1000, "Unsupported field count")
    fields = []
    for _ in range(count):
        name_field = r.string(maximum=20000, nullable=False)
        _need(_FIELD_NAME.fullmatch(name_field) is not None, "Invalid serialized field name")
        fields.append({"name": name_field, "descriptor": _descriptor(r)})
    return {"class_id": -class_id, "class_id_offset": prefix - 4,
            "type_prefix_offset": prefix, "name": name.split(",", 1)[0],
            "qualified_name": name, "fields": fields, "fields_end": r.pos}


def _schemas(raw, start):
    found = {}
    for run in _ASCII.finditer(raw, start):
        if b", Version=" not in run.group() or b"PublicKeyToken=" not in run.group():
            continue
        candidates = []
        for prefix in range(max(start + 4, run.start() - 5), min(run.start() + 4, run.end())):
            try:
                row = _schema_at(raw, prefix)
                text = _Reader(raw, prefix)
                name = text.string(maximum=20000, nullable=False)
                # The entire type string must belong to this ASCII run.
                text_start = text.pos - len(name.encode("utf-8"))
                if text_start >= run.start() and text.pos <= run.end():
                    candidates.append(row)
            except (SaveFormatError, struct.error):
                pass
        _need(len(candidates) == 1, "Ambiguous or unsupported serialized class definition")
        row = candidates[0]
        _need(row["class_id"] not in found, "Duplicate class ID in game-state schema")
        found[row["class_id"]] = row
    _need(bool(found), "No game-state schemas recognized")
    return found


def _fields_at(raw, pos, row, references=0):
    _need(len({f["name"] for f in row["fields"]}) == len(row["fields"]),
          "Duplicate field names in an inspected object schema")
    r = _Reader(raw, pos)
    singleton = r.boolean()
    r.skip(references * 4)
    fields = {}
    for field in reversed(row["fields"]):
        length_pos = r.pos
        size = r.number()
        _need(0 <= size <= r.end - r.pos, "Serialized field exceeds the buffer")
        code = field["descriptor"][0]
        if code in _SIZES:
            _need(size == _SIZES[code], "Primitive field has an invalid length")
        if code in (3, 4, 5, 29):
            _need(size >= 4, "Collection field is truncated")
            _need(_i32(raw, r.pos) >= -1, "Invalid collection count")
        if code == 0:
            _need(size >= 5, "Reference field is truncated")
        if code == 26:
            _need(size >= 4 and _i32(raw, r.pos) >= 0, "Invalid delegate field")
        value = None
        if code in _FMTS:
            values = struct.unpack_from("<" + _FMTS[code], raw, r.pos)
            value = values[0] if len(values) == 1 else list(values)
            if code == 19:
                _need(value in (0, 1), "Invalid serialized boolean")
                value = bool(value)
        fields[field["name"]] = {"name": field["name"], "descriptor": field["descriptor"],
                                  "length_offset": length_pos, "offset": r.pos,
                                  "length": size, "value": value}
        r.skip(size)
    custom_size = r.number()
    custom_start = r.pos
    r.skip(custom_size)
    return {"start": pos, "end": r.pos, "type": row["name"], "singleton": singleton,
            "fields": fields, "custom_start": custom_start, "custom_length": custom_size}


class _Parser:
    def __init__(self, raw):
        self.raw = raw
        self.start = _main_start(raw)
        self.schemas = _schemas(raw, self.start)
        self.owners_by_start = {}
        self._plain_cache = {}

    def schema(self, name):
        rows = [r for r in self.schemas.values() if r["name"] == name]
        _need(len(rows) == 1, "Expected one schema for " + name)
        return rows[0]

    def typeinfo(self, r):
        cid = r.number()
        if not cid:
            return None
        row = self.schemas.get(abs(cid))
        _need(row is not None, "Unknown class ID")
        if cid < 0:
            _need(r.pos == row["type_prefix_offset"], "Class definition is in an unexpected location")
            r.pos = row["fields_end"]
        return row

    def tables(self, name, component=False):
        row = self.schema(name)
        count = 0 if component else sum(f["descriptor"][0] == 0 for f in row["fields"])
        positions = {row["fields_end"]}
        pattern = struct.pack("<i", row["class_id"])
        pos = self.start
        while True:
            pos = self.raw.find(pattern, pos)
            if pos < 0:
                break
            positions.add(pos + 4)
            pos += 1
        result = []
        for pos in sorted(positions):
            try:
                result.append(_fields_at(self.raw, pos, row, count))
            except (SaveFormatError, struct.error):
                pass
        return result

    def unique_table(self, name, component=False):
        rows = self.tables(name, component)
        _need(len(rows) == 1, "Expected exactly one valid field table for " + name)
        return rows[0]

    def gameobject(self, pos):
        if pos in self.owners_by_start:
            return self.owners_by_start[pos]
        r = _Reader(self.raw, pos)
        oid = r.number()
        _need(oid > 0, "Invalid GameObject ID")
        _need(self.typeinfo(r) is None, "Unsupported singleton GameObject context")
        _need(r.number() == 0 and r.number() == 0, "Unsupported GameObject serialization context")
        is_prefab = r.boolean()
        path, indexes, prefab, pooled = None, None, None, None
        if is_prefab:
            pooled = r.boolean()
            prefab = r.string(maximum=4096, nullable=False)
        else:
            count = r.number("H")
            _need(0 < count <= 128, "Unsupported transform path")
            path = [r.string(maximum=4096, nullable=False) for _ in range(count)]
            indexes = [r.number("H") for _ in range(count)]
        name = r.string(maximum=4096, nullable=False)
        active_offset = r.pos
        active = r.boolean()
        count = r.number()
        _need(0 < count <= 512, "Unsupported component count")
        components = []
        for _ in range(count):
            cid = r.number()
            serial = r.boolean()
            row = self.typeinfo(r)
            _need(cid > 0 and row is not None, "Invalid component definition")
            if serial:
                r.skip(4 * sum(f["descriptor"][0] == 0 for f in row["fields"]))
            components.append({"id": cid, "type": row["name"], "serial": serial})
        _need(len({c["id"] for c in components}) == len(components), "Repeated component ID")
        serial_components = [c for c in components if c["serial"]]
        count = r.number()
        _need(count == len(serial_components), "Component body count does not match its header")
        bodies = []
        for component in serial_components:
            enabled_offset = r.pos
            enabled = r.boolean()
            row = self.typeinfo(r)
            _need(row is not None and row["name"] == component["type"], "Component body order does not match its header")
            body = _fields_at(self.raw, r.pos, row)
            r.pos = body["end"]
            body.update(enabled=enabled, enabled_offset=enabled_offset, component_id=component["id"])
            bodies.append(body)
        owner = {"start": pos, "end": r.pos, "id": oid, "name": name, "active": active,
                 "active_offset": active_offset, "prefab": prefab, "pooled": pooled,
                 "path": path, "path_indices": indexes, "components": components, "bodies": bodies}
        self.owners_by_start[pos] = owner
        return owner

    def character_owners(self):
        pattern = b"\x13Character/Character"
        pos, result = self.start, []
        while True:
            pos = self.raw.find(pattern, pos)
            if pos < 0:
                break
            prefix = pos
            pos += len(pattern)
            try:
                owner = self.gameobject(prefix - 18)
                if owner["prefab"] == "Character/Character":
                    result.append(owner)
            except (SaveFormatError, struct.error):
                pass
        _need(bool(result), "No complete Character/Character actors found")
        _need(len({o["id"] for o in result}) == len(result), "Duplicate character GameObject ID")
        return result

    def plain_ref(self, field, expected_type):
        _need(field["descriptor"] == [0, []], "Unsupported reference descriptor")
        r = _Reader(self.raw, field["offset"], field["offset"] + field["length"])
        _need(r.number("B") == 0, "Unsupported dynamic reference encoding")
        oid = r.number()
        _need(oid not in (0, -2147483648, 2147483647), "Missing or unsupported object reference")
        if oid < 0:
            _need(not r.boolean(), "Expected a plain managed object reference")
            row = self.typeinfo(r)
            _need(row is not None and row["name"] == expected_type, "Unexpected managed object type")
            refs = sum(f["descriptor"][0] == 0 for f in row["fields"])
            table = _fields_at(self.raw, r.pos, row, refs)
            _need(table["end"] == r.end, "Inline managed reference has trailing bytes")
            return -oid, table
        _need(r.pos == r.end, "Positive managed reference has trailing bytes")
        key = (oid, expected_type)
        if key in self._plain_cache:
            return oid, self._plain_cache[key]
        pattern = struct.pack("<i", -oid) + b"\x00"
        pos, found = self.start, []
        while True:
            pos = self.raw.find(pattern, pos)
            if pos < 0:
                break
            q = pos + len(pattern)
            pos += 1
            try:
                definition = _Reader(self.raw, q)
                row = self.typeinfo(definition)
                if row is None or row["name"] != expected_type:
                    continue
                refs = sum(f["descriptor"][0] == 0 for f in row["fields"])
                found.append(_fields_at(self.raw, definition.pos, row, refs))
            except (SaveFormatError, struct.error):
                pass
        _need(len(found) == 1, "Managed reference definition is missing or ambiguous: " + expected_type)
        self._plain_cache[key] = found[0]
        return oid, found[0]

    def world_parent(self, owner):
        transform = _body(owner, TRANSFORM)
        _need(transform["custom_length"] >= 44, "Transform custom data is truncated")
        p = transform["custom_start"] + 40
        ref = _i32(self.raw, p)
        _need(ref not in (0, -2147483648, 2147483647), "Character has no supported parent reference")
        if ref < 0:
            _need(self.raw[p + 4] == 1, "Parent reference is not a Unity object")
            parent = self.gameobject(p + 5)
            _need(parent["end"] == transform["custom_start"] + transform["custom_length"], "Transform parent has trailing custom data")
        else:
            _need(transform["custom_length"] == 44, "Unsupported Transform custom framing")
            matches = []
            for candidate in self.owners_by_start.values():
                if any(c["id"] == ref and c["type"] == TRANSFORM for c in candidate["components"]):
                    matches.append(candidate)
            if not matches:
                pattern = struct.pack("<i", -ref) + b"\x01"
                q = self.start
                while True:
                    q = self.raw.find(pattern, q)
                    if q < 0:
                        break
                    try:
                        candidate = self.gameobject(q + 5)
                        if any(c["id"] == ref and c["type"] == TRANSFORM for c in candidate["components"]):
                            matches.append(candidate)
                    except (SaveFormatError, struct.error):
                        pass
                    q += 1
            matches = {m["start"]: m for m in matches}
            _need(len(matches) == 1, "Character parent definition is missing or ambiguous")
            parent = next(iter(matches.values()))
        _need(any(c["id"] == abs(ref) and c["type"] == TRANSFORM for c in parent["components"]), "Parent reference ID does not match its Transform")
        _need(parent["prefab"] is None and parent["name"] == "Character World Area" and
              parent["path"] == ["Character World Area"] and parent["path_indices"] == [0],
              "World character does not belong to the expected scene area")
        _need(any(c["type"] == AREA for c in parent["components"]), "World area lacks its CharacterArea component")
        return parent["id"]


def _body(owner, name):
    found = [b for b in owner["bodies"] if b["type"] == name]
    _need(len(found) == 1, "Expected one actor component body for " + name)
    return found[0]


def _field(table, name, descriptor=None):
    field = table["fields"].get(name)
    _need(field is not None, "Missing serialized field " + name)
    if descriptor is not None:
        _need(field["descriptor"] == descriptor, "Unsupported descriptor for " + name)
    return field


def _positive_ref(raw, field):
    _need(field["descriptor"] == [0, []] and field["length"] == 5, "Unsupported simple object reference")
    _need(raw[field["offset"]] == 0, "Unsupported dynamic reference type")
    value = _i32(raw, field["offset"] + 1)
    _need(0 < value < 2147483647, "Expected a resolved positive object ID")
    return value


def _analyze(raw):
    parser = _Parser(raw)
    career = parser.unique_table(CAREER)
    achievement = _field(career, ACHIEVEMENT_FIELD, [19, []])
    crew_table = parser.unique_table(CREW, component=True)
    roster = _field(crew_table, "characters")
    _need(roster["descriptor"] == [3, [[0, []]]], "Unsupported PlayerCrew roster descriptor")
    count = _i32(raw, roster["offset"])
    _need(0 < count <= 4096 and roster["length"] == 4 + count * 5, "Unsupported PlayerCrew roster layout")
    crew_ids = []
    for index in range(count):
        offset = roster["offset"] + 4 + index * 5
        _need(raw[offset] == 0, "Unsupported crew reference type")
        cid = _i32(raw, offset + 1)
        _need(0 < cid < 2147483647, "Unsupported inline or missing crew member")
        crew_ids.append(cid)
    _need(len(set(crew_ids)) == count, "Duplicate PlayerCrew roster member")
    status_id, status = parser.plain_ref(_field(crew_table, "skipper"), SKIPPER)
    skipper_id = _positive_ref(raw, _field(status, "character"))
    _need(skipper_id in crew_ids, "Actual skipper is outside the PlayerCrew roster")
    owners = parser.character_owners()
    component_owners = {}
    for owner in owners:
        for component in owner["components"]:
            _need(component["id"] not in component_owners, "Component belongs to multiple actors")
            component_owners[component["id"]] = owner
    for crew_id in crew_ids:
        _need(crew_id in component_owners, "A crew member has no validated full actor owner")
        _need(_body(component_owners[crew_id], PLAYABLE)["component_id"] == crew_id,
              "Crew reference does not identify a PlayableCharacter")
    skipper = component_owners[skipper_id]
    _need(bool(skipper["name"]), "Actual skipper has an empty name")
    actual_data_id, actual_data = parser.plain_ref(_field(_body(skipper, PLAYABLE), "data"), DATA)
    _need(_field(actual_data, "characterType", [13, []])["value"] == 2, "Actual skipper data does not identify a skipper")
    nav_tables = parser.tables(NAV, component=True)
    owner_navs = [_body(o, NAV) for o in owners]
    _need({t["start"] for t in nav_tables} == {t["start"] for t in owner_navs} and len(nav_tables) == len(owner_navs),
          "NavMeshNavigator ownership coverage is incomplete or ambiguous")
    clones = []
    data_ids = set()
    for owner, nav in zip(owners, owner_navs):
        playable = _body(owner, PLAYABLE)
        cid = playable["component_id"]
        layer = _field(nav, "navMeshLayer", [13, []])["value"]
        if cid in crew_ids or cid == skipper_id or layer != 16 or owner["name"] != skipper["name"]:
            continue
        # A matching extra World skipper must meet every invariant. Unexpected
        # actors are refused rather than silently treated as safe candidates.
        _need(owner["pooled"] is False, "Extra skipper is a pooled representation")
        _need(not any(c["type"] == SCENE_OBJECT or c["type"].endswith(".SceneObject") for c in owner["components"]),
              "Extra skipper has a persistent SceneObject identity")
        _need(_field(playable, "isTemporaryRepresentation", [19, []])["value"] is False,
              "Extra skipper is a temporary representation")
        _need(_field(playable, "ignoreSkills", [19, []])["value"] is False,
              "Extra skipper has preview-character flags")
        _need(nav["enabled"] and _field(nav, "agentMode", [13, []])["value"] == 0 and
              _field(nav, "autoNavigatorEnabled", [19, []])["value"] is True,
              "Extra skipper has unsupported navigation state")
        _need(nav["custom_length"] == 28, "Unsupported navigator custom framing")
        _need(all(math.isfinite(v) for v in struct.unpack_from("<7f", raw, nav["custom_start"])),
              "Navigator contains non-finite coordinates")
        data_id, data = parser.plain_ref(_field(playable, "data"), DATA)
        _need(_field(data, "characterType", [13, []])["value"] == 2, "Extra World actor is not a skipper")
        for name in ("entity", "storyCharacterGroup"):
            field = _field(data, name, [0, []])
            _need(field["length"] == 5 and raw[field["offset"]:field["offset"] + 5] == b"\x00" * 5,
                  "Extra skipper has a persistent entity or story association")
        for name in ("enrollmentTicks", "commandStartTicks", "deathTicks"):
            _need(_field(data, name, [15, []])["value"] == 0,
                  "Extra skipper has an enrollment, command, or death history")
        _need(_field(data, "wasSeenUpclose", [19, []])["value"] is False,
              "Extra skipper has an established interaction history")
        _need(data_id != actual_data_id and data_id not in data_ids, "Extra skipper shares its character data")
        data_ids.add(data_id)
        parent_id = parser.world_parent(owner)
        clones.append({"gameobject_id": owner["id"], "playable_character_id": cid,
                       "data_id": data_id, "world_area_gameobject_id": parent_id,
                       "active": owner["active"], "active_offset": owner["active_offset"]})
    active = [c["active_offset"] for c in clones if c["active"]]
    _need(len(set(active)) == len(active), "Repeated patch offset")
    # A campaign object's field can inline large graphs, including unrelated
    # actors. Protect the actual flag and roster/reference bytes, rather than
    # rejecting all actors nested anywhere in the campaign graph.
    skipper_field = _field(crew_table, "skipper")
    protected = [(achievement["length_offset"], achievement["offset"] + achievement["length"]),
                 (roster["length_offset"], roster["offset"] + roster["length"]),
                 (skipper_field["length_offset"], skipper_field["offset"] + skipper_field["length"])]
    for offset in active:
        _need(raw[offset] == 1 and not any(start <= offset < end for start, end in protected),
              "Patch overlaps protected campaign or crew data")
    return {"main_payload_offset": parser.start, "schema_count": len(parser.schemas),
            "crew_count": count, "crew_character_ids": crew_ids,
            "skipper_status_id": status_id, "skipper_character_id": skipper_id,
            "full_character_actor_count": len(owners), "navigator_table_count": len(nav_tables),
            "achievement_lockout": bool(achievement["value"]),
            "achievement_field_offset": achievement["offset"],
            "achievement_field_framing_sha256": _sha(raw[achievement["length_offset"]:achievement["offset"] + 1]),
            "clones_identified": len(clones), "clones_active": len(active),
            "clones_already_inactive": len(clones) - len(active),
            "patch_offsets": active, "clones": clones}


def _stat_record(stat):
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
            "device": stat.st_dev, "inode": stat.st_ino}


def _read_source(path, max_compressed_bytes, max_expanded_bytes):
    source = Path(path).expanduser().resolve(strict=True)
    _need(source.is_file(), "Input is not a regular file")
    _need(isinstance(max_compressed_bytes, int) and max_compressed_bytes > 0, "Compressed-size limit must be positive")
    before = source.stat()
    _need(before.st_size <= max_compressed_bytes, "Compressed save exceeds the size limit")
    with source.open("rb") as stream:
        data = stream.read(max_compressed_bytes + 1)
    after = source.stat()
    _need(len(data) <= max_compressed_bytes, "Compressed save exceeds the size limit")
    _need(_stat_record(before) == _stat_record(after) and len(data) == after.st_size,
          "Input changed while being read")
    raw = _inflate(data, max_expanded_bytes)
    return source, data, raw, _stat_record(after)


def _check_source_stable(source, data, stat):
    _need(_stat_record(source.stat()) == stat, "Input changed during inspection")
    # Also detect an in-place rewrite that deliberately preserved file size and
    # timestamp. Read only; no handles or locks are imposed on the game.
    with source.open("rb") as stream:
        digest = hashlib.sha256()
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    _need(digest.hexdigest() == _sha(data) and _stat_record(source.stat()) == stat,
          "Input changed during inspection")


def _report(source, compressed, raw, stat, analysis):
    return {"tool_version": __version__, "input": str(source), "input_stat": stat,
            "input_sha256": _sha(compressed), "input_compressed_bytes": len(compressed),
            "input_raw_sha256": _sha(raw), "input_expanded_bytes": len(raw),
            "strict_raw_deflate_eof": True, "source_unchanged": True,
            "status": "repair_available" if analysis["clones_active"] else "no_repair_needed",
            "achievement_flag_preserved": True, "crew_preserved": True,
            "output_created": False, **analysis}


def inspect_save(path, *, max_compressed_bytes=DEFAULT_MAX_COMPRESSED_BYTES,
                 max_expanded_bytes=DEFAULT_MAX_EXPANDED_BYTES):
    """Inspect a save read-only; return a JSON-serializable report or raise.

    ``achievement_lockout`` is the saved campaign flag, never an unlock request.
    Unsupported or ambiguous structures raise SaveFormatError.
    """
    source, compressed, raw, stat = _read_source(path, max_compressed_bytes, max_expanded_bytes)
    analysis = _analyze(raw)
    _check_source_stable(source, compressed, stat)
    return _report(source, compressed, raw, stat, analysis)


def _target(path):
    target = Path(path).expanduser().absolute()
    _need(target.parent.is_dir(), "Output parent directory does not exist: " + str(target.parent))
    _need(not os.path.lexists(str(target)), "Refusing to overwrite an existing file: " + str(target))
    return target


def _same_path(a, b):
    return os.path.normcase(str(a.resolve(strict=False))) == os.path.normcase(str(b.resolve(strict=False)))


def _publish_new(path, data):
    """Publish with atomic no-replace semantics; remove only our own temp file."""
    temp = None
    try:
        fd, name = tempfile.mkstemp(prefix=".uboat-fps-", suffix=".partial", dir=str(path.parent))
        temp = Path(name)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(str(temp), str(path))
        except FileExistsError:
            raise SaveFixError("Output appeared during processing; nothing was overwritten: " + str(path))
        except OSError as exc:
            if os.name != "nt":
                raise SaveFixError("Atomic exclusive publication failed: " + str(exc)) from exc
            import ctypes
            move = ctypes.WinDLL("kernel32", use_last_error=True).MoveFileExW
            move.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
            move.restype = ctypes.c_int
            # MOVEFILE_WRITE_THROUGH only; never MOVEFILE_REPLACE_EXISTING.
            if not move(str(temp), str(path), 8):
                raise SaveFixError("Atomic exclusive publication failed (Windows error %d): %s" %
                                   (ctypes.get_last_error(), path)) from exc
            temp = None
    finally:
        if temp is not None:
            try:
                temp.unlink()
            except FileNotFoundError:
                pass


def repair_save(path, output_path=None, *, dry_run=False, json_report=None,
                max_compressed_bytes=DEFAULT_MAX_COMPRESSED_BYTES,
                max_expanded_bytes=DEFAULT_MAX_EXPANDED_BYTES):
    """Inspect, verify and optionally create one NEW repaired copy.

    If no active verified clones exist, no save is written. ``dry_run`` never
    writes a save. An explicitly requested JSON report is an exclusive new file.
    A report failure after publication raises an error identifying the new save;
    the successfully published save is retained.
    """
    source = Path(path).expanduser().resolve(strict=True)
    if output_path is None:
        stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        output = source.with_name(source.stem + "_FPS_FIXED_" + stamp + source.suffix)
    else:
        output = Path(output_path).expanduser().absolute()
    report_path = Path(json_report).expanduser().absolute() if json_report is not None else None
    # Preflight BOTH destinations before any save/report is written, including
    # dry-run and clean-input cases so conflicting requests cannot be mistaken
    # for a successful repair operation.
    _need(not _same_path(source, output), "Input and output must be different files")
    output = _target(output)
    if report_path is not None:
        _need(not _same_path(source, report_path), "JSON report cannot replace the input")
        _need(not _same_path(output, report_path), "JSON report and save output must be different files")
        report_path = _target(report_path)
    source, compressed, raw, stat = _read_source(source, max_compressed_bytes, max_expanded_bytes)
    analysis = _analyze(raw)
    result = _report(source, compressed, raw, stat, analysis)
    result.update(dry_run=bool(dry_run), requested_output=str(output))
    offsets = analysis["patch_offsets"]
    output_data = None
    if offsets and not dry_run:
        patched = bytearray(raw)
        for offset in offsets:
            _need(patched[offset] == 1, "Patch target is not an activeSelf true byte")
            patched[offset] = 0
        expected = bytes(patched)
        # Verify the complete uncompressed file, not a selected subset of fields.
        for offset in offsets:
            patched[offset] = 1
        _need(patched == raw, "Unexpected raw changes outside activeSelf bytes")
        encoder = zlib.compressobj(6, zlib.DEFLATED, -15)
        output_data = encoder.compress(expected) + encoder.flush()
        _need(_inflate(output_data, max_expanded_bytes) == expected, "Recompressed save failed exact round-trip verification")
        flag_offset = analysis["achievement_field_offset"]
        _need(expected[flag_offset] == raw[flag_offset], "Achievement flag changed")
        result.update(output=str(output), output_sha256=_sha(output_data),
                      output_compressed_bytes=len(output_data), output_raw_sha256=_sha(expected),
                      changed_byte_count=len(offsets), exact_raw_diff_verified=True)
    else:
        result.update(changed_byte_count=0, exact_raw_diff_verified=True)
    _check_source_stable(source, compressed, stat)
    if output_data is not None:
        _publish_new(output, output_data)
        result.update(status="repaired_copy_created", output_created=True)
    elif dry_run and offsets:
        result["status"] = "dry_run_repair_available"
    if report_path is not None:
        result["json_report"] = str(report_path)
        try:
            _publish_new(report_path, (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        except (OSError, SaveFixError) as exc:
            if result["output_created"]:
                raise SaveFixError("Repaired save was created at %s, but the JSON report could not be created: %s" %
                                   (output, exc)) from exc
            raise
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description="Create a new UBOAT save copy with verified extra World skipper actors deactivated. Never overwrites files or unlocks achievements.")
    ap.add_argument("input", help="Path to the original .save file (read only)")
    ap.add_argument("--output", help="New output .save path; default: input_FPS_FIXED_<UTC timestamp>.save")
    ap.add_argument("--dry-run", action="store_true", help="Inspect and report; do not create a save")
    ap.add_argument("--json-report", help="Create a new JSON report at this path; existing files are refused")
    ap.add_argument("--max-compressed-mib", type=int, default=256, help="Maximum compressed input size in MiB (default: 256)")
    ap.add_argument("--max-expanded-mib", type=int, default=512, help="Maximum expanded save size in MiB (default: 512)")
    ap.add_argument("--version", action="version", version=__version__)
    args = ap.parse_args(argv)
    try:
        result = repair_save(args.input, args.output, dry_run=args.dry_run, json_report=args.json_report,
                             max_compressed_bytes=args.max_compressed_mib * 1024 * 1024,
                             max_expanded_bytes=args.max_expanded_mib * 1024 * 1024)
    except (SaveFixError, OSError) as exc:
        print("ERROR: " + str(exc), file=sys.stderr)
        return 2
    print("Status: " + result["status"])
    print("Verified crew: %d; extra World skippers: %d active, %d already inactive." %
          (result["crew_count"], result["clones_active"], result["clones_already_inactive"]))
    print("Saved achievement lockout flag: %s (preserved)." % str(result["achievement_lockout"]).lower())
    if result["output_created"]:
        print("Created: " + result["output"])
    if result.get("json_report"):
        print("Report: " + result["json_report"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
