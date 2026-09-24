from enum import StrEnum


class AgentEventInType(StrEnum):
    RUN_ARTIFACT = "run.artifact"
    RUN_LOG = "run.log"
    RUN_METRIC = "run.metric"
    RUN_PROGRESS = "run.progress"

    def __str__(self) -> str:
        return str(self.value)
