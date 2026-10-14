import httpcore
import httpx
import pytest

from micropub_server.auth.client import FetchError, GuardedBackend, guarded_client


class FakeBackend(httpcore.AsyncNetworkBackend):
    def __init__(self):
        self.connected_to = []

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        self.connected_to.append(host)
        raise httpcore.ConnectError("fake: never really connects")

    async def sleep(self, seconds):
        pass


async def test_connects_to_the_validated_address_not_the_name():
    inner = FakeBackend()
    backend = GuardedBackend(inner, resolver=lambda host: ["93.184.216.34"])
    with pytest.raises(httpcore.ConnectError):
        await backend.connect_tcp("app.example", 443)
    assert inner.connected_to == ["93.184.216.34"]  # no second lookup can change where it goes


@pytest.mark.parametrize(
    "answer", ["127.0.0.1", "10.0.0.5", "169.254.169.254", "100.80.1.1", "::1", "192.168.1.1"]
)
async def test_refuses_names_that_resolve_to_private_addresses(answer):
    inner = FakeBackend()
    backend = GuardedBackend(inner, resolver=lambda host: [answer])
    with pytest.raises(FetchError):
        await backend.connect_tcp("rebind.example", 80)
    assert inner.connected_to == []


async def test_all_answers_must_be_public():
    backend = GuardedBackend(FakeBackend(), resolver=lambda host: ["93.184.216.34", "10.0.0.1"])
    with pytest.raises(FetchError):
        await backend.connect_tcp("mixed.example", 80)


async def test_a_literal_private_address_is_refused():
    backend = GuardedBackend(FakeBackend(), resolver=lambda host: [host])
    with pytest.raises(FetchError):
        await backend.connect_tcp("127.0.0.1", 80)


async def test_the_client_is_built_on_the_guard():
    client = guarded_client(resolver=lambda host: ["10.0.0.9"])
    with pytest.raises((FetchError, httpx.ConnectError, httpcore.ConnectError)):
        await client.get("http://internal.example/")
    await client.aclose()
