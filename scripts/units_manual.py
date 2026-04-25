"""Hand-authored description units for CHP-project spike.

20 function_unit (Python, anchored to graph fn_id)
 7 traceability_unit (JS UI tabs, hardcoded outside graph)
"""

# ---------------------------------------------------------------------------
# 1-8: original Python core
# 9-20: extended Python coverage (TESPy, MILP-rest, calibration, climate, UI render, infra)
# ---------------------------------------------------------------------------

PYTHON_UNITS = [
    {
        "id": "engine/chp_network.py::extract_coefficients",
        "kind": "function_unit",
        "name": "extract_coefficients",
        "anchor": {"file": "engine/chp_network.py", "fn": "extract_coefficients"},
        "description": (
            "Извлекает коэффициенты линеаризации MILP-задачи из решённой "
            "термодинамической модели TESPy, формируя dataclass `LinearCoeffs` "
            "с энтальпиями `W_HP`, `W_MP_PT`, `W_LP_PT`, `W_COND`, `W_MP_T`, "
            "`W_R`, `DH_HEAT`, `H_FW` и долей регенерации `K_regen`. Метод "
            "`CHPNetwork.extract_coefficients()`, дёргается из SLP-цикла "
            "`solve_design()` сразу после `net.solve('design')`, чтобы "
            "передать обновлённые коэффициенты в следующую итерацию MILP — "
            "это и есть «калибровка MILP по TESPy», «recalibration of linear "
            "coefficients». `K_regen` вычисляется как `(ПВД + ПНД) / D_total`, "
            "а не только по первой ПТ-турбине, иначе constraint 4b "
            "(`m_i = regen_i + prod_i + heat_i + cond_i`) даст неверный "
            "`cond_i`. При отсутствии активных ПТ-турбин подставляются "
            "жёстко зашитые значения `LinearCoeffs.hardcoded()`."
        ),
    },
    {
        "id": "engine/chp_model.py::solve_design",
        "kind": "function_unit",
        "name": "CHPPlant.solve_design",
        "anchor": {"file": "engine/chp_model.py", "fn": "solve_design", "class": "CHPPlant"},
        "description": (
            "Рассчитывает оптимальный режим работы ТЭЦ на одну расчётную "
            "точку, минимизируя расход топлива при заданных нагрузках по "
            "теплу (`heat_demand`, Гкал/ч) и пару (`steam_demand`, т/ч). "
            "Точка входа из `api_calculate` и помесячного цикла — выполняет "
            "калибровку TESPy, первый MILP, затем SLP-итерации (Sequential "
            "Linear Programming), на каждой пересобирая `LinearCoeffs` из "
            "термодинамики и перерешая MILP. Это «расчёт режима», "
            "«design-point optimization», «MILP↔TESPy iteration». Сходимость "
            "определяется одновременно по трём критериям: `H_FW`, `K_regen` "
            "и отсутствию `cond_violations`; результат и `slp_iterations` "
            "пишутся в return-словарь. Использует `calib_cache` для тёплого "
            "старта и накапливает `K_regen_per_turb` между итерациями, "
            "иначе MILP осциллирует при переключении турбин."
        ),
    },
    {
        "id": "engine/equations.py::build_constraints",
        "kind": "function_unit",
        "name": "build_constraints",
        "anchor": {"file": "engine/equations.py", "fn": "build_constraints"},
        "description": (
            "Собирает систему линейных ограничений MILP-задачи оптимизации "
            "режима ТЭЦ: возвращает кортеж `(A_rows, integrality, lb, ub, "
            "heat_row_idx)` для передачи в `scipy.optimize.milp`. Вызывается "
            "из `solve_milp()` и формирует восемь групп — Big-M связки "
            "активности турбин, балансы пара и тепла, per-turbine балансы "
            "4a/4b/4c, глобальный баланс с регенерацией, capacity-ограничения "
            "котлов, пользовательские `constraints` и диаграмма ПТ-65/75. Это "
            "«constraint matrix», «assemble MILP rows», «уравнения баланса». "
            "Ключевая тонкость — per-turbine коэффициенты регенерации читаются "
            "из `K_regen_per_turb`, если задан, иначе используется глобальный "
            "`K_regen`, что критично для корректного решения SLP-цикла при "
            "переключении турбин."
        ),
    },
    {
        "id": "engine/unified_solver.py::solve_milp",
        "kind": "function_unit",
        "name": "solve_milp",
        "anchor": {"file": "engine/unified_solver.py", "fn": "solve_milp"},
        "description": (
            "Решает смешанно-целочисленную задачу линейной оптимизации режима "
            "ТЭЦ через `scipy.optimize.milp`, выбирая подмножество активных "
            "турбин и значения непрерывных переменных при заданных коэффициентах "
            "`LinearCoeffs`. Вызывается из `solve_design()` на каждой итерации "
            "SLP-цикла; возвращает dict со `scenario`, x-вектором, `total_steam`, "
            "`P_net_mw` и `milp_per_turbine`. Это «MILP solver», «оптимизатор "
            "режима», «LP-решение». При infeasible-результате автоматически "
            "включает БРОУ-16 (расширяет `heat_row` коэффициентом `DH_HEAT/3600`) "
            "и решает повторно. Округляет только бинарные индексы 17..24; явные "
            "переменные `regen_i` и `cond_i` (25..32) остаются непрерывными, "
            "иначе ломается per-turbine баланс."
        ),
    },
    {
        "id": "app.py::api_calculate",
        "kind": "function_unit",
        "name": "api_calculate",
        "anchor": {"file": "app.py", "fn": "api_calculate", "route": "POST /api/calculate"},
        "description": (
            "Принимает `POST /api/calculate` от UI и запускает расчёт режима "
            "ТЭЦ на один или двенадцать месяцев. HTTP-эндпоинт Flask, дёргается "
            "JS-фронтендом по кнопке Расчёт; парсит body со `scenario`, "
            "overrides спроса, `disabled_turbines`, `mode` (`year`/`month`/"
            "`single`), `constraints`, `objective` и помесячные `monthly_inputs`. "
            "Это «calculate endpoint», «запуск расчёта», «main optimization API». "
            "Внутри применяет `apply_scenario` к конфигу, считает помесячные "
            "нагрузки через `calc_monthly_loads`, и для каждой точки вызывает "
            "`CHPPlant.solve_design()`. Результат сохраняется в глобальный "
            "`_last_results`, чтобы `/api/tespy/formulas` позднее мог отдать "
            "live-коэффициенты последнего расчёта."
        ),
    },
    {
        "id": "app.py::api_tespy_formulas",
        "kind": "function_unit",
        "name": "api_tespy_formulas",
        "anchor": {"file": "app.py", "fn": "api_tespy_formulas", "route": "GET /api/tespy/formulas"},
        "description": (
            "Отдаёт каталог формул для вкладки Расчёт/Формулы фронтенда, "
            "подменяя статическую запись `optimizer_balance` live-версией с "
            "актуальными коэффициентами `LinearCoeffs` последнего расчёта. "
            "HTTP `GET`-эндпоинт Flask, читает базовый JSON из "
            "`data/tespy_formulas.json`, затем приоритетно достаёт "
            "`milp_coeffs` из глобального `_last_results` (последний успешный "
            "месячный результат); fallback — свежий вызов "
            "`calibrate(_load_config())`. Это «formulas API», «live equations "
            "catalog», «UI formula coefficients». Если `_last_results` пуст — "
            "возвращает design-point калибровку, чтобы UI всегда видел "
            "численные значения `W_HP`, `K_regen` и др., а не плейсхолдеры."
        ),
    },
    {
        "id": "engine/chp_model.py::_format",
        "kind": "function_unit",
        "name": "CHPPlant._format",
        "anchor": {"file": "engine/chp_model.py", "fn": "_format", "class": "CHPPlant"},
        "description": (
            "Преобразует сырой словарь `CHPNetwork.get_results()` в форму, "
            "ожидаемую фронтендом: блок `turbines` с поэлементными `steam`/"
            "`prod`/`heat`/`cond`/`pvd`/`P_MW` по именам `PT1..R4`, блок "
            "`rou` с расходами и подмесом, totals и параметры активных "
            "котлов. Метод `CHPPlant._format()`, вызывается в `solve_design()` "
            "после каждого успешного TESPy-solve, перед возвратом наружу через "
            "`api_calculate`. Это «format UI response», «shape result JSON», "
            "«UI-преобразование результатов». Округляет численные поля до "
            "десятой, вычисляет число активных котлов как "
            "`ceil(total_steam / cap_each)` с верхней границей `n_boilers`; "
            "для каждой турбины из конфига, отсутствующей в `res`, "
            "подставляется блок нулей, чтобы JS не падал."
        ),
    },
    {
        "id": "engine/chp_model.py::_cond_violations",
        "kind": "function_unit",
        "name": "_cond_violations",
        "anchor": {"file": "engine/chp_model.py", "fn": "_cond_violations"},
        "description": (
            "Сравнивает конденсат, предсказанный MILP-моделью, с фактическим "
            "из TESPy для каждой активной ПТ/Т-турбины и возвращает список "
            "нарушений с дельтой и `k_pvd_actual`. Используется `solve_design()` "
            "как один из трёх критериев сходимости SLP-цикла (наряду с `H_FW` "
            "и `K_regen`) — пока есть нарушения, цикл продолжает пересобирать "
            "коэффициенты. Это «cond violations», «нарушения по конденсату», "
            "«MILP/TESPy mismatch check». Приоритет отдаётся явной MILP-"
            "переменной `cond_i` из `milp_per_turbine`; fallback на аппроксимацию "
            "`K_COND × m_i − prod_i − heat_i`. Турбины с расходом меньше 1 т/ч "
            "пропускаются (выключены), порог нарушения `COND_DELTA_OK = 1.0` т/ч."
        ),
    },
    {
        "id": "engine/chp_network.py::_build",
        "kind": "function_unit",
        "name": "CHPNetwork._build",
        "anchor": {"file": "engine/chp_network.py", "fn": "_build", "class": "CHPNetwork"},
        "description": (
            "Конструирует полную термодинамическую сеть TESPy под текущий "
            "`scenario`: создаёт активные турбины (`PT1`/`PT2`/`PT6`/`T5`/"
            "`R3`/`R4`) через `build_pt_turbine`/`build_t_turbine`/"
            "`build_r_turbine`, мерджи производственного, конденсатного, "
            "ПВД- и теплофикационного коллекторов, деаэратор на 6 входов, "
            "котлы и питательную воду. Метод `CHPNetwork._build()`, дёргается "
            "из `__init__` перед `nw.solve('design')`. Это «TESPy network "
            "construction», «сборка сети», «build steam-cycle topology». Самый "
            "большой оркестратор проекта (~300 строк, out=104). Ключевая "
            "тонкость — счётчики портов `next_prod()`/`next_cond()`/"
            "`next_pvd()`/`next_hc()` распределяют входы Merge-блоков; при "
            "добавлении новой турбины обязательно увеличить `num_in` "
            "соответствующего merge и пробросить порт, иначе TESPy отвалится "
            "с port-mismatch. Летний режим `PT6` (`summer_pt6`) шунтирует "
            "MP→prod минуя LP и конденсатор, для него часть портов помечается "
            "`'in_unused'`."
        ),
    },
    {
        "id": "engine/chp_network.py::get_results",
        "kind": "function_unit",
        "name": "CHPNetwork.get_results",
        "anchor": {"file": "engine/chp_network.py", "fn": "get_results", "class": "CHPNetwork"},
        "description": (
            "Извлекает все результаты решённой TESPy-сети в один словарь: "
            "потурбинные `steam`/`pvd`/`prod`/`heat`/`cond`/`P_MW`, суммарные "
            "`total_steam`/`total_prod`/`total_heat`/`total_cond`, мощности "
            "`P_gross`/`P_net`/`Q_base`/`Q_peak` из шин (`buses`) и тепловой "
            "баланс котлов с энтальпией питательной воды. Метод "
            "`CHPNetwork.get_results()`, вызывается `solve_design()` после "
            "`nw.solve('design')` и снова в SLP-цикле для проверки "
            "`_cond_violations`. Это «extract results», «съём результатов "
            "сети», «turbine output dump». Внутренний `turb_power_mw(name)` "
            "суммирует абсолютные мощности всех ступеней (`hp`/`mp`/`lp`/"
            "`cond`/`bp`) и применяет `ETA_MECH_GEN`; нетто-мощность считается "
            "как `P_gross × (1 − AUX_FRACTION)` — постобработочное допущение. "
            "Если `fw_hot` connection отсутствует (регенерация не замкнута), "
            "`h_fw` падает на CoolProp по `T_FEEDWATER`/`P_FEEDWATER` дефолтам."
        ),
    },
    {
        "id": "engine/turbine_factory.py::build_pt_turbine",
        "kind": "function_unit",
        "name": "build_pt_turbine",
        "anchor": {"file": "engine/turbine_factory.py", "fn": "build_pt_turbine"},
        "description": (
            "Строит полный паровой тракт ПТ-турбины (ПТ-60/75-130, ПТ-60/80-130) "
            "в существующей TESPy-сети: цепочка `HP → splitter (ПВД) → MP → "
            "splitter (prod) → LP → 3-way splitter (heat/cond/PND)` с "
            "подключением к глобальным мерджам `prod_merge`, `cond_merge`, "
            "`pvd_merge`, `heat_cond_merge`. Вызывается из `CHPNetwork._build()` "
            "для каждой активной ПТ; `build_t_turbine`/`build_r_turbine` "
            "повторяют паттерн с упрощённой топологией. Это «turbine factory», "
            "«сборка ПТ-турбины», «extraction-condensing build». Ключевая "
            "деталь — каскадный дренаж ПНД через `Valve('pnd_cascade')` "
            "разрывает цепочку давлений `P_HEAT → P_COND`, иначе TESPy замкнёт "
            "цикл. В летнем режиме (`summer=True`) MP-выход шунтируется в "
            "`prod_merge` минуя LP и конденсатор; расход ПНД по умолчанию "
            "`flows['heat'] × 0.1`."
        ),
    },
    {
        "id": "engine/equations.py::build_objective",
        "kind": "function_unit",
        "name": "build_objective",
        "anchor": {"file": "engine/equations.py", "fn": "build_objective"},
        "description": (
            "Собирает вектор `c` целевой функции MILP (минимизация `c @ x`) "
            "под выбранную стратегию: `min_coal` — суммарный расход пара "
            "плюс `STARTUP_PENALTY` на бинарных активациях, `max_power` — "
            "максимизация выработки через `EL_EFF × _el_row(coeffs)`, "
            "`max_factory` — максимум промышленного отбора с поправкой "
            "`(1 − DEA_STEAM_FRAC)` для `R3`/`R4`/`rou9`, `min_cost` — "
            "баланс цен угля и электроэнергии из `objective_params`. Парная "
            "функция к `build_constraints`, вызывается из `solve_milp()` перед "
            "обращением к `scipy.optimize.milp`. Это «objective vector», "
            "«целевая функция MILP», «c-вектор оптимизации». Тонкость — "
            "`max_factory` использует отрицательные коэффициенты на "
            "`prod_*`-переменных (минимизация инвертированной целевой), и "
            "`factory_th` в `solve_milp` пересчитывается из реального "
            "`_factory_actual`. Неизвестное значение `objective` поднимает "
            "`ValueError`."
        ),
    },
    {
        "id": "engine/linear_model.py::calibrate",
        "kind": "function_unit",
        "name": "calibrate",
        "anchor": {"file": "engine/linear_model.py", "fn": "calibrate"},
        "description": (
            "Калибрует линейную модель MILP в референсной точке: строит "
            "`CHPNetwork` с номинальными расходами из `config['turbines']`, "
            "решает TESPy в design-режиме и возвращает извлечённые "
            "`LinearCoeffs` через `extract_coefficients()`. Вызывается перед "
            "каждым `solve_design()` (в т. ч. из `api_calculate`) для "
            "получения стартовых коэффициентов SLP-цикла; результат "
            "мемоизируется в модульном `_calibration_cache` по md5-ключу "
            "config'а. Это «начальная калибровка», «design-point linearization», "
            "«warm-start coefficients». Если TESPy не сходится — поднимает "
            "`CalibrationError` с пояснением «проверьте конфигурацию». "
            "Номинальные расходы для калибровки задаются эмпирически: "
            "ПТ-турбины — `m_nom` с `prod = 30%`, `heat = 15%`, Т-турбины — "
            "`heat = 20%`, Р-турбины — весь расход в производственный отбор; "
            "при изменении конфига кэш сбрасывается явным вызовом "
            "`invalidate_cache()`."
        ),
    },
    {
        "id": "engine/climate.py::calc_monthly_loads",
        "kind": "function_unit",
        "name": "calc_monthly_loads",
        "anchor": {"file": "engine/climate.py", "fn": "calc_monthly_loads"},
        "description": (
            "Рассчитывает помесячные тепловые нагрузки ТЭЦ — сетевую "
            "(отопление), промышленную (пром. пар) и ГВС — на основе "
            "климатических данных города. Вызывается из `api_calculate` "
            "перед циклом `_solve_one_month`, возвращает список из 12 dict "
            "с полями `q_network`/`q_industrial`/`q_gwh`/`q_total` (Гкал/ч) "
            "и интегральными `e_network`/`e_total` (Гкал за месяц). Это "
            "«monthly thermal loads», «помесячные нагрузки», «отопительная "
            "характеристика». Тепловая характеристика здания линейная: "
            "`q_network = q_design × (t_int − t) / (t_int − t_des)`, "
            "отопительный сезон активен только при `temp < heating_threshold`. "
            "Зимой ГВС увеличена на 20% относительно летней; промышленный пар "
            "(`steam_industrial`, т/ч) пересчитывается в Гкал/ч по "
            "фиксированному коэффициенту 0.62 (типично для пара 13 бар), "
            "который зашит в коде, а не в `climate`."
        ),
    },
    {
        "id": "engine/scenarios.py::apply_scenario",
        "kind": "function_unit",
        "name": "apply_scenario",
        "anchor": {"file": "engine/scenarios.py", "fn": "apply_scenario"},
        "description": (
            "Накладывает именованный сценарий работы ТЭЦ (`base`, `no_t5`, "
            "`no_pt2`) на базовый конфиг и возвращает модифицированную копию "
            "с обновлённым `cfg['turbines'][X]['enabled']`. Вызывается каждым "
            "endpoint, который зависит от выбранного сценария — `api_calculate`, "
            "`api_scheme_svg`, `api_tespy_solve` и др. (in=10 — самая "
            "фундаментальная функция `scenarios.py`). Это «scenario apply», "
            "«применить сценарий», «turbine enable override». Сценарии "
            "описаны в module-level `SCENARIOS` dict; каждая запись содержит "
            "`name`, `description` и опциональный `turbines_override` со "
            "списком турбин и обновлений `enabled`. Тонкость — вход не "
            "мутируется, делается `copy.deepcopy(base_config)`; неизвестный "
            "`scenario_id` тихо возвращает копию без изменений, что важно для "
            "пустых дефолтов фронта (например, `?scenario=`)."
        ),
    },
    {
        "id": "engine/svg_builder.py::build_svg",
        "kind": "function_unit",
        "name": "build_svg",
        "anchor": {"file": "engine/svg_builder.py", "fn": "build_svg"},
        "description": (
            "Генерирует SVG-схему ТЭЦ для основной вкладки фронтенда: "
            "определяет активные компоненты (`PT1`/`PT2`/`PT6`/`T5`/`R3`/"
            "`R4`/`rou9`/`brou16`) по `config['turbines'][X]['enabled']`, "
            "вычисляет горизонтальные позиции и склеивает SVG-фрагменты от "
            "`_defs`/`_boilers_block`/`_gpp` для шапки, `_component` для "
            "каждого блока турбины, `_extraction_lines`/`_collector_lines` "
            "для трубопроводов и `_output_blocks`/`_params_block`/"
            "`_mode_block`/`_legend` для нижней части. Точка входа SVG-"
            "генератора, дёргается из `api_scheme_svg`. Это «scheme SVG», "
            "«build CHP diagram», «render plant scheme». Layout детерминирован: "
            "порядок компонентов задаётся модульной константой `_ORDER`, "
            "шаг — `_STEP`, ширина SVG автоматически растягивается под "
            "крайнюю позицию (минимум 1050px). Если ни одна турбина не "
            "помечена `enabled` — fallback включает все турбины кроме "
            "РОУ/БРОУ, иначе пользователь видит пустой холст."
        ),
    },
    {
        "id": "engine/svg_builder.py::_component",
        "kind": "function_unit",
        "name": "_component",
        "anchor": {"file": "engine/svg_builder.py", "fn": "_component"},
        "description": (
            "Рисует визуальный блок одного компонента ТЭЦ — турбины, РОУ-9 "
            "или БРОУ-16 — со стрелкой пара сверху, прямоугольной рамкой, "
            "заголовком, подзаголовком и плейсхолдерами `—` для значений "
            "`val-{name}-m`/`heat`/`prod`/`pvd`/`cond`. Helper-функция, "
            "вызывается из `build_svg()` в цикле по `active`-компонентам; "
            "каждый тип (`pt`/`t`/`r`/`rou`/`brou`) имеет свой набор полей. "
            "Это «component SVG block», «отрисовка турбины», «render plant "
            "component». Цветовая схема и метаданные читаются из модульных "
            "`_META` и `_CLR`. Ключевое правило — id полей строго "
            "`val-<name>-<metric>` (например `val-PT1-heat`); JS-фронтенд "
            "(`tab_scheme.js`, `_setText`) ищет узлы по этим id и подменяет "
            "`—` живыми значениями из `/api/calculate`, поэтому переименование "
            "id ломает обновление схемы без видимой ошибки в консоли."
        ),
    },
    {
        "id": "app.py::api_scheme_svg",
        "kind": "function_unit",
        "name": "api_scheme_svg",
        "anchor": {"file": "app.py", "fn": "api_scheme_svg", "route": "GET /api/scheme/svg"},
        "description": (
            "Отдаёт SVG-схему ТЭЦ для основной вкладки фронтенда — HTTP "
            "`GET /api/scheme/svg?scenario=<id>`. Дёргается JS при открытии "
            "вкладки и при переключении сценария; читает базовый конфиг через "
            "`_load_config()`, накладывает сценарий через "
            "`apply_scenario(cfg, scenario_id)` и передаёт в `build_svg(cfg)`. "
            "Это «scheme endpoint», «SVG-схема», «plant diagram API». "
            "Возвращает SVG-строку с `Content-Type: image/svg+xml; "
            "charset=utf-8` — без правильного MIME браузер покажет SVG как "
            "текст, а frontend ожидает inline-рендер через `innerHTML`. "
            "Параметр `scenario` опционален (default `base`), неизвестный id "
            "молча обрабатывается `apply_scenario` без ошибки."
        ),
    },
    {
        "id": "app.py::api_chat_ask",
        "kind": "function_unit",
        "name": "api_chat_ask",
        "anchor": {"file": "app.py", "fn": "api_chat_ask", "route": "POST /api/chat/ask"},
        "description": (
            "Принимает `POST /api/chat/ask` от UI чат-панели и отвечает через "
            "Claude Haiku с поддержкой инструментов: получает `message` и "
            "`history` из тела, вызывает `client.messages.create` с системным "
            "промптом `_CHAT_SYSTEM` и набором tool definitions `_CHAT_TOOLS`, "
            "в цикле обрабатывает `tool_use`-блоки через `_execute_chat_tool()` "
            "— даёт LLM прочитать сценарии, последний расчёт, формулы — и "
            "завершает на `end_turn`. Точка входа AI-ассистента, доступная "
            "пользователю через значок чата на UI. Это «chat endpoint», "
            "«AI assistant API», «Claude tool-use chat». Лимит — максимум 6 "
            "итераций tool-use, иначе возвращает 500; HTTP-клиент `httpx` "
            "создаётся с `verify=False` для совместимости с корпоративным "
            "прокси. Без `ANTHROPIC_API_KEY` в env возвращает 500 сразу, "
            "не дёргая SDK."
        ),
    },
    {
        "id": "app.py::_solve_one_month",
        "kind": "function_unit",
        "name": "_solve_one_month",
        "anchor": {"file": "app.py", "fn": "_solve_one_month"},
        "description": (
            "Считает один помесячный расчёт внутри `api_calculate`: "
            "накладывает переопределения месяца (`q_city_gcal`, "
            "`steam_factory_th`, `disabled_turbines`, `disabled_boilers`, "
            "локальные `constraints`) поверх глобального конфига и вызывает "
            "`CHPPlant(month_cfg).solve_design(q_city, steam, **kw)`. Closure "
            "внутри `api_calculate`, исполняется 12 раз в `mode=year` или "
            "единожды в `mode=month`/`single`. Это «monthly solve», "
            "«помесячный расчёт», «inner calculation loop». Конфиг "
            "изолируется на каждый месяц через `json.loads(json.dumps("
            "base_cfg))`, а не `copy.deepcopy` — в конфиге попадаются "
            "несериализуемые объекты. `boiler_capacity` пробрасывается в "
            "MILP только если суммарная мощность активных котлов > 0; "
            "глобальные и помесячные `constraints` сливаются, а `objective`/"
            "`objective_params` берутся из верхнеуровневого body запроса."
        ),
    },
]


