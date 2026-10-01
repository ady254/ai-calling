import os

from locust import HttpUser, task, between


class AnalyticsUser(HttpUser):
    host = "http://localhost:8000"
    wait_time = between(1, 2)

    @task
    def get_analytics(self):
        token = os.getenv("API_TOKEN")
        
        self.client.get(
            "/call/analytics",
            
            headers={
                "Authorization": f"Bearer {token}"
            },
            name="GET /call/analytics",
        )