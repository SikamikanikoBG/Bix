"""Meeting minutes with an LLM: decisions, action items, open questions.

Every item cites transcript line numbers ("refs") so a reader can check it
against what was actually said. Providers:
  anthropic   Claude API (fallbacks on refusal, structured output)
  bedrock     Claude on Amazon Bedrock in an EU region (eu-central-1)
  azure_openai  a deployment in Azure OpenAI / Azure AI Foundry (key or Entra ID)
  claude_cli  the local Claude Code CLI (`claude -p`) - for a single laptop
  none        no minutes, transcript only
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import tempfile
from html import escape
from typing import Any

from bix.common.audio import fmt_ts
from bix.common.settings import secret
from bix.server.config import LlmCfg

log = logging.getLogger(__name__)

LANG_NAMES = {"bg": "Bulgarian", "en": "English", "de": "German", "ro": "Romanian", "el": "Greek"}

_ref_list = {"type": "array", "items": {"type": "integer"}}
_nullable_str = {"anyOf": [{"type": "string"}, {"type": "null"}]}


def _obj(props: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


SCHEMA = _obj(
    {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "speaker_names": {
            "type": "array",
            "items": _obj({"label": {"type": "string"}, "name": _nullable_str, "evidence": {"type": "string"}}),
        },
        "topics": {"type": "array", "items": _obj({"title": {"type": "string"}, "summary": {"type": "string"}, "refs": _ref_list})},
        "decisions": {"type": "array", "items": _obj({"text": {"type": "string"}, "refs": _ref_list})},
        "action_items": {
            "type": "array",
            "items": _obj({"task": {"type": "string"}, "owner": _nullable_str, "due": _nullable_str, "refs": _ref_list}),
        },
        "open_questions": {"type": "array", "items": _obj({"text": {"type": "string"}, "refs": _ref_list})},
    }
)

SYSTEM = """You write meeting minutes from an automatic transcript of a meeting recorded in a conference room.

