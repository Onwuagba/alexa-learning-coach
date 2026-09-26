import pytest

from learning_coach import bedrock, service, tutor


@pytest.fixture(autouse=True)
def fast_kdf_all(monkeypatch):
    monkeypatch.setattr(service, "SCRYPT_N", 2**4)


@pytest.fixture(autouse=True)
def aws_features_off(monkeypatch):
    """Whatever the developer's shell exports, tests start with Bedrock and the Strands tutor disabled."""
    bedrock.set_ai(bedrock.BedrockAI(bedrock.BedrockConfig(enabled=False)))
    tutor.set_planner(tutor.TutorPlanner(mode="off"))
    yield
    bedrock.set_ai(None)
    tutor.set_planner(None)
