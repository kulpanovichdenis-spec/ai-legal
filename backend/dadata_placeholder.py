import re

# Паттерны плейсхолдеров: [PREFIX_N], {{PREFIX_N}}, <PREFIX_N>
_BRACKET = re.compile(r'^\[([A-Z][A-Z0-9_]*\d*)\]$')
_DOUBLE_BRACE = re.compile(r'^\{\{([A-Z][A-Z0-9_]*\d*)\}\}$')
_ANGLE = re.compile(r'^<([A-Z][A-Z0-9_]*\d*)>$')

_KNOWN_PREFIXES = {
    "COMPANY", "INN", "OGRN", "OGRNIP", "KPP", "ADDRESS",
    "BANK_DETAILS", "EMAIL", "PHONE", "PERSON", "DATE", "AMOUNT",
}

# Паттерн для поиска плейсхолдеров идентификаторов в тексте
_ID_PLACEHOLDER_IN_TEXT = re.compile(
    r'(\[(?:INN|OGRN|OGRNIP|KPP|COMPANY)[_\d]*\]'
    r'|\{\{(?:INN|OGRN|OGRNIP|KPP|COMPANY)[_\d]*\}\}'
    r'|<(?:INN|OGRN|OGRNIP|KPP|COMPANY)[_\d]*>)',
    re.IGNORECASE,
)


def is_anonymized_placeholder(value: str | None) -> bool:
    if not value:
        return False
    v = value.strip()
    for pattern in (_BRACKET, _DOUBLE_BRACE, _ANGLE):
        m = pattern.match(v)
        if m:
            inner = m.group(1).upper()
            for prefix in _KNOWN_PREFIXES:
                if inner.startswith(prefix):
                    return True
    return False


def text_contains_id_placeholders(text: str) -> bool:
    return bool(_ID_PLACEHOLDER_IN_TEXT.search(text))
