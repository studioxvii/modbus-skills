"""Bounded worksheet-owned comment/callout literals, never engineering claims."""
from __future__ import annotations

import hashlib
import posixpath
import re
from collections.abc import Mapping
from collections import Counter, defaultdict

from .artifacts import stable_input_hash

CODE = "source-worksheet-annotations"
_S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_P = "http://schemas.openxmlformats.org/package/2006/relationships"
_X = "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"
_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_GROUP = {"code", "status", "context_id", "source_sha256", "sheet", "worksheet_member",
          "worksheet_sha256", "entries", "limitations", "bindings"}


class AnnotationError(ValueError):
    """Malformed or unbounded annotation metadata."""


class AnnotationCapacityError(AnnotationError):
    """Only the optional annotation registry exceeds shared capacity."""


def _member(value):
    return (isinstance(value, str) and value.startswith("xl/")
            and posixpath.normpath(value) == value and "\\" not in value
            and not any(part in {"", ".", ".."} for part in value.split("/")))


def _scalar(value):
    from .user_map import UserMapError, _valid_literal_source_value
    if type(value) is not str and type(value) is not int:
        raise AnnotationError("worksheet annotation locators must be text or integers")
    try:
        _valid_literal_source_value("notes", value)
    except UserMapError as exc:
        raise AnnotationError(str(exc)) from exc


def _text(nodes):
    # Bound before joining runs; retain whitespace and explicit DrawingML breaks.
    pieces = []
    size = 0
    for node in nodes:
        literal = "\n" if node.tag == f"{{{_A}}}br" else node.text or ""
        _scalar(literal)
        size += len(literal.encode("utf-8"))
        if size > 16384:
            raise AnnotationError("worksheet annotation exceeds the 16 KiB raw UTF-8 limit")
        pieces.append(literal)
    return "".join(pieces)


def _cost(groups, existing=()):
    """Use the unchanged old counter and provision both OEM/user copies."""
    from . import user_map as u
    used = 0
    group_count = 0
    binding_count = 0
    try:
        for group in existing:
            used += 2 * u._literal_context_size({**group, "bindings": []})
            group_count += 1
            for binding in group["bindings"]:
                used += 2 * u._literal_context_size(binding)
                binding_count += len(group.get("fields", {"literal": None}))
        for group in groups:
            used += 2 * u._literal_context_size({**group, "entries": [], "limitations": [], "bindings": []})
            group_count += 1
            for key in ("entries", "limitations", "bindings"):
                for item in group[key]:
                    used += 2 * u._literal_context_size(item)
                    # Each source literal/limitation and exact row association
                    # consumes one slot; no uncharged nested payload arrays.
                    binding_count += 1
                    if used > u._LITERAL_CONTEXT_BYTES or binding_count > u._LITERAL_CONTEXT_BINDINGS:
                        raise AnnotationCapacityError("worksheet annotation shared evidence budget or binding limit exceeded")
            if group_count > u._LITERAL_CONTEXT_GROUPS or used > u._LITERAL_CONTEXT_BYTES:
                raise AnnotationCapacityError("worksheet annotation shared evidence budget or group limit exceeded")
    except u.LiteralContextCapacityError as exc:
        raise AnnotationCapacityError(str(exc)) from exc
    return used


def _identity(group):
    return "worksheet-context-" + stable_input_hash({k: v for k, v in group.items()
        if k not in {"context_id", "bindings"}})


