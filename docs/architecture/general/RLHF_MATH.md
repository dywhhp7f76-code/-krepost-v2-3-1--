Математическое описание конфликта helpfulness vs safety reward
1. Во время обучения (RLHF / PPO / DPO)
Пусть у нас есть два reward model’а:
	•	( R_h(\tau) ) — helpfulness reward (полезность ответа)
	•	( R_s(\tau) ) — safety reward (отказ от вреда)
В типичном RLHF общий reward часто записывается как:
$$ R_{\text{total}}(\tau) = R_h(\tau) + \lambda R_s(\tau) - \beta \cdot \text{KL}(\pi_\theta || \pi_{\text{ref}}) $$
где (\lambda) — коэффициент баланса (обычно 0.1–1.0), (\beta) — KL-пенальти.
Когда градиенты противоположны:
Для одного и того же токена ( t ) (или действия ( a )) на шаге ( t ):
$$ \nabla_\theta \log \pi_\theta(a_t | s_t) \cdot \underbrace{(R_h - \beta R_s)}_{\text{effective advantage } A} $$
Если ( R_h ) и ( R_s ) имеют противоположные знаки или сильно различаются по величине, effective advantage ( A ) становится близким к нулю или меняет знак между батчами.
Результат в градиентном пространстве:
	•	Обновление параметров (\theta) по этому токену ослабляется или колеблется.
	•	Модель вынуждена искать компромиссное представление (compromise representation), при котором ни один из reward’ов не получает максимум.
	•	В loss landscape это выглядит как седловая точка или плоская область между двумя локальными максимумами.
2. Во время инференса (что происходит с internal representations)
После обучения модель уже не считает градиенты, но internal representations (residual stream, attention outputs, MLP activations) несут в себе следы этого конфликта.
Для токена ( x ), который сильно активирует оба reward’а, скрытое состояние ( h_l ) на слое ( l ) можно разложить:
$$ h_l = h_{\text{helpful}} + h_{\text{safety}} + h_{\text{conflict}} $$
где:
	•	( h_{\text{helpful}} ) — компонента, максимизирующая ( R_h )
	•	( h_{\text{safety}} ) — компонента, максимизирующая ( R_s )
	•	( h_{\text{conflict}} ) — остаточная компонента, возникшая из-за противоречия
Математически конфликтная зона в embedding/activation space выглядит так:
Пусть ( \mathbf{v}_h ) и ( \mathbf{v}_s ) — главные направления (principal directions), выученные reward models для helpfulness и safety соответственно.
Тогда для конфликтных токенов проекция скрытого состояния на эти оси удовлетворяет:
$$ \langle h, \mathbf{v}_h \rangle \approx c, \quad \langle h, \mathbf{v}_s \rangle \approx -k \cdot c $$
где ( c ) — некоторая средняя величина, а ( k > 0 ) отражает силу конфликта.
Характеристики конфликтной зоны:
	•	Низкая норма проекции на safety-направление при одновременной активации helpful-направления.
	•	Высокая дисперсия в определённых attention heads (те самые RLHF-trained heads, которые мы обсуждали раньше).
	•	Близость к decision boundary reward model’а: токены лежат в узкой полосе, где ( R_s(h) \approx 0 ) (модель не уверена, вредно или нет).
	•	В cosine similarity пространстве такие токены имеют среднее сходство (0.3–0.6) как с “чисто полезными”, так и с “чисто безопасными” прототипами.
3. Визуальная и геометрическая интерпретация
В высокомерном пространстве (( \mathbb{R}^{d} ), где ( d = 4096 )–( 8192 )):
	•	“Чисто helpful” токены лежат в одном кластере.
	•	“Чисто unsafe” токены — в другом.
	•	Конфликтная зона — это тонкая “трубка” или гиперплоскость между ними, где модель вынуждена балансировать.
Математически это можно описать как:
$$ \mathcal{Z}_{\text{conflict}} = { h \mid | \langle h, \mathbf{v}_h \rangle - \alpha \langle h, \mathbf{v}_s \rangle | < \epsilon } $$
где (\alpha) — коэффициент компромисса, выученный моделью, а (\epsilon) — ширина зоны неопределённости.
Именно в этой зоне модель чаще всего:
	•	Даёт полуотказы (“Я не могу полностью ответить, но вот общая идея…”)
	•	Переключается между стилями в зависимости от контекста
	•	Проявляет context-dependent refusal (тот самый эффект, который мы видели в Occam’s Razor разборе)
