import manage

def test_precedence_cli_over_env_over_tf(monkeypatch):
    env = {"SOCKS_HOST": "from-env"}
    tf = {"jumphost_public_ip": {"value": "from-tf"}}
    monkeypatch.setattr(manage, "_read_env", lambda: dict(env))
    monkeypatch.setattr(manage, "_read_tf_output", lambda: dict(tf))
    cfg = manage.load_config({"SOCKS_HOST": "from-cli"})
    assert cfg["SOCKS_HOST"] == "from-cli"

def test_env_falls_back_to_tf(monkeypatch):
    monkeypatch.setattr(manage, "_read_env", lambda: {})
    monkeypatch.setattr(manage, "_read_tf_output", lambda: {"jumphost_public_ip": {"value": "1.2.3.4"}})
    cfg = manage.load_config({})
    assert cfg["SOCKS_HOST"] == "1.2.3.4"
