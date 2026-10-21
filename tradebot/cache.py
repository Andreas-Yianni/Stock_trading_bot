"""Απλή προσωρινή μνήμη με λήξη, για να μη ζητάμε τα ίδια δεδομένα σε κάθε ανάλυση της σάρωσης."""
import time

_cache: dict[str, tuple[float, object]] = {}


def cached(key: str, fn, seconds: int):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < seconds:
        return hit[1]
    value = fn()
    _cache[key] = (time.time(), value)
    return value