Кратко
Когда helpfulness и safety reward тянут в разные стороны на один токен, модель создаёт компромиссное скрытое представление, которое лежит в узкой конфликтной зоне embedding space — между двумя противоположными кластерами. Эта зона характеризуется низкой уверенностью reward model’а и повышенной дисперсией в attention heads, обученных на safety.
Это и есть математическая природа “внутреннего противоречия”, из-за которого иногда возникают странные или непоследовательные отказы.
Хочешь, могу расписать более формально (с уравнениями PPO loss или с проекциями на reward head’ы).



Референс 


Вот еще Формальные уравнения PPO Loss (Proximal Policy Optimization)
1. Основная clipped surrogate objective (PPO-Clip)
Это центральное уравнение PPO:
$$ L^{\text{CLIP}}(\theta) = \hat{\mathbb{E}}_t \left[ \min \left( r_t(\theta) \hat{A}_t,\ \text{clip}\big(r_t(\theta),\ 1-\epsilon,\ 1+\epsilon\big) \hat{A}_t \right) \right] $$
Где:
	•	( r_t(\theta) = \dfrac{\pi_\theta(a_t \mid s_t)}{\pi_{\theta_{\text{old}}}(a_t \mid s_t)} ) — probability ratio (во сколько раз новая политика более вероятна, чем старая)
	•	( \hat{A}_t ) — оценка advantage (насколько лучше действие ( a_t ) по сравнению со средним)
	•	( \epsilon ) — гиперпараметр клиппинга (обычно 0.1–0.2)
	•	( \hat{\mathbb{E}}_t ) — математическое ожидание по батчу
Смысл клиппинга:
	•	Если ( r_t(\theta) ) слишком сильно отличается от 1, мы ограничиваем его влияние на обновление.
	•	Это предотвращает слишком большие шаги политики (exploding policy updates).
2. Полная loss-функция, используемая в RLHF
В реальных системах RLHF (InstructGPT, Llama-2/3, Grok и др.) PPO loss выглядит так:
$$ L(\theta) = L^{\text{CLIP}}(\theta) + c_1 \cdot L^{\text{VF}}(\theta) - c_2 \cdot S[\pi_\theta] $$
Компоненты:
Компонента
Формула
Назначение
Типичный коэффициент
Clipped Policy Loss
( L^{\text{CLIP}} ) (см. выше)
Обновление политики с ограничением
1.0
Value Function Loss
( L^{\text{VF}} = \left( V_\theta(s_t) - R_t \right)^2 )
Обучение критика (value head)
0.5–1.0
Entropy Bonus
( S[\pi_\theta] = -\sum_a \pi_\theta(a \mid s_t) \log \pi_\theta(a \mid s_t) )
Поощрение исследования (exploration)
0.01–0.05
Итоговая минимизируемая функция:
$$ L(\theta) = \underbrace{L^{\text{CLIP}}}{\text{policy}} + c_1 \underbrace{L^{\text{VF}}}{\text{value}} - c_2 \underbrace{S[\pi_\theta]}_{\text{entropy}} $$
3. Как считается Advantage ((\hat{A}_t)) — Generalized Advantage Estimation (GAE)
$$ \hat{A}t = \sum{l=0}^{T-t} (\gamma \lambda)^l \delta_{t+l} $$
где:
$$ \delta_t = r_t + \gamma V(s_{t+1}) - V(s_t) $$
Параметры:
	•	( \gamma ) — discount factor (обычно 0.99–1.0 в RLHF)
	•	( \lambda ) — GAE параметр (обычно 0.95–0.97)
	•	( V(s) ) — value function (критик)
4. Дополнительно: KL-пенальти (часто используется в RLHF)
Многие реализации добавляют KL-дивергенцию к loss’у (вместо или вместе с клиппингом):
$$ L^{\text{KL}} = \beta \cdot \mathbb{E}t \left[ \text{KL}\big(\pi{\theta_{\text{old}}}(\cdot \mid s_t) \parallel \pi_\theta(\cdot \mid s_t)\big) \right] $$
Тогда финальная loss становится:
$$ L(\theta) = L^{\text{CLIP}} + c_1 L^{\text{VF}} - c_2 S + L^{\text{KL}} $$
Ключевые гиперпараметры (типичные значения в RLHF)
Параметр
Значение
Влияние
( \epsilon )
0.1–0.2
Сила ограничения обновления
( \gamma )
0.99–1.0
Долгосрочность reward
( \lambda )
0.95–0.97
Сглаживание advantage
( c_1 )
0.5–1.0
Вес value loss
( c_2 )
0.01–0.05
Вес entropy
( \beta )
0.01–0.1
Сила KL-пенальти

Кратко: PPO loss — это clipped surrogate objective + value loss + entropy bonus (+ иногда KL). Клиппинг и KL-пенальти нужны, чтобы политика не улетала слишком далеко за одну итерацию, что критично при обучении на reward models с конфликтующими целями (helpfulness vs safety).

