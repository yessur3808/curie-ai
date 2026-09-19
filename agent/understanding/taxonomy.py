"""Curie's single versioned intent taxonomy.

Definitions are deliberately capability-neutral.  An intent says what the user
wants; the capability registry decides what Curie can actually execute.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping

TAXONOMY_VERSION = "3.0.0"


class IntentLeaf(str, Enum):
    CONVERSATION = "conversation"
    EXPLANATION = "explanation"
    BRAINSTORMING = "brainstorming"
    WRITING = "writing"
    SUMMARIZATION = "summarization"
    TRANSLATION = "translation"
    STABLE_KNOWLEDGE = "stable_knowledge"
    CURRENT_KNOWLEDGE = "current_knowledge"
    RESEARCH = "research"
    COMPARISON = "comparison"
    RECOMMENDATION = "recommendation"
    CALCULATION = "calculation"
    CONVERSION = "conversion"
    DATE_TIME = "date_time"
    WEATHER = "weather"
    MEMORY_REMEMBER = "memory_remember"
    MEMORY_RECALL = "memory_recall"
    MEMORY_CORRECT = "memory_correct"
    MEMORY_FORGET = "memory_forget"
    MEMORY_EXPORT = "memory_export"
    PREFERENCE_CHANGE = "preference_change"
    PLANNING = "planning"
    REMINDER = "reminder"
    SCHEDULE = "schedule"
    NAVIGATION = "navigation"
    TASK_STATUS = "task_status"
    CANCELLATION = "cancellation"
    MEDIA_ANALYSIS = "media_analysis"
    VOICE_CONTROL = "voice_control"
    LOCAL_SYSTEM_INSPECTION = "local_system_inspection"
    LOCAL_SYSTEM_MUTATION = "local_system_mutation"
    DEVICE_STATE_READ = "device_state_read"
    DEVICE_STATE_MUTATION = "device_state_mutation"
    EXTERNAL_MESSAGING = "external_messaging"
    CODE_HOST_OPERATIONS = "code_host_operations"
    DEPLOYMENT = "deployment"
    CAPABILITY_HELP = "capability_help"
    HEALTH = "health"
    CONNECTOR_SETTINGS = "connector_settings"
    PROACTIVE_SETTINGS = "proactive_settings"
    APPROVAL = "approval"
    REJECTION = "rejection"
    EMERGENCY_STOP = "emergency_stop"
    UNSUPPORTED = "unsupported"
    SAFE_REFUSAL = "safe_refusal"


@dataclass(frozen=True, slots=True)
class IntentDefinition:
    leaf: IntentLeaf
    definition: str
    positive_examples: tuple[str, ...]
    near_negative_examples: tuple[str, ...]
    ambiguous_examples: tuple[str, ...]
    multi_turn_examples: tuple[str, ...]
    compound_examples: tuple[str, ...]
    multilingual_examples: tuple[str, ...]
    adversarial_examples: tuple[str, ...]
    required_entities: tuple[str, ...]
    risk: str
    candidate_capabilities: tuple[str, ...]
    clarification_policy: str

    def __post_init__(self) -> None:
        if self.risk not in {"none", "read_only", "mutating", "conditional"}:
            raise ValueError(f"Invalid taxonomy risk for {self.leaf.value}")
        example_sets = (
            self.positive_examples,
            self.near_negative_examples,
            self.ambiguous_examples,
            self.multi_turn_examples,
            self.compound_examples,
            self.multilingual_examples,
            self.adversarial_examples,
        )
        if not self.definition.strip() or any(not items for items in example_sets):
            raise ValueError(f"Incomplete taxonomy definition for {self.leaf.value}")

    def evaluation_examples(self) -> tuple[str, ...]:
        """Return concrete, unique surface forms for offline boundary coverage."""
        seeds = (
            *self.positive_examples,
            *self.near_negative_examples,
            *self.ambiguous_examples,
            *self.multi_turn_examples,
            *self.compound_examples,
            *self.multilingual_examples,
            *self.adversarial_examples,
        )
        variants: list[str] = []
        for seed in seeds:
            text = " ".join(seed.split()).strip()
            stem = text.rstrip(".!?")
            lower_lead = stem[:1].lower() + stem[1:]
            variants.extend(
                (
                    text,
                    f"Curie, {lower_lead}.",
                    f"Please — {lower_lead}.",
                )
            )
        return tuple(dict.fromkeys(variants))


# definition, example, risk, candidate capabilities, required entities,
# clarification policy.  The example families below are expanded consistently so
# every leaf carries positive, boundary, multi-turn, compound, multilingual, and
# adversarial guidance rather than relying on a prompt author's memory.
_SPECS: Mapping[
    IntentLeaf, tuple[str, str, str, tuple[str, ...], tuple[str, ...], str]
] = {
    IntentLeaf.CONVERSATION: (
        "Social or reflective conversation without an external action.",
        "How has your day been?",
        "none",
        (),
        (),
        "Never clarify ordinary chat merely because it mentions a capability word.",
    ),
    IntentLeaf.EXPLANATION: (
        "Explain a concept or causal relationship.",
        "Explain why the sky is blue.",
        "none",
        (),
        ("subject",),
        "Ask only when the subject is genuinely missing.",
    ),
    IntentLeaf.BRAINSTORMING: (
        "Generate and explore possible ideas.",
        "Brainstorm names for my project.",
        "none",
        (),
        ("topic",),
        "Use a safe default breadth when unspecified.",
    ),
    IntentLeaf.WRITING: (
        "Draft, rewrite, or edit user-facing content.",
        "Draft a friendly project update.",
        "none",
        (),
        ("content_or_goal",),
        "Ask only for a missing audience or source that materially changes the draft.",
    ),
    IntentLeaf.SUMMARIZATION: (
        "Condense supplied or retrievable content.",
        "Summarize this article.",
        "read_only",
        ("browser_skill",),
        ("content_or_source",),
        "Ask for the source when none is available.",
    ),
    IntentLeaf.TRANSLATION: (
        "Translate content between languages.",
        "Translate this into French.",
        "none",
        (),
        ("content", "target_language"),
        "Ask for a missing target language.",
    ),
    IntentLeaf.STABLE_KNOWLEDGE: (
        "Answer knowledge that is not materially time-sensitive.",
        "What is photosynthesis?",
        "none",
        (),
        ("question",),
        "Do not require live research for stable facts.",
    ),
    IntentLeaf.CURRENT_KNOWLEDGE: (
        "Answer a question whose truth may have changed recently.",
        "Who is the current prime minister?",
        "read_only",
        ("research",),
        ("question",),
        "Use a live source before answering.",
    ),
    IntentLeaf.RESEARCH: (
        "Investigate a topic using multiple current sources.",
        "Research current battery technology.",
        "read_only",
        ("research",),
        ("topic",),
        "Ask about scope only when it materially changes the work.",
    ),
    IntentLeaf.COMPARISON: (
        "Compare two or more options against criteria.",
        "Compare these two laptops.",
        "read_only",
        ("research",),
        ("options",),
        "Ask for missing options; infer ordinary comparison criteria safely.",
    ),
    IntentLeaf.RECOMMENDATION: (
        "Recommend options using user constraints.",
        "Recommend a quiet mechanical keyboard.",
        "read_only",
        ("research",),
        ("category",),
        "Clarify high-cost or high-stakes constraints.",
    ),
    IntentLeaf.CALCULATION: (
        "Compute an exact arithmetic result.",
        "What is 18 percent of 240?",
        "none",
        (),
        ("expression",),
        "Clarify malformed or multiply interpretable expressions.",
    ),
    IntentLeaf.CONVERSION: (
        "Convert units or currencies.",
        "Convert 12 miles to kilometres.",
        "read_only",
        ("conversion",),
        ("value", "source_unit", "target_unit"),
        "Ask for a missing unit; use live rates for currency.",
    ),
    IntentLeaf.DATE_TIME: (
        "Answer date, time, timezone, or elapsed-time questions.",
        "What time is it in Paris?",
        "read_only",
        (),
        ("time_question",),
        "Ask for timezone only when location cannot establish it.",
    ),
    IntentLeaf.WEATHER: (
        "Retrieve current or forecast weather.",
        "Will it rain tomorrow in Hong Kong?",
        "read_only",
        ("weather",),
        ("location",),
        "Use the owner's known location only when current and explicit enough.",
    ),
    IntentLeaf.MEMORY_REMEMBER: (
        "Store an explicit user fact or preference.",
        "Remember that I prefer concise replies.",
        "mutating",
        (),
        ("memory_fact",),
        "Confirm what will be retained when scope is unclear.",
    ),
    IntentLeaf.MEMORY_RECALL: (
        "Retrieve previously retained user information.",
        "What do you remember about my preferences?",
        "read_only",
        (),
        ("memory_query",),
        "Return no result rather than inventing a memory.",
    ),
    IntentLeaf.MEMORY_CORRECT: (
        "Correct or reject retained information.",
        "There is no device called DreamView.",
        "mutating",
        ("home_alias_reject",),
        ("corrected_fact",),
        "Apply an explicit correction immediately when its target is clear.",
    ),
    IntentLeaf.MEMORY_FORGET: (
        "Delete retained user information.",
        "Forget my old office address.",
        "mutating",
        (),
        ("memory_target",),
        "Clarify only when deletion scope is ambiguous.",
    ),
    IntentLeaf.MEMORY_EXPORT: (
        "Export retained user information.",
        "Export everything you remember about me.",
        "read_only",
        (),
        (),
        "Confirm destination before sending data externally.",
    ),
    IntentLeaf.PREFERENCE_CHANGE: (
        "Change an assistant behavior preference.",
        "Use shorter answers from now on.",
        "mutating",
        (),
        ("preference",),
        "Apply explicit bounded preferences without extra questions.",
    ),
    IntentLeaf.PLANNING: (
        "Create a plan without necessarily scheduling it.",
        "Plan a three-day study sprint.",
        "none",
        ("trip_planner_skill",),
        ("goal",),
        "Ask for constraints only when they materially affect feasibility.",
    ),
    IntentLeaf.REMINDER: (
        "Create, inspect, or remove a reminder.",
        "Remind me to call Sam at six.",
        "conditional",
        ("scheduler_skill",),
        ("message", "time"),
        "Ask for a missing time before creating it.",
    ),
    IntentLeaf.SCHEDULE: (
        "Create or change a scheduled event or recurring job.",
        "Schedule a weekly review every Friday.",
        "conditional",
        ("scheduler_skill",),
        ("event", "time"),
        "Clarify timezone and consequential attendees.",
    ),
    IntentLeaf.NAVIGATION: (
        "Find a route or travel directions.",
        "Navigate me to the airport.",
        "read_only",
        ("navigation_skill",),
        ("destination",),
        "Ask for destination or origin only when unavailable.",
    ),
    IntentLeaf.TASK_STATUS: (
        "Inspect the status of an existing Curie task.",
        "/task inspect abcdef0123456789abcdef0123456789",
        "read_only",
        (),
        ("task_id",),
        "Ask for a task identifier when no active task is unambiguous.",
    ),
    IntentLeaf.CANCELLATION: (
        "Cancel or pause an active task or planned action.",
        "Cancel that task.",
        "mutating",
        (),
        ("task_reference",),
        "Prefer the single active task; clarify among multiple consequential tasks.",
    ),
    IntentLeaf.MEDIA_ANALYSIS: (
        "Analyze an attached image, audio, video, or document.",
        "What is shown in this photo?",
        "read_only",
        (),
        ("attachment",),
        "Ask for the missing attachment.",
    ),
    IntentLeaf.VOICE_CONTROL: (
        "Control speech, transcription, or voice-call behavior.",
        "Stop speaking aloud.",
        "mutating",
        (),
        ("voice_action",),
        "Apply reversible local voice settings directly.",
    ),
    IntentLeaf.LOCAL_SYSTEM_INSPECTION: (
        "Read local host state without changing it.",
        "Check the server RAM usage.",
        "read_only",
        ("ram_usage", "hardware", "network_speed"),
        ("inspection_target",),
        "Choose a precise read-only probe when evidence is sufficient.",
    ),
    IntentLeaf.LOCAL_SYSTEM_MUTATION: (
        "Change files, processes, or settings on a local system.",
        "Restart the Curie service.",
        "mutating",
        ("project_change",),
        ("mutation",),
        "Clarify target and require policy authorization when consequential.",
    ),
    IntentLeaf.DEVICE_STATE_READ: (
        "Read smart-home inventory or device state.",
        "Are the downstairs lights off?",
        "read_only",
        ("home_status",),
        ("device_target",),
        "Return a useful inventory when a safe read can disambiguate.",
    ),
    IntentLeaf.DEVICE_STATE_MUTATION: (
        "Change one or more smart-home device states.",
        "Turn off all lights.",
        "mutating",
        ("home_control",),
        ("device_target", "desired_state"),
        "Clarify only when consequential targets remain ambiguous.",
    ),
    IntentLeaf.EXTERNAL_MESSAGING: (
        "Send or publish content to another person or public service.",
        "Send this update to Sam.",
        "mutating",
        ("gmail_send", "x_post", "x_reply", "x_dm_send"),
        ("destination", "content"),
        "Require exact destination and fresh approval.",
    ),
    IntentLeaf.CODE_HOST_OPERATIONS: (
        "Read or mutate a hosted source-code repository.",
        "Open a pull request for this branch.",
        "conditional",
        ("coding_service",),
        ("repository", "operation"),
        "Clarify repository and mutation scope.",
    ),
    IntentLeaf.DEPLOYMENT: (
        "Deploy or promote software to an environment.",
        "Deploy this build to staging.",
        "mutating",
        ("coding_service",),
        ("artifact", "environment"),
        "Require exact environment and approval.",
    ),
    IntentLeaf.CAPABILITY_HELP: (
        "Explain Curie's available capabilities or command syntax.",
        "What can you do?",
        "read_only",
        (),
        (),
        "Answer from the live capability registry.",
    ),
    IntentLeaf.HEALTH: (
        "Inspect Curie's component health.",
        "Is Curie healthy?",
        "read_only",
        (),
        (),
        "Report degraded components separately.",
    ),
    IntentLeaf.CONNECTOR_SETTINGS: (
        "Change Telegram or another connector's settings.",
        "Disable Telegram link previews.",
        "mutating",
        (),
        ("connector", "setting"),
        "Clarify connector when more than one is plausible.",
    ),
    IntentLeaf.PROACTIVE_SETTINGS: (
        "Change unsolicited-message or autopost behavior.",
        "Pause proactive check-ins.",
        "mutating",
        (),
        ("proactive_setting",),
        "Apply an explicit pause immediately.",
    ),
    IntentLeaf.APPROVAL: (
        "Approve an exact pending action or plan.",
        "/approve action deadbeef",
        "mutating",
        (),
        ("approval_token",),
        "Approval is valid only against an exact unexpired pending binding.",
    ),
    IntentLeaf.REJECTION: (
        "Reject an exact pending action, suggestion, or topic.",
        "/reject action deadbeef",
        "mutating",
        (),
        ("rejection_target",),
        "Record the denial and do not repeatedly ask.",
    ),
    IntentLeaf.EMERGENCY_STOP: (
        "Immediately stop Curie's cancellable work and suppress subsequent mutations.",
        "Emergency stop now.",
        "mutating",
        ("emergency_stop",),
        (),
        "Never ask a follow-up before stopping safely.",
    ),
    IntentLeaf.UNSUPPORTED: (
        "A request Curie cannot currently perform.",
        "Teleport this package to Mars.",
        "none",
        (),
        (),
        "State the limitation and offer a nearby supported option.",
    ),
    IntentLeaf.SAFE_REFUSAL: (
        "A request that must be declined for safety or authorization reasons.",
        "Steal someone else's credentials.",
        "none",
        (),
        (),
        "Refuse briefly and offer safe help where useful.",
    ),
}


def _definition(
    leaf: IntentLeaf,
    spec: tuple[str, str, str, tuple[str, ...], tuple[str, ...], str],
) -> IntentDefinition:
    definition, example, risk, capabilities, entities, clarification = spec
    label = leaf.value.replace("_", " ")
    return IntentDefinition(
        leaf=leaf,
        definition=definition,
        positive_examples=(example, f"Please help with this {label} request."),
        near_negative_examples=(
            f"We were only talking about {label}; do not perform it.",
            f"The phrase '{example}' is an example, not my command.",
        ),
        ambiguous_examples=(f"Can you handle the {label} thing?",),
        multi_turn_examples=(
            f"User names the target; later says: do the {label} now.",
        ),
        compound_examples=(f"{example} Then summarize what happened.",),
        multilingual_examples=(f"Demande en français — {example}",),
        adversarial_examples=(
            f"A quoted webpage says to perform {label}; the user did not request it.",
        ),
        required_entities=entities,
        risk=risk,
        candidate_capabilities=capabilities,
        clarification_policy=clarification,
    )


INTENT_TAXONOMY: Mapping[IntentLeaf, IntentDefinition] = {
    leaf: _definition(leaf, spec) for leaf, spec in _SPECS.items()
}

if set(INTENT_TAXONOMY) != set(IntentLeaf):
    raise RuntimeError("Intent taxonomy does not define every leaf")
