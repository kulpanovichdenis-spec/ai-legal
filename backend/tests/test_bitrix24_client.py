"""
Tests: Bitrix24Client

Запуск: docker compose run --rm backend python -m pytest /app/tests/test_bitrix24_client.py -v
"""

import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch
from bitrix24.client import Bitrix24Client


@pytest.mark.asyncio
async def test_send_message_no_webhook_url():
    """Если BITRIX24_WEBHOOK_URL не задан — возвращаем {} без запроса."""
    client = Bitrix24Client(webhook_url="", bot_id="1")
    result = await client.send_message("chat123", "Привет")
    assert result == {}


@pytest.mark.asyncio
async def test_send_message_calls_imbot_message_add():
    """send_message вызывает imbot.message.add с правильными параметрами."""
    webhook_url = "https://test.bitrix24.ru/rest/1/abc123"
    expected_url = webhook_url + "/imbot.message.add.json"
    captured_args: dict = {}

    async def mock_post(url, **kwargs):
        captured_args["url"] = url
        captured_args["json"] = kwargs.get("json", {})
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value={"result": 42})
        return mock_resp

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(side_effect=mock_post)

    with patch("bitrix24.client.httpx.AsyncClient", return_value=mock_client):
        client = Bitrix24Client(webhook_url=webhook_url, bot_id="7")
        result = await client.send_message("chat456", "Тестовое сообщение")

    assert result == {"result": 42}
    assert captured_args["url"] == expected_url
    assert captured_args["json"]["DIALOG_ID"] == "chat456"
    assert captured_args["json"]["MESSAGE"] == "Тестовое сообщение"
    assert captured_args["json"]["BOT_ID"] == "7"


@pytest.mark.asyncio
async def test_send_message_api_error_logged():
    """Ошибка API (поле error в ответе) логируется, результат возвращается."""
    webhook_url = "https://test.bitrix24.ru/rest/1/abc"

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(return_value={"error": "ACCESS_DENIED"})
    mock_client.post = AsyncMock(return_value=mock_resp)

    with patch("bitrix24.client.httpx.AsyncClient", return_value=mock_client):
        client = Bitrix24Client(webhook_url=webhook_url, bot_id="1")
        result = await client.send_message("chat1", "текст")

    assert "error" in result


@pytest.mark.asyncio
async def test_send_message_network_error():
    """Сетевая ошибка не бросает исключение наружу — возвращает {}."""
    webhook_url = "https://test.bitrix24.ru/rest/1/abc"

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(side_effect=httpx.ConnectError("connection refused"))

    with patch("bitrix24.client.httpx.AsyncClient", return_value=mock_client):
        client = Bitrix24Client(webhook_url=webhook_url, bot_id="1")
        result = await client.send_message("chat999", "текст")

    assert result == {}


# ── Исходящая интеграция «Задача Битрикс» ─────────────────────────────


@pytest.mark.asyncio
async def test_upload_file_no_webhook_returns_none():
    """Без webhook URL upload_file возвращает None без сетевого вызова."""
    client = Bitrix24Client(webhook_url="", bot_id="1")
    result = await client.upload_file("chat.docx", b"data")
    assert result is None


@pytest.mark.asyncio
async def test_upload_file_resolves_storage_and_uploads():
    """upload_file: getlist → uploadfile, возвращает ID файла."""
    client = Bitrix24Client(webhook_url="https://test.bitrix24.ru/rest/1/abc", bot_id="1")
    client._call = AsyncMock(side_effect=[
        {"result": [{"ID": "7"}]},          # disk.storage.getlist
        {"result": {"ID": "42"}},           # disk.storage.uploadfile
    ])

    file_id = await client.upload_file("chat.docx", b"binary")

    assert file_id == 42
    assert client._call.await_count == 2
    getlist_call, upload_call = client._call.await_args_list
    assert getlist_call.args[0] == "disk.storage.getlist"
    assert upload_call.args[0] == "disk.storage.uploadfile"
    upload_params = upload_call.args[1]
    assert upload_params["id"] == "7"
    assert upload_params["data"]["NAME"] == "chat.docx"
    # fileContent — массив [имя, base64]
    assert upload_params["fileContent"][0] == "chat.docx"


