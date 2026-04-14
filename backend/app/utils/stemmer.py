"""
Jednostavni stemmer za hrvatski jezik.
Dijeli se između notifier.py i admin.py.
"""

_HR_SUFFIXES = sorted(
    [
        "icama", "stvima",
        "stvo", "stva", "stvu", "stvom",
        "nika", "nice", "nici", "niku",
        "ama", "ima", "ski", "ska", "sko",
        "ni", "na", "no", "ne",
        "om", "og",
        "a", "e", "i", "o", "u",
    ],
    key=len,
    reverse=True,
)
_MIN_STEM_LEN = 4
_MIN_KW_LEN = 6


def stem_keyword(keyword: str) -> str:
    """
    Uklanja tipični nastavak samo za riječi dulje od _MIN_KW_LEN znakova.
    Primjeri:
      'poljoprivreda' → 'poljoprivred'
      'zdravstvo'     → 'zdravstv'
      'porez'         → 'porez'   (≤6 znakova, bez promjene)
    """
    kw = keyword.strip().lower()
    if len(kw) <= _MIN_KW_LEN:
        return kw
    for suffix in _HR_SUFFIXES:
        if kw.endswith(suffix) and (len(kw) - len(suffix)) >= _MIN_STEM_LEN:
            return kw[: -len(suffix)]
    return kw
