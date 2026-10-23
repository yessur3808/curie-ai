"""Model selection and fallback for ordinary conversation."""

from __future__ import annotations

import asyncio
import re

from agent.orchestration.contracts import ResponseCandidate


class ModelConversationService:
    def __init__(self, local_manager):
        self.local_manager = local_manager

    @staticmethod
    def is_lightweight_opinion(user_text: str) -> bool:
        """Keep everyday choices and opinions on the conversational model path."""
        text = user_text.strip().casefold()
        asks_for_opinion = re.search(
            r"\b(?:do you think|what do you think|pick one|which (?:one|would you)|"
            r"your opinion|give me (?:one|your) opinion|tell me why (?:that|this|it)|"
            r"be honest)\b",
            text,
        )
        complex_domain = re.search(
            r"\b(?:code|api|database|algorithm|architecture|security|network|model|"
            r"medical|medicine|legal|contract|financial|investment|trading|physics|"
            r"engineering|statistics|research|diagnos(?:e|is)|risk analysis)\b",
            text,
        )
        everyday_activity_choice = re.search(
            r"\b(?:after work|before bed|another hour|call it a (?:day|night)|take a break|wind down)\b",
            text,
        ) and not re.search(
            r"\b(?:root cause|architecture|benchmark|diagnos\w*|implement)\b", text
        )
        return bool(
            asks_for_opinion
            and (not complex_domain or everyday_activity_choice)
            and len(text.split()) <= 32
        )

    @staticmethod
    def token_budget(user_text: str, role: str | None = None) -> int:
        """Choose a measured output ceiling without reducing requested depth."""
        text = user_text.strip().casefold()
        if re.fullmatch(
            r"(?:hi|hello|hey|bonjour)(?:\s+(?:curie|there))?[!,.? ]*", text
        ) or re.search(r"\bhow are you(?: doing)?\b", text):
            return 96
        if re.search(
            r"\b(?:in detail|detailed|deep dive|thorough|comprehensive|step by step)\b",
            text,
        ):
            return 1024
        selected_role = role or ModelConversationService.model_role(user_text)
        if selected_role in {"reasoning", "coding"}:
            return 640
        if selected_role == "fast":
            return 192
        if ModelConversationService.is_lightweight_opinion(user_text):
            return 224
        return 256

    @staticmethod
    def model_role(user_text: str) -> str:
        """Select a model specialty from the user's actual task."""
        text = user_text.strip().casefold()
        if ModelConversationService.is_lightweight_opinion(user_text):
            return "general"
        if re.search(
            r"\b(?:http\s+[45]\d\d|api credits|login problem|logged out)\b", text
        ) and not re.search(r"\b(?:fix|debug|implement|write|code|refactor)\b", text):
            return "general"
        if (
            re.search(
                r"\b(?:would|should|why|tradeoffs?|first step|first measure|root cause)\b",
                text,
            )
            and re.search(
                r"\b(?:api|database|latency|architecture|performance|p95)\b", text
            )
            and not re.search(r"\b(?:write|implement|refactor|fix|debug)\b", text)
        ):
            return "reasoning"
        if (
            re.search(
                r"\b(?:code|coding|program|function|class|api|endpoint|debug|bug|"
                r"refactor|repository|repo|pull request|unit tests?|pytest|javascript|"
                r"typescript|python|rust|sql|html|css|docker|authentication)\b",
                text,
            )
            or len(re.findall(r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm)\b", text)) >= 2
        ):
            return "coding"
        if re.search(
            r"\b(?:reason|analyse|analyze|evaluate|compare|tradeoffs?|prove|math|"
            r"calculate|architecture|design|strategy|plan|research|investigate|"
            r"diagnose|why does|root cause|step by step|correlate|infer|deduce|"
            r"cause and effect|pros and cons|best approach|best option|recommend|"
            r"prioriti[sz]e|decision|implications?|constraints?|contradiction|"
            r"troubleshoot|what follows|what would happen|if .+ then|"
            r"do you think|(?:your|one|an) opinion|pick one|push back|agree|disagree|"
            r"be honest|terrible idea|visionary|what gets .+ first)\b",
            text,
        ):
            return "reasoning"
        if re.search(
            r"\b(?:summari[sz]e|rewrite|rephrase|extract|classify|categorize|"
            r"translate|format|shorten|proofread)\b",
            text,
        ):
            return "fast"
        return "general"

    @staticmethod
    def contextual_role(user_text: str, prompt: str) -> str:
        """Keep an analytical follow-up with its specialist, not an old topic."""
        from agent.conversation_context import context_followup
        from llm.chat_prompt import ChatPrompt

        role = ModelConversationService.model_role(user_text)
        if role != "general" or not isinstance(prompt, ChatPrompt):
            return role
        history = prompt.messages[1:-1]
        if not context_followup(user_text, history):
            return role
        previous_users = [m["content"] for m in history if m["role"] == "user"]
        if any(
            ModelConversationService.model_role(text) == "reasoning"
            for text in previous_users[-2:]
        ):
            return "reasoning"
        return role

    @staticmethod
    def reasoning_temperature(role: str, requested: float) -> float:
        """Keep analytical work stable while preserving personality in casual chat."""
        if role == "reasoning":
            return min(requested, 0.25)
        if role == "coding":
            return min(requested, 0.20)
        if role == "fast":
            return min(requested, 0.15)
        return requested

    @staticmethod
    def quality_prompt(prompt: str, role: str) -> str:
        """Add one-pass internal verification for complex work, without extra calls."""
        if role in {"reasoning", "coding"}:
            guidance = (
                "Internal answer-quality policy: identify the user's actual goal and all "
                "referenced entities; silently form a concise plan; check constraints, "
                "contradictions, calculations, and tool evidence; then return only the "
                "direct final answer. Do not reveal private chain-of-thought. Distinguish "
                "verified facts from assumptions and ask only when an unresolved ambiguity "
                "would materially change the result. When asked to choose or give an opinion, "
                "take a defensible position instead of listing both sides. Never say you agree "
                "and then argue the opposite."
                " For diagnosis, distinguish hypotheses from measured causes. Prefer evidence "
                "that separates competing explanations before recommending a system change."
            )
        else:
            guidance = (
                "Final conversational check: answer the newest message directly in plain, "
                "complete sentences. Use a natural everyday voice, take a position when one "
                "is requested, and do not add flattery, a generic offer to help, or a trailing "
                "question. Do not infer a personality trait beyond the conversation evidence."
            )
        guidance += (
            " When drafting or editing, output the requested text itself, keeping the "
            "requested length. A short note normally means one or two sentences, not an "
            "email with a subject and sign-off. Do not invent reasons, excuses, relationships, "
            "or events. Keep the speaker, recipient, and requested action unchanged."
            " Distinguish the original request from a later question about it: explaining "
            "or quoting an earlier command does not retroactively change what was asked."
        )
        from llm.chat_prompt import ChatPrompt

        if isinstance(prompt, ChatPrompt):
            return prompt.with_system_guidance(guidance)
        stripped = prompt.rstrip()
        if stripped.endswith("Assistant:"):
            return (
                stripped[: -len("Assistant:")].rstrip() + f"\n\n{guidance}\nAssistant:"
            )
        return f"{stripped}\n\n{guidance}"

    async def generate(
        self,
        prompt: str,
        user_text: str,
        temperature: float,
        *,
        owner_id: str = "anonymous",
        request_id: str | None = None,
        priority: str = "active",
    ) -> ResponseCandidate:
        role = self.contextual_role(user_text, prompt)
        max_tokens = self.token_budget(user_text, role)
        from services.resource_governor import resource_governor
        from services.self_diagnostics import diagnostic_monitor

        decision = resource_governor.admit(
            "conversation",
            owner_id,
            max_tokens,
            preferred_model=role,
            fallback_model="general" if role in {"reasoning", "coding"} else "fast",
            diagnostics=diagnostic_monitor.snapshot(owner_id=owner_id, limit=50),
        )
        if not decision.allowed:
            return ResponseCandidate(
                "I’m at the current resource limit, so I’m pausing new model work rather than giving you a poor answer. `/budgets` will show what was reached.",
                "resource_governor",
                {
                    "role": role,
                    "max_tokens": 0,
                    "budget_mode": decision.mode,
                    "budget_reason": decision.reason,
                    "fallback": False,
                },
            )
        role = decision.model or role
        max_tokens = decision.max_tokens
        effective_temperature = self.reasoning_temperature(role, temperature)
        effective_prompt = self.quality_prompt(prompt, role)
        outcome = {
            "provider": self.local_manager.DEFAULT_LLAMA_MODEL,
            "fallback": False,
        }

        async def operation(emit, cancelled):
            response = None
            try:
                from llm.ensemble import ask_ensemble, should_use_ensemble

                if should_use_ensemble(user_text):
                    response = ask_ensemble(effective_prompt, max_tokens=max_tokens)
                    outcome["provider"] = "ensemble"
                else:
                    from llm.providers import ask_best_provider

                    response = ask_best_provider(
                        effective_prompt,
                        temperature=effective_temperature,
                        max_tokens=max_tokens,
                        role=role,
                        owner_scope=owner_id,
                    )
                    outcome["provider"] = f"best_provider:{role}"
            except Exception:
                response = None
            if cancelled.is_set():
                raise asyncio.CancelledError
            if response is None or response.startswith("[Error"):
                outcome["fallback"] = True
                response = self.local_manager.ask_llm(
                    effective_prompt,
                    max_tokens=max_tokens,
                    temperature=effective_temperature,
                    role=role,
                    owner_scope=owner_id,
                )
                outcome["provider"] = f"{self.local_manager.DEFAULT_LLAMA_MODEL}:{role}"
            await emit(response)
            return None

        from llm.inference_service import get_inference_service

        managed = await get_inference_service().submit(
            operation,
            owner_id=owner_id,
            priority=priority,
            request_id=request_id,
            supersede_owner=priority == "active",
        )
        resource_governor.record(
            "conversation",
            owner_id,
            tokens=int(managed.output_tokens or 0),
            wall_seconds=float(managed.total_ms or 0) / 1000,
            model=str(outcome["provider"]),
        )
        return ResponseCandidate(
            managed.text,
            str(outcome["provider"]),
            {
                "role": role,
                "reasoning_effort": (
                    "high" if role in {"reasoning", "coding"} else "efficient"
                ),
                "temperature": effective_temperature,
                "max_tokens": max_tokens,
                "fallback": bool(outcome["fallback"]),
                "budget_mode": decision.mode,
                "budget_reason": decision.reason,
                "model_downgraded": decision.downgraded,
                "request_id": managed.request_id,
                "queue_ms": managed.queue_ms,
                "first_token_ms": managed.first_token_ms,
                "total_ms": managed.total_ms,
                "output_tokens": managed.output_tokens,
                "tokens_per_second": managed.tokens_per_second,
            },
        )
