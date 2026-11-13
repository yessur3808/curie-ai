"""Advisory specialists; never execute the reviewed plan or a model-supplied tool."""

from agent.tooling.contracts import ToolResult
from services import letta_specialists


class LettaSpecialistTool:
    read_only = True

    def __init__(self, specialty):
        self.specialty = specialty
        self.name = f"letta_{specialty}_skill"

    async def execute(self, params, context):
        result = await letta_specialists.review(
            context.internal_id, self.specialty, str(params.get("text", ""))
        )
        if result.status == "completed":
            text = (
                result.text
                + "\n\nSpecialist assessment of supplied information; no actions were executed."
            )
        else:
            text = (
                "The specialist review could not complete ("
                + result.status
                + "). I haven’t verified a specialist assessment for this request."
            )
        return ToolResult(
            text=text,
            source=self.name,
            data={
                "status": result.status,
                "specialty": self.specialty,
                "executed_actions": False,
            },
        )
