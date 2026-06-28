"""Unit tests for the aretry helper (pure — no I/O, injected sleep).

  python3 test_aretry.py    # standalone
  pytest test_aretry.py     # in CI
"""
import asyncio
from aretry import aretry


def test_succeeds_first_try():
    async def main():
        calls = {"n": 0}
        async def fn():
            calls["n"] += 1
            return "ok"
        delays = []
        async def sleep(d):
            delays.append(d)
        out = await aretry(fn, sleep=sleep)
        assert out == "ok"
        assert calls["n"] == 1          # no retry
        assert delays == []             # never slept
    asyncio.run(main())


def test_retries_then_succeeds():
    async def main():
        calls = {"n": 0}
        async def fn():
            calls["n"] += 1
            if calls["n"] < 3:
                raise ValueError("boom")
            return "ok"
        delays = []
        async def sleep(d):
            delays.append(d)
        out = await aretry(fn, attempts=3, base_delay=0.5, sleep=sleep)
        assert out == "ok" and calls["n"] == 3
        assert delays == [0.5, 1.0]     # exponential backoff after attempts 1 and 2
    asyncio.run(main())


def test_exhausts_and_raises_last():
    async def main():
        calls = {"n": 0}
        async def fn():
            calls["n"] += 1
            raise KeyError("nope")
        async def sleep(d):
            pass
        raised = False
        try:
            await aretry(fn, attempts=2, sleep=sleep)
        except KeyError:
            raised = True
        assert raised and calls["n"] == 2
    asyncio.run(main())


def test_only_retries_listed_exceptions():
    async def main():
        calls = {"n": 0}
        async def fn():
            calls["n"] += 1
            raise TypeError("x")
        async def sleep(d):
            pass
        raised = False
        try:
            await aretry(fn, attempts=3, retry_on=(ValueError,), sleep=sleep)
        except TypeError:
            raised = True
        assert raised and calls["n"] == 1   # TypeError not retried
    asyncio.run(main())


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ✓ {fn.__name__}")
    print(f"\n{len(fns)} aretry tests passed")


if __name__ == "__main__":
    _run()
