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
  &quot;isgood&quot;: false,
  &quot;reason&quot;: &quot;Содержит фразу отказа; Слишком короткий ответ на сложный запрос&quot;,
  &quot;confidence&quot;: 0.3,
  &quot;shouldfallback&quot;: true
}

prompts/improvementanalyzer.md

json
{
  &quot;targetversion&quot;: &quot;v1.0&quot;,
  &quot;proposedchanges&quot;: [
    &quot;Fix: refusal - добавлена инструкция давать гипотезы вместо отказов&quot;,
    &quot;Fix: tooshort - требование развёрнутых ответов на сложные вопросы&quot;
  ],
  &quot;newsystemprompt&quot;: &quot;[ПОЛНЫЙ НОВЫЙ ПРОМПТ]&quot;,
  &quot;rationale&quot;: &quot;Автоматическое исправление: частые отказы и короткие ответы&quot;,
  &quot;estimatedimpact&quot;: {
    &quot;refusal&quot;: 0.7,
    &quot;tooshort&quot;: 0.6
  }
}

🔧 ОСНОВНЫЕ СКРИПТЫ

krepost/rag/ultimaterag.py

krepost/agents/councilmode.py

krepost/fallback/smartfallback.py (ИСПРАВЛЕННАЯ ВЕРСИЯ)

