import pytest
from web import app # Import the 'app' instance from your web.py

@pytest.fixture
def client():
    """Create a test client for the Flask app."""
    with app.test_client() as client:
        yield client

def test_homepage_loads(client):
    """
    GIVEN a running Flask app
    WHEN the '/' page is requested (GET)
    THEN check that the response is valid and contains expected content
    """
    response = client.get('/')
    assert response.status_code == 200
    assert b"Little Joe's" in response.data
    assert b"Choose a view from the nav" in response.data

def test_drivers_page_loads(client):
    """Tests if the drivers page loads correctly."""
    response = client.get('/drivers')
    assert response.status_code == 200
    assert b"<h1>Drivers</h1>" in response.data