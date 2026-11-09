import re as _re
from datetime import datetime, timezone
from dadata_mcp_client import DadataCompanyResult
from dadata_comparator import ComparisonResult

_STATUS_LABELS = {
    "ACTIVE": "действующая",
    "LIQUIDATED": "ликвидирована",
    "LIQUIDATING": "в процессе ликвидации",
    "BANKRUPT": "банкрот",
    "REORGANIZING": "в процессе реорганизации",
}

_TAX_SYSTEM_LABELS = {
    "USN":  "упрощенная система налогообложения (УСН)",
    "AUSN": "автоматизированная упрощенная система налогообложения (АУСН)",
    "ESHN": "единый сельскохозяйственный налог (ЕСХН)",
    "SRP":  "система налогообложения при выполнении соглашений о разделе продукции (СРП)",
}

_ENTITY_TYPE_LABELS = {
    "LEGAL":      "юридическое лицо",
    "INDIVIDUAL": "индивидуальный предприниматель",
}

_BRANCH_TYPE_LABELS = {
    "MAIN":   "головная организация",
    "BRANCH": "филиал",
}

_INVALIDITY_LABELS = {
    "PARTY": "обращение лица или организации",
    "FTS":   "проверка налоговой",
    "COURT": "решение суда",
    "OTHER": "прочие причины",
}

_SMB_LABELS = {
    "MICRO":  "микропредприятие",
    "SMALL":  "малое предприятие",
    "MEDIUM": "среднее предприятие",
}

_FOUNDER_TYPE_LABELS = {
    "LEGAL":    "юридическое лицо",
    "PHYSICAL": "физическое лицо",
}

_SHARE_TYPE_LABELS = {
    "PERCENT":  "процент",
    "DECIMAL":  "десятичная дробь",
    "FRACTION": "обыкновенная дробь",
}

_MANAGER_TYPE_LABELS = {
    "EMPLOYEE": "сотрудник",
    "LEGAL":    "юридическое лицо",
}

_INTENT_LABELS = {
    "documents_verify_party_details": "проверка реквизитов",
    "documents_fill_document_requisites": "реквизиты претензии",
    "documents_verify_notice_address": "адрес для уведомления",
    "documents_counterparty_screening": "саммари договора",
}


def _status(s: str) -> str:
    return _STATUS_LABELS.get(s.upper() if s else "", s or "неизвестен")


