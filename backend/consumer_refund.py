"""Оформление возврата денег потребителю за продукцию компании."""

import re

_REFUND = re.compile(
    r"\b(?:возврат\w*|верн(?:уть|ите|ул[аи]?))\s+(?:мне\s+)?"
    r"(?:денег|деньги|денежн\w+\s+средств\w*)\b"
)
_PRODUCT = re.compile(
    r"\b(?:бад(?:а|у|ом|е|ы|ов)?|лс|лп|лекарств\w*|препарат\w*|"
    r"космет\w*|крем\w*|потребител\w*|"
    r"некачественн\w*\s+товар\w*|товар\w*\s+с\s+недостатк\w*)\b"
)
_CHOICE = re.compile(r"\b(?:или|либо)\s+(?:замен\w*|обмен\w*)\b")


def is_consumer_refund_request(message: str) -> bool:
    text = message.lower()
    return bool(_REFUND.search(text) and _PRODUCT.search(text) and not _CHOICE.search(text))
