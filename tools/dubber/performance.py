"""Source-linked timing and relationship-aware performance authoring."""

import math
import re
from .core import need, safe_id

TIMING_TOLERANCE = 0.25
DEFAULT_REGISTER = {
    "self": "aku",
    "address": "kau",
    "forms_of_address": [],
    "honorifics": [],
}


def check_profile(profile):
    need(
        isinstance(profile, dict)
        and all(
            isinstance(profile.get(k), str) and profile[k].strip()
            for k in ["self", "address"]
        ),
        "Register needs explicit self/address pronouns",
    )
    need(
        all(
            isinstance(profile.get(k), list)
            and all(isinstance(x, str) and x.strip() for x in profile[k])
            for k in ["forms_of_address", "honorifics"]
        ),
        "Register address/honorific lists must contain nonempty strings",
    )


def check_character_register(character, default):
    register = character.get("register", {"default": default, "relationships": []})
    check_profile(register["default"])
    seen = set()
    for relationship in register.get("relationships", []):
        target = safe_id(relationship["addressee_id"])
        need(target not in seen, "Duplicate relationship-specific register")
        seen.add(target)
        check_profile(relationship)
        need(
            bool(relationship.get("notes", "").strip()),
            "Relationship register needs contextual notes",
        )


def pronunciation_notes(project, text):
    entries = project.config.get("pronunciations", [])
    need(
        isinstance(entries, list),
        "Pronunciations must be a list of term/spoken_form records",
    )
    seen = set()
    notes = []
    for entry in entries:
        need(
            all(
                isinstance(entry.get(k), str) and entry[k].strip()
                for k in ["term", "spoken_form"]
            ),
            "Pronunciation needs term and spoken_form",
        )
        term = entry["term"].casefold()
        need(term not in seen, "Duplicate pronunciation term")
        seen.add(term)
        if re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text.casefold()):
            notes.append(f"Pronounce {entry['term']} as {entry['spoken_form']}.")
    return notes


def timing_report(script, units):
    by_scene = {scene: {u["id"]: u for u in values} for scene, values in units.items()}
    uses = {}
    reports = []
    splits = {}
    for line in script["lines"]:
        sid = line["scene"]
        refs = line["source_ids"]
        need(
            len(refs) == len(set(refs)),
            "Repeated source id within a single performance",
        )
        need(
            all(ref in by_scene[sid] for ref in refs),
            "Performance cites an unknown source unit",
        )
        declared = {}
        for part in line.get("source_splits", []):
            ref = part["source_id"]
            need(
                ref in refs and ref not in declared,
                "Split declaration must identify one cited source unit",
            )
            unit = by_scene[sid][ref]
            start, end = part["start"], part["end"]
            need(
                all(
                    isinstance(t, (int, float)) and math.isfinite(t)
                    for t in [start, end]
                )
                and unit["start"] <= start < end <= unit["end"],
                "Declared source split lies outside its unit",
            )
            need(
                all(
                    isinstance(part.get(k), int) and not isinstance(part[k], bool)
                    for k in ["part", "parts"]
                )
                and 1 <= part["part"] <= part["parts"]
                and part["parts"] >= 2,
                "Source split needs valid part/parts numbers",
            )
            need(
                bool(part.get("reason", "").strip())
                and bool(part.get("evidence", "").strip()),
                "Source split needs a reason and source evidence",
            )
            declared[ref] = part
        windows = []
        for ref in refs:
            unit = declared.get(ref, by_scene[sid][ref])
            windows.append(
                {"source_id": ref, "start": unit["start"], "end": unit["end"]}
            )
            uses.setdefault((sid, ref), []).append(line["id"])
            if ref in declared:
                splits[(line["id"], ref)] = declared[ref]
        start = min(w["start"] for w in windows)
        end = max(w["end"] for w in windows)
        significant = (
            max(abs(line["start"] - start), abs(line["end"] - end)) > TIMING_TOLERANCE
        )
        adjustment = line.get("timing_adjustment", {})
        need(
            not significant
            or bool(adjustment.get("reason", "").strip())
            and bool(adjustment.get("evidence", "").strip()),
            f"{line['id']}: performance timing differs from cited source units by more than {TIMING_TOLERANCE}s; record timing_adjustment reason and evidence",
        )
        reports.append(
            {
                "id": line["id"],
                "scene": sid,
                "source_windows": windows,
                "derived_start": start,
                "derived_end": end,
                "performance_start": line["start"],
                "performance_end": line["end"],
                "significant_shift": significant,
                "adjustment": adjustment,
            }
        )
    for (sid, ref), line_ids in uses.items():
        parts = [splits.get((line_id, ref)) for line_id in line_ids]
        if len(line_ids) == 1:
            need(
                parts[0] is None,
                "A declared source split is missing its other performances",
            )
            continue
        need(
            all(parts),
            f"Source unit {ref} is voiced more than once; use explicit source_splits or split the corrected source unit",
        )
        need(
            all(p["parts"] == len(parts) for p in parts)
            and [p["part"] for p in parts] == list(range(1, len(parts) + 1)),
            "Source split part numbering does not match its chronological performances",
        )
        need(
            all(a["end"] <= b["start"] for a, b in zip(parts, parts[1:])),
            "Source split parts overlap",
        )
        unit = by_scene[sid][ref]
        need(
            abs(parts[0]["start"] - unit["start"]) <= TIMING_TOLERANCE
            and abs(parts[-1]["end"] - unit["end"]) <= TIMING_TOLERANCE,
            "Source split does not account for the full unit; correct source boundaries/omissions first",
        )
    # Source simultaneity cannot become serial delivery just by moving each
    # boundary within the individual timing tolerance.
    for scene in by_scene:
        windows = sorted(
            (round(w["start"] * 48000), round(w["end"] * 48000), report["id"])
            for report in reports
            if report["scene"] == scene
            for w in report["source_windows"]
        )
        active = []
        for start, end, line_id in windows:
            active = [item for item in active if item[1] > start]
            need(
                not any(item[2] != line_id for item in active),
                f"Known source overlap in {scene} includes {line_id}; simultaneous lanes are unsupported. Correct inaccurate source bounds or resolve treatment; do not serialize simultaneous performances.",
            )
            active.append((start, end, line_id))
    return {"tolerance_seconds": TIMING_TOLERANCE, "performances": reports}


