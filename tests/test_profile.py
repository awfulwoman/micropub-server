import httpx

from .conftest import ME, sign_in

HOME = """<html><body>
<div class="h-card"><a class="u-url" href="https://elsewhere.example/">Other</a><span class="p-name">Not me</span></div>
<div class="h-card"><a class="u-url u-uid p-name" href="https://awfulwoman.com/">Charlie O'Hara</a>
<img class="u-photo" src="/me.jpg" alt=""></div>
</body></html>"""


def stub_home(no_network, text=HOME, status=200):
    return no_network.get(ME).respond(status, headers={"content-type": "text/html"}, text=text)


def token_response(client, scope="create profile"):
    return sign_in(client, scope=scope)


def test_the_profile_comes_from_the_h_card_that_matches_the_site(client, no_network):
    stub_home(no_network)
    assert token_response(client)["profile"] == {
        "name": "Charlie O'Hara",
        "url": "https://awfulwoman.com/",
        "photo": "https://awfulwoman.com/me.jpg",
    }


def test_configured_values_win_over_discovered_ones(client, state, no_network):
    stub_home(no_network)
    state.config.publication.profile = {"name": "C. O'Hara"}
    profile = token_response(client)["profile"]
    assert profile["name"] == "C. O'Hara" and profile["photo"] == "https://awfulwoman.com/me.jpg"


def test_first_h_card_is_used_when_none_matches(client, no_network):
    stub_home(no_network, '<div class="h-card"><span class="p-name">Only card</span></div>')
    assert token_response(client)["profile"] == {"name": "Only card", "url": ME}


def test_no_profile_scope_no_profile(client, no_network):
    stub_home(no_network)
    assert "profile" not in token_response(client, scope="create")


def test_a_site_that_is_down_just_means_no_profile(client, no_network):
    no_network.get(ME).mock(side_effect=httpx.ConnectError("down"))
    assert "profile" not in token_response(client)


def test_userinfo_uses_the_same_profile(client, no_network):
    stub_home(no_network)
    token = token_response(client)["access_token"]
    r = client.get("/auth/userinfo", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200 and r.json()["name"] == "Charlie O'Hara"


def test_the_profile_is_cached(client, no_network):
    route = stub_home(no_network)
    token_response(client)
    token_response(client)
    assert route.call_count == 1


def test_private_addresses_are_never_fetched(client, state, no_network):
    state.resolver = lambda host: ["10.0.0.5"]
    stub_home(no_network)
    assert "profile" not in token_response(client)
