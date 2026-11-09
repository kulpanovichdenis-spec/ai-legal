"""
Pydantic-схемы для структурированного ответа проверки контрагента.
schemaVersion=1 — первая публичная версия.
"""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel


class OkvedItem(BaseModel):
    code: str
    name: str


class FounderItem(BaseModel):
    name: str
    share: float | None = None       # 0..1  (0.49 = 49%)
    invalid: bool = False
    invalidityLabel: str = ""


class KeyMetrics(BaseModel):
    ageYears: float | None = None            # лет с момента регистрации (округление до 0.5)
    registrationDate: str | None = None      # "YYYY-MM-DD"
    charterCapital: float | None = None      # уставный капитал, руб., числом
    headcount: int | None = None             # численность сотрудников


class CounterpartyHeader(BaseModel):
    fullName: str
    inn: str = ""
    ogrn: str = ""
    kpp: str | None = None
    opfShort: str | None = None              # ООО / ПАО / ИП
    status: str = ""                         # lowercase: "active" / "liquidated" / ...
    entityType: str | None = None            # "legal" / "individual"


class CounterpartySummary(BaseModel):
    verdict: Literal["ok", "warning", "danger"] = "ok"
    text: str = ""                           # читабельный итог (статус + ключевые факты)


class CounterpartyFinancials(BaseModel):
    year: str | None = None
    revenue: float | None = None             # руб., числом
    income: float | None = None
    expense: float | None = None
    debt: float | None = None                # недоимки по налогам
    taxSystem: str | None = None             # человекочитаемое название системы


class CounterpartyOkved(BaseModel):
    primary: OkvedItem | None = None
    additional: list[OkvedItem] = []


class CounterpartyDetails(BaseModel):
    address: str = ""
    addressFull: str = ""
    addressInvalid: bool = False
    addressInvalidityCode: str = ""
    addressInvalidityLabel: str = ""
    registrationDate: str = ""               # DD.MM.YYYY (отображение)
    ogrnDate: str = ""
    managementName: str = ""
    managementPost: str = ""
    isInvalid: bool = False
    smb: str = ""
    smbDate: str = ""
    branchCount: int = 0
    branchType: str = ""
    ftsName: str = ""
    ftsAddress: str = ""
    pf: str = ""
    sif: str = ""
    okpo: str = ""
    oktmo: str = ""
    phones: list[str] = []
    emails: list[str] = []


class CounterpartyData(BaseModel):
    header: CounterpartyHeader
    summary: CounterpartySummary
    keyMetrics: KeyMetrics
    details: CounterpartyDetails
    founders: list[FounderItem] = []
    financials: CounterpartyFinancials = CounterpartyFinancials()
    okved: CounterpartyOkved = CounterpartyOkved()
    licenses: list[str] = []
    limitations: list[str] = []
    generatedAt: str = ""


_ASSESSMENT_DISCLAIMER = (
    "Оценка носит информационный характер и основана на открытых данных реестра. "
    "Для принятия юридически значимых решений рекомендуется привлечение квалифицированного юриста."
)


class LegalAssessmentData(BaseModel):
    verdict: Literal["ok", "warning", "danger"] = "ok"
    riskLevel: Literal["low", "medium", "high"] = "low"
    risks: list[str] = []
    recommendations: list[str] = []
    disclaimer: str = _ASSESSMENT_DISCLAIMER


class CheckBlock(BaseModel):
    type: str
    data: CounterpartyData | LegalAssessmentData


class CheckMeta(BaseModel):
    degraded: bool = False


class CheckResponse(BaseModel):
    schemaVersion: int = 1
    blocks: list[CheckBlock]
    meta: CheckMeta = CheckMeta()
