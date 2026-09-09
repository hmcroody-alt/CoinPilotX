import pytest
import requests

from services.business_os.suppliers.cj import BASE_URL, RequestsTransport, CJAdapter
from services.business_os.suppliers.errors import SupplierError
from tests.business_os.test_cj_adapter import make_adapter, Response


def test_streamed_download_closes_and_preserves_bounded_body(monkeypatch):
    monkeypatch.setenv("CJ_NETWORK_ENABLED", "true")
    transport = RequestsTransport()
    closed, kwargs_seen = [], []
    response = requests.Response()
    response.status_code = 200
    response.iter_content = lambda **kwargs: iter([b'{"ok":', b'true}'])
    response.close = lambda: closed.append(True)
    def request(method, url, **kwargs):
        kwargs_seen.append(kwargs)
        return response
    monkeypatch.setattr(transport.session, "request", request)
    assert transport.request("GET", BASE_URL + "/setting/get").json() == {"ok": True}
    assert kwargs_seen[0]["stream"] is True and closed == [True]


def test_oversized_decompressed_stream_stops_and_marks_write_ambiguous(monkeypatch):
    monkeypatch.setenv("CJ_NETWORK_ENABLED", "true")
    transport = RequestsTransport()
    closed = []
    response = requests.Response()
    response.iter_content = lambda **kwargs: iter([b"x" * 5_000_000, b"x", b"never consumed"])
    response.close = lambda: closed.append(True)
    monkeypatch.setattr(transport.session, "request", lambda *args, **kwargs: response)
    with pytest.raises(SupplierError) as failure:
        transport.request("POST", BASE_URL + "/shopping/order/createOrderV2")
    assert failure.value.ambiguous_write and closed == [True]


def test_background_inventory_cannot_consume_critical_reserve():
    adapter, _, quota, _ = make_adapter(Response({"variantInventories": []}))
    adapter.background = True
    adapter.get_inventory("1001")
    assert quota.calls[0][1]["cost"] == 10
    assert quota.calls[0][1]["critical"] is False


@pytest.mark.parametrize("bad_id", [None, True, False, 1.25, [], {}])
def test_order_normalization_never_stringifies_invalid_identifier(bad_id):
    with pytest.raises(SupplierError):
        CJAdapter._order({"orderId": bad_id})


@pytest.mark.parametrize("http_status", [200, 429])
def test_rate_limited_response_retains_points_metadata_without_retry(http_status):
    adapter, transport, quota, _ = make_adapter(Response(status=http_status, body={"code": 429, "result": False,
        "pointsInfo": {"remaining": 0, "usedToday": 50000, "total": 50000}}))
    with pytest.raises(SupplierError) as failure:
        adapter.get_inventory("1001")
    assert failure.value.code == "RATE_LIMITED"
    assert adapter.points_info["remaining"] == 0
    assert quota.observations[0][1]["remaining"] == 0 and len(quota.penalties) == 1
    assert len(transport.calls) == 1