def normalize_for_documents(
    result: DadataCompanyResult,
    intent: str,
    comparison: ComparisonResult | None = None,
) -> str:
    if not result.found:
        err = result.error or "не найдено"
        return (
            "## РЕЗУЛЬТАТЫ ВНЕШНЕЙ ПРОВЕРКИ РЕКВИЗИТОВ\n\n"
            "Источник: DaData/MCP\n"
            f"Статус проверки: {'не найдено' if not result.error else 'ошибка'}\n"
            f"Причина: {err}\n\n"
            "Используй реквизиты из договора или плейсхолдеры. Не придумывай недостающие данные."
        )

    intent_label = _INTENT_LABELS.get(intent, "проверка контрагента")
    parts = [
        "## РЕЗУЛЬТАТЫ ВНЕШНЕЙ ПРОВЕРКИ РЕКВИЗИТОВ",
        "",
        "Источник: DaData/MCP",
        "Статус проверки: найдено",
        f"Цель проверки: {intent_label}",
        "",
        "### Найденные данные",
        f"- Наименование: {result.name_full or result.name_short}",
        f"- ОПФ: {result.opf_full or result.opf_short or '—'}",
        f"- ИНН: {result.inn}",
        f"- ОГРН / ОГРНИП: {result.ogrn}",
        f"- КПП: {result.kpp or '—'}",
        f"- Адрес: {result.address_full or result.address}",
        f"- Статус: {_status(result.status)}",
        f"- Дата регистрации: {result.registration_date or '—'}",
        f"- Дата выдачи ОГРН: {result.ogrn_date or '—'}",
    ]
    if result.capital_value:
        parts.append(f"- {result.capital_type or 'Уставный капитал'}: {int(result.capital_value):,} руб.".replace(",", " "))
    if result.management_name:
        parts.append(f"- Руководитель: {result.management_name} ({result.management_post or 'должность не указана'})")
    if result.employee_count:
        parts.append(f"- Численность сотрудников: {result.employee_count} чел.")
    if result.phones:
        parts.append(f"- Телефоны: {', '.join(result.phones)}")
    if result.emails:
        parts.append(f"- Email: {', '.join(result.emails)}")
    if result.okved:
        okved_str = result.okved + (f" — {result.okved_name}" if result.okved_name else "")
        parts.append(f"- Основной ОКВЭД: {okved_str}")
    if result.okveds_extra:
        parts.append(f"- Доп. ОКВЭД: {'; '.join(result.okveds_extra[:5])}")
    if result.okpo:
        stat = f"ОКПО: {result.okpo}"
        if result.oktmo:
            stat += f", ОКТМО: {result.oktmo}"
        parts.append(f"- {stat}")
    if result.branch_type == "MAIN" and result.branch_count:
        parts.append(f"- Филиалов / представительств: {result.branch_count}")
    if result.fts_name:
        fts_str = result.fts_name
        if result.fts_address:
            fts_str += f", {result.fts_address}"
        parts.append(f"- Налоговый орган: {fts_str}")
    if result.is_invalid:
        parts.append("- ⚠️ НЕДОСТОВЕРНЫЕ СВЕДЕНИЯ: ФНС признала данные организации недостоверными")
    if result.address_invalidity:
        parts.append(f"- ⚠️ Адрес признан недостоверным (причина: {result.address_invalidity})")
    if result.founders:
        parts.append(f"- Учредители:")
        for f in result.founders:
            parts.append(f"  • {f}")
    if result.pf_name:
        parts.append(f"- Отделение ПФР: {result.pf_name}")
    if result.sif_name:
        parts.append(f"- Отделение ФСС: {result.sif_name}")
    if result.smb_category:
        smb_str = result.smb_category
        if result.smb_date:
            smb_str += f" (с {result.smb_date})"
        parts.append(f"- Реестр МСП: {smb_str}")
    if result.finance_year:
        parts += ["", f"### Финансы ({result.finance_year} г.)"]
        if result.finance_revenue:
            parts.append(f"- Выручка: {result.finance_revenue}")
        if result.finance_income:
            parts.append(f"- Доходы: {result.finance_income}")
        if result.finance_expense:
            parts.append(f"- Расходы: {result.finance_expense}")
        if result.finance_debt:
            parts.append(f"- Недоимки по налогам: {result.finance_debt}")
        if result.finance_tax_system:
            tax_label = _TAX_SYSTEM_LABELS.get(result.finance_tax_system)
            if tax_label:
                parts.append(f"- Система налогообложения: {tax_label}")
    if result.licenses:
        parts.append(f"- Лицензии ({len(result.licenses)} шт.):")
        for lic in result.licenses[:5]:
            parts.append(f"  • {lic}")

    if comparison and comparison.discrepancies:
        parts += ["", "### Расхождения с договором"]
        for d in comparison.discrepancies:
            parts.append(
                f"- [{d.severity.upper()}] {d.field}: "
                f"в договоре «{d.contract_value}», по DaData/MCP «{d.dadata_value}»"
            )

        if comparison.has_address_mismatch:
            addr_d = next(d for d in comparison.discrepancies if d.field == "адрес")
            parts += [
                "",
                "### Рекомендация по направлению",
                "Для юридически значимого сообщения использовать оба адреса:",
                f"1. Адрес из договора: {addr_d.contract_value}",
                f"2. Адрес из ЕГРЮЛ/ЕГРИП по DaData/MCP: {addr_d.dadata_value}",
            ]

    parts += [
        "",
        "### Ограничения",
        "DaData/MCP используется только для проверки реквизитов, адресов и статуса. "
        "Юридический вывод строится на договоре и применимом праве.",
        "",
        "### Инструкция по ответу",
        "Отдельную карточку контрагента в ответ не вставляй — в чате её не будет. "
        "Подставь найденные наименование, ИНН, ОГРН и адрес в шапку и текст документа. "
        "Не выдумывай реквизиты, которых нет в этих данных.",
    ]
    return "\n".join(parts)


