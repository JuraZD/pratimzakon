import pytest
import json
import os

def test_bridge_creates_notification_file():
    from agrolex_bridge import AgrolexBridge

    bridge = AgrolexBridge(queue_dir="/tmp/agrolex_queue_test")

    result = bridge.notify_new_document(
        doc_id=123,
        naziv="Pravilnik o izmjenama",
        nn_broj="15/25",
        datum="12.02.2025",
        link="https://nn.hr/doc/123"
    )

    assert result == True

    # Check file exists
    files = os.listdir("/tmp/agrolex_queue_test")
    assert len(files) == 1

    # Cleanup
    for f in files:
        os.remove(f"/tmp/agrolex_queue_test/{f}")
    os.rmdir("/tmp/agrolex_queue_test")
