"""Проверка Monero-адресов: base58 (блочный вариант Monero) + контрольная сумма Keccak-256."""
from Crypto.Hash import keccak

_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_IDX = {c: i for i, c in enumerate(_ALPHABET)}
_ENC_SIZES = [0, 2, 3, 5, 6, 7, 9, 10, 11]  # длина закодированного блока для 0..8 байт
# сетевой префикс -> тип (mainnet)
PREFIXES = {18: "standard", 42: "subaddress", 19: "integrated"}


def _decode(s: str) -> bytes:
    out = bytearray()
    for i in range(0, len(s), 11):
        block = s[i:i + 11]
        if len(block) not in _ENC_SIZES:
            raise ValueError("bad block")
        size = _ENC_SIZES.index(len(block))
        num = 0
        for ch in block:
            num = num * 58 + _IDX[ch]
        if num >= 1 << (8 * size):
            raise ValueError("overflow")
        out += num.to_bytes(size, "big")
    return bytes(out)


def address_type(addr: str) -> str | None:
    addr = addr.strip()
    if len(addr) not in (95, 106) or any(c not in _IDX for c in addr):
        return None
    try:
        raw = _decode(addr)
    except (ValueError, KeyError):
        return None
    data, checksum = raw[:-4], raw[-4:]
    if keccak.new(digest_bits=256, data=data).digest()[:4] != checksum:
        return None
    kind = PREFIXES.get(data[0])
    expected = 1 + 64 + (8 if kind == "integrated" else 0)
    return kind if kind and len(data) == expected else None


def valid_address(addr: str) -> bool:
    return address_type(addr) is not None
