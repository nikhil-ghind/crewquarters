from enum import StrEnum


class VoiceCallOutState(StrEnum):
    CONNECTED = "connected"
    CREATED = "created"
    DIALING = "dialing"
    ENDED = "ended"
    FAILED = "failed"
    RINGING = "ringing"

    def __str__(self) -> str:
        return str(self.value)
