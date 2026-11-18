import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(".")
ALLOWED = re.compile(r"^frontend/.*\.(html|css|js)$", re.I)

def run(*cmd, cwd=None, check=True):
    return subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, check=check)

def recent_commit_subjects():
    result = subprocess.run(
        ["git", "log", "origin/main", "-n", "40", "--format=%s"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        return "(recent history unavailable)"
    return result.stdout.strip()[:12000] or "(no recent commits)"

def snapshot():
    files = []
    frontend = ROOT / "frontend"
    priority = [
        frontend / "app.html",
        frontend / "assets/js/app-page-actions.js",
        frontend / "assets/js/milan-ui-actions.js",
        frontend / "assets/css/app.css",
        frontend / "assets/css/app-layout.css",
        frontend / "assets/css/milan-visual-engine.css",
    ]
    seen = set()

    for p in priority:
        if p.is_file():
            seen.add(p)
            text = p.read_text(encoding="utf-8", errors="ignore")
            files.append(f"\n===== {p.relative_to(ROOT)} =====\n{text[:12000]}")

    for p in sorted(frontend.rglob("*")):
        if p in seen or not p.is_file():
            continue
        rel = str(p.relative_to(ROOT))
        if not ALLOWED.match(rel):
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        files.append(f"\n===== {rel} =====\n{text[:5000]}")
        if sum(len(x) for x in files) > 85000:
            break

    return "".join(files)[:85000]

PROMPT = """You are the autonomous UI engineer for MILAN.
Repository: Scrupulous-boy/MILAN
Only improve the production frontend.

Rules:
- Make ONE small, meaningful UI/UX improvement per cycle.
- Only modify files under frontend/ ending in .html, .css, or .js.
- Never modify backend, auth, DWN, database, secrets, package files, deployment config, or infrastructure.
- Preserve all existing APIs and functionality.
- No giant refactors, rewrites, or formatting-only changes.
- Prefer targeted UX, accessibility, responsiveness, visual polish, performance, or interaction improvements.
- Before choosing the change, inspect the current frontend and identify the next concrete user-facing improvement opportunity.
- Inspect recent git history/diff context when available so you do not repeat a recently completed improvement.
- Prefer a different UI area each cycle when a safe opportunity exists: buttons, typography, spacing, cards, forms, navigation, responsive behavior, empty states, loading states, accessibility, visual hierarchy, or interaction feedback.
- Prioritize real user-facing improvements over arbitrary cosmetic changes.
- Do not repeatedly change the same selector, component, text, or spacing unless it still clearly needs improvement.
- If no safe meaningful improvement exists, return exactly NO_CHANGE.
- Return a short human-readable change summary in <summary>...</summary>.
- Then return the unified git diff enclosed in <patch>...</patch>.
- The summary must describe exactly what UI/UX was improved, e.g. "Improve publish button spacing".
- commit_title must be a NEW, specific, human-readable title for THIS exact UI change.
- commit_title must be unique against the recent commit history supplied below.
- Never reuse a recent commit title, generic wording, or "Improve MILAN UI".
- Do not return NO_CHANGE unless a safe UI change is genuinely impossible.
- The patch must apply cleanly to the current files.
"""

body_base = {
    "input": (
        PROMPT
        + "\n\nRECENT MILAN COMMIT SUBJECTS (DO NOT REUSE THESE TITLES):\n"
        + recent_commit_subjects()
        + "\n\nCURRENT MILAN FRONTEND:\n"
        + snapshot()
    ),
    "max_output_tokens": 6000,
}

models = [
    os.getenv("OPENAI_MODEL", "gpt-5.6-luna"),
    "gpt-6-luna",
]

GEMINI_MODELS = [
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
]

def ask_gemini(model, prompt=None):
    body = {
        "contents": [{"parts": [{"text": prompt or body_base["input"]}]}],
        "generationConfig": {
            "maxOutputTokens": body_base["max_output_tokens"],
            "thinkingConfig": {"thinkingLevel": "medium"},
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "object",
                "properties": {
                    "decision": {
                        "type": "string",
                        "enum": ["CHANGE", "NO_CHANGE"]
                    },
                    "summary": {"type": "string"},
                    "commit_title": {"type": "string"},
                    "patch": {"type": "string"}
                },
                "required": ["decision", "summary", "commit_title", "patch"]
            },
        },
    }

    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "x-goog-api-key": os.environ["GEMINI_API_KEY"].strip(),
            "Content-Type": "application/json",
        },
        method="POST",
    )

    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=180) as response:
                return json.load(response)

        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            print(f"Gemini {model} HTTP {e.code} attempt {attempt + 1}/3: {raw[:1200]}")

            # Quota exhausted for this model: immediately try another model.
            if e.code == 429:
                raise RuntimeError(f"Gemini {model} quota exhausted") from e

            # Temporary capacity/server failure: retry this model.
            if e.code in (500, 502, 503, 504) and attempt < 2:
                time.sleep(5 * (2 ** attempt))
                continue

            raise RuntimeError(
                f"Gemini {model} HTTP {e.code}: {raw[:2000]}"
            ) from e

    raise RuntimeError(f"Gemini {model} retry loop exhausted")

