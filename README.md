# Mitrade AI Signal System v5.2

## 快速部署

```bash
cp .env.example .env   # 填入 API Key
pip install -r requirements.txt
python app.py
```

## 重要說明

- 標記為「請從 Artifact 複製」的檔案：需從 Claude 對話複製程式碼
- `.env` 不包含在此 ZIP，請自行填入 API Key
- `instance/mitrade.db` 首次啟動自動建立

## 檔案說明

| 檔案 | 說明 |
|------|------|
| adaptive_weight_engine.py | 全自動七維度權重引擎 |
| scoring_engine.py | Kelly/Sharpe/Regime/滑點 |
| state_store.py | SQLite 持久化 |
| backtester.py | Walk-Forward 回測 |
| watchdog.py | 健康監控 + Telegram 限流 |
| app.py | 主程式 + 27個 API 端點 |

## API 端點

GET  /api/state          系統完整快照
GET  /api/signals        當前訊號
GET  /api/macro          宏觀數據
GET  /api/performance    Sharpe/MaxDD/Calmar
GET  /api/regime         市場機制
GET  /api/weights        自適應七維度權重
GET  /api/equity         真實資金曲線
POST /api/scan/force     手動觸發掃描
POST /api/signal/:id/result  標記訊號結果
GET  /api/backtest/:sym  單品種回測
