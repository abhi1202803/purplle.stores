import os
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from app.database.connection import Base
from app.database.crud import create_event, create_transaction, create_alert, get_events, get_alerts
from app.analytics.metrics import calculate_store_metrics, calculate_revenue_analytics
from app.api.main import app, get_db

# Create an in-memory SQLite database for testing CRUD and Analytics logic
SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@pytest.fixture(scope="function")
def db_session():
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)

@pytest.fixture()
def client(db_session):
    # Override get_db in FastAPI
    def override_get_db():
        try:
            yield db_session
        finally:
            pass
    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()

def test_database_crud(db_session):
    """Test inserting and retrieving events, transactions, and alerts."""
    # Test Event insert
    evt = create_event(
        db=db_session,
        event_id="evt_test_01",
        store_id="store1",
        camera_id="cam3",
        customer_id="cust_test_101",
        event_type="customer_entered",
        timestamp="2026-06-01T12:00:00Z"
    )
    assert evt.id is not None
    assert evt.event_id == "evt_test_01"
    
    # Test Retrieve
    events = get_events(db_session, store_id="store1")
    assert len(events) == 1
    assert events[0].customer_id == "cust_test_101"
    
    # Test Transaction insert
    tx = create_transaction(
        db=db_session,
        order_id=1,
        order_date="10-04-2026",
        order_time="12:15:00",
        store_id="ST1008",
        product_id="399945",
        brand_name="Faces Canada",
        total_amount=302.33
    )
    assert tx.order_id == 1
    
    # Test Alert insert
    alert = create_alert(
        db=db_session,
        alert_id="alert_test_01",
        store_id="store1",
        alert_type="Crowding",
        description="Spike detected",
        timestamp="2026-06-01T12:05:00Z",
        severity="Warning"
    )
    assert alert.alert_id == "alert_test_01"
    
    alerts = get_alerts(db_session, store_id="store1")
    assert len(alerts) == 1

def test_analytics_calculations(db_session):
    """Test that analytics engine aggregates statistics correctly."""
    # Seed events
    create_event(db_session, "e1", "store1", "cam3", "cust_1", "customer_entered", "2026-06-01T12:00:00Z")
    create_event(db_session, "e2", "store1", "cam3", "cust_1", "customer_exited", "2026-06-01T12:10:00Z") # 10 mins (600s)
    
    # Seed transactions
    create_transaction(db_session, 1, "10-04-2026", "12:15:00", "ST1076", "prod1", "BrandA", 150.0)
    
    metrics = calculate_store_metrics(db_session, store_id="store1")
    
    assert metrics["footfall"] == 1
    assert metrics["avg_dwell_time_sec"] == 600.0
    assert metrics["revenue"] == 150.0
    assert metrics["conversion_rate"] == 1.0 # 1 purchase / 1 visitor

def test_api_health_endpoint(client):
    """Test standard health check router."""
    response = client.get("/health")
    assert response.status_code == 200
    json_data = response.json()
    assert json_data["status"] == "healthy"
    assert "database" in json_data

def test_api_funnel_endpoint(client, db_session):
    """Test standard conversion funnel router."""
    # Seed events
    create_event(db_session, "e1", "store1", "cam3", "cust_1", "customer_entered", "2026-06-01T12:00:00Z")
    create_event(db_session, "e2", "store1", "cam1", "cust_1", "zone_entered", "2026-06-01T12:02:00Z", extra_data='{"roi_name": "Left Shelf"}')
    create_event(db_session, "e3", "store1", "cam1", "cust_1", "zone_exited", "2026-06-01T12:05:00Z", extra_data='{"roi_name": "Left Shelf"}')
    create_event(db_session, "e4", "store1", "cam5", "cust_1", "billing_started", "2026-06-01T12:08:00Z")
    create_event(db_session, "e5", "store1", "cam5", "cust_1", "billing_completed", "2026-06-01T12:10:00Z")

    response = client.get("/funnel?store_id=store1")
    assert response.status_code == 200
    json_data = response.json()
    assert json_data["store_id"] == "store1"
    steps = json_data["steps"]
    assert steps[0]["count"] == 1
    assert steps[1]["count"] == 1
    assert steps[2]["count"] == 1
    assert steps[3]["count"] == 1