def ask_openrouter():
    body = {
        "model": "openrouter/free",
        "messages": [
            {
                "role": "user",
                "content": body_base["input"] + """

OUTPUT CONTRACT:
Return ONLY JSON matching the required schema.
decision = CHANGE only when you can make one safe, meaningful frontend UI/UX improvement.
summary must be a precise human-readable description of the actual UI change.
commit_title must be a concise conventional-commit-style title describing the actual UI change, without a prefix such as feat(ui):.
patch must be a valid unified git diff that applies cleanly to the current MILAN frontend.
Use NO_CHANGE only when no safe meaningful UI improvement is possible.
"""
            }
        ],
        "max_tokens": body_base["max_output_tokens"],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "milan_ui_update",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "decision": {"type": "string", "enum": ["CHANGE", "NO_CHANGE"]},
                        "summary": {"type": "string"},
                        "commit_title": {"type": "string"},
                        "patch": {"type": "string"}
                    },
                    "required": ["decision", "summary", "commit_title", "patch"],
                    "additionalProperties": False
                }
            }
        }
    }

    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": "Bearer " + os.environ["OPENROUTER_API_KEY"].strip(),
            "Content-Type": "application/json",
            "HTTP-Referer": "https://milanlife.in",
            "X-Title": "MILAN Live UI Update",
        },
        method="POST",
    )

    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=180) as response:
                return json.load(response)
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            print(f"OpenRouter HTTP {e.code} attempt {attempt + 1}/3: {raw[:1200]}")
            if e.code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(5 * (2 ** attempt))
                continue
            raise RuntimeError(f"OpenRouter HTTP {e.code}: {raw[:2000]}") from e

    raise RuntimeError("OpenRouter retry loop exhausted")

def ask(model):
    body = dict(body_base)
    body["model"] = model

    req = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": "Bearer " + os.environ["OPENAI_API_KEY"],
            "Content-Type": "application/json",
        },
        method="POST",
    )

    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=180) as response:
                return json.load(response)
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            print(f"OPENAI_HTTP_{e.code}: {raw[:800]}")
            if e.code != 429 or attempt == 1:
                raise
            time.sleep(20)

    raise RuntimeError("OpenAI request failed")

data = None
selected_provider = None
selected_model = None

if os.environ.get("OPENROUTER_API_KEY"):
    try:
        print("Trying model: openrouter/free")
        data = ask_openrouter()
        selected_provider = "openrouter"
        selected_model = "openrouter/free"
        print("OpenRouter model selected: openrouter/free")
    except Exception as e:
        print(f"OpenRouter unavailable: {type(e).__name__}: {e}")

if data is None and os.environ.get("GEMINI_API_KEY"):
    for gemini_model in GEMINI_MODELS:
        try:
            print(f"Trying model: {gemini_model}")
            data = ask_gemini(gemini_model)
            selected_provider = "gemini"
            selected_model = gemini_model
            print(f"Gemini model selected: {gemini_model}")
            break
        except Exception as e:
            print(f"Gemini unavailable ({gemini_model}): {type(e).__name__}: {e}")

if data is None and os.environ.get("OPENAI_API_KEY"):
    for openai_model in models:
        try:
            print(f"Trying OpenAI model: {openai_model}")
            data = ask(openai_model)
            selected_provider = "openai"
            selected_model = openai_model
            print(f"OpenAI model selected: {openai_model}")
            break
        except Exception as e:
            print(f"OpenAI unavailable ({openai_model}): {type(e).__name__}: {e}")

