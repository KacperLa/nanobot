import pytest

from nanobot.bus.queue import MessageBus
from nanobot.channels.signal import SignalChannel, SignalConfig, SignalDMConfig, SignalGroupConfig


def _make_group_allowlist_config() -> SignalConfig:
    return SignalConfig(
        enabled=True,
        account="+15550000000",
        allow_from=["allowed-dm-sender"],
        dm=SignalDMConfig(enabled=True, policy="allowlist"),
        group=SignalGroupConfig(
            enabled=True,
            policy="allowlist",
            allow_from=["allowed-group"],
            require_mention=False,
        ),
    )


@pytest.mark.asyncio
async def test_group_message_uses_signal_group_policy_without_base_allowlist(monkeypatch) -> None:
    bus = MessageBus()
    channel = SignalChannel(_make_group_allowlist_config(), bus)
    published = []
    typing_events = []

    async def _fake_publish_inbound(message) -> None:
        published.append(message)

    async def _fake_start_typing(chat_id: str) -> None:
        typing_events.append(chat_id)

    monkeypatch.setattr(bus, "publish_inbound", _fake_publish_inbound)
    monkeypatch.setattr(channel, "_start_typing", _fake_start_typing)

    await channel._handle_data_message(
        sender_id="group-sender",
        sender_number="group-sender",
        data_message={
            "message": "hello there",
            "timestamp": 123,
            "groupInfo": {"groupId": "allowed-group"},
        },
        sender_name="Alice",
    )

    assert typing_events == ["allowed-group"]
    assert len(published) == 1
    assert published[0].chat_id == "allowed-group"
    assert published[0].sender_id == "group-sender"
    assert published[0].channel == "signal"
