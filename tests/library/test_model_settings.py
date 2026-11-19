import json
from library.config import LibraryConfig
from library.model_settings import ModelSettings, ModelProfiles


def test_settings_keep_secrets_private_and_do_not_send_them_to_new_service(tmp_path):
    store = ModelSettings(LibraryConfig(data_dir=tmp_path))
    profiles = store.defaults()
    values = profiles.model_dump(mode="json")
    for value in values.values():
        value["api_key"] = "secret-example"
    store.save(ModelProfiles.model_validate(values))
    assert "secret-example" not in json.dumps(store.public())
    assert "secret-example" not in store.path.read_text()
    assert store.secrets_path.stat().st_mode & 0o777 == 0o600
    for value in values.values():
        value["api_key"] = ""
    store.save(ModelProfiles.model_validate(values))
    assert store.load().text.key() == "secret-example"
    values["text"]["base_url"] = "http://localhost:4000/v1"
    store.save(ModelProfiles.model_validate(values))
    assert store.load().text.key() == "noapikey"
    assert store.load().vision.key() == "secret-example"


def test_default_runtime_reuses_current_client(tmp_path):
    cfg = LibraryConfig(data_dir=tmp_path)
    store = ModelSettings(cfg)
    client = object()
    with store.runtime(client) as (runtime_cfg, runtime_client):
        assert runtime_cfg is cfg
        assert runtime_client is client