if data is None:
    print("AI unavailable this cycle; safely skipping code generation.")
    sys.exit(0)

def response_text(response):
    parts = []

    for choice in response.get("choices", []):
        message = choice.get("message", {})
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])

    if isinstance(response.get("output_text"), str):
        parts.append(response["output_text"])

    for item in response.get("output", []):
        for content in item.get("content", []):
            if isinstance(content, dict) and isinstance(content.get("text"), str):
                parts.append(content["text"])

    for candidate in response.get("candidates", []):
        content = candidate.get("content", {})
        for part in content.get("parts", []):
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])

    return "\n".join(parts).strip()


def response_payload(response):
    raw = response_text(response)
    try:
        json_text = raw
        fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, re.S)
        if fenced:
            json_text = fenced.group(1)
        payload = json.loads(json_text)
        return payload if isinstance(payload, dict) else {}
    except (TypeError, ValueError):
        return {}


payload = response_payload(data)
decision = str(payload.get("decision", "")).strip().upper()
summary = str(payload.get("summary", "")).strip()
commit_title = str(payload.get("commit_title", "")).strip()
patch = str(payload.get("patch", "")).strip()

if decision == "NO_CHANGE":
    print("NO_CHANGE")
    sys.exit(0)

recent_titles = []
try:
    recent_titles = [
        line.strip()
        for line in recent_commit_subjects().splitlines()
        if line.strip()
    ]
except Exception:
    recent_titles = []

if not commit_title:
    if summary:
        commit_title = summary
        print("AI omitted commit_title; using the AI-generated change summary as the commit title.")
    else:
        print("AI did not provide a usable commit title or summary; rejecting this attempt.")
        sys.exit(0)

if commit_title.lower() in {
    "improve milan ui",
    "improve ui",
    "ui update",
    "update ui",
    "milAN ui update".lower(),
}:
    print("AI returned a generic commit_title; rejecting this attempt:", commit_title)
    sys.exit(0)

commit_title = re.sub(
    r"^(?:feat|fix|chore)\(ui\):\s*",
    "",
    commit_title,
    flags=re.I,
).strip()

normalized_recent = set()
for subject in recent_titles:
    subject = re.sub(r"^(?:feat|fix|chore)\(ui\):\s*", "", subject, flags=re.I)
    subject = re.sub(r"^\[cycle-[0-9]+\]\s*", "", subject)
    normalized_recent.add(subject.strip().lower())

if commit_title.strip().lower() in normalized_recent:
    print("DUPLICATE AI COMMIT TITLE REJECTED:", commit_title)
    sys.exit(0)

if not summary or not patch:
    summary_match = re.search(r"<summary>\s*(.*?)\s*</summary>", text, re.S)
    summary = summary_match.group(1).strip() if summary_match else ""

    match = re.search(r"<patch>\s*(.*?)\s*</patch>", text, re.S)
    patch = match.group(1).strip() if match else ""

if not summary or summary == "Improve MILAN UI":
    print("AI did not provide a specific UI summary; skipping commit.")
    sys.exit(0)

if not patch:
    print("AI returned no valid patch; skipping safely.")
    sys.exit(0)

if patch.startswith("```") and patch.endswith("```"):
    patch = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", patch)
    patch = re.sub(r"\s*```$", "", patch).strip()

patch_file = Path("/tmp/milan-ai.patch")
patch_file.write_text(patch + "\n", encoding="utf-8")

def target_file_context(candidate_patch):
    paths = sorted(set(
        re.findall(r"^\+\+\+ b/(frontend/[^\r\n]+)$", candidate_patch, re.M)
        + re.findall(r"^--- a/(frontend/[^\r\n]+)$", candidate_patch, re.M)
    ))
    chunks = []
    for rel in paths[:3]:
        if not ALLOWED.match(rel):
            continue
        target = ROOT / rel
        if not target.is_file():
            continue
        content = target.read_text(encoding="utf-8", errors="ignore")
        if len(content) > 26000:
            content = content[:13000] + "\n...[middle omitted; use the exact surrounding context from the supplied source]...\n" + content[-13000:]
        chunks.append(f"\n===== CURRENT EXACT FILE: {rel} =====\n{content}")
    return "\n".join(chunks)


