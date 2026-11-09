"""Preview and apply a private installation profile through the local admin API."""

import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
STARTER = ROOT / "backend" / "starter_content"


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class AdminAPI:
    def __init__(self, origin):
        parts = urlsplit(origin)
        if (
            parts.scheme != "http"
            or parts.hostname != "127.0.0.1"
            or parts.username is not None
            or parts.password is not None
            or not parts.port
            or parts.path
            or parts.query
            or parts.fragment
        ):
            raise ValueError("Use http://127.0.0.1:PORT on the server or via an SSH tunnel")
        self.origin = origin
        self.opener = build_opener(ProxyHandler({}), NoRedirects())
        self.token = None

    def request(self, method, path, body=None, *, json_body=False):
        headers = {}
        data = None
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        if body is not None:
            headers["Content-Type"] = "application/json" if json_body else "application/x-www-form-urlencoded"
            data = (json.dumps(body) if json_body else urlencode(body)).encode("utf-8")
        try:
            with self.opener.open(Request(self.origin + path, data, headers, method=method), timeout=30) as response:
                return json.load(response)
        except HTTPError as exc:
            raise ValueError(f"{method} {path}: HTTP {exc.code}") from None
        except URLError:
            raise ValueError(f"{method} {path}: instance unreachable") from None

    def login(self):
        password = os.environ.get("ASSISTANT_ADMIN_PASSWORD") or getpass.getpass("Assistant admin password: ")
        self.token = self.request(
            "POST", "/api/auth/login", {"username": "admin", "password": password}, json_body=True
        )["token"]


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def inventory(manifest):
    marker = re.compile(r"example\.test|\[[А-ЯЁ][А-ЯЁ /-]{6,}\]")
    for kind in ("modes", "skills"):
        for entry in manifest[kind]:
            path = STARTER / entry["file"]
            body = path.read_text(encoding="utf-8")
            lines = [str(i) for i, line in enumerate(body.splitlines(), 1) if marker.search(line)]
            print(f"{kind}: {entry.get('id', entry.get('name'))} | {entry['file']} | markers: {','.join(lines) or 'none'}")


def private_body(profile_dir, relative):
    if not isinstance(relative, str) or Path(relative).is_absolute():
        raise ValueError("Profile entries must be relative file paths")
    path = (profile_dir / relative).resolve()
    if not path.is_relative_to(profile_dir) or not path.is_file() or path.suffix.lower() != ".md":
        raise ValueError("Every profile entry must point to a Markdown file inside the private profile directory")
    if path.stat().st_size > 512 * 1024:
        raise ValueError("Profile entry exceeds 512 KiB")
    body = path.read_text(encoding="utf-8-sig").strip()
    if not body:
        raise ValueError("Profile entry is empty")
    return body


def build_plan(api, manifest, profile_path):
    if profile_path.parent.is_relative_to(ROOT):
        raise ValueError("Keep the installation profile outside this repository")
    profile = read_json(profile_path)
    if not isinstance(profile, dict) or set(profile) != {"modes", "skills"}:
        raise ValueError("Profile must contain modes and skills objects")
    if not all(isinstance(profile[key], dict) for key in profile):
        raise ValueError("Profile must contain modes and skills objects")
    starter_modes = {entry["id"] for entry in manifest["modes"]}
    starter_skills = {entry["name"]: entry for entry in manifest["skills"]}
    modes = {row["mode"]: row for row in api.request("GET", "/api/admin/modes")}
    skills = api.request("GET", "/api/skills")
    plan = []
    for mode, filename in profile["modes"].items():
        if mode not in starter_modes or mode not in modes:
            raise ValueError(f"Unsupported or missing mode: {mode}")
        body = private_body(profile_path.parent, filename)
        row = modes[mode]
        if row["instruction"].strip() != body:
            plan.append(("PUT", f"/api/admin/modes/{mode}", mode, row, {"instruction": body}))
    for name, filename in profile["skills"].items():
        if name not in starter_skills:
            raise ValueError(f"Unknown starter skill: {name}")
        body = private_body(profile_path.parent, filename)
        matches = [row for row in skills if row["name"] == name]
        if len(matches) > 1:
            raise ValueError(f"Resolve duplicate skills before continuing: {name}")
        row = matches[0] if matches else None
        if row is not None and row["content"].strip() == body:
            continue
        entry = row or starter_skills[name]
        scenarios = entry.get("scenario_ids")
        form = {
            "name": name,
            "content": body,
            "mode": entry["mode"],
            "time_category": entry.get("time_category") or "",
            "scenario_ids": json.dumps(scenarios, ensure_ascii=False) if isinstance(scenarios, list) else scenarios or "",
            "activation_condition": entry["activation_condition"] or "always",
            "priority": entry["priority"],
        }
        if row is None:
            plan.append(("POST", "/api/skills", name, None, form))
        else:
            plan.append(("PUT", f"/api/skills/{row['id']}", name, row, form))
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("inventory", "plan", "apply"))
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--approved-plan")
    args = parser.parse_args()
    manifest = read_json(STARTER / "manifest.json")
    if args.action == "inventory":
        inventory(manifest)
        return
    if not args.profile or (args.action == "apply" and not args.approved_plan):
        parser.error("plan requires --profile; apply also requires --approved-plan")
    api = AdminAPI(args.url)
    api.login()
    profile = args.profile.resolve()
    plan = build_plan(api, manifest, profile)
    digest = hashlib.sha256(json.dumps([api.origin, plan], sort_keys=True).encode("utf-8")).hexdigest()
    for method, _, label, _, _ in plan:
        print(f"{'Create' if method == 'POST' else 'Update'}: {label}")
    print(f"Changes: {len(plan)} | Plan: {digest}")
    if args.action == "apply":
        if args.approved_plan != digest:
            raise ValueError("Plan changed. Review a new plan before applying")
        for method, path, label, _, form in plan:
            api.request(method, path, form)
            print(f"Applied: {label}", flush=True)
        if build_plan(api, manifest, profile):
            raise ValueError("Read-back verification found unapplied changes")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(f"Setup error: {exc}", file=sys.stderr)
        sys.exit(1)