def normalize_for_consultation(result: DadataCompanyResult, query_type: str) -> str:
    if not result.found:
        err = result.error or "не найдено"
        return (
            "## РЕЗУЛЬТАТЫ ПРОВЕРКИ КОНТРАГЕНТА\n\n"
            "Источник: DaData/MCP\n"
            f"Статус проверки: {'не найдено' if not result.error else 'ошибка'}\n"
            f"Проверка выполнена по: {query_type}\n"
            f"Причина: {err}\n\n"
            "Сообщи пользователю, что контрагент не найден в реестре по указанному идентификатору."
        )

    risks = []
    if result.status and result.status != "ACTIVE":
        risks.append(f"статус компании: {_status(result.status)}")

    parts = [
        "## РЕЗУЛЬТАТЫ ПРОВЕРКИ КОНТРАГЕНТА",
        "",
        "Источник: DaData/MCP",
        "Статус проверки: найдено",
        f"Проверка выполнена по: {query_type}",
        "",
        "### Данные контрагента",
        f"- Наименование / ФИО ИП: {result.name_full or result.name_short}",
        f"- ОПФ: {result.opf_full or result.opf_short or '—'}",
        f"- ИНН: {result.inn}",
        f"- ОГРН / ОГРНИП: {result.ogrn}",
        f"- КПП: {result.kpp or '—'}",
        f"- Адрес: {result.address_full or result.address}",
        f"- Статус: {_status(result.status)}",
        f"- Дата регистрации: {result.registration_date or '—'}",
        f"- Дата выдачи ОГРН: {result.ogrn_date or '—'}",
    ]
    if result.capital_value:
        parts.append(f"- {result.capital_type or 'Уставный капитал'}: {int(result.capital_value):,} руб.".replace(",", " "))
    if result.management_name:
        parts.append(f"- Руководитель: {result.management_name} ({result.management_post or 'должность не указана'})")
    if result.employee_count:
        parts.append(f"- Численность сотрудников: {result.employee_count} чел.")
    if result.phones:
        parts.append(f"- Телефоны: {', '.join(result.phones)}")
    if result.emails:
        parts.append(f"- Email: {', '.join(result.emails)}")
    if result.okved:
        okved_str = result.okved + (f" — {result.okved_name}" if result.okved_name else "")
        parts.append(f"- Основной ОКВЭД: {okved_str}")
    if result.okveds_extra:
        parts.append(f"- Доп. ОКВЭД: {'; '.join(result.okveds_extra[:5])}")
    if result.okpo:
        stat = f"ОКПО: {result.okpo}"
        if result.oktmo:
            stat += f", ОКТМО: {result.oktmo}"
        parts.append(f"- {stat}")
    if result.branch_type == "MAIN" and result.branch_count:
        parts.append(f"- Филиалов / представительств: {result.branch_count}")
    if result.fts_name:
        fts_str = result.fts_name
        if result.fts_address:
            fts_str += f", {result.fts_address}"
        parts.append(f"- Налоговый орган: {fts_str}")
    if result.is_invalid:
        parts.append("- ⚠️ НЕДОСТОВЕРНЫЕ СВЕДЕНИЯ: ФНС признала данные организации недостоверными")
        risks.append("недостоверные сведения по данным ФНС")
    if result.address_invalidity:
        parts.append(f"- ⚠️ Адрес признан недостоверным (причина: {result.address_invalidity})")
        risks.append("недостоверный адрес")
    if result.founders:
        parts.append("- Учредители:")
        for f in result.founders:
            parts.append(f"  • {f}")
    if result.pf_name:
        parts.append(f"- Отделение ПФР: {result.pf_name}")
    if result.sif_name:
        parts.append(f"- Отделение ФСС: {result.sif_name}")
    if result.smb_category:
        smb_str = result.smb_category
        if result.smb_date:
            smb_str += f" (с {result.smb_date})"
        parts.append(f"- Реестр МСП: {smb_str}")
    if result.finance_year:
        parts += ["", f"### Финансы ({result.finance_year} г.)"]
        if result.finance_revenue:
            parts.append(f"- Выручка: {result.finance_revenue}")
        if result.finance_income:
            parts.append(f"- Доходы: {result.finance_income}")
        if result.finance_expense:
            parts.append(f"- Расходы: {result.finance_expense}")
        if result.finance_debt:
            parts.append(f"- Недоимки по налогам: {result.finance_debt}")
        if result.finance_tax_system:
            tax_label = _TAX_SYSTEM_LABELS.get(result.finance_tax_system)
            if tax_label:
                parts.append(f"- Система налогообложения: {tax_label}")
    if result.licenses:
        parts.append(f"- Лицензии ({len(result.licenses)} шт.):")
        for lic in result.licenses[:5]:
            parts.append(f"  • {lic}")

    status_label = "действующий" if result.status == "ACTIVE" else _status(result.status)
    risk_str = ", ".join(risks) if risks else "не выявлены по данным реестра"

    parts += [
        "",
        "### Предварительный вывод",
        "- Контрагент найден.",
        f"- Статус: {status_label}.",
        f"- Риски: {risk_str}.",
        "",
        "### Ограничения",
        "DaData/MCP используется только как источник справочных данных. "
        "Вывод о возможности работы с контрагентом не должен строиться только на DaData/MCP.",
        "",
        "### Инструкция по ответу",
        "Данные контрагента уже отображены пользователю в структурированной карточке в интерфейсе. "
        "Не перечисляй и не дублируй реквизиты (ИНН, ОГРН, адрес, руководитель и т.д.) в своём ответе. "
        "Сформулируй только краткий вывод и рекомендацию на основе этих данных.",
    ]
    return "\n".join(parts)


