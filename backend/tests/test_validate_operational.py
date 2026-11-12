import pytest

from app.validate.operational import Detail, find_operational_detail


@pytest.mark.parametrize(
    ("text", "detail", "kind"),
    [
        ("Moved the queue to 10.0.4.12.", "10.0.4.12", "an IP address"),
        ("Opened 10.20.0.0/16 to the new service.", "10.20.0.0/16", "an IP address"),
        ("Moved orders to queue.payments.internal.", "queue.payments.internal", "a host name"),
        ("Served it from api.eu.northwind.com.", "api.eu.northwind.com", "a host name"),
        ("Retired prod-db-01 after the move.", "prod-db-01", "a server name"),
        ("Moved jobs off worker3.", "worker3", "a server name"),
        ("Exposed the service on port 8443.", "port 8443", "a port"),
        ("Pointed it at localhost:5432.", "localhost:5432", "a port"),
        ("Linked https://wiki.example.com/runbook in the alert.", None, "a web address"),
        ("Kept the logs in /var/log/orders/ for a week.", "/var/log/orders/", "a file path"),
        ("Copied it to C:\\deploy\\orders.", "C:\\deploy\\orders.", "a file path"),
        ("Set ORDERS_QUEUE_URL for the new queue.", "ORDERS_QUEUE_URL", "a configuration name"),
        ("Ran `kubectl rollout restart` after each deploy.", None, "code or a command"),
        ("Patched CVE-2025-12345 in the gateway.", "CVE-2025-12345", "a CVE number"),
        ("Resolved INC0012345 within the hour.", "INC0012345", "a ticket or incident number"),
        ("Fixed the export after incident #42.", "incident #42", "a ticket or incident number"),
        ("Closed PAY-1234 in one sprint.", "PAY-1234", "a ticket number"),
        ("Rotated the root passwords each quarter.", "root passwords", "credentials or access"),
        ("Managed the SSH keys for the fleet.", "SSH keys", "credentials or access"),
        ("Ran the export at 02:00 UTC each night.", "02:00 UTC", "a time of day"),
        ("Ran the export at 3am each night.", "3am", "a time of day"),
        ("Sent alerts to oncall@northwind.example.", None, "an email address"),
    ],
)
def test_operational_specifics_are_found(text: str, detail: str | None, kind: str) -> None:
    found = find_operational_detail(text)
    assert [item.kind for item in found] == [kind]
    if detail is not None:
        assert found[0].text == detail


@pytest.mark.parametrize(
    "text",
    [
        "Build and maintain Python services that process order events from a message queue.",
        "Cut the nightly export job from 50 to 12 minutes by batching database writes.",
        "Led the move of order processing from a cron job to a message queue.",
        "Designed the database access layer for the order service.",
        "Built a password reset flow and an API key management service.",
        "Encrypted data at rest with AES-256 and signed it with SHA-256.",
        "Met ISO-27001 and WCAG 2.1 AA, and kept COVID-19 dashboards running 24/7.",
        "Set up CI/CD for Node.js and ASP.NET services on Vue.js and socket.io.",
        "Ran 1:1 meetings with 4 engineers and cut p99 latency by 30%.",
        "Ran product2 and the appendix1 study in Python 3.12.",
        "Moved the team to TCP/IP and/or gRPC, e.g. for the U.S. office.",
    ],
)
def test_architecture_and_outcomes_are_not_operational_detail(text: str) -> None:
    assert find_operational_detail(text) == ()


def test_overlapping_matches_keep_the_widest_and_each_detail_once() -> None:
    text = (
        "Moved https://db.prod.northwind.com:5432/orders to 10.0.4.12, "
        "then to 10.0.4.12 again at 02:00."
    )
    assert find_operational_detail(text) == (
        Detail(text="https://db.prod.northwind.com:5432/orders", kind="a web address"),
        Detail(text="10.0.4.12", kind="an IP address"),
        Detail(text="02:00", kind="a time of day"),
    )
