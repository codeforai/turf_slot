from prometheus_client.parser import text_string_to_metric_families


def test_liveness(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_checks_database_and_migrations(client):
    response = client.get("/health/ready")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"] == {"database": "ok", "migrations": "ok"}


def test_request_id_is_returned_and_propagated(client):
    generated = client.get("/health")
    assert len(generated.headers["x-request-id"]) == 32
    echoed = client.get("/health", headers={"X-Request-ID": "abc-123"})
    assert echoed.headers["x-request-id"] == "abc-123"


def test_metrics_endpoint_reports_requests(client):
    client.get("/turfs")
    client.get("/turfs/999999")
    metrics = client.get("/metrics")
    assert metrics.status_code == 200

    families = {f.name: f for f in text_string_to_metric_families(metrics.text)}
    recorded = {
        (s.labels["method"], s.labels["route"], s.labels["status"])
        for s in families["http_requests"].samples
        if s.name == "http_requests_total"
    }
    assert ("GET", "/turfs", "200") in recorded, recorded
    assert ("GET", "/turfs/{turf_id}", "404") in recorded, recorded
    assert "http_request_duration_seconds" in families
    assert "turfslot_bookings_created" in families


def test_openapi_docs_available(client):
    assert client.get("/docs").status_code == 200
    schema = client.get("/openapi.json").json()
    assert "/bookings/{booking_id}/confirm-payment" in schema["paths"]


def test_root_redirects_to_docs(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/docs"


def test_metrics_can_require_a_bearer_token(client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "metrics_token", "scrape-secret")
    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/metrics", headers={"Authorization": "Bearer scrape-secret"}).status_code == 200