# ---------------------------------------------------------------------------
# 21-27: JS UI tabs (traceability_unit kind — hardcoded outside graph)
# ---------------------------------------------------------------------------

JS_UI_UNITS = [
    {
        "id": "ui_tab_app_shell",
        "kind": "traceability_unit",
        "name": "Application shell (app.js)",
        "source_files": ["static/js/app.js"],
        "description": (
            "Загружает первым на странице и держит глобальное состояние "
            "фронтенда: `_currentConfig`, `_lastResults`, `_activeTab`, плюс "
            "реализует переключение вкладок через `switchTab(name)`, оверлей "
            "загрузки `showLoading`/`hideLoading`, всплывающее уведомление "
            "`toast()`, главную кнопку «Рассчитать» (`runCalculate()` шлёт "
            "`POST /api/calculate`, обновляет `_lastResults`, рендерит "
            "результаты, оживляет SVG-схему и переключает на вкладку "
            "Результаты), подгрузку списка сохранённых сценариев "
            "(`loadScenarios`) и рестарт сервера. Это «application shell», "
            "«UI-роутер», «глобальный фронтенд». Точка входа всего фронта — "
            "без него ничего не работает; вкладочные `tab_*.js` полагаются "
            "на глобалы `_currentConfig`/`_lastResults`/`_activeTab` и "
            "хелперы `switchTab`/`toast`/`_fmt`/`_esc`. Переименование любого "
            "из них тихо ломает все вкладки сразу."
        ),
        "consumers": [
            {
                "file": "static/js/tab_inputs.js",
                "anchor": "global state references",
                "what_to_check": "Все обращения к `_currentConfig`/`_lastResults`/`_activeTab`/`switchTab`.",
            },
            {
                "file": "static/js/tab_results.js",
                "anchor": "global state references",
                "what_to_check": "Обращения к `_lastResults` и хелперам `_fmt`/`_esc`/`toast`.",
            },
            {
                "file": "static/js/tab_scheme.js",
                "anchor": "global state references",
                "what_to_check": "Обращения к `_lastResults` для подмеса значений в SVG.",
            },
            {
                "file": "templates/index.html",
                "anchor": 'CSS classes ".ltab", ".tab-pane", ids "loading"/"toast"/"btn-calculate"',
                "what_to_check": "DOM-контракт между HTML и app.js (классы вкладок, id оверлея и тоста).",
            },
        ],
    },
    {
        "id": "ui_tab_inputs",
        "kind": "traceability_unit",
        "name": "Вкладка Расчёт (форма ввода + кнопка Рассчитать)",
        "source_files": ["static/js/tab_inputs.js"],
        "description": (
            "Главная форма ввода для расчёта режима ТЭЦ: переключатель "
            "«Год / Месяц / Single», помесячная редактируемая таблица "
            "нагрузок и оборудования, выбор целевой функции (`min_coal`/"
            "`max_power`/`min_cost`/`max_factory`), список ограничений, "
            "выпадающий список сохранённых сценариев и кнопка «Рассчитать». "
            "Точка входа `initInputsTab()` параллельно дёргает `/api/config` "
            "и `/api/constraints/meta`, после чего собирает форму через "
            "`_renderInputsForm` и `_initModeToggle`/`_initObjectiveToggle`. "
            "Это «inputs panel», «форма расчёта», «calc inputs tab». При "
            "нажатии «Рассчитать» сборщик `collectInputs()` формирует body "
            "для `POST /api/calculate` — поля и их имена должны строго "
            "соответствовать тому, что ждёт `app.py::api_calculate`; иначе "
            "расчёт упадёт с unhelpful HTTP 500 без видимой подсказки в UI."
        ),
        "consumers": [
            {
                "file": "app.py",
                "anchor": "api_calculate()",
                "what_to_check": "Схема body: `overrides`, `disabled_turbines`, `mode`, `monthly_inputs`, `objective`, `constraints`. Имена полей должны совпадать.",
            },
            {
                "file": "templates/index.html",
                "anchor": 'ids "inp-mode", "inp-month", "inp-objective", "inp-yearly-section", "btn-calculate"',
                "what_to_check": "DOM-контракт между HTML-формой и tab_inputs.js.",
            },
            {
                "file": "engine/scenarios.py",
                "anchor": "SCENARIOS dict",
                "what_to_check": "Список id, отображаемый в селекторе сценариев.",
            },
        ],
    },
    {
        "id": "ui_tab_results",
        "kind": "traceability_unit",
        "name": "Вкладка Результаты",
        "source_files": ["static/js/tab_results.js", "static/js/result_rows.js"],
        "description": (
            "Рендерит результаты помесячного расчёта в две группы: пять "
            "summary-карточек сверху (`Выработка эл.`, `Теплоотпуск`, "
            "`Макс. мощность`, `Ср. мощность`, `Макс. пар котлы`) и большая "
            "помесячная таблица с разделами из общего конфига `RESULT_SECTIONS` "
            "(`result_rows.js`) плюс monthly-only секции `Тепловые нагрузки` "
            "и `Выработка`. Точка входа `renderResults(data)` дёргается из "
            "`runCalculate()` и при загрузке сохранённого сценария. Это "
            "«results panel», «вкладка Результаты», «monthly results table». "
            "Обращается к полям `data.monthly[].result.totals.power_net`, "
            "`.boilers.total_steam`, `.energy.power_net_gwh`, "
            "`.loads.q_network` — переименование любого ключа в "
            "`CHPPlant._format` или `_solve_one_month` ломает таблицу "
            "беззвучно (отображаются `—` или нули, без ошибки в консоли)."
        ),
        "consumers": [
            {
                "file": "engine/chp_model.py",
                "anchor": "CHPPlant._format()",
                "what_to_check": "Поля `turbines`, `boilers`, `totals`, `monthly[].energy` — имена должны сохраняться.",
            },
            {
                "file": "app.py",
                "anchor": "_solve_one_month()",
                "what_to_check": "Общая схема результата месяца, в т. ч. `loads.q_network`/`q_industrial`/`q_gwh`.",
            },
            {
                "file": "static/js/result_rows.js",
                "anchor": "RESULT_SECTIONS array",
                "what_to_check": "Определение строк таблицы — добавление поля в `_format` требует записи здесь.",
            },
            {
                "file": "templates/index.html",
                "anchor": 'ids "results-summary", "results-monthly-wrap", "results-scenario-select"',
                "what_to_check": "DOM-якоря, в которые рендерятся карточки и таблица.",
            },
        ],
    },
    {
        "id": "ui_tab_scheme",
        "kind": "traceability_unit",
        "name": "Вкладка Схема (живая SVG)",
        "source_files": ["static/js/tab_scheme.js"],
        "description": (
            "Грузит SVG-схему ТЭЦ через `GET /api/scheme/svg` в контейнер "
            "`#scheme-container` и оживляет её цифрами из последнего расчёта: "
            "для каждой турбины и котла находит DOM-узлы по строго заданным "
            "id `val-{name}-{metric}` (`val-PT1-heat`, `val-boilers-steam` и "
            "т. п.) и подменяет плейсхолдеры через `_setText`. Точка входа "
            "`initScheme()`, обновление — `updateScheme(data)` из "
            "`runCalculate()`. Это «scheme panel», «вкладка Схема», «live "
            "SVG diagram». Для отображения берётся «зимняя» точка с "
            "максимальным `total_steam` через `_pickDesignPoint` — компромисс: "
            "одна схема не показывает помесячные различия, отображает "
            "worst-case режим. Переименование id-полей в "
            "`engine/svg_builder.py::_component` ломает обновление схемы без "
            "ошибки в консоли — поля просто остаются как `—`."
        ),
        "consumers": [
            {
                "file": "engine/svg_builder.py",
                "anchor": "_component()",
                "what_to_check": "id-контракт `val-<name>-<metric>` критичен. JS ищет узлы по этим id.",
            },
            {
                "file": "engine/chp_model.py",
                "anchor": "CHPPlant._format()",
                "what_to_check": "Поля `turbines[].steam`/`heat`/`prod`/`cond`/`pvd`/`P_MW`, `boilers.total_steam`/`count_active`/`load_pct`.",
            },
            {
                "file": "app.py",
                "anchor": "api_scheme_svg()",
                "what_to_check": "Content-Type SVG, параметр `?scenario=`.",
            },
        ],
    },
    {
        "id": "ui_tab_tespy",
        "kind": "traceability_unit",
        "name": "Вкладка Расчёт (Формулы / Уравнения / Взаимосвязи / TESPy-конфиг)",
        "source_files": ["static/js/tab_tespy.js"],
        "description": (
            "Самый большой клиент-сайд (1190 строк) — реализует четыре "
            "связанных под-вкладки: Конфиг (статические параметры TESPy из "
            "`/api/tespy/model-info`), Формулы (живой каталог уравнений из "
            "`/api/tespy/formulas` с актуальными коэффициентами `LinearCoeffs`), "
            "Уравнения (отрисовывает MILP-ограничения через `renderEquationsTab` "
            "с подсчётом «N переменных, K групп ограничений»), Взаимосвязи "
            "(граф топологии TESPy через `/api/tespy/topology`). Точки входа "
            "`initChpConfigTab`, `initFormulasTab`, `initCalcTab`, "
            "`initCalcGraphTab`. Это «TESPy panel», «вкладка Формулы», "
            "«equations view». Содержит **жёстко зашитые счётчики** "
            "(«N переменных», «K групп ограничений») в `renderEquationsTab` — "
            "при добавлении переменных в `engine/equations.py::IDX` или новой "
            "группы в `build_constraints` эти числа надо обновлять руками, "
            "иначе UI рассинхронизирован с MILP. Это документировано в "
            "`data/ui_traceability.json`."
        ),
        "consumers": [
            {
                "file": "engine/equations.py",
                "anchor": "IDX dict, build_constraints, get_equation_catalog",
                "what_to_check": "Размерность IDX и число групп должны соответствовать счётчикам в `renderEquationsTab`.",
            },
            {
                "file": "engine/linear_model.py",
                "anchor": "LinearCoeffs dataclass",
                "what_to_check": "Поля коэффициентов, отображаемых в Формулах.",
            },
            {
                "file": "app.py",
                "anchor": "api_tespy_formulas, api_tespy_topology, api_tespy_model_info, api_tespy_solve",
                "what_to_check": "Схемы ответов 4 эндпоинтов TESPy.",
            },
            {
                "file": "data/tespy_formulas.json",
                "anchor": '$.[?(@.id=="optimizer_balance")]',
                "what_to_check": "Базовые формулы, переопределяемые live-версией из `get_equation_catalog()`.",
            },
            {
                "file": "data/ui_traceability.json",
                "anchor": "milp_variables, milp_constraints",
                "what_to_check": "Обязательная сверка при изменении IDX или числа групп — счётчики на UI зашиты.",
            },
        ],
    },
    {
        "id": "ui_tab_scenarios",
        "kind": "traceability_unit",
        "name": "Вкладка Сценарии",
        "source_files": ["static/js/tab_scenarios.js"],
        "description": (
            "Управление сохранёнными расчётными точками: создание (`scRun` "
            "шлёт `POST /api/scenarios` c `name`/`heat_demand`/`steam_demand`/"
            "`scenario`/`dea_steam_th`/`min_cond_th`/`P_net_min_mw`/"
            "`P_net_max_mw`), список и удаление, сравнение полей между "
            "точками. Точка входа `initScenariosTab` тянет `/api/scenarios` "
            "и наполняет селектор предустановленных сценариев (`base`/`no_t5`/"
            "`no_pt2`). Это «scenarios panel», «вкладка Сценарии», «saved "
            "calculation points». Поле `scenario` в payload ссылается на "
            "ключи `engine/scenarios.py::SCENARIOS` — добавление нового "
            "сценария требует править И `SCENARIOS` (бэкенд), И массив в "
            "`initScenariosTab` (захардкожен на фронте), иначе пользователь "
            "либо не увидит опцию, либо отправит в API id, который "
            "`apply_scenario` тихо проигнорирует."
        ),
        "consumers": [
            {
                "file": "engine/scenarios.py",
                "anchor": "SCENARIOS dict + apply_scenario()",
                "what_to_check": "Список id сценариев. При добавлении — синхронизировать с массивом в `initScenariosTab`.",
            },
            {
                "file": "app.py",
                "anchor": "api_scenarios_create, api_scenarios_list, api_scenario_get, api_scenario_delete",
                "what_to_check": "CRUD-роуты сценариев и их JSON-схема.",
            },
            {
                "file": "engine/chp_model.py",
                "anchor": "CHPPlant.solve_design",
                "what_to_check": "kwargs `dea_steam_th`, `min_cond_th`, `P_net_min_mw`, `P_net_max_mw` — имена должны совпадать с полями формы.",
            },
            {
                "file": "templates/index.html",
                "anchor": 'ids "sc-name", "sc-heat", "sc-steam", "sc-scenario-select"',
                "what_to_check": "DOM-якоря формы создания сценария.",
            },
        ],
    },
    {
        "id": "ui_tab_ai_chat",
        "kind": "traceability_unit",
        "name": "Панель AI Чат (тикеты)",
        "source_files": ["static/js/tab_ai_chat.js"],
        "description": (
            "Интерфейс к `ticket_service`: создание тикетов, просмотр "
            "статусов (`pending`/`in_progress`/`done`/`error`), история "
            "переписки, прикрепление файлов (текст / картинки base64 / "
            "бинарные base64), polling статуса каждые несколько секунд. "
            "Состояние хранится в локальном `_chat` объекте; "
            "`_collectUiContext` собирает контекст активной вкладки, "
            "выбранного сценария, последней ошибки и отдаёт его на бэк "
            "вместе с тикетом. Это «AI chat panel», «панель тикетов», «AI "
            "assistant tickets». **Не путать с** `app.py::api_chat_ask` — "
            "это другой роут под прямой Claude-чат, в текущем UI не "
            "используется. Дефолтный `ticket_pattern` ловит тикеты вида "
            "`T-XXXXXX` (hex); при изменении паттерна нужно синхронно "
            "править regex и парсеры на фронте, и в бэке `ticket_service.store`."
        ),
        "consumers": [
            {
                "file": "<external>",
                "anchor": "ticket_service package endpoints",
                "what_to_check": "API создания/чтения/обновления тикетов (`/tickets/*`).",
            },
            {
                "file": "app.py",
                "anchor": "_log_ticket_activity()",
                "what_to_check": "Логирование событий тикетов в access-log.",
            },
            {
                "file": "data/wip/<ticket_id>.md",
                "anchor": "WIP file format",
                "what_to_check": "Markdown-формат, читаемый следующим прогоном тикета как контекст.",
            },
            {
                "file": "patches/ticket_service_runner.py",
                "anchor": "_execute(), _execute_reply()",
                "what_to_check": "Subprocess `claude --print` команда, allowedTools, env.",
            },
        ],
    },
]


