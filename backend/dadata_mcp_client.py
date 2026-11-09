import json
import logging
from dataclasses import dataclass

import httpx

from config import settings

logger = logging.getLogger("legal-assistant.dadata")

_INVALIDITY_LABELS = {
    "PARTY": "обращение лица или организации",
    "FTS":   "проверка налоговой",
    "COURT": "решение суда",
    "OTHER": "прочие причины",
}


@dataclass
class DadataCompanyResult:
    found: bool
    name_full: str = ""
    name_short: str = ""
    inn: str = ""
    ogrn: str = ""
    kpp: str = ""
    address: str = ""
    address_full: str = ""
    status: str = ""        # ACTIVE / LIQUIDATED / LIQUIDATING / BANKRUPT / REORGANIZING
    entity_type: str = ""   # LEGAL / INDIVIDUAL
    okved: str = ""
    okved_name: str = ""
    opf_short: str = ""     # ООО, ПАО, ИП и т.д.
    opf_full: str = ""      # полная ОПФ
    registration_date: str = ""  # дата регистрации
    ogrn_date: str = ""     # дата выдачи ОГРН
    capital_value: str = "" # уставный капитал
    capital_type: str = ""  # тип капитала
    management_name: str = ""   # ФИО руководителя
    management_post: str = ""   # должность руководителя
    employee_count: str = ""    # численность сотрудников
    phones: list = None         # телефоны
    emails: list = None         # email
    okveds_extra: list = None   # дополнительные ОКВЭД
    okpo: str = ""              # ОКПО
    oktmo: str = ""             # ОКТМО
    branch_type: str = ""       # MAIN / BRANCH
    branch_count: int = 0       # кол-во филиалов
    fts_name: str = ""          # налоговый орган (наименование)
    fts_address: str = ""       # адрес налогового органа
    pf_name: str = ""           # отделение ПФР
    sif_name: str = ""          # отделение ФСС
    licenses: list = None       # лицензии
    founders: list = None       # учредители с долями
    finance_year: str = ""      # год финансовой отчётности
    finance_income: str = ""    # доходы
    finance_revenue: str = ""   # выручка
    finance_expense: str = ""   # расходы
    finance_debt: str = ""      # недоимки по налогам
    finance_tax_system: str = "" # система налогообложения
    smb_category: str = ""      # категория МСП (MICRO/SMALL/MEDIUM)
    smb_date: str = ""          # дата включения в реестр МСП
    is_invalid: bool = False    # признак недостоверных сведений
    address_invalidity: str = "" # причина недостоверности адреса
    error: str = ""
    # Структурированные данные для новой схемы карточки (schemaVersion=1)
    founders_raw: list = None   # [{name, share(0..1 float), invalid, invalidity_code, invalidity_label}]
    finance_revenue_raw: float | None = None
    finance_income_raw: float | None = None
    finance_expense_raw: float | None = None
    finance_debt_raw: float | None = None


def _build_headers() -> dict:
    # Не логируем ключи — только заголовки строим здесь
    return {
        "Authorization": f"Token {settings.dadata_api_key}",
        "X-Secret": settings.dadata_secret_key,
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }


def _parse_sse(raw: str) -> dict | None:
    for line in raw.splitlines():
        if line.startswith("data:"):
            data = line[5:].strip()
            if data:
                try:
                    return json.loads(data)
                except json.JSONDecodeError:
                    return None
    return None