def event_plan(script, scenes, final=False):
    events = script.get("performance_events", [])
    seen = set()
    lines = {l["id"]: l for l in script["lines"]}
    for event in events:
        safe_id(event["id"])
        group = event.get("character_ids", [])
        need(isinstance(group, list), "Group performer identities must be a list")
        need(
            not group
            or not event.get("character_id")
            and len(group) >= 2
            and len(group) == len(set(group)),
            "A group event needs distinct character_ids and an empty single character_id",
        )
        need(
            all(
                c in {character["id"] for character in script["characters"]}
                for c in group
            ),
            "Group event names an unknown performer identity",
        )
        if event.get("character_id"):
            need(
                event["character_id"] in {c["id"] for c in script["characters"]},
                "Performance event names an unknown character; use an empty identity while uncertain",
            )
        need(event["id"] not in seen, "Duplicate performance event id")
        seen.add(event["id"])
        need(
            event["scene"] in scenes,
            "Performance event points to an unknown/passthrough scene",
        )
        scene = scenes[event["scene"]]
        end = (
            scene.get("sample_count", round((scene["end"] - scene["start"]) * 48000))
            / 48000
        )
        need(
            all(
                isinstance(t, (int, float)) and math.isfinite(t)
                for t in [event["start"], event["end"]]
            )
            and 0 <= event["start"] < event["end"] <= end + 0.01,
            "Performance event is out of scene bounds",
        )
        need(
            event["kind"]
            in {
                "overlap",
                "interruption",
                "shout",
                "laughter",
                "gasp",
                "exertion",
                "other",
            },
            "Unknown performance event kind",
        )
        need(
            event["decision"] in {"unresolved", "handled_as_line", "omit"},
            "Unsupported event treatment; original mixed dialogue must not be patched back in",
        )
        need(
            bool(event.get("reason", "").strip())
            and bool(event.get("evidence", "").strip()),
            "Performance events need a reason and source evidence",
        )
        refs = event.get("line_ids", [])
        if event["decision"] == "handled_as_line":
            need(
                event["kind"] != "overlap",
                "An overlap event cannot be handled as serial lines while simultaneous lanes are unsupported",
            )
            need(
                refs
                and all(
                    r in lines
                    and lines[r]["scene"] == event["scene"]
                    and lines[r]["start"] < event["end"] + TIMING_TOLERANCE
                    and lines[r]["end"] > event["start"] - TIMING_TOLERANCE
                    for r in refs
                ),
                "Event line references must overlap its observed source interval",
            )
            performers = set(group) or (
                {event["character_id"]} if event.get("character_id") else set()
            )
            need(
                not performers
                or {lines[r]["character_id"] for r in refs} == performers,
                "Handled event lines must match every known performer identity; another character's dialogue cannot handle this event",
            )
        else:
            need(
                not refs,
                "Unresolved/omitted events cannot also cite rendered performances",
            )
        need(
            not final or event["decision"] != "unresolved",
            f"{event['id']}: resolve this vocal effort/overlap before approving the selected scene; do not silently drop or serialize it",
        )
    return {
        "events": events,
        "overlap_mixer_supported": False,
        "policy": "Serial performed lines or evidenced omission; unresolved selected events block review.",
    }
