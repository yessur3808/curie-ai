"""Model selection and fallback for ordinary conversation."""

from __future__ import annotations

import asyncio
import re

from agent.orchestration.contracts import ResponseCandidate


class ModelConversationService:
    def __init__(self, local_manager):
        self.local_manager = local_manager

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
            return 768
        if selected_role == "fast":
            return 256
        return 384

    @staticmethod
    def model_role(user_text: str) -> str:
        """Select a model specialty from the user's actual task."""
        text = user_text.strip().casefold()
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
            r"troubleshoot|what follows|what would happen|if .+ then)\b",
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
        if role not in {"reasoning", "coding"}:
            return prompt
        guidance = (
            "Internal answer-quality policy: identify the user's actual goal and all "
            "referenced entities; silently form a concise plan; check constraints, "
            "contradictions, calculations, and tool evidence; then return only the "
            "direct final answer. Do not reveal private chain-of-thought. Distinguish "
            "verified facts from assumptions and ask only when an unresolved ambiguity "
            "would materially change the result."
        )
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
        role = self.model_role(user_text)
        max_tokens = self.token_budget(user_text, role)
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
                "request_id": managed.request_id,
                "queue_ms": managed.queue_ms,
                "first_token_ms": managed.first_token_ms,
                "total_ms": managed.total_ms,
                "output_tokens": managed.output_tokens,
                "tokens_per_second": managed.tokens_per_second,
            },
        )
