"""
generate_cv_standalone.py — Data-dumper for the CV generation skill.

Two modes:

  --mode dump   (default)
      Fetch job markdown via VPS Firecrawl + profile from VPS postgres.
      Print both as JSON to stdout. Claude Code reads this output,
      reasons in context, writes the CV sections, then calls --mode save.

  --mode save
      Receive pre-written CV sections JSON from Claude Code (via --sections-json).
      Call cv_generator.generate_cv() to format and save TXT + PDF.
      Print file paths as JSON.

Usage:
    python generate_cv_standalone.py --profile <id> --job-url <url>
    python generate_cv_standalone.py --profile <id> --job-url <url> --mode save --sections-json '<json>'
    python generate_cv_standalone.py --profile <id> --job-url <url> --cover-letter
"""
import argparse
import json
import sys
import urllib.request
from pathlib import Path

CB_DIR  = Path(__file__).resolve().parent.parent
CV_DIR  = CB_DIR / "cvs"
VPS_URL = "http://localhost:8713/mcp"

sys.path.insert(0, str(CB_DIR))

_seq = 0


def _mcp_call(tool: str, **kwargs) -> dict:
    global _seq
    _seq += 1
    body = json.dumps({
        "jsonrpc": "2.0", "id": _seq,
        "method":  "tools/call",
        "params":  {"name": tool, "arguments": kwargs},
    }).encode()
    req = urllib.request.Request(
        VPS_URL, data=body, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            resp = json.loads(r.read())
        content = resp["result"]["content"][0]["text"]
        return json.loads(content)
    except Exception as e:
        return {"error": str(e)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile",       required=True, help="Profile ID")
    parser.add_argument("--job-url",       required=True, help="Job posting URL")
    parser.add_argument("--mode",          default="dump",
                        choices=["dump", "save"],
                        help="dump: fetch and print data | save: format sections to PDF")
    parser.add_argument("--sections-json", default="",
                        help="JSON string of CV sections (for --mode save)")
    parser.add_argument("--cover-letter",  action="store_true")
    parser.add_argument("--job-id",        default="",
                        help="Optional job ID for filename")
    args = parser.parse_args()

    if args.mode == "dump":
        # Fetch job markdown and profile — dump for Claude Code to reason over
        profile = _mcp_call("get_profile", profile_id=args.profile)
        if "error" in profile:
            print(json.dumps({"error": f"Profile not found: {profile['error']}"}))
            sys.exit(1)

        job_content = _mcp_call("firecrawl_scrape", url=args.job_url)
        if "error" in job_content and not job_content.get("markdown"):
            print(json.dumps({"error": f"Could not fetch job page: {job_content['error']}"}))
            sys.exit(1)

        print(json.dumps({
            "mode":        "dump",
            "profile":     profile,
            "job_url":     args.job_url,
            "job_markdown": job_content.get("markdown", ""),
            "job_metadata": job_content.get("metadata", {}),
            "source":      job_content.get("source", "unknown"),
            "instructions": (
                "Read job_markdown and profile above. "
                "Extract requirements, map to profile, write CV sections. "
                "Then call: python generate_cv_standalone.py "
                f"--profile {args.profile} --job-url '{args.job_url}' "
                "--mode save --sections-json '<your_sections_json>'"
            ),
        }, indent=2))

    elif args.mode == "save":
        if not args.sections_json:
            print(json.dumps({"error": "--sections-json required for --mode save"}))
            sys.exit(1)

        try:
            sections = json.loads(args.sections_json)
        except Exception as e:
            print(json.dumps({"error": f"Invalid --sections-json: {e}"}))
            sys.exit(1)

        profile = _mcp_call("get_profile", profile_id=args.profile)
        if "error" in profile:
            print(json.dumps({"error": f"Profile not found: {profile['error']}"}))
            sys.exit(1)

        job_meta = {
            "id":      args.job_id or "custom",
            "title":   sections.get("job_title", ""),
            "company": sections.get("company", ""),
        }

        CV_DIR.mkdir(parents=True, exist_ok=True)

        from cv_generator import generate_cv, generate_cover_letter

        cv = generate_cv(sections, profile, job_meta, out_dir=str(CV_DIR))
        result = {"cv": cv, "profile_id": args.profile, "job_url": args.job_url}

        if args.cover_letter:
            cover_text = sections.get("cover_letter_text", "")
            cl = generate_cover_letter(profile, job_meta,
                                       cover_text=cover_text,
                                       out_dir=str(CV_DIR))
            result["cover_letter"] = cl

        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
