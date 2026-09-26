"""AWS deployment pieces, offline: AgentCore-mode server switches, Secrets Manager adapter (botocore Stubber),
and structural/least-privilege checks of the CloudFormation templates."""
import importlib
import json
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# -- server switches for AgentCore Runtime ---------------------------------------------------------------------
def _server(monkeypatch, tmp_path, **env):
    monkeypatch.setenv("PROGRESS_DB", str(tmp_path / "s.db"))
    monkeypatch.setenv("PARENT_PIN", "1234")
    for k in ("DATABASE_URL", "MCP_AUTH_TOKEN", "MCP_STATELESS", "MCP_ONLY", "MCP_LINK_TOKEN_HEADER", "REQUIRE_MCP_AUTH"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import learning_coach.server as srv
    return importlib.reload(srv)


def test_default_bearer_behaviour_unchanged(monkeypatch, tmp_path):
    srv = _server(monkeypatch, tmp_path)
    assert srv._bearer({"authorization": "Bearer abc"}) == "abc"
    assert srv._bearer({"x-lc-link-token": "zzz"}) == ""
    assert srv.mcp.settings.stateless_http is False


def test_link_token_from_custom_header(monkeypatch, tmp_path):
    srv = _server(monkeypatch, tmp_path, MCP_LINK_TOKEN_HEADER="X-Lc-Link-Token", MCP_STATELESS="1", REQUIRE_MCP_AUTH="1")
    fam = srv.coach.create_family("Okafor", "1111", "", "Ada")
    assert srv.mcp.settings.stateless_http is True
    assert srv.family_of_headers({"x-lc-link-token": fam["link_token"]}) == fam["family_id"]
    # the OAuth JWT in Authorization is NOT mistaken for a family token
    assert srv.family_of_headers({"authorization": "Bearer " + fam["link_token"]}) is None
    assert srv.family_of_headers({}) is None


def test_mcp_only_serves_mcp_and_health_but_not_dashboard_or_tv_api(monkeypatch, tmp_path):
    from starlette.testclient import TestClient
    srv = _server(monkeypatch, tmp_path, MCP_ONLY="1", MCP_LINK_TOKEN_HEADER="x-lc-link-token", REQUIRE_MCP_AUTH="1")
    c = TestClient(srv.build_app())
    assert c.get("/healthz").text == "ok"
    assert c.get("/parent/").status_code == 404 and c.get("/api/progress/ada").status_code == 404
    r = c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers={"x-lc-link-token": "nope"})
    assert r.status_code == 401
    full = _server(monkeypatch, tmp_path)   # default build still mounts everything (contract for firetv unchanged)
    assert TestClient(full.build_app()).get("/parent/").status_code in (200, 303, 307)


# -- secrets adapter against the real service model -----------------------------------------------------------------
def test_get_secret_uses_secrets_manager_shape(monkeypatch):
    boto3 = pytest.importorskip("boto3")
    from botocore.stub import Stubber
    from learning_coach import ops
    client = boto3.client("secretsmanager", region_name="us-east-1", aws_access_key_id="x", aws_secret_access_key="x")
    stub = Stubber(client)
    stub.add_response("get_secret_value", {"SecretString": json.dumps({"PARENT_PIN": "4321", "DATABASE_URL": "postgresql://h/db"})},
                      {"SecretId": "arn:aws:secretsmanager:us-east-1:123456789012:secret:lc-abc"})
    stub.activate()
    monkeypatch.setattr(boto3, "client", lambda name, **kw: client)
    monkeypatch.setenv("AWS_SECRETS_ID", "arn:aws:secretsmanager:us-east-1:123456789012:secret:lc-abc")
    for k in ("PARENT_PIN", "DATABASE_URL", "SESSION_SECRET"):
        monkeypatch.delenv(k, raising=False)
    ops.get_secret.__dict__.pop("_cache", None)
    assert ops.get_secret("PARENT_PIN") == "4321"
    assert ops.get_secret("DATABASE_URL") == "postgresql://h/db"     # cached: one API call only
    assert ops.get_secret("SESSION_SECRET", "dflt") == "dflt"
    monkeypatch.setenv("PARENT_PIN", "9999")
    assert ops.get_secret("PARENT_PIN") == "9999"                     # env wins
    stub.assert_no_pending_responses()
    ops.get_secret.__dict__.pop("_cache", None)


# -- CloudFormation templates ---------------------------------------------------------------------------------------------
yaml = pytest.importorskip("yaml")