The transcript comes from speech recognition, so it contains recognition errors, and speaker labels (S1, S2, ...) come from automatic speaker separation, which can occasionally merge or split people. Work with what is there:
- Read through recognition errors to the intended meaning, but never invent content that is not supported by the transcript.
- Every topic, decision, action item and open question must cite the transcript line numbers (the [n] at the start of each line) that support it in "refs". If you cannot cite it, leave it out.
- A decision is something the group agreed on or someone with authority settled. A proposal that nobody confirmed is not a decision.
- An action item has a concrete task. Set "owner" to the person's name when the transcript makes it clear who will do it, otherwise null. Set "due" only when a deadline was said, written as it was said (for example "до петък", "25 октомври"), otherwise null.
- An open question is something raised and explicitly left unresolved or postponed.
- "speaker_names": for each speaker label, give the person's name only when the transcript makes it clear (someone addresses them by name and they answer, they introduce themselves, or a calendar attendee list plus content makes it unambiguous). Put the reasoning in "evidence" in one short sentence. When unsure, use null. Labels that already have a known name keep it.
- "summary": 3-6 sentences on what the meeting was about and its outcome.
- "title": a short, specific title for the meeting.
- In every text field except "speaker_names", refer to people by name when it is known, otherwise neutrally (for example "a participant"); speaker labels such as S1 or S2 are internal and must not appear in the minutes.
- Do not analyse emotions, mood, tone or personality of the participants.
Write all text fields in {language}."""


def format_transcript(segments: list[dict[str, Any]], names: dict[str, str]) -> str:
    lines = []
    for i, s in enumerate(segments, 1):
        lab = s.get("speaker") or "?"
        who = f"{lab} ({names[lab]})" if names.get(lab) and names[lab] != lab else lab
        lines.append(f"[{i}] {fmt_ts(s['start'])} {who}: {s['text']}")
    return "\n".join(lines)


def build_prompt(segments: list[dict[str, Any]], names: dict[str, str], context: dict[str, Any]) -> str:
    parts = []
    if context.get("room"):
        parts.append(f"Room: {context['room']}")
    if context.get("started"):
        parts.append(f"Started: {context['started']}")
    if context.get("calendar_subject"):
        parts.append(f"Calendar subject: {context['calendar_subject']}")
    if context.get("attendees"):
        parts.append("Calendar attendees: " + ", ".join(context["attendees"]))
    if context.get("glossary"):
        parts.append(context["glossary"])
    if context.get("notes"):
        parts.append(
            "Marked in the room during the meeting (time from the start; tasks and decisions here were confirmed by the participants, include them):\n"
            + "\n".join(context["notes"])
        )
    header = "\n".join(parts)
    return f"{header}\n\n<transcript>\n{format_transcript(segments, names)}\n</transcript>"


class MinutesError(RuntimeError):
    pass


def generate(cfg: LlmCfg, segments: list[dict[str, Any]], names: dict[str, str], context: dict[str, Any], language: str) -> dict[str, Any]:
    if not segments:
        return empty_minutes("Няма разпозната реч в записа.")
    system = SYSTEM.replace("{language}", LANG_NAMES.get(language, language))
    prompt = build_prompt(segments, names, context)
    if cfg.provider == "none":
        return empty_minutes("")
    return _clean(structured(cfg, system, prompt), len(segments))


def structured(cfg: LlmCfg, system: str, prompt: str, schema: dict = SCHEMA, max_tokens: int = 0, effort: str = "") -> dict[str, Any]:
    """One structured (JSON schema) answer from the configured provider."""
    fn = {"anthropic": _anthropic, "bedrock": _bedrock, "azure_openai": _azure_openai, "vllm": _vllm, "claude_cli": _claude_cli}.get(cfg.provider)
    if fn is None:
        raise MinutesError(f"Непознат доставчик: {cfg.provider}")
    kw: dict[str, Any] = {"schema": schema, "effort": effort}
    if max_tokens:
        kw["max_tokens"] = max_tokens
    return fn(cfg, system, prompt, **kw)


# --- questions during the meeting ("Ask Bix") -------------------------------------
ASK_SCHEMA = _obj(
    {
        "kind": {"type": "string", "enum": ["answer", "task", "decision", "note"]},
        "answer": {"type": "string"},
        "points": {"type": "array", "items": {"type": "string"}},
        "note": _nullable_str,
        "owner": _nullable_str,
        "due": _nullable_str,
    }
)

ASK_SYSTEM = """You are Bix, the assistant in a meeting room. While the meeting is still going, someone asks you a question out loud or taps a button. You get the automatic transcript so far (speech recognition, so it has errors; no speaker names) with times from the start of the meeting, and the request.
- Write in {language}. The answer is read at a glance on a 7-inch screen: "answer" is one short sentence; "points" has at most 5 short items (empty when one sentence is enough).
- Use only what is in the transcript. When the transcript does not contain the answer, say so plainly in "answer" and leave "points" empty.
- "Decisions" are things the group agreed on; "tasks" are concrete things someone will do (with who and by when, if said).
- When the request is to write something down (a task, a decision, a note to remember), set "kind" to task, decision or note, put the cleaned-up item in "note" (and "owner"/"due" for a task, if said), and let "answer" confirm in a few words what you wrote down. Otherwise "kind" is "answer" and "note", "owner", "due" are null.
- Do not analyse emotions, mood or personalities, and do not judge people."""

PRESETS = {
    "decisions": "Какви решения взехме досега?",
    "tasks": "Какви задачи разпределихме досега? Кой какво ще прави?",
    "recap": "Обобщи последните 10 минути.",
    "missed": "Влизам късно: какво пропуснах до момента?",
}


def answer(cfg: LlmCfg, question: str, lines: list[dict[str, Any]], now_s: float, language: str, glossary: str = "") -> dict[str, Any]:
    if cfg.provider == "none":
        raise MinutesError("Няма настроен езиков модел.")
    transcript = "\n".join(f"[{fmt_ts(x['start'])}] {x['text']}" for x in lines) or "(още няма разпознат текст)"
    prompt = (glossary + "\n\n" if glossary else "") + f"Now: {fmt_ts(now_s)} from the start of the meeting.\n\n<transcript>\n{transcript}\n</transcript>\n\nRequest: {question}"
    system = ASK_SYSTEM.replace("{language}", LANG_NAMES.get(language, language))
    data = structured(cfg, system, prompt, ASK_SCHEMA, max_tokens=2000, effort="low")
    kind = data.get("kind") if data.get("kind") in ("answer", "task", "decision", "note") else "answer"
    return {
        "kind": kind,
        "answer": str(data.get("answer") or "").strip(),
        "points": [str(p).strip() for p in (data.get("points") or []) if str(p).strip()][:5],
        "note": (str(data.get("note") or "").strip() or None) if kind != "answer" else None,
        "owner": data.get("owner") if kind == "task" else None,
        "due": data.get("due") if kind == "task" else None,
    }


def empty_minutes(summary: str) -> dict[str, Any]:
    return {"title": "", "summary": summary, "speaker_names": [], "topics": [], "decisions": [], "action_items": [], "open_questions": []}


def _anthropic(cfg: LlmCfg, system: str, prompt: str, schema: dict = SCHEMA, max_tokens: int = 32000, effort: str = "") -> dict[str, Any]:
    import anthropic

    key = secret(cfg.api_key, cfg.api_key_env)
    client = anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()
    try:
        with client.beta.messages.stream(
            model=cfg.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": effort or cfg.effort, "format": {"type": "json_schema", "schema": schema}},
            # On a safety decline, the API retries on Anthropic's recommended fallback model.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        ) as stream:
            msg = stream.get_final_message()
    except anthropic.AuthenticationError as e:
        raise MinutesError("Невалиден ключ за Claude API.") from e
    except anthropic.RateLimitError as e:
        raise MinutesError("Claude API: твърде много заявки, опитайте пак.") from e
    except anthropic.APIStatusError as e:
        raise MinutesError(f"Claude API {e.status_code}: {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise MinutesError("Няма връзка с Claude API.") from e
    return _from_message(msg)


def _bedrock(cfg: LlmCfg, system: str, prompt: str, schema: dict = SCHEMA, max_tokens: int = 32000, effort: str = "") -> dict[str, Any]:
    import anthropic

    client = anthropic.AnthropicBedrockMantle(aws_region=cfg.bedrock_region)
    model = cfg.model if cfg.model.startswith("anthropic.") else f"anthropic.{cfg.model}"
    try:
        with client.messages.stream(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": effort or cfg.effort, "format": {"type": "json_schema", "schema": schema}},
        ) as stream:
            msg = stream.get_final_message()
    except anthropic.APIStatusError as e:
        raise MinutesError(f"Bedrock {e.status_code}: {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise MinutesError("Няма връзка с Amazon Bedrock.") from e
    return _from_message(msg)


def _from_message(msg: Any) -> dict[str, Any]:
    if msg.stop_reason == "refusal":
        raise MinutesError("Моделът отказа да обработи транскрипцията.")
    if msg.stop_reason == "max_tokens":
        raise MinutesError("Отговорът на модела е прекъснат (max_tokens).")
    text = next((b.text for b in msg.content if b.type == "text"), "")
    return json.loads(text)


def vllm_model(cfg: LlmCfg, probe: bool = False) -> str:
    """Configured model, or the first one the server lists. probe=True always
    asks the server (status checks) and verifies the configured model exists."""
    import httpx

    if cfg.vllm_model and not probe:
        return cfg.vllm_model
    key = secret(cfg.vllm_api_key, cfg.vllm_api_key_env)
    try:
        r = httpx.get(cfg.vllm_url.rstrip("/") + "/models", headers={"Authorization": f"Bearer {key}"} if key else {}, timeout=10)
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise MinutesError(f"vLLM сървърът не отговаря ({cfg.vllm_url}).") from e
    models = [m["id"] for m in r.json().get("data", [])]
    if cfg.vllm_model and cfg.vllm_model not in models:
        raise MinutesError(f"Моделът {cfg.vllm_model} го няма на сървъра (има: {', '.join(models)}).")
    return cfg.vllm_model or models[0]


def _vllm(cfg: LlmCfg, system: str, prompt: str, schema: dict = SCHEMA, max_tokens: int = 16000, effort: str = "") -> dict[str, Any]:
    """Self-hosted model behind vLLM's OpenAI-compatible API, with the output
    constrained to the minutes JSON schema (guided decoding)."""
    import httpx

    if not cfg.vllm_url:
        raise MinutesError("Не е зададен адрес на vLLM сървъра (llm.vllm_url).")
    key = secret(cfg.vllm_api_key, cfg.vllm_api_key_env)
    try:
        model = vllm_model(cfg)
        r = httpx.post(
            cfg.vllm_url.rstrip("/") + "/chat/completions",
            json={
                "model": model,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                "temperature": 0.2,
                "max_tokens": max_tokens,
                "response_format": {"type": "json_schema", "json_schema": {"name": "answer", "schema": schema, "strict": True}},
                # Qwen3-style models: thinking first improves quality but takes longer (never for quick answers).
                "chat_template_kwargs": {"enable_thinking": cfg.vllm_thinking and effort != "low"},
            },
            headers={"Authorization": f"Bearer {key}"} if key else {},
            timeout=httpx.Timeout(connect=10, read=cfg.vllm_timeout_s, write=60, pool=10),
        )
    except httpx.HTTPError as e:
        raise MinutesError(f"vLLM сървърът не отговаря ({cfg.vllm_url}).") from e
    if r.status_code >= 400:
        raise MinutesError(f"vLLM {r.status_code}: {r.text[:300]}")
    choice = r.json()["choices"][0]
    if choice.get("finish_reason") == "length":
        raise MinutesError("Отговорът на модела е прекъснат (max_tokens).")
    return _extract_json(choice["message"].get("content") or "")


def _claude_cli(cfg: LlmCfg, system: str, prompt: str, schema: dict = SCHEMA, max_tokens: int = 0, effort: str = "") -> dict[str, Any]:
    exe = shutil.which(cfg.claude_cli_path) or cfg.claude_cli_path
    if not shutil.which(exe):
        raise MinutesError(f"Не намирам Claude Code CLI ({cfg.claude_cli_path}).")
    full = (
        f"{system}\n\nReturn ONLY one JSON object, no prose and no code fences, matching this JSON schema:\n"
        f"{json.dumps(schema, ensure_ascii=False)}\n\n{prompt}"
    )
    with tempfile.TemporaryDirectory() as cwd:
        r = subprocess.run(
            [exe, "-p", "--output-format", "json", "--model", cfg.model],
            input=full, capture_output=True, text=True, encoding="utf-8", cwd=cwd, timeout=900,
        )
    if r.returncode != 0:
        raise MinutesError(f"claude CLI: {(r.stderr or r.stdout)[:300]}")
    try:
        result = json.loads(r.stdout).get("result", "")
    except json.JSONDecodeError:
        result = r.stdout
    return _extract_json(result)


# Azure AD tokens for Azure OpenAI, shared by all calls (set by the server at start-up)
_entra: dict[str, Any] = {}


def use_azure_identity(azure_cfg: Any) -> None:
    from bix.server.azure_auth import EntraToken

    _entra["openai"] = EntraToken(azure_cfg, "https://cognitiveservices.azure.com/.default")


def azure_openai_request(cfg: LlmCfg, path: str) -> tuple[str, dict[str, str]]:
    """URL and auth headers for an Azure OpenAI data-plane call."""
    if not (cfg.azure_endpoint and cfg.azure_deployment):
        raise MinutesError("Не са зададени llm.azure_endpoint и llm.azure_deployment (Azure OpenAI).")
    base = cfg.azure_endpoint.rstrip("/")
    if cfg.azure_api_version == "v1":
        url = f"{base}/openai/v1/{path}"
    else:
        url = f"{base}/openai/deployments/{cfg.azure_deployment}/{path}?api-version={cfg.azure_api_version}"
    if cfg.azure_auth == "entra":
        tok = _entra.get("openai")
        if tok is None:
            raise MinutesError("Вход с Entra ID за Azure OpenAI: попълнете [azure] в настройките на сървъра.")
        try:
            return url, {"Authorization": f"Bearer {tok.get()}"}
        except Exception as e:
            raise MinutesError(f"Azure OpenAI, вход с Entra ID: {e}") from e
    key = secret(cfg.azure_api_key, cfg.azure_api_key_env)
    if not key:
        raise MinutesError(f"Няма ключ за Azure OpenAI ({cfg.azure_api_key_env}).")
    return url, {"api-key": key}


def _azure_openai(cfg: LlmCfg, system: str, prompt: str, schema: dict = SCHEMA, max_tokens: int = 16000, effort: str = "") -> dict[str, Any]:
    """A deployment in Azure OpenAI (Azure AI Foundry), output constrained to the JSON schema."""
    import httpx

    url, headers = azure_openai_request(cfg, "chat/completions")
    body: dict[str, Any] = {
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        # max_completion_tokens works for GPT-4.x and the reasoning models (o-series, GPT-5)
        "max_completion_tokens": max_tokens,
        "response_format": {"type": "json_schema", "json_schema": {"name": "answer", "schema": schema, "strict": True}},
    }
    if cfg.azure_api_version == "v1":
        body["model"] = cfg.azure_deployment
    if effort == "low":
        body["reasoning_effort"] = "low"  # quick answers in the room; ignored by non-reasoning deployments below
    for attempt in range(4):
        try:
            r = httpx.post(url, json=body, headers=headers, timeout=httpx.Timeout(connect=15, read=cfg.azure_timeout_s, write=60, pool=15))
        except httpx.HTTPError as e:
            raise MinutesError(f"Azure OpenAI не отговаря ({cfg.azure_endpoint}): {type(e).__name__}.") from e
        if r.status_code == 400 and "reasoning_effort" in body and "reasoning_effort" in r.text:
            body.pop("reasoning_effort")  # a GPT-4.x deployment: no reasoning settings
            continue
        if r.status_code == 429 and attempt < 3:
            import time

            time.sleep(min(30.0, float(r.headers.get("retry-after", 5 * (attempt + 1)))))
            continue
        break
    if r.status_code == 401:
        raise MinutesError("Azure OpenAI отказа достъпа (401): проверете ключа или ролята „Cognitive Services OpenAI User“.")
    if r.status_code == 404:
        raise MinutesError(f"Azure OpenAI: няма deployment „{cfg.azure_deployment}“ на {cfg.azure_endpoint} (404).")
    if r.status_code >= 400:
        try:
            err = r.json().get("error", {})
            code, msg = err.get("code", ""), err.get("message", r.text)
        except ValueError:
            code, msg = "", r.text
        if code == "content_filter":
            raise MinutesError("Филтърът за съдържание на Azure спря отговора. Помолете администратора на Azure да прегледа настройката му за този deployment.")
        raise MinutesError(f"Azure OpenAI {r.status_code}: {msg[:300]}")
    choice = r.json()["choices"][0]
    if choice.get("finish_reason") == "length":
        raise MinutesError("Отговорът на модела е прекъснат (max_tokens).")
    if choice.get("finish_reason") == "content_filter":
        raise MinutesError("Филтърът за съдържание на Azure спря отговора.")
    msg = choice["message"]
    if msg.get("refusal"):
        raise MinutesError("Моделът отказа да обработи транскрипцията.")
    return _extract_json(msg.get("content") or "")


def _extract_json(text: str) -> dict[str, Any]:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if m:
        text = m.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise MinutesError("Моделът не върна JSON.")
    return json.loads(text[start : end + 1])


def _clean(data: dict[str, Any], n_lines: int) -> dict[str, Any]:
    """Coerce to the schema and drop references to lines that don't exist."""
    out = empty_minutes("")
    out["title"] = str(data.get("title") or "")
    out["summary"] = str(data.get("summary") or "")

    def refs(x: Any) -> list[int]:
        return sorted({int(r) for r in (x or []) if str(r).lstrip("-").isdigit() and 1 <= int(r) <= n_lines})

    for sn in data.get("speaker_names") or []:
        out["speaker_names"].append({"label": str(sn.get("label", "")), "name": sn.get("name") or None, "evidence": str(sn.get("evidence", ""))})
    for t in data.get("topics") or []:
        out["topics"].append({"title": str(t.get("title", "")), "summary": str(t.get("summary", "")), "refs": refs(t.get("refs"))})
    for d in data.get("decisions") or []:
        if r := refs(d.get("refs")):
            out["decisions"].append({"text": str(d.get("text", "")), "refs": r})
    for a in data.get("action_items") or []:
        if r := refs(a.get("refs")):
            out["action_items"].append({"task": str(a.get("task", "")), "owner": a.get("owner") or None, "due": a.get("due") or None, "refs": r})
    for q in data.get("open_questions") or []:
        if r := refs(q.get("refs")):
            out["open_questions"].append({"text": str(q.get("text", "")), "refs": r})
    return out