ALL_MANUAL_UNITS = PYTHON_UNITS + JS_UI_UNITS


# ---------------------------------------------------------------------------
# Description overrides for traceability_units derived from ui_traceability.json.
#
# Auto-synthesis from structured fields gives mechanical text without domain
# vocabulary. These hand-authored replacements add the domain words users
# actually type ("коллекторы", "13 ата", "доля регенерации", "отбор на ПВД",
# "потурбинные результаты") so the embedding can bridge the lexical gap
# observed in T-FC3170 and T-132CDB.
# ---------------------------------------------------------------------------

TRACEABILITY_DESCRIPTION_OVERRIDES = {
    "topology": (
        "Описывает структуру TESPy-сети ТЭЦ — какие компоненты есть, как они "
        "соединены и какие коллекторы пара собирают потоки от турбин: "
        "`prod_merge` (производственный коллектор, 9 ата), `cond_merge` "
        "(конденсатный), `pvd_merge` (ПВД-дренаж), `heat_cond_merge` "
        "(теплофикационный). Промышленные отборы ПТ-турбин (13 ата) и "
        "теплофикационные отборы исторически могут направляться на "
        "отдельные коллекторы пара под разные потребители. Хранится в двух "
        "местах: программная топология строится в `get_full_topology()` "
        "(`engine/chp_network.py`), а layout с координатами узлов — в "
        "`data/tespy_topology.json` для UI-вкладки Взаимосвязи. Это «TESPy "
        "topology», «коллекторы пара», «steam collectors», «топология сети», "
        "«давления отборов». При добавлении компонента в `_build()` он "
        "попадает в граф автоматически без координат — координаты задаются "
        "вручную в JSON."
    ),
    "linear_coeffs": (
        "Хранит коэффициенты линеаризации `LinearCoeffs` MILP-задачи, "
        "извлекаемые из решённой TESPy-сети: энтальпии ступеней (`W_HP`/"
        "`W_MP_PT`/`W_LP_PT`/`W_COND`/`W_MP_T`/`W_R`), теплофикация "
        "`DH_HEAT`, питательная вода `H_FW`, доля регенерации `K_regen` "
        "(ранее `K_PVD`) — это отбор на ПВД относительно общего расхода "
        "пара по ТЭЦ — и производные `K_COND`/`DH_STEAM`/`C_M_PT`. "
        "Используется во всех MILP-ограничениях через `coeffs.<field>` и "
        "обновляется на каждой итерации SLP-цикла из `extract_coefficients()`. "
        "Это «linear coefficients», «коэффициенты линеаризации», "
        "«доля регенерации», «отбор на ПВД», «K_PVD / K_regen». Поле "
        "`K_regen_per_turb` хранит per-turbine коэффициенты регенерации — "
        "критично когда активны разные комбинации ПТ-турбин (один "
        "глобальный `K_regen` приводит к расхождению баланса 4b и "
        "отрицательному конденсату)."
    ),
    "chp_results_structure": (
        "Описывает структуру словаря, который возвращает "
        "`CHPNetwork.get_results()` после решения TESPy-сети: блок "
        "`turbines` с потурбинными результатами (`PT1`/`PT2`/`PT6`/`T5`/"
        "`R3`/`R4`) — для каждой турбины `steam` (расход пара на турбину, "
        "т/ч), `pvd` (отбор на ПВД), `prod` (промышленный отбор), `heat` "
        "(теплофикационный), `cond` (конденсат), `P_MW` (мощность). Также "
        "`boilers` (паропроизводительность котлов), `totals` (агрегаты по "
        "ТЭЦ — `power_gross`/`power_net`/`coal_th`/`eta_total`), "
        "`steam_balance` (детальный баланс энтальпий и потоков). Это "
        "«results structure», «потурбинные результаты», «output schema», "
        "«расходы пара на турбину», «steam balance». При добавлении нового "
        "поля — синхронно обновлять JS (`result_rows.js::RESULT_SECTIONS`, "
        "`tab_scheme.js::updateScheme`), иначе UI-таблица и схема не "
        "увидят новые данные."
    ),
}
