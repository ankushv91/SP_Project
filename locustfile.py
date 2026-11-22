import logging

from locust import HttpUser, between, events, task

logger = logging.getLogger(__name__)
rate_limited_responses = 0
unexpected_responses = 0


@events.request.add_listener
def count_responses(response=None, **kwargs):
    global rate_limited_responses, unexpected_responses
    if response is not None and response.status_code == 429:
        rate_limited_responses += 1
    elif response is None or response.status_code != 200:
        unexpected_responses += 1


@events.quitting.add_listener
def require_rate_limited_response(environment, **kwargs):
    if rate_limited_responses == 0:
        environment.process_exit_code = 1
        logger.error("Load test did not receive any HTTP 429 responses")
    elif unexpected_responses:
        environment.process_exit_code = 1
        logger.error(
            "Load test received %s unexpected responses",
            unexpected_responses,
        )
    else:
        environment.process_exit_code = 0
        logger.info("Load test received %s HTTP 429 responses", rate_limited_responses)


class ApiUser(HttpUser):
    wait_time = between(0.1, 0.5)

    @task
    def check_health(self):
        with self.client.get(
            "/health-check", name="/health-check", catch_response=True
        ) as response:
            if response.status_code == 429:
                response.failure("Rate limit exceeded as expected")
            elif response.status_code != 200:
                response.failure(f"Unexpected status code: {response.status_code}")
