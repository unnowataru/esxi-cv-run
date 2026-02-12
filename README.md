# esxi-cv-run

ESXi Host Client の画面を ROI（Region Of Interest）で定期キャプチャし、  
Google Gemini + テンプレート照合で状態分類するデモ/検証向けツールです。

- リアルタイムで状態をオーバーレイ表示
- 終了時に時系列ログ（`run_*.json`）とサマリ（`summary_*.md`）を出力

詳細は `architecture.md` を参照してください。

## セットアップ

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 実行

1. ROIを選択

```powershell
python .\pick_roi.py
```

2. APIキーを設定して実行

```powershell
$env:GOOGLE_API_KEY="<YOUR_API_KEY>"
python .\realtime_summary.py
```

## 出力

- `out/run_<timestamp>.json`: セッションログ
- `out/summary_<timestamp>.md`: セッションサマリ
- `out/last_roi.jpg`: 最新ROI画像