def normalize_skipped_documents(reason: str) -> str:
    _REASON_LABELS = {
        "no_identifier": "нет ИНН, ОГРН или ОГРНИП",
        "anonymized_placeholder": "данные анонимизированы",
        "unsupported_mode": "нет разрешённого сценария",
        "unsupported_intent": "нет разрешённого сценария",
        "deanonymization_risk": "риск деанонимизации",
    }
    reason_text = _REASON_LABELS.get(reason, reason)
    return (
        "## РЕЗУЛЬТАТЫ ВНЕШНЕЙ ПРОВЕРКИ РЕКВИЗИТОВ\n\n"
        "Источник: DaData/MCP\n"
        "Статус проверки: пропущено\n"
        f"Причина: {reason_text}\n\n"
        "Не придумывай реквизиты. Используй данные из договора или плейсхолдеры."
    )


def normalize_skipped_consultation(reason: str) -> str:
    if reason == "no_identifier":
        return (
            "## РЕЗУЛЬТАТЫ ПРОВЕРКИ КОНТРАГЕНТА\n\n"
            "Источник: DaData/MCP\n"
            "Статус проверки: пропущено\n"
            "Причина: не указан ИНН, ОГРН или ОГРНИП\n\n"
            "Для проверки контрагента запроси у пользователя ИНН, ОГРН или ОГРНИП."
        )
    _REASON_LABELS = {
        "anonymized_placeholder": "данные анонимизированы",
        "unsupported_intent": "нет разрешённого сценария",
    }
    return (
        "## РЕЗУЛЬТАТЫ ПРОВЕРКИ КОНТРАГЕНТА\n\n"
        "Источник: DaData/MCP\n"
        "Статус проверки: пропущено\n"
        f"Причина: {_REASON_LABELS.get(reason, reason)}"
    )


def normalize_error_documents() -> str:
    return (
        "DaData/MCP недоступна. Реквизиты не проверены. "
        "Используй реквизиты из договора или плейсхолдеры. Не придумывай недостающие данные."
    )


def normalize_error_consultation() -> str:
    return (
        "DaData/MCP недоступна. Проверка контрагента не выполнена. "
        "Попроси пользователя повторить проверку позже или передать выписку / "
        "карточку контрагента для ручного анализа."
    )


def _fmt_years(registration_date: str) -> float | None:
    try:
        reg = datetime.strptime(registration_date, "%d.%m.%Y")
        days = (datetime.now(timezone.utc).date() - reg.date()).days
        raw = days / 365.25
        return round(raw * 2) / 2  # nearest 0.5
    except Exception:
        return None


def _fmt_years_str(years: float) -> str:
    int_part = int(years)
    has_half = 0.25 <= years % 1 < 0.75
    display = f"{int_part}½" if has_half else str(int_part)
    r = int_part % 10
    r100 = int_part % 100
    if 11 <= r100 <= 19:
        word = "лет"
    elif r == 1:
        word = "год"
    elif 2 <= r <= 4:
        word = "года"
    else:
        word = "лет"
    return f"{display} {word}"


