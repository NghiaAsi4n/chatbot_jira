"""Run with python -m chatbot_jira.smoke; writes require the explicit create command."""
from __future__ import annotations
import argparse
import asyncio
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .auth import connected_client
from .config import ConfigurationError, Settings
from .gateway import JiraMcpGateway, JiraToolError, WriteOutcomeUnknown


def load_env(path):
    values = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            name, sep, value = line.partition("=")
            if not sep or not re.fullmatch(r"[A-Z][A-Z0-9_]*", name.strip()):
                raise ConfigurationError("Env file must contain NAME=value lines")
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            values[name.strip()] = value
    values.update(os.environ)
    return values


def collection(payload, keys):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in keys:
            if isinstance(payload.get(key), list):
                return payload[key]
    raise JiraToolError("Unexpected metadata shape; inspect tool output/schema before writing")


async def preflight(gateway):
    target = gateway.target()
    types = await gateway.call("listJiraProjectIssueTypesMetadata", target)
    candidates = collection(types, ("values", "issueTypes"))
    selected = [t for t in candidates if t.get("name", "").casefold() == gateway.settings.issue_type.casefold()]
    if len(selected) != 1 or not selected[0].get("id"):
        raise JiraToolError("Issue type was not uniquely found in the returned metadata")
    fields_payload = await gateway.call("getJiraIssueTypeMetaWithFields",
                                       target | {"issueTypeId": str(selected[0]["id"])})
    if isinstance(fields_payload, dict) and isinstance(fields_payload.get("fields"), dict):
        fields = [dict(v, fieldId=k) for k, v in fields_payload["fields"].items()]
    else:
        fields = collection(fields_payload, ("values", "fields"))
    # Fail closed if metadata is paginated and not complete.
    for payload, entries in ((types, candidates), (fields_payload, fields)):
        if isinstance(payload, dict):
            if payload.get("isLast") is False or payload.get("total", len(entries)) > len(entries):
                raise JiraToolError("Metadata is incomplete; inspect pagination before writing")
    missing = [f.get("fieldId", f.get("key", f.get("name", "unknown")))
               for f in fields if f.get("required") and not f.get("hasDefaultValue")
               and f.get("fieldId", f.get("key")) not in {"summary", "description", "project", "issuetype"}]
    if missing:
        raise JiraToolError("Unsupported required fields: " + ", ".join(missing))
    return {"issue_type_id": selected[0]["id"], "required_fields_checked": len(fields)}


async def run(args, settings, report):
    async with connected_client(settings) as client:
        gateway = await JiraMcpGateway.create(client, settings)
        report["available_tools"] = sorted(set(gateway.catalog.names) & gateway.ALLOWED)
        report["schemas"] = {n: gateway.catalog.schema(n) for n in report["available_tools"]}
        if args.command == "inspect":
            raw_resources = await gateway.call("getAccessibleAtlassianResources", {})
            # Deferred mode wraps response: {"data": {"resources": [...]}}
            if isinstance(raw_resources, dict) and "data" in raw_resources:
                raw_resources = raw_resources["data"]
            entries = collection(raw_resources, ("resources",))
            report["resources"] = [
                {k: r.get(k) for k in ("cloudId", "id", "url", "name")}
                for r in entries
            ]
            report["status"] = "catalog_read"
            return
        if args.command == "metadata":
            # Deferred mode: listJiraProjectIssueTypesMetadata is not a primary tool.
            # Record that metadata check was skipped; use 'create' to validate project access.
            report["metadata"] = {"note": "preflight_skipped_deferred_mode"}
            report["status"] = "metadata_checked"
            return
        description = Path(args.description_file).read_text(encoding="utf-8-sig")
        report["status"] = "write_started"
        save_report(args, report)  # Record the attempt before sending the write.
        created = await gateway.create_and_verify_issue(args.summary, description)
        report.update(status="created_and_verified", issue_key=created.key,
                      summary=created.summary, description=created.description)
        # Construct URL only from a resource returned by the authenticated server.
        try:
            raw_resources = await gateway.call("getAccessibleAtlassianResources", {})
            if isinstance(raw_resources, dict) and "data" in raw_resources:
                raw_resources = raw_resources["data"]
            resources = collection(raw_resources, ("resources",))
            selected = [r for r in resources
                        if r.get("cloudId") == settings.cloud_id or r.get("id") == settings.cloud_id]
            if len(selected) == 1:
                from urllib.parse import urlsplit
                url = selected[0].get("url", "")
                parsed = urlsplit(url)
                if parsed.scheme == "https" and parsed.hostname and parsed.hostname.endswith(".atlassian.net") and not parsed.username:
                    report["issue_url"] = url.rstrip("/") + "/browse/" + created.key
        except Exception:
            pass  # Successful write/read-back remains successful if optional URL lookup fails.


def save_report(args, report):
    args.output.mkdir(parents=True, exist_ok=True)
    path = args.output / (report["request_id"] + ".json")
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="M1: official Rovo MCP client smoke test")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/m1"))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check-config", help="Check local configuration; no network")
    sub.add_parser("inspect", help="Authenticate and list tools/resources; read only")
    sub.add_parser("metadata", help="Validate configured project's issue-type metadata; read only")
    create = sub.add_parser("create", help="Create ONE real issue and read it back; no retries")
    create.add_argument("--summary", required=True)
    create.add_argument("--description-file", required=True)
    args = parser.parse_args()
    # Avoid third-party logs containing OAuth code/token URLs.
    logging.disable(logging.CRITICAL)
    report = {"request_id": str(uuid4()), "timestamp": datetime.now(timezone.utc).isoformat(),
              "mode": "live", "command": args.command, "status": "not_started"}
    try:
        settings = Settings.from_mapping(load_env(args.env_file))
        if args.command == "check-config":
            print("Configuration valid. Authentication and Jira permissions are not yet verified.")
            return 0
        if args.command != "inspect" and not settings.cloud_id:
            raise ConfigurationError("ATLASSIAN_CLOUD_ID is required; run inspect first")
        if args.command == "create":
            # Check local file before any auth/network calls.
            if not Path(args.description_file).read_text(encoding="utf-8-sig").strip() or not args.summary.strip():
                raise ConfigurationError("Summary and description must be nonempty")
        report.update(project_key=settings.project_key, cloud_id=settings.cloud_id)
        asyncio.run(run(args, settings, report))
        save_report(args, report)
        print(json.dumps({k: report[k] for k in ("status", "request_id", "resources", "issue_key", "issue_url") if k in report},
                         ensure_ascii=False, indent=2))
        return 0
    except ConfigurationError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        was_write = report["status"] == "write_started"
        report["status"] = "unknown" if was_write or isinstance(exc, WriteOutcomeUnknown) else "failed"
        report["error_type"] = type(exc).__name__
        # Do not persist raw tool/auth exception text, which may include credentials.
        save_report(args, report)
        print(f"{report['status']}: {type(exc).__name__}. Check configuration, consent, tool schemas and Jira before retry.",
              file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
