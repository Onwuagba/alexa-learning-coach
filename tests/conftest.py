import pytest

from learning_coach import service


@pytest.fixture(autouse=True)
def fast_kdf_all(monkeypatch):
    monkeypatch.setattr(service, "SCRYPT_N", 2**4)