```python
&quot;&quot;&quot;Smart Fallback System — Production Ready v2&quot;&quot;&quot;
import asyncio
import aiohttp
import time
import logging
from typing import Optional, Literal, Dict, Any
from pydantic import BaseModel, Field
from dataclasses import dataclass
from contextlib import asynccontextmanager

logger = logging.getLogger(&quot;SmartFallback&quot;)

class CloudProviderConfig(BaseModel):
    name: Literal[&quot;venice&quot;, &quot;grok&quot;]
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
    provider: Optional[Literal[&quot;venice&quot;, &quot;grok&quot;]] = None
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
    &quot;&quot;&quot;Circuit breaker на провайдер&quot;&quot;&quot;
    def init(self, threshold: int = 3, cooldownseconds: int = 300):
        self.threshold = threshold
        self.cooldown = cooldownseconds
        self.failurecount = 0
        self.lastfailuretime = 0
        self.isopen = False
        self.state = &quot;closed&quot;  # closed, open, halfopen
    
    def recordfailure(self):
        self.failurecount += 1
        self.lastfailuretime = time.time()
        if self.failurecount &gt;= self.threshold:
            self.isopen = True
            self.state = &quot;open&quot;
            logger.warning(f&quot;Circuit breaker OPENED for {self.cooldown}s&quot;)
    
    def recordsuccess(self):
        self.failurecount = 0
        self.isopen = False
        self.state = &quot;closed&quot;
    
    def canexecute(self) -&gt; bool:
        if not self.isopen:
            return True
        if time.time() - self.lastfailuretime &gt; self.cooldown:
            self.isopen = False
            self.failurecount = 0
            self.state = &quot;halfopen&quot;
            logger.info(&quot;Circuit breaker HALFOPEN&quot;)
            return True
        return False

class QualityAssessor:
    REFUSALPHRASES = [
        &quot;не знаю&quot;, &quot;не уверен&quot;, &quot;недостаточно данных&quot;, &quot;не могу ответить&quot;,
        &quot;не имею информации&quot;, &quot;извините&quot;, &quot;не могу помочь&quot;, &quot;нет информации&quot;
    ]
    
    def assess(
        self,
        response: str,
        query: str,
        ragscore: float = 1.0,
        forcecloud: bool = False
    ) -&gt; dict:
        response = response.strip()
        querytokens = len(query.split())
        reasons = []
        confidence = 0.8
        
        if any(phrase in response.lower() for phrase in self.REFUSALPHRASES):
            reasons.append(&quot;Содержит фразу отказа&quot;)
            confidence = 0.3
        
        if len(response) &lt; 50 and querytokens &gt; 25:
            reasons.append(&quot;Слишком короткий ответ на сложный запрос&quot;)
            confidence = min(confidence, 0.4)
        
        if ragscore &lt; 0.6:
            reasons.append(f&quot;Низкий RAG retrieval score ({ragscore:.2f})&quot;)
            confidence = min(confidence, 0.5)
        
        if forcecloud:
            reasons.append(&quot;Принудительный fallback&quot;)
            confidence = 0.1
        
        shouldfallback = len(reasons) &gt; 0 or confidence &lt; 0.55
        
        return {
            &quot;isgood&quot;: not shouldfallback,
            &quot;reason&quot;: &quot;; &quot;.join(reasons) if reasons else &quot;Качество приемлемое&quot;,
            &quot;confidence&quot;: confidence,
            &quot;shouldfallback&quot;: shouldfallback
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
        provider: Literal[&quot;venice&quot;, &quot;grok&quot;],
        prompt: str,
        systemprompt: Optional[str] = None,
        ragcontext: str = &quot;&quot;,
        temperature: float = 0.7,
        maxtokens: int = 2048,
    8,
    ) -&gt; dict:
        cfg = self.config[provider]
        
        # Build messages with RAG context
        messages = []
        if systemprompt:
            messages.append({&quot;role&quot;: &quot;system&quot;, &quot;content&quot;: systemprompt})
        if ragcontext:
            messages.append({&quot;role&quot;: &quot;system&quot;, &quot;content&quot;: f&quot;Контекст из базы знаний:\n{ragcontext}&quot;})
        messages.append({&quot;role&quot;: &quot;user&quot;, &quot;content&quot;: prompt})
        
        payload = {
            &quot;model&quot;: cfg[&quot;model&quot;],
            &quot;messages&quot;: messages,
            &quot;temperature&quot;: temperature,
            &quot;maxtokens&quot;: maxtokens,
            &quot;stream&quot;: False,
        }
        
        headers = {
            &quot;Authorization&quot;: f&quot;Bearer {cfg[&#x27;apikey&#x27;]}&quot;,
            &quot;Content-Type&quot;: &quot;application/json&quot;,
        }
        
        url = f&quot;{cfg[&#x27;baseurl&#x27;]}/chat/completions&quot;
        
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
                            raise Exception(f&quot;{provider} API {resp.status}: {text}&quot;)
                        
                        data = await resp.json()
                        message = data[&quot;choices&quot;][0][&quot;message&quot;][&quot;content&quot;]
                        
                        usage = data.get(&quot;usage&quot;, {})
                        inputtokens = usage.get(&quot;prompttokens&quot;, 0)
                        outputtokens = usage.get(&quot;completiontokens&quot;, 0)
                        
                        cfgpricing = self.config[&quot;pricing&quot;][provider]
                        cost = (
                            inputtokens / 1000000  cfgpricing[&quot;input&quot;] +
                            outputtokens / 1000000  cfgpricing[&quot;output&quot;]
                        )
                        
                        return {
                            &quot;content&quot;: message,
                            &quot;model&quot;: cfg[&quot;model&quot;],
                            &quot;provider&quot;: provider,
                            &quot;inputtokens&quot;: inputtokens,
                            &quot;outputtokens&quot;: outputtokens,
                            &quot;cost&quot;: cost,
                            &quot;duration&quot;: time.time() - start,
                        }
                except asyncio.TimeoutError:
                    if attempt == 2:
                        raise
                    await asyncio.sleep(1.5  (attempt + 1))
                except Exception as e:
                    if attempt == 2:
                        raise
                    await asyncio.sleep(1.5  (attempt + 1))
            raise Exception(&quot;Max retries exceeded&quot;)

class SmartRouter:
    def init(self, config: dict):
        self.config = config
        self.cloudengine = CloudEngine(config)
        self.circuitbreakers = {
            &quot;venice&quot;: CircuitBreaker(config.get(&quot;circuitbreaker&quot;, {})),
            &quot;grok&quot;: CircuitBreaker(config.get(&quot;circuitbreaker&quot;, {})),
        }
        self.assessor = QualityAssessor()
        self.totalcloudcost = 0.0
        self.stats = {&quot;local&quot;: 0, &quot;cloud&quot;: 0, &quot;fallbackfailed&quot;: 0}
    
    async def route(
        self,
        query: str,
        localresponse: str,
        ragcontext: str,
        ragscore: float = 1.0,
        forcecloud: bool = False,
        preferredcloud: Literal[&quot;venice&quot;, &quot;grok&quot;] = &quot;venice&quot;
    ) -&gt; tuple[dict, Optional[dict]]:
        
        assessment = self.assessor.assess(localresponse, query, ragscore, forcecloud)
        
        if not assessment[&quot;shouldfallback&quot;]:
            self.stats[&quot;local&quot;] += 1
            return {
                &quot;usecloud&quot;: False,
                &quot;reason&quot;: &quot;Локальный ответ качественный&quot;,
                &quot;provider&quot;: None,
                &quot;costestimate&quot;: 0.0
            }, None
        
        # Check circuit breaker for preferred provider
        if not self.circuitbreakers[preferredcloud].canexecute():
            # Try alternative
            alt = &quot;grok&quot; if preferredcloud == &quot;venice&quot; else &quot;venice&quot;
            if self.circuitbreakers[alt].canexecute():
                preferredcloud = alt
            else:
                return {
                    &quot;usecloud&quot;: False,
                    &quot;reason&quot;: &quot;Все облачные провайдеры в circuit breaker&quot;,
                    &quot;provider&quot;: None,
                    &quot;costestimate&quot;: 0.0
                }, None
        
        try:
            async with CloudEngine(self.config[&quot;providers&quot;]) as cloud:
                cloudresponse = await cloud.generate(
                    provider=preferredcloud,
                    prompt=query,
                    systemprompt=&quot;Ты — полезный и точный ассистент.&quot;,
                    ragcontext=ragcontext,  # ВАЖНО: передаем контекст!
                )
            
            self.circuitbreakers[preferredcloud].recordsuccess()
            self.totalcloudcost += cloudresponse[&quot;cost&quot;]
            self.stats[&quot;cloud&quot;] += 1
            
            return {
                &quot;usecloud&quot;: True,
                &quot;provider&quot;: preferredcloud,
                &quot;reason&quot;: &quot;Fallback: &quot; + &quot;; &quot;.join([&quot;Причина fallback&quot;]),
                &quot;costestimate&quot;: cloudresponse[&quot;cost&quot;]
            }, cloudresponse
            
        except Exception as e:
            self.circuitbreakers[preferredcloud].recordfailure()
            logger.error(f&quot;Cloud fallback failed: {e}&quot;)
            self.stats[&quot;fallbackfailed&quot;] += 1
            return {
                &quot;usecloud&quot;: False,
                &quot;reason&quot;: f&quot;Облако недоступно: {str(e)}&quot;,
                &quot;provider&quot;: None,
                &quot;costestimate&quot;: 0.0
            }, None
    
    def getstats(self) -&gt; dict:
        return {
            &quot;totalcloudcostusd&quot;: round(self.totalcloudcost, 4),
            &quot;localrequests&quot;: self.stats[&quot;local&quot;],
            &quot;cloudrequests&quot;: self.stats[&quot;cloud&quot;],
            &quot;fallbackfailed&quot;: self.stats[&quot;fallbackfailed&quot;],
            &quot;circuitbreakers&quot;: {
                k: {&quot;open&quot;: v.isopen, &quot;failures&quot;: v.failurecount}
                for k, v in self.circuitbreakers.items()