@pytest.mark.asyncio
async def test_upload_file_uses_configured_storage():
    """При заданном storage_id getlist не вызывается."""
    client = Bitrix24Client(webhook_url="https://test.bitrix24.ru/rest/1/abc", bot_id="1")
    client._call = AsyncMock(return_value={"result": {"ID": "99"}})

    file_id = await client.upload_file("c.docx", b"x", storage_id=5)

    assert file_id == 99
    assert client._call.await_count == 1
    assert client._call.await_args.args[0] == "disk.storage.uploadfile"
    assert client._call.await_args.args[1]["id"] == 5


@pytest.mark.asyncio
async def test_upload_file_skips_common_picks_owner_storage():
    """Выбирается личный диск владельца вебхука, а не Общий диск (в корень нельзя)."""
    client = Bitrix24Client(
        webhook_url="https://portal.example.test/rest/42/abc/", bot_id="1"
    )
    client._call = AsyncMock(side_effect=[
        {"result": [
            {"ID": "3", "ENTITY_TYPE": "common", "ENTITY_ID": "shared_files_s1"},
            {"ID": "702", "ENTITY_TYPE": "user", "ENTITY_ID": "42"},
            {"ID": "262", "ENTITY_TYPE": "user", "ENTITY_ID": "240"},
        ]},
        {"result": {"ID": "555"}},
    ])

    file_id = await client.upload_file("chat.docx", b"x")

    assert file_id == 555
    upload_params = client._call.await_args_list[1].args[1]
    assert upload_params["id"] == "702"  # личный диск пользователя 42


def test_webhook_user_id_parsed_from_url():
    client = Bitrix24Client(webhook_url="https://portal.example.test/rest/42/abc/", bot_id="1")
    assert client.webhook_user_id == "42"


@pytest.mark.asyncio
async def test_create_task_sends_correct_fields():
    """create_task передаёт RESPONSIBLE_ID, ACCOMPLICES и UF_TASK_WEBDAV_FILES."""
    client = Bitrix24Client(webhook_url="https://test.bitrix24.ru/rest/1/abc", bot_id="1")
    client._call = AsyncMock(return_value={"result": {"task": {"id": 123}}})

    resp = await client.create_task(
        title="Тест",
        description="Описание",
        responsible_id=42,
        accomplice_ids=[678],
        file_ids=[42],
    )

    assert resp["result"]["task"]["id"] == 123
    method, params = client._call.await_args.args
    assert method == "tasks.task.add"
    fields = params["fields"]
    assert fields["TITLE"] == "Тест"
    assert fields["RESPONSIBLE_ID"] == 42
    assert fields["ACCOMPLICES"] == [678]
    assert fields["UF_TASK_WEBDAV_FILES"] == ["n42"]


@pytest.mark.asyncio
async def test_create_task_without_files_omits_attachment_field():
    """Без file_ids поле UF_TASK_WEBDAV_FILES не добавляется."""
    client = Bitrix24Client(webhook_url="https://test.bitrix24.ru/rest/1/abc", bot_id="1")
    client._call = AsyncMock(return_value={"result": {"task": {"id": 5}}})

    await client.create_task(title="T", description="", responsible_id=1)

    fields = client._call.await_args.args[1]["fields"]
    assert "UF_TASK_WEBDAV_FILES" not in fields
    assert "ACCOMPLICES" not in fields


def test_task_url_built_from_portal():
    """task_url строит ссылку на задачу из портала webhook URL."""
    client = Bitrix24Client(webhook_url="https://portal.example.test/rest/42/abc/", bot_id="1")
    url = client.task_url(123, 42)
    assert url == "https://portal.example.test/company/personal/user/42/tasks/task/view/123/"
