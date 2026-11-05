from typing import Protocol


class VoiceSender(Protocol):
    async def send_text(self, to: str, text: str, critical: bool = False) -> str: ...


class CaptureVoiceSender:
    """Collects what the agent says during one live call turn; the webhook speaks it.

    `critical` (Decision Framework Section 17.2) is a parallel list, one entry per line in
    `lines` - True when that line is a write-flow confirm_*/done_* reply reading an order
    ref/amount as a yes-or-no ask or a just-completed-action statement, which
    app.voice.service._finish_turn uses to decide whether the turn may be interrupted
    mid-sentence. The flag is set by the caller (app.agent.runner_v1._send, via
    app.actions.engine.submit_reply/_deliver/_send) - this class only stores it.
    """

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.critical: list[bool] = []

    async def send_text(self, to: str, text: str, critical: bool = False) -> str:
        self.lines.append(text)
        self.critical.append(critical)
        return f"voice-{len(self.lines)}"
