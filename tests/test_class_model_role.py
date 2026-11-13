import pytest

from agent.orchestration.model_service import ModelConversationService


@pytest.mark.parametrize(
    "text,role",
    [
        ("Our pottery class has six students. Acknowledge.", "general"),
        ("My evening art class meets in the green room.", "general"),
        ("Back to the pottery class: give the student count and budget.", "general"),
        ("Explain a Python class.", "coding"),
        ("Define a class named Rectangle.", "coding"),
    ],
)
def test_class_role_depends_on_programming_context(text, role):
    assert ModelConversationService.model_role(text) == role
