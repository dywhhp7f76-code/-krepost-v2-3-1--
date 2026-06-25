#!/usr/bin/env python3
"""
Крепость — петля генерации кода: generator -> исполнение -> critic -> fix -> ...
Останов при "запускается чисто И critic PASS" либо по лимиту итераций.
Готовый артефакт НЕ применяется автоматически: кладётся в quarantine/ и ждёт
ручного одобрения (human_verdict gate).

Зачем так: генерация у LLM — слабая сторона, ревью и реальный запуск — сильные.
Петля ставит сильные перед слабой. Critic — ОТДЕЛЬНЫЙ вызов (в идеале другая
модель), чтобы не было самооправдания.

Запуск:
    python krepost_codeloop.py "опиши задачу для кода"
Модели берутся из локального OpenAI-совместимого эндпоинта (vLLM/Ollama и т.п.).
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import requests

# --- Конфиг: два РАЗНЫХ вызова, по возможности разные модели/семейства ---
BASE_URL = os.environ.get("KREPOST_LLM_URL", "http://localhost:11434/v1")
GENERATOR_MODEL = os.environ.get("KREPOST_GEN_MODEL", "qwen2.5-coder")
CRITIC_MODEL = os.environ.get("KREPOST_CRITIC_MODEL", "llama3.1")
API_KEY = os.environ.get("KREPOST_LLM_KEY", "local")  # локалке ключ обычно не нужен

MAX_ITERS = int(os.environ.get("KREPOST_MAX_ITERS", "4"))
EXEC_TIMEOUT = int(os.environ.get("KREPOST_EXEC_TIMEOUT", "30"))  # сек на запуск
QUARANTINE = Path(os.environ.get("KREPOST_QUARANTINE", "quarantine"))

GENERATOR_SYSTEM = (
    "Ты — инженер-генератор Крепости. Пишешь рабочий Python под задачу. "
    "Только код, без пояснений и без markdown-заборов. Если пришёл лог ошибки или "
    "замечания критика — чинишь именно их, не переписывая всё подряд. Не глотай "
    "исключения молча. Закрывай файлы/сессии. Ставь таймауты на сеть."
)

CRITIC_SYSTEM = (
    "Ты — независимый аудитор Крепости. Этот код писал НЕ ты. Найди, где он "
    "сломается, а не хвали. Проверь: запустимость, крайние случаи, обработку "
    "ошибок, ресурсы/таймауты, соответствие задаче, безопасность (никаких скрытых "
    "сетевых вызовов, eval/exec, записи вне рабочей папки). "
    'Верни СТРОГО JSON без текста вокруг: '
    '{"verdict":"PASS"|"FAIL","issues":["проблема + где", ...]}. '
    "PASS только если код реально запустится и решает задачу. Сомневаешься — FAIL."
)


def call_llm(model: str, system: str, user: str, temperature: float = 0.2) -> str:
    """Один вызов к OpenAI-совместимому эндпоинту. Падать с понятной ошибкой."""
    resp = requests.post(
        f"{BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {API_KEY}"},
        json={
            "model": model,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        },
        timeout=300,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def strip_code(text: str) -> str:
    """Вытащить код из ```...``` если модель всё же обернула его в забор."""
    m = re.search(r"```(?:python)?\s*(.*?)```", text, re.DOTALL)
    return (m.group(1) if m else text).strip()


def run_code(code: str) -> tuple[bool, str]:
    """Запуск в изолированной временной папке с таймаутом. (ok, лог)."""
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "candidate.py"
        f.write_text(code, encoding="utf-8")
        try:
            p = subprocess.run(
                [sys.executable, str(f)],
                capture_output=True,
                text=True,
                timeout=EXEC_TIMEOUT,
                cwd=tmp,
            )
        except subprocess.TimeoutExpired:
            return False, f"TIMEOUT: дольше {EXEC_TIMEOUT}s — вероятно вечный цикл/блокировка"
        if p.returncode == 0:
            return True, (p.stdout or "").strip()
        return False, f"EXIT {p.returncode}\nSTDERR:\n{p.stderr.strip()}"


def critique(model: str, task: str, code: str) -> tuple[bool, list[str]]:
    """Независимый разбор. Кривой JSON трактуем как FAIL (не доверяем)."""
    raw = call_llm(model, CRITIC_SYSTEM, f"Задача:\n{task}\n\nКод:\n{code}")
    raw = re.sub(r"```json|```", "", raw).strip()
    try:
        data = json.loads(raw)
        return data.get("verdict") == "PASS", list(data.get("issues", []))
    except json.JSONDecodeError:
        return False, [f"критик вернул не-JSON: {raw[:300]}"]


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit('Использование: python krepost_codeloop.py "задача"')
    task = sys.argv[1]

    feedback = ""  # сюда копим лог ошибки + замечания критика между итерациями
    code = ""
    for i in range(1, MAX_ITERS + 1):
        print(f"\n=== Итерация {i}/{MAX_ITERS} ===")
        prompt = task if not feedback else f"{task}\n\nИСПРАВЬ ПО ЗАМЕЧАНИЯМ:\n{feedback}"
        code = strip_code(call_llm(GENERATOR_MODEL, GENERATOR_SYSTEM, prompt))

        ok, log = run_code(code)
        print(f"[запуск] {'OK' if ok else 'СЛОМАЛОСЬ'}")
        if not ok:
            print(log)
            feedback = f"Код упал при запуске:\n{log}"
            continue

        passed, issues = critique(CRITIC_MODEL, task, code)
        print(f"[критик] {'PASS' if passed else 'FAIL'}")
        for it in issues:
            print(f"  - {it}")
        if passed:
            break
        feedback = "Критик нашёл проблемы:\n" + "\n".join(f"- {x}" for x in issues)
    else:
        print("\nЛимит итераций исчерпан — чистого PASS нет. Артефакт под подозрением.")

    # human_verdict gate: НЕ применяем, только в карантин + ручное одобрение.
    QUARANTINE.mkdir(parents=True, exist_ok=True)
    out = QUARANTINE / f"candidate_{uuid.uuid4().hex[:8]}.py"
    out.write_text(code, encoding="utf-8")
    print(f"\nАртефакт в карантине: {out}")
    print("Применение — только после твоей ручной проверки. Авто-применения нет.")


if __name__ == "__main__":
    main()