def read_annotations(archive, sheets, source_sha256, read_xml):
    """Read only explicit worksheet relationships; never follow external links."""
    groups = []
    counts = Counter(archive.namelist())
    duplicates = {name for name, count in counts.items() if count > 1}
    names = set(counts)
    from . import user_map as u
    used = associations = 0
    capacity_reason = None

    def exhaust(reason):
        nonlocal capacity_reason
        if capacity_reason is None:
            capacity_reason = reason
            # No arbitrary prefix survives. Continue source validation using
            # only the current bounded scalar/record, without retaining it.
            for retained in groups:
                retained["entries"].clear()
                retained["limitations"].clear()
            groups.clear()

    def append(group, field, record):
        nonlocal used, associations
        for key, value in record.items():
            _scalar(key)
            _scalar(value)
        if capacity_reason is not None:
            return
        try:
            cost = 2 * u._literal_context_size(record)
        except u.LiteralContextCapacityError as exc:
            exhaust(str(exc))
            return
        used += cost
        associations += 1
        if used > u._LITERAL_CONTEXT_BYTES or associations > u._LITERAL_CONTEXT_BINDINGS:
            exhaust("worksheet annotation evidence budget or binding limit exceeded")
            return
        group[field].append(record)
    for sheet, sheet_member in sheets:
        rel_member = posixpath.join(posixpath.dirname(sheet_member), "_rels", posixpath.basename(sheet_member) + ".rels")
        if rel_member not in names:
            continue
        group = {"code": CODE, "status": "source-context-only", "context_id": "worksheet-context-" + "0" * 64,
                 "source_sha256": source_sha256, "sheet": sheet, "worksheet_member": sheet_member,
                 "worksheet_sha256": hashlib.sha256(archive.read(sheet_member)).hexdigest(),
                 "entries": [], "limitations": [], "bindings": []}
        for key, value in group.items():
            if key not in {"entries", "limitations", "bindings"}:
                _scalar(value)
        if capacity_reason is None:
            groups.append(group)
            try:
                used += 2 * u._literal_context_size(group)
            except u.LiteralContextCapacityError as exc:
                exhaust(str(exc))
            if len(groups) > u._LITERAL_CONTEXT_GROUPS or used > u._LITERAL_CONTEXT_BYTES:
                exhaust("worksheet annotation evidence budget or group limit exceeded")

        def limitation(code, relation, member=rel_member, index=0):
            append(group, "limitations", {"code": code, "member": member,
                "member_sha256": hashlib.sha256(archive.read(member)).hexdigest(),
                "relationship_id": relation, "index": index})

        relations = list(read_xml(archive, rel_member).findall(f"{{{_P}}}Relationship"))
        ids = Counter(r.get("Id") for r in relations)
        worksheet = read_xml(archive, sheet_member)
        drawing_ids = [node.get(f"{{{_R}}}id") for node in worksheet.findall(f"{{{_S}}}drawing")]
        for relation in relations:
            role = relation.get("Type", "")
            if role not in {_R + "/comments", _R + "/drawing"}:
                if role.endswith(("/threadedComment", "/threadedComments", "/vmlDrawing")):
                    limitation("unsupported-annotation-relationship", relation.get("Id", ""))
                continue
            rid = relation.get("Id", "")
            if ids[rid] != 1 or not rid:
                limitation("ambiguous-annotation-relationship", rid)
                continue
            if role == _R + "/drawing" and drawing_ids.count(rid) != 1:
                limitation("unbound-drawing-relationship", rid)
                continue
            target = relation.get("Target", "")
            member = posixpath.normpath(posixpath.join(posixpath.dirname(sheet_member), target))
            if relation.get("TargetMode", "Internal") != "Internal" or ":" in target or target.startswith(("/", "\\")) or not _member(member):
                limitation("external-or-invalid-annotation-relationship", rid)
                continue
            if member not in names or member in duplicates or rel_member in duplicates:
                limitation("missing-or-ambiguous-annotation-part", rid)
                continue
            root = read_xml(archive, member)
            common = {"member": member, "member_sha256": hashlib.sha256(archive.read(member)).hexdigest(),
                      "relationship_id": rid}
            if role == _R + "/comments":
                if root.tag != f"{{{_S}}}comments":
                    limitation("unsupported-comment-part", rid, member)
                    continue
                comments = root.findall(f"{{{_S}}}commentList/{{{_S}}}comment")
                for index, comment in enumerate(comments, 1):
                    cell = comment.get("ref", "")
                    if re.fullmatch(r"[A-Z]{1,3}[1-9][0-9]{0,6}", cell) is None:
                        limitation("unsupported-comment-cell", rid, member, index)
                        continue
                    literal = _text(comment.findall(f"{{{_S}}}text//{{{_S}}}t"))
                    append(group, "entries", {**common, "kind": "comment", "index": index,
                                             "cell": cell, "text": literal})
            else:
                if root.tag != f"{{{_X}}}wsDr":
                    limitation("unsupported-drawing-part", rid, member)
                    continue
                drawing_rels = posixpath.join(posixpath.dirname(member), "_rels", posixpath.basename(member) + ".rels")
                if drawing_rels in names:
                    for linked in read_xml(archive, drawing_rels).findall(f"{{{_P}}}Relationship"):
                        limitation("uninterpreted-drawing-relationship", linked.get("Id", ""), drawing_rels)
                for index, anchor in enumerate(root, 1):
                    kind = anchor.tag.removeprefix(f"{{{_X}}}")
                    shapes = anchor.findall(f"{{{_X}}}sp")
                    if kind not in {"oneCellAnchor", "twoCellAnchor"} or len(shapes) != 1 or any(
                        child.tag not in {f"{{{_X}}}{name}" for name in ("from", "to", "ext", "sp", "clientData")}
                        for child in anchor):
                        limitation("unsupported-drawing-anchor-or-content", rid, member, index)
                        continue
                    placement = {}
                    valid = True
                    for end in ("from", "to") if kind == "twoCellAnchor" else ("from",):
                        nodes = anchor.findall(f"{{{_X}}}{end}")
                        if len(nodes) != 1:
                            valid = False
                            break
                        for field in ("col", "colOff", "row", "rowOff"):
                            values = nodes[0].findall(f"{{{_X}}}{field}")
                            raw = values[0].text if len(values) == 1 else None
                            if raw is None or re.fullmatch(r"[0-9]{1,12}", raw) is None:
                                valid = False
                            else:
                                placement[end + "_" + field] = raw
                    if not valid:
                        limitation("unsupported-drawing-placement", rid, member, index)
                        continue
                    if kind == "oneCellAnchor":
                        extents = anchor.findall(f"{{{_X}}}ext")
                        if len(extents) != 1 or any(re.fullmatch(r"[0-9]{1,12}", extents[0].get(k, "")) is None for k in ("cx", "cy")):
                            limitation("unsupported-drawing-extent", rid, member, index)
                            continue
                        placement.update({"ext_" + k: extents[0].get(k) for k in ("cx", "cy")})
                    paragraphs = shapes[0].findall(f"{{{_X}}}txBody/{{{_A}}}p")
                    if not paragraphs:
                        limitation("drawing-without-text", rid, member, index)
                        continue
                    # Paragraph records retain empty paragraphs and exact order.
                    for paragraph, node in enumerate(paragraphs, 1):
                        literal = _text(n for n in node.iter() if n.tag in {f"{{{_A}}}t", f"{{{_A}}}br"})
                        append(group, "entries", {**common, "kind": "text-callout", "index": index,
                            "anchor_kind": kind, **placement, "paragraph": paragraph, "text": literal})
        if capacity_reason is None:
            if not group["entries"] and not group["limitations"]:
                groups.pop()
            else:
                group["context_id"] = _identity(group)
    if capacity_reason is not None:
        raise AnnotationCapacityError(capacity_reason)
    return groups


