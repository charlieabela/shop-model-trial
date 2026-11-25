"""The API serves only once a model is loaded (Session 6, Stage 15). No registry needed: a local file stands in."""
import importlib
import time

import pytest
from fastapi.testclient import TestClient

from shop_model.features import add_vat
from shop_model.model import save_model, train_model


@pytest.fixture
def model_file(tmp_path, sample_df):
    path = tmp_path / "model.joblib"
    save_model(train_model(add_vat(sample_df, 0.18), seed=42), path)
    return str(path)


def test_ready_and_predict_with_a_model(monkeypatch, model_file):
    monkeypatch.setenv("MODEL_URI", model_file)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    import shop_model.api as api
    api = importlib.reload(api)
    with TestClient(api.app) as client:
        for _ in range(50):          # the model loads in the background
            if client.get("/ready").status_code == 200:
                break
            time.sleep(0.1)
        assert client.get("/ready").json()["ready"] is True
        body = client.post("/predict", json={"price": 20}).json()
        assert 0 <= body["probability_sold"] <= 1
        assert body["model_version"] == f"file:{model_file}"


def test_alive_but_not_ready_without_a_model(monkeypatch, tmp_path):
    monkeypatch.setenv("MODEL_URI", str(tmp_path / "missing.joblib"))
    import shop_model.api as api
    api = importlib.reload(api)
    with TestClient(api.app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/ready").status_code == 503
        assert client.post("/predict", json={"price": 20}).status_code == 503