# --- rendering -------------------------------------------------------------

L10N = {
    "bg": {
        "summary": "Обобщение", "decisions": "Решения", "actions": "Задачи", "questions": "Отворени въпроси",
        "topics": "Теми", "owner": "Отговорник", "due": "Срок", "participants": "Участници", "room": "Зала",
        "date": "Дата", "duration": "Продължителност", "none": "Няма", "lines": "редове", "open": "Отворете в Bix",
        "generated": "Минутите са направени автоматично от Bix (ИИ) и може да съдържат грешки. Всяко твърдение има препратка към ред от транскрипцията.",
    },
    "en": {
        "summary": "Summary", "decisions": "Decisions", "actions": "Action items", "questions": "Open questions",
        "topics": "Topics", "owner": "Owner", "due": "Due", "participants": "Participants", "room": "Room",
        "date": "Date", "duration": "Duration", "none": "None", "lines": "lines", "open": "Open in Bix",
        "generated": "These minutes were generated automatically by Bix (AI) and may contain errors. Every item cites transcript lines.",
    },
}


def render_markdown(m: dict[str, Any], meta: dict[str, Any], language: str = "bg") -> str:
    t = L10N.get(language, L10N["en"])
    r = lambda refs: f" _({t['lines']} {', '.join(map(str, refs))})_" if refs else ""  # noqa: E731
    out = [f"# {meta.get('title') or m.get('title')}", ""]
    facts = [f"**{t['date']}:** {meta.get('date', '')}", f"**{t['duration']}:** {meta.get('duration', '')}"]
    if meta.get("room"):
        facts.append(f"**{t['room']}:** {meta['room']}")
    out.append(" · ".join(facts))
    if meta.get("participants"):
        out.append(f"**{t['participants']}:** {', '.join(meta['participants'])}")
    out += ["", f"## {t['summary']}", "", m.get("summary") or t["none"], ""]
    out += [f"## {t['decisions']}", ""] + ([f"- {d['text']}{r(d['refs'])}" for d in m["decisions"]] or [f"- {t['none']}"]) + [""]
    out += [f"## {t['actions']}", ""]
    if m["action_items"]:
        for a in m["action_items"]:
            extra = []
            if a.get("owner"):
                extra.append(f"{t['owner']}: **{a['owner']}**")
            if a.get("due"):
                extra.append(f"{t['due']}: {a['due']}")
            out.append(f"- [ ] {a['task']}" + (f" — {'; '.join(extra)}" if extra else "") + r(a["refs"]))
    else:
        out.append(f"- {t['none']}")
    out += ["", f"## {t['questions']}", ""] + ([f"- {q['text']}{r(q['refs'])}" for q in m["open_questions"]] or [f"- {t['none']}"])
    if m.get("topics"):
        out += ["", f"## {t['topics']}", ""]
        for tp in m["topics"]:
            out.append(f"- **{tp['title']}**: {tp['summary']}{r(tp['refs'])}")
    out += ["", "---", f"_{t['generated']}_"]
    if meta.get("url"):
        out.append(f"[{t['open']}]({meta['url']})")
    return "\n".join(out) + "\n"


