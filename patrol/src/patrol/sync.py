"""结果同步到 prod 服务器（票 06，评审 #1 重塑）。

与 capture/env 客户端同一 seam：patrol.links.Transport，部署侧注入实现。
at-least-once 契约：传输重试可能导致重复行，prod 端 /ingest 以
INSERT OR REPLACE 保证幂等（ADR-0001）。失败保留 outbox 待补传。
"""

from __future__ import annotations

from patrol.store import JsonlStore

# prod 接收端点（spec §2.3：10.77.77.39；端口在部署时可覆盖）
PROD_INGEST_URL = "http://10.77.77.39:8000/ingest"


class SyncClient:
    def __init__(self, *, endpoint: str = PROD_INGEST_URL,
                 transport=None, batch_size: int = 500):
        self.endpoint = endpoint
        # transport：patrol.links.Transport 形状（send(url, *, params, body) -> dict）
        self._transport = transport
        self.batch_size = batch_size

    def flush(self, store: JsonlStore) -> int:
        """把 outbox 中的记录推送到 prod；全部成功后清空，失败保留待补传。"""
        records = store.pending()
        if not records:
            return 0
        if self._transport is None:
            raise NotImplementedError("未注入同步 transport（部署侧提供 links.Transport）")
        for i in range(0, len(records), self.batch_size):
            self._transport(self.endpoint, body={"rows": records[i:i + self.batch_size]})
        store.replace([])
        return len(records)