def to_card(result: DadataCompanyResult) -> dict:
    """Преобразует DadataCompanyResult в структурированный JSON (schemaVersion=1)."""
    now = datetime.now(timezone.utc)

    # Verdict
    founders_raw = result.founders_raw or []
    has_flags = (
        result.is_invalid
        or bool(result.address_invalidity)
        or any(f.get("invalid") for f in founders_raw)
    )
    if result.status and result.status.upper() != "ACTIVE":
        verdict = "danger"
    elif has_flags:
        verdict = "warning"
    else:
        verdict = "ok"

    # Summary text
    status_label = _STATUS_LABELS.get(result.status.upper() if result.status else "", result.status or "неизвестен")
    age_years = _fmt_years(result.registration_date) if result.registration_date else None
    age_str = f", {_fmt_years_str(age_years)} на рынке" if age_years is not None else ""
    if verdict == "danger":
        summary_text = f"{status_label}{age_str}. Требуется проверка."
    elif verdict == "warning":
        summary_text = f"{status_label}{age_str}. Выявлены признаки риска."
    else:
        summary_text = f"{status_label}{age_str}. Нарушений не выявлено по данным реестра."

    # Key metrics (typed object)
    cap_raw: float | None = None
    if result.capital_value:
        try:
            cap_raw = float(result.capital_value)
        except Exception:
            pass

    headcount_raw: int | None = None
    if result.employee_count:
        try:
            headcount_raw = int(result.employee_count)
        except Exception:
            pass

    reg_date_iso: str | None = None
    if result.registration_date:
        try:
            d = datetime.strptime(result.registration_date, "%d.%m.%Y")
            reg_date_iso = d.strftime("%Y-%m-%d")
        except Exception:
            pass

    # Founders (float share 0..1)
    founders_list = [
        {
            "name": f["name"],
            "share": f.get("share"),
            "invalid": f.get("invalid", False),
            "invalidityLabel": f.get("invalidity_label", ""),
        }
        for f in founders_raw
    ]

    # Financials (raw numbers)
    financials: dict = {}
    if result.finance_year:
        financials["year"] = result.finance_year
    if result.finance_revenue_raw is not None:
        financials["revenue"] = result.finance_revenue_raw
    if result.finance_income_raw is not None:
        financials["income"] = result.finance_income_raw
    if result.finance_expense_raw is not None:
        financials["expense"] = result.finance_expense_raw
    if result.finance_debt_raw is not None:
        financials["debt"] = result.finance_debt_raw
    if result.finance_tax_system:
        tax_label = _TAX_SYSTEM_LABELS.get(result.finance_tax_system)
        if tax_label:
            financials["taxSystem"] = tax_label

    # OKVED ({code, name} objects)
    okved_primary = None
    if result.okved:
        okved_primary = {"code": result.okved, "name": result.okved_name or ""}
    okved_additional = []
    for item in (result.okveds_extra or []):
        parts = item.split(" — ", 1)
        okved_additional.append({
            "code": parts[0].strip(),
            "name": parts[1].strip() if len(parts) > 1 else "",
        })

    # Limitations
    limitations = []
    if result.is_invalid:
        limitations.append("Организация имеет признак недостоверных сведений по данным ФНС")
    if result.address_invalidity:
        inv_label = _INVALIDITY_LABELS.get(result.address_invalidity, "")
        limitations.append(
            f"Адрес признан недостоверным ({inv_label or result.address_invalidity})"
        )
    if any(f.get("invalid") for f in founders_raw):
        limitations.append("Один или несколько учредителей имеют признак недостоверности")

    return {
        "header": {
            "fullName": result.name_full or result.name_short,
            "inn": result.inn,
            "ogrn": result.ogrn,
            "kpp": result.kpp or None,
            "opfShort": result.opf_short or None,
            "status": result.status.lower() if result.status else "",
            "entityType": result.entity_type.lower() if result.entity_type else None,
        },
        "summary": {
            "verdict": verdict,
            "text": summary_text,
        },
        "keyMetrics": {
            "ageYears": age_years,
            "registrationDate": reg_date_iso,
            "charterCapital": cap_raw,
            "headcount": headcount_raw,
        },
        "details": {
            "address": result.address,
            "addressFull": result.address_full,
            "addressInvalid": bool(result.address_invalidity),
            "addressInvalidityCode": result.address_invalidity,
            "addressInvalidityLabel": _INVALIDITY_LABELS.get(result.address_invalidity, ""),
            "registrationDate": result.registration_date,
            "ogrnDate": result.ogrn_date,
            "managementName": result.management_name,
            "managementPost": result.management_post,
            "isInvalid": result.is_invalid,
            "smb": result.smb_category,
            "smbDate": result.smb_date,
            "branchCount": result.branch_count,
            "branchType": result.branch_type,
            "ftsName": result.fts_name,
            "ftsAddress": result.fts_address,
            "pf": result.pf_name,
            "sif": result.sif_name,
            "okpo": result.okpo,
            "oktmo": result.oktmo,
            "phones": result.phones or [],
            "emails": result.emails or [],
        },
        "founders": founders_list,
        "financials": financials,
        "okved": {
            "primary": okved_primary,
            "additional": okved_additional,
        },
        "licenses": result.licenses or [],
        "limitations": limitations,
        "generatedAt": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
