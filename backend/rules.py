def judge(doc: dict) -> tuple[str, str]:
    steps = doc.get("steps") or []
    fry = next((s for s in steps if s.get("name") == "清炒"), None)
    if fry is None:
        return "未放行", "缺少清炒工序"
    temp = float(fry.get("temp_c", 0))
    minutes = float(fry.get("minutes", 0))
    if not 80 <= temp <= 150:
        return "未放行", "清炒温度不在范围内"
    if not 5 <= minutes <= 30:
        return "未放行", "清炒时长不在范围内"
    return "放行", "清炒工序符合炮制要求"


def energy_cost(doc: dict) -> int:
    """开炒能耗点：清炒温度 × 时长 ÷ 10 取整（向下取整）。无清炒工序记 0 点。"""
    steps = doc.get("steps") or []
    fry = next((s for s in steps if s.get("name") == "清炒"), None)
    if fry is None:
        return 0
    temp = float(fry.get("temp_c", 0))
    minutes = float(fry.get("minutes", 0))
    return int(temp * minutes / 10)