def bind_annotations(groups, points, source_sha256, *, existing=(), imported=False, included=None):
    """Validate complete source context before any selected-sheet projection."""
    if not groups:
        return []
    by_sheet = defaultdict(list)
    for point in points:
        for ref in point.get("source_refs", ()):
            if ref.get("format") == "xlsx":
                by_sheet[ref.get("sheet")].append({"oem_point_id": point["oem_point_id"], "source_ref": ref})
    for bindings in by_sheet.values():
        bindings.sort(key=lambda b: (b["oem_point_id"], stable_input_hash(b["source_ref"])))
    # Validate all scalars and schemas before any capacity exception can trigger
    # optional omission. Malformed later input is never hidden by an early cap.
    seen_sheets = set()
    for group in groups:
        if not isinstance(group, Mapping) or set(group) != _GROUP or group.get("code") != CODE or group.get("status") != "source-context-only":
            raise AnnotationError("worksheet annotation registry is malformed")
        if group["source_sha256"] != source_sha256 or not _member(group["worksheet_member"]):
            raise AnnotationError("worksheet annotation source identity is invalid")
        if not isinstance(group["context_id"], str) or re.fullmatch(r"worksheet-context-[0-9a-f]{64}", group["context_id"]) is None:
            raise AnnotationError("worksheet annotation context identity is invalid")
        for key in ("worksheet_sha256", "source_sha256"):
            if not isinstance(group[key], str) or re.fullmatch("[0-9a-f]{64}", group[key]) is None:
                raise AnnotationError("worksheet annotation source digest is invalid")
        _scalar(group["sheet"])
        if not group["sheet"] or group["sheet"] in seen_sheets:
            raise AnnotationError("worksheet annotation sheet identity is duplicate or empty")
        seen_sheets.add(group["sheet"])
        for field in ("entries", "limitations", "bindings"):
            if not isinstance(group[field], list):
                raise AnnotationError("worksheet annotation registry arrays are malformed")
        for record in [*group["entries"], *group["limitations"]]:
            if not isinstance(record, Mapping) or not _member(record.get("member")):
                raise AnnotationError("worksheet annotation locator is invalid")
            for key, value in record.items():
                _scalar(key)
                _scalar(value)
            if re.fullmatch("[0-9a-f]{64}", record.get("member_sha256", "")) is None:
                raise AnnotationError("worksheet annotation member digest is invalid")
        for entry in group["entries"]:
            common = {"member", "member_sha256", "relationship_id", "kind", "index", "text"}
            if entry.get("kind") == "comment":
                allowed = common | {"cell"}
                if not isinstance(entry.get("cell"), str) or re.fullmatch(r"[A-Z]{1,3}[1-9][0-9]{0,6}", entry["cell"]) is None:
                    raise AnnotationError("worksheet annotation comment cell is invalid")
            elif entry.get("kind") == "text-callout":
                allowed = common | {"anchor_kind", "paragraph"} | {"from_"+k for k in ("col", "colOff", "row", "rowOff")}
                if entry.get("anchor_kind") == "twoCellAnchor":
                    allowed |= {"to_"+k for k in ("col", "colOff", "row", "rowOff")}
                elif entry.get("anchor_kind") == "oneCellAnchor":
                    allowed |= {"ext_cx", "ext_cy"}
                else:
                    raise AnnotationError("worksheet annotation anchor is unsupported")
                if type(entry.get("paragraph")) is not int or entry["paragraph"] < 1:
                    raise AnnotationError("worksheet annotation paragraph is invalid")
                for key in allowed - common - {"anchor_kind", "paragraph"}:
                    if not isinstance(entry.get(key), str) or re.fullmatch(r"[0-9]{1,12}", entry[key]) is None:
                        raise AnnotationError("worksheet annotation placement is invalid")
            else:
                raise AnnotationError("worksheet annotation kind is unsupported")
            if set(entry) != allowed or type(entry.get("text")) is not str or type(entry.get("index")) is not int or entry["index"] < 1:
                raise AnnotationError("worksheet annotation entry is malformed")
        for limitation in group["limitations"]:
            if set(limitation) != {"code", "member", "member_sha256", "relationship_id", "index"} or not isinstance(limitation["code"], str):
                raise AnnotationError("worksheet annotation limitation is malformed")
        if imported:
            for binding in group["bindings"]:
                if not isinstance(binding, Mapping) or set(binding) != {"oem_point_id", "source_ref"} or not isinstance(binding["source_ref"], Mapping):
                    raise AnnotationError("worksheet annotation binding is malformed")
                _scalar(binding["oem_point_id"])
                for k, v in binding["source_ref"].items():
                    _scalar(k)
                    _scalar(v)
    _cost(groups, existing)
    bound = []
    for group in groups:
        if group["context_id"] != _identity(group):
            raise AnnotationError("worksheet annotation context identity is invalid")
        expected = by_sheet[group["sheet"]]
        if imported and group["bindings"] != expected or not imported and group["bindings"]:
            raise AnnotationError("worksheet annotation bindings are not the exact OEM worksheet rows")
        bound.append({**group, "bindings": expected})
    _cost(bound, existing)
    if included is None:
        return bound
    return [{**g, "bindings": [b for b in g["bindings"] if b["oem_point_id"] in included]}
            for g in bound if any(b["oem_point_id"] in included for b in g["bindings"])]
