def test_chat_remembers_history(client, login):
    headers = login()
    conv_id = client.post("/conversations", headers=headers).json()["id"]

    first = client.post(f"/conversations/{conv_id}/messages", headers=headers, json={"content": "my name is Sam"})
    assert "1 messages" in first.json()["content"]

    second = client.post(f"/conversations/{conv_id}/messages", headers=headers, json={"content": "what's my name?"})
    assert "3 messages" in second.json()["content"]

    messages = client.get(f"/conversations/{conv_id}/messages", headers=headers).json()
    assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant"]


def test_first_message_becomes_title(client, login):
    headers = login()
    conv_id = client.post("/conversations", headers=headers).json()["id"]
    client.post(f"/conversations/{conv_id}/messages", headers=headers, json={"content": "Plan my AWS week"})

    conversations = client.get("/conversations", headers=headers).json()
    assert conversations[0]["title"] == "Plan my AWS week"


def test_users_cannot_see_each_others_chats(client, login):
    alice = login("alice")
    conv_id = client.post("/conversations", headers=alice).json()["id"]

    bob = login("bob")
    assert client.get("/conversations", headers=bob).json() == []
    assert client.get(f"/conversations/{conv_id}/messages", headers=bob).status_code == 404
    assert client.delete(f"/conversations/{conv_id}", headers=bob).status_code == 404


def test_delete_conversation(client, login):
    headers = login()
    conv_id = client.post("/conversations", headers=headers).json()["id"]

    assert client.delete(f"/conversations/{conv_id}", headers=headers).status_code == 204
    assert client.get(f"/conversations/{conv_id}/messages", headers=headers).status_code == 404
