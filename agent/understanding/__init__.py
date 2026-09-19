"""Versioned understanding, decomposition, and entity-resolution contracts."""

from .classifier import (
    CLASSIFIER_PROMPT_VERSION,
    ClassifierTrace,
    SchemaClassification,
    classify_schema_constrained,
    relevant_taxonomy_subset,
)
from .decomposition import (
    ClauseRelation,
    CompoundClause,
    CompoundRequest,
    decompose_request,
)
from .policy import ClarificationDecision, decide_clarification
from .recognizers import EvidenceSpan, Recognition, recognize_deterministic
from .taxonomy import INTENT_TAXONOMY, TAXONOMY_VERSION, IntentDefinition, IntentLeaf

__all__ = [
    "CLASSIFIER_PROMPT_VERSION",
    "ClauseRelation",
    "ClassifierTrace",
    "ClarificationDecision",
    "CompoundClause",
    "CompoundRequest",
    "EvidenceSpan",
    "INTENT_TAXONOMY",
    "IntentDefinition",
    "IntentLeaf",
    "Recognition",
    "SchemaClassification",
    "TAXONOMY_VERSION",
    "classify_schema_constrained",
    "decide_clarification",
    "decompose_request",
    "recognize_deterministic",
    "relevant_taxonomy_subset",
]