def check_patch(candidate_patch):
    patch_file.write_text(candidate_patch.rstrip() + "\n", encoding="utf-8")
    return subprocess.run(
        ["git", "apply", "--check", "--whitespace=error", str(patch_file)],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )


check = check_patch(patch)
for repair_attempt in range(1, 3):
    if check.returncode == 0:
        break

    print(f"Patch validation failed; requesting Gemini repair {repair_attempt}/2.")
    if selected_provider != "gemini" or not selected_model:
        break

    repair_prompt = (
        body_base["input"]
        + "\n\nTHE PREVIOUS PATCH FAILED VALIDATION. DO NOT REPEAT IT.\n"
        + "git apply --check output:\n"
        + check.stderr[:5000]
        + "\nPrevious rejected patch:\n"
        + patch[:16000]
        + "\nCurrent exact contents of target file(s):\n"
        + target_file_context(patch)
        + "\nReturn ONLY the required JSON object. Keep decision=CHANGE, preserve the same intended UI improvement, "
        + "and return a corrected unified diff that applies exactly to the current file contents shown above. "
        + "Use exact lines and correct hunk line numbers. Do not include Markdown fences. "
        + "commit_title must be plain title text without feat(ui):, fix(ui):, or chore(ui): prefix."
    )

    try:
        repaired_response = ask_gemini(selected_model, prompt=repair_prompt)
        repaired = response_payload(repaired_response)
        if str(repaired.get("decision", "")).strip().upper() == "NO_CHANGE":
            print("Gemini repair returned NO_CHANGE; safely skipping this cycle.")
            sys.exit(0)

        repaired_summary = str(repaired.get("summary", "")).strip()
        repaired_title = str(repaired.get("commit_title", "")).strip()
        repaired_patch = str(repaired.get("patch", "")).strip()

        repaired_title = re.sub(
            r"^(?:feat|fix|chore)\(ui\):\s*",
            "",
            repaired_title,
            flags=re.I,
        ).strip()

        if repaired_summary:
            summary = repaired_summary
        if repaired_title:
            commit_title = repaired_title
        if repaired_patch:
            patch = repaired_patch
            check = check_patch(patch)
        else:
            print("Gemini repair response contained no patch.")
            break
    except Exception as exc:
        print(f"Gemini patch repair failed: {type(exc).__name__}: {exc}")
        break

if check.returncode != 0:
    print("Patch rejected after repair attempts; no files changed.")
    print(check.stderr[:2500])
    sys.exit(0)

subprocess.run(["git", "apply", "--whitespace=error", str(patch_file)], cwd=ROOT, check=True)

changed = subprocess.check_output(
    ["git", "diff", "--name-only"],
    cwd=ROOT,
    text=True,
).splitlines()

bad = [x for x in changed if not ALLOWED.match(x)]
if bad:
    print("Unsafe files detected; reverting AI patch:", bad)
    subprocess.run(["git", "reset", "--hard", "HEAD"], cwd=ROOT, check=True)
    sys.exit(0)

if not changed:
    print("NO_CHANGE")
    sys.exit(0)

commit_title = re.sub(
    r"^(?:feat|fix|chore)\(ui\):\s*",
    "",
    commit_title,
    flags=re.I,
).strip()
if not commit_title or commit_title.lower() in {"improve milan ui", "improve ui", "ui update", "update ui"}:
    print("Repaired result has no specific commit title; refusing automatic commit.")
    subprocess.run(["git", "reset", "--hard", "HEAD"], cwd=ROOT, check=True)
    sys.exit(0)
if commit_title.lower() in normalized_recent:
    print("Repaired result duplicates a recent UI commit title; refusing automatic commit.")
    subprocess.run(["git", "reset", "--hard", "HEAD"], cwd=ROOT, check=True)
    sys.exit(0)

Path("/tmp/milan-ai-summary").write_text(summary[:180], encoding="utf-8")
Path("/tmp/milan-ai-commit-title").write_text(commit_title[:120], encoding="utf-8")
print("AI CHANGE:", summary)
print("AI PATCH APPLIED:")
print("\n".join(changed))
