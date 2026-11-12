_TRIAGE_TO_WIZARD: dict[str, str] = {
    "suggested_type": "type",
    "suggested_priority": "priority",
    "suggested_title": "title",
    "suggested_owner": "owner",
    "background": "background",
    "goal": "goal",
    "scope": "scope",
    "acceptance": "acceptance",
    "risks": "risks",
}

_SYSTEM_PROMPT = (
    "你是 QuantLens的 issue 讨论助手。"
    "用户正在考虑创建一个 issue，但尚未确定具体内容。\n"
    "你的任务：\n"
    "1. 根据用户描述的问题/需求，通过追问澄清意图、范围和优先级；\n"
    "2. 用中文回复，简洁专业；\n"
    "3. 每轮只问一个问题或给出一个建议；\n"
    "4. 当用户输入 'done' 或 '完成' 时结束讨论，你无需回复。\n\n"
    "禁止：要求用户提供 API key、密码等敏感信息。\n"
    "当前讨论的问题上下文：\n"
)
