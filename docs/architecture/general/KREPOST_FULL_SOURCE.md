🏰 КРЕПОСТЬ — Полный исходный код (All-in-One)

Полный исходный код системы «Крепость» в одном сообщении  
Скопируйте структуру папок и файлы — система готова к запуску.

📁 Структура проекта

📄 КОНФИГУРАЦИОННЫЕ ФАЙЛЫ

.env.example (переименуйте в .env и заполните)

config.yaml

requirements.txt

docker-compose.yml

config/prometheus.yml

🐍 PYTHON МОДУЛИ (krepost/)

krepost/init.py

krepost/config.py

krepost/models.py

🤖 АГЕНТЫ И ПРОМПТЫ

prompts/krepostbase.md

markdown
Ответ
[суть ответа]

Источники
[[Заметка1]]
[[Заметка2#Раздел]]

Уровень уверенности
[Высокий/Средний/Низкий] — [обоснование]

prompts/teacher.md

markdown
🎓 Ответ Учителя

Суть
[краткая суть ответа в 2-3 предложения]

Подробно
[развёрнутое объяснение с примерами]

Пошагово (если применимо)
[шаг 1]
[шаг 2]

⚠️ Типичные ошибки
[ошибка 1]
[ошибка 2]

📚 Источники
[[Заметка1]]
[[Заметка2#Раздел]]

Уверенность: [Высокая/Средняя/Низкая]

prompts/critic.md

markdown
🔍 Аудит Критика

❌ Найденные проблемы
| # | Проблема | Критичность | Доказательство | Рекомендация |
|---|----------|-------------|----------------|--------------|
| 1 | [проблема] | 🔴/🟡/🟢 | [цитата/факт] | [как исправить] |

⚠️ Риски
[риск 1]: [последствия]
[риск 2]: [последствия]

✅ Что хорошо
[плюс 1]
[плюс 2]

📋 Чек-лист исправлений
[ ] [действие 1]
[ ] [действие 2]

Уверенность: [Высокая/Средняя/Низкая]

prompts/researcher.md

markdown
🔬 Расследование Исследователя

Запрос
[исходный вопрос]

Найденные факты
| Факт | Источник | Дата | Уверенность |
|-------|----------|------|-------------|
| [факт 1] | [[Источник1]] | 2024-01 | 95% |
| [факт 2] | [[Источник2]] | 2023-11 | 80% |

Противоречия
[если есть противоречивые данные]

Пробелы в данных
[что не найдено / требует уточнения]

Резюме
[краткое резюме в 3-5 пунктах]

Источники
[[Источник1#Раздел]]
[[Источник2]]

Уверенность: [Высокая/Средняя/Низкая]

prompts/psycho.md

markdown
💀 ВЕРДИКТ ПСИХОПАТА

Суть (без прикрас)
[одна фраза — суть проблемы]

Почему это хуйня / гениально
[жёсткий разбор без прикрас]

Что делать (если не хочешь провалиться)
[жёсткое действие 1]
[жёсткое действие 2]

Риск игнорирования
[что будет, если не слушаешь]

Источники (если есть)
[[Заметка]]

Уверенность: 100% (или не стоило спрашивать)

prompts/synthesizer.md

markdown
⚖️ ВЕРДИКТ СОВЕТА

Итоговый ответ
[объединённый практический ответ]

🔑 Ключевые инсайты
[инсайт 1]
[инсайт 2]

⚔️ Конфлекты агентов
Критик vs Учитель: [суть разногласия]
Психопат vs Исследователь: [суть разногласия]

⚖️ Взвешенное решение
[почему принято именно это решение]

📋 План действий
[шаг 1]
[шаг 2]

⚠️ Риски и предупреждения
[риск 1]
[риск 2]

📚 Объединённые источники
[[Источник1]]
[[Источник2]]

Уверенность Совета: [Высокая/Средняя/Низкая]

prompts/qualityassessor.md

json
{
  "isgood": false,
  "reason": "Содержит фразу отказа; Слишком короткий ответ на сложный запрос",
  "confidence": 0.3,
  "shouldfallback": true
}

prompts/improvementanalyzer.md

json
{
  "targetversion": "v1.0",
  "proposedchanges": [
    "Fix: refusal - добавлена инструкция давать гипотезы вместо отказов",
    "Fix: tooshort - требование развёрнутых ответов на сложные вопросы"
  ],
  "newsystemprompt": "[ПОЛНЫЙ НОВЫЙ ПРОМПТ]",
  "rationale": "Автоматическое исправление: частые отказы и короткие ответы",
  "estimatedimpact": {
    "refusal": 0.7,
    "tooshort": 0.6
  }
}

🔧 ОСНОВНЫЕ СКРИПТЫ

krepost/rag/ultimaterag.py

krepost/agents/councilmode.py

krepost/fallback/smartfallback.py (ИСПРАВЛЕННАЯ ВЕРСИЯ)

```python
"""Smart Fallback System — Production Ready v2"""
import asyncio
import aiohttp
import time
import logging
from typing import Optional, Literal, Dict, Any
from pydantic import BaseModel, Field
from dataclasses import dataclass
from contextlib import asynccontextmanager

logger = logging.getLogger("SmartFallback")

class CloudProviderConfig(BaseModel):
    name: Literal["venice", "grok"]
    apikey: str
    baseurl: str  # БЕЗ /chat/completions
    model: str
    pricepermillioninput: float = 0.0
    pricepermillionoutput: float = 0.0

class FallbackConfig(BaseModel):
    venice: CloudProviderConfig
    grok: CloudProviderConfig
    circuitbreakerthreshold: int = 3
    circuitbreakercooldownseconds: int = 300
    minresponselength: int = 50
    ragscorethreshold: float = 0.6

class QualityAssessment(BaseModel):
    isgood: bool
    reason: str
    confidence: float = Field(ge=0.0, le=1.0)
    shouldfallback: bool

class RouteDecision(BaseModel):
    usecloud: bool
    provider: Optional[Literal["venice", "grok"]] = None
    reason: str
    costestimate: Optional[float] = None

class CloudResponse(BaseModel):
    content: str
    model: str
    provider: str
    inputtokens: int
    outputtokens: int
    cost: float
    duration: float

class PerProviderCircuitBreaker:
    """Circuit breaker на провайдер"""
    def init(self, threshold: int = 3, cooldownseconds: int = 300):
        self.threshold = threshold
        self.cooldown = cooldownseconds
        self.failurecount = 0
        self.lastfailuretime = 0
        self.isopen = False
        self.state = "closed"  # closed, open, halfopen
    
    def recordfailure(self):
        self.failurecount += 1
        self.lastfailuretime = time.time()
        if self.failurecount >= self.threshold:
            self.isopen = True
            self.state = "open"
            logger.warning(f"Circuit breaker OPENED for {self.cooldown}s")
    
    def recordsuccess(self):
        self.failurecount = 0
        self.isopen = False
        self.state = "closed"
    
    def canexecute(self) -> bool:
        if not self.isopen:
            return True
        if time.time() - self.lastfailuretime > self.cooldown:
            self.isopen = False
            self.failurecount = 0
            self.state = "halfopen"
            logger.info("Circuit breaker HALFOPEN")
            return True
        return False

class QualityAssessor:
    REFUSALPHRASES = [
        "не знаю", "не уверен", "недостаточно данных", "не могу ответить",
        "не имею информации", "извините", "не могу помочь", "нет информации"
    ]
    
    def assess(
        self,
        response: str,
        query: str,
        ragscore: float = 1.0,
        forcecloud: bool = False
    ) -> dict:
        response = response.strip()
        querytokens = len(query.split())
        reasons = []
        confidence = 0.8
        
        if any(phrase in response.lower() for phrase in self.REFUSALPHRASES):
            reasons.append("Содержит фразу отказа")
            confidence = 0.3
        
        if len(response) < 50 and querytokens > 25:
            reasons.append("Слишком короткий ответ на сложный запрос")
            confidence = min(confidence, 0.4)
        
        if ragscore < 0.6:
            reasons.append(f"Низкий RAG retrieval score ({ragscore:.2f})")
            confidence = min(confidence, 0.5)
        
        if forcecloud:
            reasons.append("Принудительный fallback")
            confidence = 0.1
        
        shouldfallback = len(reasons) > 0 or confidence < 0.55
        
        return {
            "isgood": not shouldfallback,
            "reason": "; ".join(reasons) if reasons else "Качество приемлемое",
            "confidence": confidence,
            "shouldfallback": shouldfallback
        }

class CloudEngine:
    def init(self, config: dict):
        self.config = config
        self.session: Optional[aiohttp.ClientSession] = None
        self.sessionlock = asyncio.Lock()
    
    @asynccontextmanager
    async def sessionmanager(self):
        async with self.sessionlock:
            if self.session is None or self.session.closed:
                timeout = aiohttp.ClientTimeout(total=90, connect=10)
                self.session = aiohttp.ClientSession(timeout=timeout)
            yield self.session
    
    async def generate(
        self,
        provider: Literal["venice", "grok"],
        prompt: str,
        systemprompt: Optional[str] = None,
        ragcontext: str = "",
        temperature: float = 0.7,
        maxtokens: int = 2048,
    8,
    ) -> dict:
        cfg = self.config[provider]
        
        # Build messages with RAG context
        messages = []
        if systemprompt:
            messages.append({"role": "system", "content": systemprompt})
        if ragcontext:
            messages.append({"role": "system", "content": f"Контекст из базы знаний:\n{ragcontext}"})
        messages.append({"role": "user", "content": prompt})
        
        payload = {
            "model": cfg["model"],
            "messages": messages,
            "temperature": temperature,
            "maxtokens": maxtokens,
            "stream": False,
        }
        
        headers = {
            "Authorization": f"Bearer {cfg['apikey']}",
            "Content-Type": "application/json",
        }
        
        url = f"{cfg['baseurl']}/chat/completions"
        
        async with self.sessionmanager() as session:
            start = time.time()
            for attempt in range(3):
                try:
                    async with session.post(url, json=payload, headers=headers) as resp:
                        if resp.status != 200:
                            text = await resp.text()
                            if resp.status == 429:
                                await asyncio.sleep(2  attempt)
                                continue
                            raise Exception(f"{provider} API {resp.status}: {text}")
                        
                        data = await resp.json()
                        message = data["choices"][0]["message"]["content"]
                        
                        usage = data.get("usage", {})
                        inputtokens = usage.get("prompttokens", 0)
                        outputtokens = usage.get("completiontokens", 0)
                        
                        cfgpricing = self.config["pricing"][provider]
                        cost = (
                            inputtokens / 1000000  cfgpricing["input"] +
                            outputtokens / 1000000  cfgpricing["output"]
                        )
                        
                        return {
                            "content": message,
                            "model": cfg["model"],
                            "provider": provider,
                            "inputtokens": inputtokens,
                            "outputtokens": outputtokens,
                            "cost": cost,
                            "duration": time.time() - start,
                        }
                except asyncio.TimeoutError:
                    if attempt == 2:
                        raise
                    await asyncio.sleep(1.5  (attempt + 1))
                except Exception as e:
                    if attempt == 2:
                        raise
                    await asyncio.sleep(1.5  (attempt + 1))
            raise Exception("Max retries exceeded")

class SmartRouter:
    def init(self, config: dict):
        self.config = config
        self.cloudengine = CloudEngine(config)
        self.circuitbreakers = {
            "venice": CircuitBreaker(config.get("circuitbreaker", {})),
            "grok": CircuitBreaker(config.get("circuitbreaker", {})),
        }
        self.assessor = QualityAssessor()
        self.totalcloudcost = 0.0
        self.stats = {"local": 0, "cloud": 0, "fallbackfailed": 0}
    
    async def route(
        self,
        query: str,
        localresponse: str,
        ragcontext: str,
        ragscore: float = 1.0,
        forcecloud: bool = False,
        preferredcloud: Literal["venice", "grok"] = "venice"
    ) -> tuple[dict, Optional[dict]]:
        
        assessment = self.assessor.assess(localresponse, query, ragscore, forcecloud)
        
        if not assessment["shouldfallback"]:
            self.stats["local"] += 1
            return {
                "usecloud": False,
                "reason": "Локальный ответ качественный",
                "provider": None,
                "costestimate": 0.0
            }, None
        
        # Check circuit breaker for preferred provider
        if not self.circuitbreakers[preferredcloud].canexecute():
            # Try alternative
            alt = "grok" if preferredcloud == "venice" else "venice"
            if self.circuitbreakers[alt].canexecute():
                preferredcloud = alt
            else:
                return {
                    "usecloud": False,
                    "reason": "Все облачные провайдеры в circuit breaker",
                    "provider": None,
                    "costestimate": 0.0
                }, None
        
        try:
            async with CloudEngine(self.config["providers"]) as cloud:
                cloudresponse = await cloud.generate(
                    provider=preferredcloud,
                    prompt=query,
                    systemprompt="Ты — полезный и точный ассистент.",
                    ragcontext=ragcontext,  # ВАЖНО: передаем контекст!
                )
            
            self.circuitbreakers[preferredcloud].recordsuccess()
            self.totalcloudcost += cloudresponse["cost"]
            self.stats["cloud"] += 1
            
            return {
                "usecloud": True,
                "provider": preferredcloud,
                "reason": "Fallback: " + "; ".join(["Причина fallback"]),
                "costestimate": cloudresponse["cost"]
            }, cloudresponse
            
        except Exception as e:
            self.circuitbreakers[preferredcloud].recordfailure()
            logger.error(f"Cloud fallback failed: {e}")
            self.stats["fallbackfailed"] += 1
            return {
                "usecloud": False,
                "reason": f"Облако недоступно: {str(e)}",
                "provider": None,
                "costestimate": 0.0
            }, None
    
    def getstats(self) -> dict:
        return {
            "totalcloudcostusd": round(self.totalcloudcost, 4),
            "localrequests": self.stats["local"],
            "cloudrequests": self.stats["cloud"],
            "fallbackfailed": self.stats["fallbackfailed"],
            "circuitbreakers": {
                k: {"open": v.isopen, "failures": v.failurecount}
                for k, v in self.circuitbreakers.items()