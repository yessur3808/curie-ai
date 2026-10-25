"""Durable, opt-in public research with bounded deadlines and private delivery."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import re
from urllib.parse import urlparse

from agent import task_engine
from memory import UserManager
from memory.local_store import (
    finish_mutation_attempt,
    list_personal_items,
    reserve_mutation_attempt,
    save_personal_item,
)
from utils.temporal_context import local_now, parse_time

KIND = "research_followup"
_CHANNELS = set()
_PRIVATE = re.compile(
    r"\b(?:password|secret|credential|token|api.?key|seed phrase|private key|"
    r"my (?:email|inbox|calendar|account|bank|medical|doctor|address)|"
    r"our (?:chat|conversation)|you (?:say|said|mention|mentioned)|error|login|log in)\b",
    re.I,
)
_UNCERTAIN = re.compile(
    r"\b(?:I (?:don['’]t|do not) know|I['’]m not sure|I am not sure|"
    r"couldn['’]t (?:find|verify)|cannot verify|can['’]t verify|"
    r"no (?:current|reliable|live) (?:information|sources|data))\b",
    re.I,
)
_DEEP = re.compile(
    r"\b(?:thorough|in.depth|detailed|comprehensive|exhaustive|"
    r"investigate|compare|research|report back|get back to me)\b",
    re.I,
)


def set_delivery_channels(channels):
    _CHANNELS.clear()
    _CHANNELS.update(channels)


def public_question(text):
    """Do not send private diagnostics, references or personal memories to search."""
    clean = str(text).strip()
    return bool(
        8 <= len(clean) <= 1800
        and not _PRIVATE.search(clean)
        and not re.search(
            r"\b(?:don't|do not|never)\s+(?:search|research|browse)\b", clean, re.I
        )
        and not re.search(
            r"\b(?:send|buy|sell|trade|post|delete|turn off|turn on)\b", clean, re.I
        )
    )


def should_defer(text):
    return public_question(text) and bool(_DEEP.search(text))


def needs_research(text, answer):
    factual = re.sub(
        r"^(?:(?:can|could) you (?:tell me|explain)|do you know)\s+",
        "",
        text.strip(),
        flags=re.I,
    )
    return (
        public_question(text)
        and bool(_UNCERTAIN.search(answer))
        and bool(
            re.search(
                r"^(?:what|who|when|where|why|how|can you|could you|is|are|does)\b",
                factual,
                re.I,
            )
        )
        and not re.search(r"\b(?:it|that|this|you|your|my|me)\b", factual, re.I)
    )


def budget_minutes(query):
    if re.search(
        r"\b(?:exhaustive|comprehensive|multiple countries|full report)\b", query, re.I
    ):
        return 120
    if re.search(r"\b(?:detailed|thorough|in.depth|compare|analysis)\b", query, re.I):
        return 60
    return 30


def _job(owner, job_id):
    return next(
        (
            item
            for item in list_personal_items(owner, KIND)
            if item.get("job_id") == job_id
        ),
        None,
    )


def queue_research(owner, platform, query, profile, origin):
    """Persist before promising; never enqueue to an arbitrary chat or URL."""
    if not profile.get("autonomous_followup_enabled") or platform not in _CHANNELS:
        return None
    if not public_question(query) or not origin or not task_engine.use_native():
        return None
    # Only private conversations have an unambiguous destination. Group threads
    # need an explicit follow-up destination instead of silently leaking to a DM.
    if str(origin.get("external_chat_id")) != str(origin.get("external_user_id")):
        return None
    event = str(origin.get("message_id") or "")
    if not event:
        return None
    key = hashlib.sha256(f"{owner}\0{platform}\0{event}\0{query}".encode()).hexdigest()[
        :32
    ]
    existing = _job(str(owner), key)
    if existing:
        return existing
    pending = [
        j
        for j in list_personal_items(str(owner), KIND)
        if j.get("status") not in {"delivered", "cancelled", "delivery_uncertain"}
    ]
    if len(pending) >= 3:
        return None
    now = datetime.now(timezone.utc)
    minutes = budget_minutes(query)
    document = save_personal_item(
        str(owner),
        KIND,
        {
            "id": key,
            "job_id": key,
            "query": query,
            "platform": platform,
            "status": "queued",
            "created_at": now.isoformat(),
            "deadline": (now + timedelta(minutes=minutes)).isoformat(),
            "budget_minutes": minutes,
            "attempts": 0,
        },
    )
    try:
        task_engine.upsert_scheduled(
            {
                "id": f"research:{owner}:{key}",
                "owner_id": str(owner),
                "kind": KIND,
                "schedule_type": "once",
                "due_at_ms": int(now.timestamp() * 1000),
                "payload": {"job_id": key},
                "max_attempts": 100,
            }
        )
    except Exception:
        document["status"] = "cancelled"
        save_personal_item(str(owner), KIND, document)
        raise
    return document


def acknowledgement(job, profile):
    due = local_now(profile, parse_time(job["deadline"]))
    return (
        f"I’ll look into it and check the sources. I’ve saved the follow-up, "
        f"and I’ll get back to you by {due:%H:%M %Z}, sooner if it’s ready. "
        f"If I can’t verify it, I’ll let you know. Research `{job['job_id'][:8]}`."
    )


def controls(owner, text):
    match = re.fullmatch(
        r"/?research(?:\s+(status|cancel)(?:\s+([a-f0-9]{8,32}))?)?", text.strip(), re.I
    )
    if not match:
        return None
    jobs = list_personal_items(str(owner), KIND)
    action, prefix = match.groups()
    selected = [j for j in jobs if not prefix or j["job_id"].startswith(prefix)]
    if action and action.lower() == "cancel":
        if not prefix or len(selected) != 1:
            return "Use `/research cancel ID` with one of your research IDs."
        item = selected[0]
        if item["status"] in {"delivered", "delivery_uncertain"}:
            return "That report has already been sent or its delivery is uncertain. I haven’t sent it again."
        item["status"] = "cancelled"
        save_personal_item(str(owner), KIND, item)
        task_engine.cancel_scheduled(str(owner), f"research:{owner}:{item['job_id']}")
        return "Cancelled. I won’t send that research follow-up."
    if not selected:
        return "You have no saved research follow-ups."
    return "Research follow-ups:\n" + "\n".join(
        f"- `{j['job_id'][:8]}`: {j['status']} · due {j['deadline']} · {j['query'][:100]}"
        for j in selected[-10:]
    )


async def _research(query):
    from agent.skills.find_info import find_info

    today = datetime.now(timezone.utc).date().isoformat()
    return await find_info(
        f"{query}\nAs of {today}. Compare reliable sources, cite supported claims, and identify uncertainty.",
        return_metadata=True,
    )


def _report(result, query):
    sources = []
    for source in result.get("sources") or []:
        url = str(source.get("url") or "")
        parsed = urlparse(url)
        if (
            parsed.scheme in {"https", "http"}
            and parsed.hostname
            and not parsed.username
            and not parsed.password
        ):
            sources.append(source)
    if not sources:
        return None
    answer = str(result.get("answer") or "").split("\n\nEvidence passages:", 1)[0]
    answer = answer.replace(
        "Synthesis (model inference unless source-cited):", ""
    ).strip()
    if not answer or answer.startswith("[Error"):
        return None
    domains = {urlparse(s["url"]).hostname for s in sources}
    caveat = (
        "Only one source was available, so treat this as provisional."
        if len(domains) < 2
        else "Source-linked synthesis, not independently verified certainty."
    )
    return (
        f"Here’s what I found about {query[:160].rstrip('.?')}:\n\n{answer[:5000]}\n\n"
        f"{caveat}\n\nSources:\n" + "\n".join(f"- {s['url']}" for s in sources[:5])
    )


class ResearchFollowupWorker:
    def __init__(self, send, *, researcher=None):
        import uuid

        self.worker_id = f"research-{uuid.uuid4().hex}"
        self.send = send
        self.researcher = researcher or _research

    async def run_once(self):
        if not task_engine.use_native():
            return
        # One owner-requested report at a time; lease exceeds the hard I/O budget.
        for work in task_engine.claim_scheduled(
            worker_id=self.worker_id, kind=KIND, lease_ms=300000, limit=1
        ):
            await self._run_claim(work)

    async def _run_claim(self, work):
        owner = str(work["owner_id"])
        job_id = work["payload"]["job_id"]
        item = _job(owner, job_id)
        success = True
        delay = 300000
        try:
            if not item or item["status"] in {
                "cancelled",
                "delivered",
                "delivery_uncertain",
            }:
                return
            profile = UserManager.get_user_profile(owner) or {}
            if not profile.get("autonomous_followup_enabled"):
                item["status"] = "cancelled"
                save_personal_item(owner, KIND, item)
                return
            deadline = parse_time(item["deadline"])
            remaining = (deadline - datetime.now(timezone.utc)).total_seconds()
            if not item.get("report") and remaining > 10:
                try:
                    result = await asyncio.wait_for(
                        self.researcher(item["query"]), timeout=min(150, remaining - 5)
                    )
                    item["report"] = _report(result, item["query"])
                except Exception as exc:
                    item["last_error"] = type(exc).__name__
                item["attempts"] += 1
                if (_job(owner, job_id) or {}).get("status") == "cancelled":
                    return
                remaining = (deadline - datetime.now(timezone.utc)).total_seconds()
                if not item.get("report") and remaining > 60:
                    item["status"] = "retrying"
                    save_personal_item(owner, KIND, item)
                    success, delay = False, int(min(300, remaining - 30) * 1000)
                    return
            if not item.get("report"):
                item["report"] = (
                    f"A quick update on {item['query'][:160].rstrip('.?')}: "
                    "I couldn’t find enough reliable evidence within the research window. "
                    "I don’t want to fill the gaps with a guess. The research has stopped."
                )
            item["status"] = "ready"
            save_personal_item(owner, KIND, item)
            if (_job(owner, job_id) or {}).get("status") == "cancelled":
                return
            # Resolve the owner's current verified identity, never a model-generated recipient.
            from memory.repositories import get_repositories

            target = get_repositories().identities.get_external_id(
                owner, item["platform"]
            )
            if not target or item["platform"] not in _CHANNELS:
                success = False
                return
            delivery_key = f"research-delivery:{job_id}"
            prior = reserve_mutation_attempt(
                owner,
                delivery_key,
                "research_followup_delivery",
                hashlib.sha256(item["report"].encode()).hexdigest(),
            )
            if prior:
                item["status"] = (
                    "delivered"
                    if prior["status"] == "completed"
                    else "delivery_uncertain"
                )
                save_personal_item(owner, KIND, item)
                return
            try:
                sent = await asyncio.wait_for(
                    self.send(item["platform"], target, item["report"]), timeout=30
                )
                if not sent:
                    raise RuntimeError("delivery_not_confirmed")
            except Exception:
                # Telegram has no idempotent send key. Never replay an ambiguous send.
                finish_mutation_attempt(owner, delivery_key, "uncertain")
                item["status"] = "delivery_uncertain"
            else:
                finish_mutation_attempt(owner, delivery_key, "completed")
                item["status"] = "delivered"
                item["delivered_at"] = datetime.now(timezone.utc).isoformat()
                from memory.session_store import get_session_manager

                get_session_manager().add_message(
                    item["platform"], owner, "assistant", item["report"]
                )
            save_personal_item(owner, KIND, item)
        except Exception:
            success = False
            raise
        finally:
            try:
                task_engine.complete_scheduled(
                    work["id"],
                    worker_id=self.worker_id,
                    lease_token=work["lease_token"],
                    success=success,
                    retry_delay_ms=delay,
                )
            except PermissionError:
                pass  # User cancellation revoked the claim.