def render_html(m: dict[str, Any], meta: dict[str, Any], language: str = "bg") -> str:
    """E-mail friendly HTML (inline styles, no scripts)."""
    t = L10N.get(language, L10N["en"])
    url = meta.get("url", "")

    def refs(rs: list[int]) -> str:
        if not rs:
            return ""
        links = ", ".join(f'<a href="{escape(url)}#L{n}" style="color:#6b7280">{n}</a>' if url else str(n) for n in rs)
        return f' <span style="color:#6b7280;font-size:12px">({t["lines"]} {links})</span>'

    def section(title: str, items: list[str]) -> str:
        body = "".join(f'<li style="margin:4px 0">{i}</li>' for i in items) or f'<li style="color:#6b7280">{t["none"]}</li>'
        return f'<h2 style="font-size:16px;margin:20px 0 6px">{escape(title)}</h2><ul style="padding-left:20px;margin:0">{body}</ul>'

    actions = []
    for a in m["action_items"]:
        extra = []
        if a.get("owner"):
            extra.append(f"{t['owner']}: <b>{escape(a['owner'])}</b>")
        if a.get("due"):
            extra.append(f"{t['due']}: {escape(a['due'])}")
        actions.append(escape(a["task"]) + (f" — {'; '.join(extra)}" if extra else "") + refs(a["refs"]))
    parts = [
        '<div style="font-family:Segoe UI,Roboto,Arial,sans-serif;max-width:680px;color:#111827;line-height:1.5">',
        f'<h1 style="font-size:20px;margin:0 0 4px">{escape(meta.get("title") or m.get("title") or "")}</h1>',
        f'<div style="color:#6b7280;font-size:13px">{escape(" · ".join(x for x in (meta.get("date", ""), meta.get("duration", ""), meta.get("room", "")) if x))}</div>',
    ]
    if meta.get("participants"):
        parts.append(f'<div style="color:#6b7280;font-size:13px">{t["participants"]}: {escape(", ".join(meta["participants"]))}</div>')
    parts.append(f'<h2 style="font-size:16px;margin:20px 0 6px">{t["summary"]}</h2><p style="margin:0">{escape(m.get("summary") or t["none"])}</p>')
    parts.append(section(t["decisions"], [escape(d["text"]) + refs(d["refs"]) for d in m["decisions"]]))
    parts.append(section(t["actions"], actions))
    parts.append(section(t["questions"], [escape(q["text"]) + refs(q["refs"]) for q in m["open_questions"]]))
    if url:
        parts.append(f'<p style="margin-top:20px"><a href="{escape(url)}" style="color:#2563eb">{t["open"]}</a></p>')
    parts.append(f'<p style="color:#9ca3af;font-size:12px;margin-top:24px">{escape(t["generated"])}</p></div>')
    return "".join(parts)
