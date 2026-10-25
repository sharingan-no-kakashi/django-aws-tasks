"""A real browser operates the IAM-protected deployed Django admin."""

import secrets
import threading
from http.server import ThreadingHTTPServer
from uuid import uuid4

from example_tools.admin_proxy import handler_for
from playwright.sync_api import sync_playwright


def test_deployed_admin_replay_and_read_only_permissions(deployment):
    submission = deployment.submit()
    deployment.wait(submission, lambda row: row["task"]["status"] == "SUCCESSFUL")
    username = f"acceptance-{uuid4().hex[:12]}"
    password = secrets.token_urlsafe(32)
    deployment.call("bootstrap-admin", {"username": username, "password": password})
    handler = handler_for(deployment.session, deployment.configuration["admin_url"].rstrip("/"))
    with ThreadingHTTPServer(("127.0.0.1", 0), handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page()
                base = f"http://127.0.0.1:{server.server_port}"
                login(page, base, username, password)
                page.goto(
                    base + f"/admin/django_tasks_aws/taskrecord/{submission['task_id']}/change/"
                )
                page.get_by_role("link", name="Review replay").click()
                page.get_by_label("Reason:").fill("Disposable deployed effect identity reviewed")
                page.get_by_label("I reviewed possible business effects").check()
                page.get_by_role("button", name="Preview eligibility").click()
                page.get_by_role("button", name="Confirm reviewed requests").click()
                replacement = (
                    page.locator("#content li a").first.get_attribute("href").split("/")[-3]
                )
                result = deployment.wait(
                    {**submission, "task_id": replacement},
                    lambda row: row["task"]["status"] == "SUCCESSFUL",
                )
                assert len(result["effects"]) == 1
                readonly_check(deployment, browser, base, submission, password)
                browser.close()
                deployment.retain(
                    "deployed-browser-replay", {"original": submission, "replacement": result}
                )
        finally:
            server.shutdown()
            thread.join(timeout=10)


def login(page, base, username, password):
    page.goto(base + "/admin/login/")
    page.get_by_label("Username:").fill(username)
    page.get_by_label("Password:").fill(password)
    page.get_by_role("button", name="Log in").click()


def readonly_check(deployment, browser, base, submission, password):
    username = f"readonly-{uuid4().hex[:12]}"
    deployment.call(
        "bootstrap-admin", {"username": username, "password": password, "read_only": True}
    )
    page = browser.new_page()
    login(page, base, username, password)
    page.goto(base + f"/admin/django_tasks_aws/taskrecord/{submission['task_id']}/change/")
    assert page.get_by_role("link", name="Review replay").count() == 0
    response = page.goto(
        base
        + "/admin/django_tasks_aws/taskrecord/review/?action=replay&ids="
        + submission["task_id"]
    )
    assert response.status == 403
    page.close()
