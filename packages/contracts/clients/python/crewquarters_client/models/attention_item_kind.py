from enum import StrEnum


class AttentionItemKind(StrEnum):
    FAILED_RUN = "failed_run"
    INPUT_REQUEST = "input_request"
    MODEL_ACTION = "model_action"
    SCHEDULE_BLOCKED = "schedule_blocked"

    def __str__(self) -> str:
        return str(self.value)
