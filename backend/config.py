from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    openrouter_api_key: str = ""
    # Базовая модель — обязательно задать в .env (OPENROUTER_MODEL).
    # Пустое значение допустимо только если для всех режимов заданы MODEL_*-переменные.
    openrouter_model: str = ""

    # Модели для конкретных режимов (если не заданы — используется openrouter_model)
    model_consult: str = ""
    model_ad_check: str = ""
    model_document_gen: str = ""
    model_regulation: str = ""

    def model_for_mode(self, mode: str) -> str:
        override = {
            "consult":      self.model_consult,
            "ad_check":     self.model_ad_check,
            "document_gen": self.model_document_gen,
            "regulation":   self.model_regulation,
        }.get(mode, "")
        return override or self.openrouter_model

    # RAG top_k по режимам (0 = использовать rag_top_k)
    rag_top_k_consult: int = 0
    rag_top_k_ad_check: int = 0
    rag_top_k_document_gen: int = 0
    rag_top_k_regulation: int = 0

    def top_k_for_mode(self, mode: str) -> int:
        override = {
            "consult":      self.rag_top_k_consult,
            "ad_check":     self.rag_top_k_ad_check,
            "document_gen": self.rag_top_k_document_gen,
            "regulation":   self.rag_top_k_regulation,
        }.get(mode, 0)
        return override or self.rag_top_k

    # Жёсткие таймауты на полный ответ от OpenRouter (секунды).
    # При стриминге — общий лимит, после которого запрос обрывается
    # с сообщением «Запрос был сложным…».
    llm_timeout_consult: int = 200
    llm_timeout_ad_check: int = 180
    llm_timeout_document_gen: int = 240
    llm_timeout_regulation: int = 90
    llm_timeout_default: int = 180
    # Idle timeout: максимальная пауза между чанками при стриминге.
    # Если модель замолчала дольше этого — обрываем.
    llm_idle_timeout: int = 90

    def timeout_for_mode(self, mode: str) -> int:
        return {
            "consult":      self.llm_timeout_consult,
            "ad_check":     self.llm_timeout_ad_check,
            "document_gen": self.llm_timeout_document_gen,
            "regulation":   self.llm_timeout_regulation,
        }.get(mode, self.llm_timeout_default)
    admin_password: str = "admin123"
    dashboard_password: str = "12$$456"
    jwt_secret: str = "change-me"
    documents_dir: str = "/app/data/documents"
    db_path: str = "/app/data/database.db"
    chroma_path: str = "/app/data/chroma"
    rag_top_k: int = 5

    # Dense-эмбеддер для ChromaDB. При смене модели коллекция несовместима
    # (другая размерность векторов) — needs_reindex() это замечает, и стартовая
    # фоновая переиндексация пересобирает индекс автоматически.
    rag_embed_model: str = "BAAI/bge-m3"

    # Гибридный поиск BM25 + dense через RRF.
    # Включить — заметно улучшает поиск по точным токенам (бренды, № статей).
    rag_bm25_enabled: bool = True
    # Вес BM25 в RRF-фьюзинге (1.0 = равноценно с dense, 0 = только dense)
    rag_bm25_weight: float = 1.0

    # Cross-encoder реранкер top-N → top-K поверх гибридного ретривинга.
    # Заметно повышает точность top-K, но грузит модель ~570 МБ и добавляет
    # ~0.5–1.5 с на запрос на CPU. По умолчанию выключен — включать после
    # установки модели.
    rag_rerank_enabled: bool = False
    rag_rerank_model: str = "BAAI/bge-reranker-v2-m3"
    # Сколько кандидатов от _raw_search передавать реранкеру.
    # Чем больше — тем лучше шансы поднять «зарытый» правильный ответ,
    # но дороже по времени.
    rag_rerank_candidates: int = 50
    rag_rerank_max_length: int = 1024

    # (#6) CORS — comma-separated origins
    cors_origins_str: str = "http://localhost:3000"

    # (#2) Self-registration toggle
    allow_self_registration: bool = False

    # Presentation drafts remain available to admins.
    public_projects_enabled: bool = False

    # (C1) Путь к файлу с дополнительными записями whitelist анонимайзера.
    # Формат: одна запись на строку, строки начинающиеся с # — комментарии.
    # Если пустой — не используется.
    whitelist_file: str = ""

    # ── Анонимайзер: опциональный NER-слой (Natasha) ──────────────────
    # Ловит ФИО/организации, которые regex пропускает (имена рядом со
    # стоп-словами, компании без ОПФ). По умолчанию выключен: добавляет
    # ~200 МБ RAM при первой загрузке и латентность на запрос. Веса идут
    # в составе пакета natasha — работает офлайн, без скачивания.
    anonymizer_ner_enabled: bool = False
    # NER не запускается на тексте длиннее лимита (бережём латентность на
    # крупных файлах; regex-маскировка работает всегда).
    anonymizer_ner_max_chars: int = 40000

    # ── Router-агент (автовыбор режима) ───────────────────────────────
    router_enabled: bool = True
    # Модель для роутера. Если пусто — берётся openrouter_model.
    # Имеет смысл указать дешёвую быструю модель со строгим JSON.
    router_model: str = ""
    # Жёсткий таймаут на ответ роутера в секундах.
    router_timeout: int = 15

    # ── Внутренний LLM-сервер (Ollama / OpenAI-совместимый) ──────────
    # Если задан — модели с именем в формате "name:tag" (без "/") идут сюда.
    # Модели в формате "provider/name" (с "/") всегда идут в OpenRouter.
    internal_api_url: str = ""    # например http://llm.example.test:11434/v1
    internal_api_key: str = "ollama"

    def is_internal_model(self, model: str) -> bool:
        return bool(self.internal_api_url and ":" in model and "/" not in model)

    def api_for_model(self, model: str) -> tuple[str, str, dict]:
        """Возвращает (url, api_key, extra_headers) для данной модели."""
        if self.is_internal_model(model):
            base = self.internal_api_url.rstrip("/")
            return f"{base}/chat/completions", self.internal_api_key, {}
        return (
            "https://openrouter.ai/api/v1/chat/completions",
            self.openrouter_api_key,
            {
                "HTTP-Referer": "https://legal-assistant.local",
                "X-Title": "Legal AI Assistant",
            },
        )

    # ── Bitrix24 ──────────────────────────────────────────────────────
    # Интеграция в стадии разработки. По умолчанию отключена.
    # Выставить BITRIX24_ENABLED=true только после полной настройки
    # токенов и тестирования в staging-среде.
    bitrix24_enabled: bool = False
    # Виртуальный user_id для Bitrix24-сессий (не JWT-пользователь,
    # просто метка owner-а для conversations/messages).
    bitrix24_bot_user_id: str = "bitrix24_bot"

    # Исходящая интеграция «Задача Битрикс»: создание задачи из веб-чата
    # с вложением Word-файла истории чата.
    # Постоянный входящий REST-вебхук Битрикса с правами task + disk.
    # Формат: https://<portal>.bitrix24.ru/rest/<user_id>/<код>/
    bitrix24_webhook_url: str = ""
    # ID ответственного за создаваемую задачу (обязательное поле Битрикса).
    bitrix24_task_responsible_id: int = 0
    # ID соисполнителей через запятую (напр. "678"). Назначаются вместе с
    # ответственным — задачу видят все указанные сотрудники.
    bitrix24_task_accomplice_ids: str = ""
    # Необязательно: ID хранилища Диска для загрузки вложений. Если 0 —
    # берётся первое доступное хранилище из disk.storage.getlist.
    bitrix24_disk_storage_id: int = 0

    @property
    def bitrix24_task_accomplice_id_list(self) -> list[int]:
        return [int(x) for x in self.bitrix24_task_accomplice_ids.split(",") if x.strip()]

    @property
    def bitrix24_task_enabled(self) -> bool:
        """Фича «Задача Битрикс» доступна, если задан вебхук и ответственный."""
        return bool(self.bitrix24_webhook_url and self.bitrix24_task_responsible_id)

    # ── DaData/MCP ────────────────────────────────────────────────────
    dadata_mcp_server_url: str = "https://mcp.dadata.ru/mcp"
    dadata_api_key: str = ""
    dadata_secret_key: str = ""

    @property
    def dadata_enabled(self) -> bool:
        return bool(self.dadata_api_key and self.dadata_secret_key)

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_origins_str.split(",") if o.strip()]

    def validate_for_production(self) -> None:
        """Валидация конфигурации для production-запуска.

        Вызывается из main.py @app.on_event("startup") только вне тестовой среды.
        SystemExit(1) при ошибке — приложение не стартует с неверной конфигурацией.
        """
        import logging as _log
        _logger = _log.getLogger("legal-assistant")
        errors: list[str] = []

        # Секреты: дефолты из репозитория не должны использоваться в проде.
        if self.jwt_secret in ("", "change-me"):
            errors.append(
                "JWT_SECRET не задан или оставлен дефолтным. Сгенерируйте длинную "
                "случайную строку (например, openssl rand -hex 32) и задайте в .env."
            )
        if self.admin_password in ("", "admin123"):
            errors.append(
                "ADMIN_PASSWORD не задан или оставлен дефолтным. Задайте надёжный "
                "пароль в .env (минимум 12 символов)."
            )
        if self.dashboard_password in ("", "12$$456"):
            errors.append(
                "DASHBOARD_PASSWORD не задан или оставлен дефолтным. "
                "Задайте надёжный пароль в .env."
            )

        # Базовая модель: либо openrouter_model, либо все MODEL_*-переменные.
        per_mode_all_set = all([
            self.model_consult, self.model_ad_check,
            self.model_document_gen, self.model_regulation,
        ])
        if not self.openrouter_model and not per_mode_all_set:
            errors.append(
                "OPENROUTER_MODEL не задан и не все MODEL_* переменные заполнены. "
                "Укажите либо общую модель (например, openai/gpt-4o), либо все четыре "
                "MODEL_CONSULT / MODEL_AD_CHECK / MODEL_DOCUMENT_GEN / MODEL_REGULATION."
            )

        # MODEL_REGULATION может быть как внутренней моделью (name:tag, Ollama),
        # так и внешней (provider/name, OpenRouter). Пусто — режим использует
        # базовую OPENROUTER_MODEL. Жёсткого требования «только внутренняя»
        # больше нет; если задана внутренняя модель — обязателен INTERNAL_API_URL.
        model = self.model_regulation
        if model and ":" in model and "/" not in model and not self.internal_api_url:
            errors.append(
                f"MODEL_REGULATION='{model}' — внутренняя модель (формат name:tag), "
                "но INTERNAL_API_URL не задан (напр. http://llm.example.test:11434/v1)"
            )

        # Любая внутренняя модель в формате name:tag требует INTERNAL_API_URL.
        for var_name, var_value in (
            ("ROUTER_MODEL", self.router_model),
            ("OPENROUTER_MODEL", self.openrouter_model),
        ):
            if var_value and ":" in var_value and "/" not in var_value and not self.internal_api_url:
                errors.append(
                    f"{var_name}='{var_value}' — внутренняя модель (формат name:tag), "
                    "но INTERNAL_API_URL не задан"
                )

        if errors:
            for e in errors:
                _logger.critical("[CONFIG] %s", e)
            raise SystemExit(1)

    model_config = SettingsConfigDict(env_file=".env")


settings = Settings()
