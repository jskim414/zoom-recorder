import html


def timestamp(seconds: float) -> str:
    value = max(0, int(seconds))
    return f"{value // 3600:02d}:{value // 60 % 60:02d}:{value % 60:02d}"


def render_markdown(metadata: dict, segments: list[dict]) -> str:
    deleting = metadata.get("storage_policy", {}).get("delete_audio_after_transcription") is True
    lines = ["# 회의 전사", "", f"- 녹음 시작: {metadata['started_at']}",
             f"- 녹음 종료: {metadata.get('ended_at') or '복구 중 / 알 수 없음'}",
             "- 시스템 출력은 Zoom 외 다른 앱 소리도 포함합니다.",
             "- 트랙은 화자 신원이 아닙니다. 겹친 발화·스피커 에코·트랙 간 중복이 있을 수 있습니다.",
             "- 자동 전사 결과에는 오류가 있을 수 있습니다. 무음/누락은 추측으로 보완하지 않습니다.",
             "- 각 WAV 시작 오프셋으로 정렬했습니다. 장시간 장치 클록 드리프트는 보정하지 않습니다.", ""]
    if metadata.get("errors") or metadata.get("gaps"):
        lines.extend(["> 주의: 녹음 오류 또는 누락이 있습니다. metadata.json의 errors/gaps를 확인하세요.", ""])
    if metadata.get("transcription", {}).get("backend") == "openai":
        lines.extend(["> OpenAI API 전사 · 시간은 최대 30초 오디오 조각의 범위이며 발화 시각이 아닙니다. 조각 경계에서 문맥이 끊길 수 있습니다.", ""])
    if deleting:
        lines.extend(["> 음성 보관 정책: 전사 성공 후 삭제. 원본 음성 링크는 제공하지 않습니다. 삭제 상태는 앱에서 확인하세요.", ""])
    for item in sorted(segments, key=lambda s: (s["start"], s["track"])):
        label = "PC 음성 · 시스템 출력" if item["track"] == "remote" else "마이크"
        # Render recognizer output as text, never as HTML/links/embedded Markdown.
        text = html.escape(" ".join(item["text"].split()))
        for char in ("\\", "*", "_", "[", "]", "`", "#", "!"):
            text = text.replace(char, "\\" + char)
        lines.append(f"[{timestamp(item['start'])}–{timestamp(item['end'])}] **{label}**: {text}  ")
        if not deleting:
            lines.append(f"원본: [{item['file']}]({item['file']})\n")
    if not segments:
        lines.append("인식된 발화가 없습니다. 오디오와 녹음 오류를 확인하세요.")
    return "\n".join(lines) + "\n"
