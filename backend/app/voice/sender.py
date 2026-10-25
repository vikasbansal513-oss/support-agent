from typing import Protocol


class VoiceSender(Protocol):
    async def send_text(self, to: str, text: str) -> str: ...


class CaptureVoiceSender:
    """Collects what the agent says during one live call turn; the webhook speaks it."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    async def send_text(self, to: str, text: str) -> str:
        self.lines.append(text)
        return f"voice-{len(self.lines)}"
