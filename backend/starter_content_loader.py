"""Load editable starter instructions and skills for new instances."""

import json
from pathlib import Path
from uuid import uuid4

_CONTENT_DIR = Path(__file__).with_name("starter_content")


def _manifest():
    return json.loads((_CONTENT_DIR / "manifest.json").read_text(encoding="utf-8"))


def _body(entry):
    return (_CONTENT_DIR / entry["file"]).read_text(encoding="utf-8").strip()


def load_mode_instructions():
    return {
        entry["id"]: {"label": entry["label"], "instruction": _body(entry)}
        for entry in _manifest()["modes"]
    }


async def seed_skills(db):
    for entry in _manifest()["skills"]:
        scenario_ids = entry["scenario_ids"]
        await db.execute(
            """
            INSERT INTO skills
                (id, name, content, mode, is_active, scenario_ids,
                 activation_condition, priority, updated_by)
            SELECT ?, ?, ?, ?, 1, ?, ?, ?, 'system'
            WHERE NOT EXISTS (SELECT 1 FROM skills WHERE name = ?)
            """,
            (
                str(uuid4()), entry["name"], _body(entry), entry["mode"],
                json.dumps(scenario_ids) if scenario_ids else None,
                entry["activation_condition"], entry["priority"], entry["name"],
            ),
        )
