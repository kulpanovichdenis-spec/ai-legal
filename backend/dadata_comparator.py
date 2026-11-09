import re
from dataclasses import dataclass, field
from typing import Literal

from dadata_mcp_client import DadataCompanyResult

Severity = Literal["critical", "warning", "info"]


@dataclass
class Discrepancy:
    field: str
    contract_value: str
    dadata_value: str
    severity: Severity


@dataclass
class ComparisonResult:
    discrepancies: list[Discrepancy] = field(default_factory=list)

    @property
    def has_critical(self) -> bool:
        return any(d.severity == "critical" for d in self.discrepancies)

    @property
    def has_address_mismatch(self) -> bool:
        return any(d.field == "адрес" for d in self.discrepancies)


def compare_with_contract(
    dadata: DadataCompanyResult,
    contract_inn: str = "",
    contract_ogrn: str = "",
    contract_kpp: str = "",
    contract_name: str = "",
    contract_address: str = "",
) -> ComparisonResult:
    result = ComparisonResult()

    if not dadata.found:
        return result

    if contract_inn and dadata.inn:
        if _norm_digits(contract_inn) != _norm_digits(dadata.inn):
            result.discrepancies.append(Discrepancy(
                field="ИНН",
                contract_value=contract_inn,
                dadata_value=dadata.inn,
                severity="critical",
            ))

    if contract_ogrn and dadata.ogrn:
        if _norm_digits(contract_ogrn) != _norm_digits(dadata.ogrn):
            result.discrepancies.append(Discrepancy(
                field="ОГРН/ОГРНИП",
                contract_value=contract_ogrn,
                dadata_value=dadata.ogrn,
                severity="critical",
            ))

    if dadata.status and dadata.status != "ACTIVE":
        result.discrepancies.append(Discrepancy(
            field="статус",
            contract_value="предполагается действующий",
            dadata_value=dadata.status,
            severity="critical",
        ))

    if contract_kpp and dadata.kpp:
        if _norm_digits(contract_kpp) != _norm_digits(dadata.kpp):
            result.discrepancies.append(Discrepancy(
                field="КПП",
                contract_value=contract_kpp,
                dadata_value=dadata.kpp,
                severity="warning",
            ))

    if contract_address:
        dadata_addr = dadata.address_full or dadata.address
        if dadata_addr and not _addresses_match(contract_address, dadata_addr):
            result.discrepancies.append(Discrepancy(
                field="адрес",
                contract_value=contract_address,
                dadata_value=dadata_addr,
                severity="warning",
            ))

    if contract_name:
        dadata_name = dadata.name_full or dadata.name_short
        if dadata_name and not _names_match(contract_name, dadata_name):
            result.discrepancies.append(Discrepancy(
                field="наименование",
                contract_value=contract_name,
                dadata_value=dadata_name,
                severity="info",
            ))

    return result


def _norm_digits(val: str) -> str:
    return re.sub(r'\D', '', val)


def _norm_str(s: str) -> str:
    return re.sub(r'[\s"\'«»,\.]+', ' ', s.lower()).strip()


def _addresses_match(a: str, b: str) -> bool:
    na, nb = _norm_str(a), _norm_str(b)
    return na == nb or na in nb or nb in na


def _names_match(a: str, b: str) -> bool:
    na, nb = _norm_str(a), _norm_str(b)
    return na == nb or na in nb or nb in na