def _load(name):
    class L(yaml.SafeLoader):
        pass

    def tag(loader, suffix, node):
        v = loader.construct_scalar(node) if isinstance(node, yaml.ScalarNode) else \
            loader.construct_sequence(node, deep=True) if isinstance(node, yaml.SequenceNode) else loader.construct_mapping(node, deep=True)
        return {suffix: v}
    L.add_multi_constructor("!", tag)
    with open(os.path.join(ROOT, "infra", name)) as f:
        return yaml.load(f, Loader=L)


def _statements(tpl):
    for r in tpl["Resources"].values():
        if r["Type"] in ("AWS::IAM::Role", "AWS::IAM::Policy"):
            docs = [p["PolicyDocument"] for p in r["Properties"].get("Policies", [])] + \
                   ([r["Properties"]["PolicyDocument"]] if "PolicyDocument" in r["Properties"] else [])
            for d in docs:
                yield from d["Statement"]


@pytest.mark.parametrize("name", ["aws-stack.yaml", "agentcore-runtime.yaml"])
def test_iam_is_least_privilege(name):
    stmts = list(_statements(_load(name)))
    assert stmts
    for s in stmts:
        acts = s["Action"] if isinstance(s["Action"], list) else [s["Action"]]
        assert not any(a == "*" or a.endswith(":*") for a in acts), s
        if any(a.startswith("bedrock:") for a in acts):
            res = json.dumps(s["Resource"])
            assert '"*"' not in res and "foundation-model/*" not in res, "Bedrock access must name a model"
        if any(a.startswith("secretsmanager:") for a in acts):
            assert s["Resource"] != "*"


def test_app_runner_stack_shape():
    t = _load("aws-stack.yaml")
    svc = t["Resources"]["Service"]["Properties"]
    img = svc["SourceConfiguration"]["ImageRepository"]["ImageConfiguration"]
    env = {e["Name"] for e in img["RuntimeEnvironmentVariables"]}
    secret_env = {e["Name"] for e in img["RuntimeEnvironmentSecrets"]}
    assert secret_env == {"DATABASE_URL", "SESSION_SECRET"} and not ({"DATABASE_URL", "SESSION_SECRET", "PARENT_PIN"} & env)
    assert {"AWS_SECRETS_ID", "BEDROCK_ENABLED", "BEDROCK_MODEL_ID", "BEDROCK_GUARDRAIL_ID", "EMF_NAMESPACE"} <= env
    assert svc["HealthCheckConfiguration"]["Path"] == "/healthz" and img["Port"] == "8000"
    assert "InstanceRoleArn" in svc["InstanceConfiguration"]
    for p in ("DatabaseUrl", "ParentPin"):
        assert t["Parameters"][p]["NoEcho"] is True
    assert t["Parameters"]["BedrockEnabled"]["Default"] == "false"          # AI is opt-in
    g = t["Resources"]["Guardrail"]["Properties"]
    assert {f["Type"] for f in g["ContentPolicyConfig"]["FiltersConfig"]} >= {"HATE", "SEXUAL", "VIOLENCE", "PROMPT_ATTACK"}
    assert {p["Type"] for p in g["SensitiveInformationPolicyConfig"]["PiiEntitiesConfig"]} >= {"NAME", "EMAIL", "PHONE"}
    alarms = [r["Properties"] for r in t["Resources"].values() if r["Type"] == "AWS::CloudWatch::Alarm"]
    assert alarms and all(a["Namespace"] == "LearningCoach" for a in alarms)


def test_agentcore_template_matches_container_contract():
    t = _load("agentcore-runtime.yaml")
    rt = t["Resources"]["Runtime"]["Properties"]
    assert rt["ProtocolConfiguration"] == "MCP" and rt["NetworkConfiguration"]["NetworkMode"] == "PUBLIC"
    env = rt["EnvironmentVariables"]
    assert env["MCP_STATELESS"] == "1" and env["MCP_ONLY"] == "1"
    header = env["MCP_LINK_TOKEN_HEADER"]
    assert header.lower() in [h.lower() for h in rt["RequestHeaderConfiguration"]["RequestHeaderAllowlist"]]
    assert not header.lower().startswith(("x-amz-", "x-amzn-"))              # restricted prefixes per the AgentCore docs
    trust = t["Resources"]["ExecutionRole"]["Properties"]["AssumeRolePolicyDocument"]["Statement"][0]
    assert trust["Principal"]["Service"] == "bedrock-agentcore.amazonaws.com"


def test_dockerfiles_follow_the_documented_contracts():
    ac = open(os.path.join(ROOT, "Dockerfile.agentcore")).read()
    assert "--platform=linux/arm64" in ac and "PORT=8000" in ac and "MCP_STATELESS=1" in ac and "MCP_ONLY=1" in ac
    main = open(os.path.join(ROOT, "Dockerfile")).read()
    assert "WITH_STRANDS" in main and "mcp>=1.27,<2" in main
