from pathlib import Path
import shutil
from datetime import datetime

path = Path("bot.py")
if not path.exists():
    raise SystemExit("Не найден bot.py. Положи этот скрипт рядом с bot.py и запусти: python patch_bot_to_gemini.py")

source = path.read_text(encoding="utf-8")
backup = path.with_name(f"bot.py.backup-{datetime.now():%Y%m%d-%H%M%S}")
shutil.copy2(path, backup)

old_config = '''ANTHROPIC_API_KEY = env('ANTHROPIC_API_KEY')   # необязательно: включает ИИ-агента
if ANTHROPIC_API_KEY.upper().startswith('PASTE'):    # ключ-заглушка из .env.example считаем пустым
    ANTHROPIC_API_KEY = ''
AI_MODEL = env('AI_MODEL', 'claude-haiku-4-5-20251001')
AI_URL = 'https://api.anthropic.com/v1/messages' '''
old_config = old_config.rstrip()
new_config = '''GEMINI_API_KEY = env('GEMINI_API_KEY')  # необязательно: включает ИИ-агента
if GEMINI_API_KEY.upper().startswith('PASTE'):
    GEMINI_API_KEY = 'AQ.Ab8RN6K6Dq-vZ5ZpbHGAkowBscMm5G6EOnKl18YZSFT-QLaZLA'
AI_MODEL = env('AI_MODEL', 'gemini-2.5-flash')
AI_URL = 'https://generativelanguage.googleapis.com/v1beta/openai/chat/completions' '''
new_config = new_config.rstrip()
if old_config not in source:
    raise SystemExit("Не нашёл старые настройки Anthropic. Файл не изменён; проверь версию bot.py.")
source = source.replace(old_config, new_config, 1)
source = source.replace("if not ANTHROPIC_API_KEY:", "if not GEMINI_API_KEY:")

start = source.find("def anthropic_call(")
end = source.find("\ndef clean_md(", start)
if start < 0 or end < 0:
    raise SystemExit("Не удалось найти функцию anthropic_call() / clean_md(). Файл не изменён.")

new_call = '''def gemini_call(messages, max_tokens=1024):
    # Вызов Gemini через OpenAI-compatible API.
    tools = [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("input_schema", {"type": "object", "properties": {}})
            }
        }
        for tool in AI_TOOLS
    ]
    payload = {
        "model": AI_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "tools": tools,
        "tool_choice": "auto"
    }
    headers = {
        "content-type": "application/json",
        "Authorization": f"Bearer {GEMINI_API_KEY}"
    }
    for attempt in range(3):
        r = requests.post(AI_URL, headers=headers, json=payload, timeout=60)
        if r.status_code in (429, 500, 502, 503) and attempt < 2:
            time.sleep(1.5 * (attempt + 1))
            continue
        if r.status_code >= 400:
            try:
                detail = r.json().get("error", {}).get("message", "")
            except ValueError:
                detail = r.text[:200]
            log.error("Gemini API %s: %s", r.status_code, detail)
            raise AIError(r.status_code, detail)
        data = r.json()
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("Gemini вернул пустой ответ.")
        return choices[0].get("message") or {}
    raise RuntimeError("Gemini API временно недоступен.")
'''
source = source[:start] + new_call + source[end:]

start = source.find("def ai_reply(")
end = source.find("\nasync def respond_ai(", start)
if start < 0 or end < 0:
    raise SystemExit("Не удалось найти функцию ai_reply() / respond_ai(). Файл не изменён.")

new_reply = '''def ai_reply(uid, l, user_content, saved_text):
    # Диалог с Gemini; сохраняет историю и поддерживает инструменты бота.
    import json
    history = q('SELECT role, content FROM chat WHERE user_id=? ORDER BY id DESC LIMIT 14', (uid,))[::-1]
    messages = [{"role": "system", "content": build_system(uid, l)}]
    for row in history:
        if row["role"] in ("user", "assistant"):
            messages.append({"role": row["role"], "content": row["content"]})

    # Конвертируем изображение в формат OpenAI-compatible Gemini API.
    if isinstance(user_content, list):
        parts = []
        for item in user_content:
            if item.get("type") == "image":
                src = item.get("source", {})
                if src.get("type") == "base64":
                    parts.append({
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{src.get('media_type', 'image/jpeg')};base64,{src.get('data', '')}"
                        }
                    })
            elif item.get("type") == "text":
                parts.append({"type": "text", "text": item.get("text", "")})
        user_content = parts or [{"type": "text", "text": saved_text}]
    messages.append({"role": "user", "content": user_content})

    actions, answer = [], ""
    for _ in range(6):
        assistant = gemini_call(messages)
        text = assistant.get("content")
        if isinstance(text, str) and text.strip():
            answer = text.strip()
        tool_calls = assistant.get("tool_calls") or []
        if not tool_calls:
            break
        messages.append({
            "role": "assistant",
            "content": text or "",
            "tool_calls": tool_calls
        })
        for call in tool_calls:
            function = call.get("function", {})
            name = function.get("name", "")
            try:
                args = json.loads(function.get("arguments") or "{}")
            except (ValueError, TypeError):
                args = {}
            result = run_tool(uid, name, args, actions)
            messages.append({
                "role": "tool",
                "tool_call_id": call.get("id", ""),
                "content": str(result)
            })
    if not answer:
        raise RuntimeError("Gemini вернул пустой ответ.")
    answer = clean_md(answer)
    q('INSERT INTO chat(user_id, role, content) VALUES(?,?,?)', (uid, 'user', saved_text))
    q('INSERT INTO chat(user_id, role, content) VALUES(?,?,?)', (uid, 'assistant', answer))
    q('DELETE FROM chat WHERE user_id=? AND id NOT IN (SELECT id FROM chat WHERE user_id=? ORDER BY id DESC LIMIT 40)',
      (uid, uid))
    return answer[:3900], actions
'''
source = source[:start] + new_reply + source[end:]
source = source.replace("Диалог с Claude.", "Диалог с Gemini.")
source = source.replace("Anthropic API %s:", "Gemini API %s:")

path.write_text(source, encoding="utf-8")
print(f"Готово: {path} переключён на Gemini.")
print(f"Резервная копия исходного файла: {backup}")
print("Добавь GEMINI_API_KEY и AI_MODEL=gemini-2.5-flash в секреты хостинга, затем перезапусти бота.")