async def find_company(*, inn: str = "", ogrn: str = "", ogrnip: str = "") -> DadataCompanyResult:
    """Ищет компанию/ИП по ИНН, ОГРН или ОГРНИП через DaData/MCP.

    Отправляет только идентификатор — без договора, без ПДн.
    """
    query = inn or ogrn or ogrnip
    if not query:
        return DadataCompanyResult(found=False, error="no_identifier")

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "find_party", "arguments": {"query": query}},
    }

    try:
        async with httpx.AsyncClient(timeout=10.0, verify=False, http2=False) as client:
            resp = await client.post(
                settings.dadata_mcp_server_url,
                json=payload,
                headers=_build_headers(),
            )
            resp.raise_for_status()
    except httpx.TimeoutException:
        logger.warning("[DADATA_MCP] timeout")
        return DadataCompanyResult(found=False, error="timeout")
    except httpx.HTTPStatusError as e:
        logger.warning(f"[DADATA_MCP] http error: {e.response.status_code}")
        return DadataCompanyResult(found=False, error="http_error")
    except Exception as e:
        logger.error(f"[DADATA_MCP] connection error: {type(e).__name__}")
        return DadataCompanyResult(found=False, error="connection_error")

    rpc = _parse_sse(resp.text)
    if rpc is None:
        return DadataCompanyResult(found=False, error="parse_error")

    if "error" in rpc:
        msg = rpc["error"].get("message", "rpc_error")
        logger.warning(f"[DADATA_MCP] rpc error: {msg}")
        return DadataCompanyResult(found=False, error="rpc_error")

    result = rpc.get("result", {})
    if result.get("isError"):
        return DadataCompanyResult(found=False, error="dadata_error")

    sc = result.get("structuredContent")
    if not sc or not sc.get("data"):
        return DadataCompanyResult(found=False)

    data = sc["data"]
    name = data.get("name") or {}
    address = data.get("address") or {}
    state = data.get("state") or {}
    opf = data.get("opf") or {}
    management = data.get("management") or {}
    capital = data.get("capital") or {}

    okved_name = ""
    for o in (data.get("okveds") or []):
        if o.get("main"):
            okved_name = o.get("name", "")
            break

    def _ts_to_date(ts) -> str:
        if not ts:
            return ""
        try:
            from datetime import datetime, timezone
            return datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc).strftime("%d.%m.%Y")
        except Exception:
            return ""

    phones_raw = data.get("phones") or []
    emails_raw = data.get("emails") or []
    phones = [p.get("value", "") for p in phones_raw if p.get("value")]
    emails = [e.get("value", "") for e in emails_raw if e.get("value")]

    okveds_extra = [
        f"{o['code']} — {o['name']}"
        for o in (data.get("okveds") or [])
        if not o.get("main") and o.get("code")
    ]

    authorities = data.get("authorities") or {}
    fts = authorities.get("fts_report") or authorities.get("fts_registration") or {}
    fts_name = fts.get("name", "")
    fts_address = fts.get("address", "") or ""
    pf_name = (authorities.get("pf") or {}).get("name", "")
    sif_name = (authorities.get("sif") or {}).get("name", "")

    licenses_raw = data.get("licenses") or []
    licenses = []
    for lic in licenses_raw:
        acts = ", ".join(lic.get("activities") or [])
        iss = _ts_to_date(lic.get("issue_date"))
        vto = _ts_to_date(lic.get("valid_to"))
        entry = f"№{lic.get('number','')} от {iss} ({lic.get('issue_authority','')})"
        if acts:
            entry += f": {acts}"
        if vto:
            entry += f"; действует до {vto}"
        licenses.append(entry)

    founders_raw_api = data.get("founders") or []
    founders = []
    founders_structured = []
    for f in founders_raw_api:
        if f.get("type") == "PHYSICAL":
            fio = f.get("fio") or {}
            fname = " ".join(filter(None, [fio.get("surname"), fio.get("name"), fio.get("patronymic")]))
        else:
            fname = f.get("name", "")
        share = f.get("share") or {}
        share_str = ""
        share_float = None
        if share.get("type") == "PERCENT":
            share_str = f"{share.get('value')}%"
            try:
                share_float = float(share.get("value", 0)) / 100.0
            except Exception:
                pass
        elif share.get("type") == "DECIMAL":
            try:
                share_float = float(share.get("value", 0))
                share_str = f"{share_float * 100:.0f}%"
            except Exception:
                pass
        elif share.get("type") == "FRACTION":
            share_str = f"{share.get('numerator')}/{share.get('denominator')}"
            try:
                n, d = float(share.get("numerator", 0)), float(share.get("denominator", 1))
                share_float = n / d if d else None
            except Exception:
                pass
        inv = f.get("invalidity")
        inv_code = ""
        inv_label_str = ""
        if inv:
            inv_code = inv.get("code", "")
            inv_label_str = _INVALIDITY_LABELS.get(inv_code, "")
            inv_str = f" [НЕДОСТОВЕРНО: {inv_label_str}]" if inv_label_str else " [НЕДОСТОВЕРНО]"
        else:
            inv_str = ""
        founders.append(f"{fname} — {share_str}{inv_str}" if share_str else f"{fname}{inv_str}")
        founders_structured.append({
            "name": fname,
            "share": share_float,
            "invalid": bool(inv),
            "invalidity_code": inv_code,
            "invalidity_label": inv_label_str,
        })

    finance = data.get("finance") or {}
    def _fmt_money(v) -> str:
        if v is None:
            return ""
        try:
            return f"{int(v):,}".replace(",", " ") + " руб."
        except Exception:
            return str(v)

    docs = data.get("documents") or {}
    smb = docs.get("smb") or {}
    smb_labels = {"MICRO": "микропредприятие", "SMALL": "малое предприятие", "MEDIUM": "среднее предприятие"}

    addr_inv = (data.get("address") or {}).get("invalidity")
    addr_inv_str = addr_inv.get("code", "") if addr_inv else ""

    ec = data.get("employee_count")

    return DadataCompanyResult(
        found=True,
        name_full=name.get("full_with_opf") or sc.get("unrestricted_value", ""),
        name_short=name.get("short_with_opf") or sc.get("value", ""),
        inn=data.get("inn", ""),
        ogrn=data.get("ogrn", ""),
        kpp=data.get("kpp") or "",
        address=address.get("value", ""),
        address_full=address.get("unrestricted_value", ""),
        status=state.get("status", ""),
        entity_type=data.get("type", ""),
        okved=data.get("okved", ""),
        okved_name=okved_name,
        opf_short=opf.get("short", ""),
        opf_full=opf.get("full", ""),
        registration_date=_ts_to_date(state.get("registration_date")),
        ogrn_date=_ts_to_date(data.get("ogrn_date")),
        capital_value=str(int(capital.get("value"))) if capital.get("value") else "",
        capital_type=capital.get("type", ""),
        management_name=management.get("name", ""),
        management_post=management.get("post", ""),
        employee_count=str(ec) if ec is not None else "",
        phones=phones,
        emails=emails,
        okveds_extra=okveds_extra,
        okpo=data.get("okpo", "") or "",
        oktmo=data.get("oktmo", "") or "",
        branch_type=data.get("branch_type", "") or "",
        branch_count=data.get("branch_count") or 0,
        fts_name=fts_name,
        fts_address=fts_address,
        pf_name=pf_name,
        sif_name=sif_name,
        licenses=licenses,
        founders=founders,
        finance_year=str(finance.get("year", "")) if finance.get("year") else "",
        finance_income=_fmt_money(finance.get("income")),
        finance_revenue=_fmt_money(finance.get("revenue")),
        finance_expense=_fmt_money(finance.get("expense")),
        finance_debt=_fmt_money(finance.get("debt")),
        finance_tax_system=finance.get("tax_system", "") or "",
        founders_raw=founders_structured,
        finance_revenue_raw=float(finance["revenue"]) if finance.get("revenue") is not None else None,
        finance_income_raw=float(finance["income"]) if finance.get("income") is not None else None,
        finance_expense_raw=float(finance["expense"]) if finance.get("expense") is not None else None,
        finance_debt_raw=float(finance["debt"]) if finance.get("debt") is not None else None,
        smb_category=smb_labels.get(smb.get("category", ""), "") if smb else "",
        smb_date=_ts_to_date(smb.get("issue_date")) if smb else "",
        is_invalid=bool(data.get("invalid")),
        address_invalidity=addr_inv_str,
    )
