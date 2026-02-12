# 概要
このプロジェクトは、ESXi Host Client の画面を一定間隔で監視し、
「今どの画面状態か」を自動判定して可視化・記録する仕組みです。

実行中はオーバーレイで状態を表示し、終了時には JSON ログと Markdown サマリを生成します。
目的は、デモや検証時の操作状況を分かりやすく残すことです。

# 用語（ROIとは）
ROI は **Region Of Interest** の略で、日本語では「関心領域」または「注目領域」です。
この仕組みでは、画面全体ではなく「判定に必要な範囲だけ」を ROI として切り出して扱います。

# 必要なコンポーネント
- `pick_roi.py`
  ROI を選択し、`out/region.json` に保存します。
- `realtime_summary.py`
  メイン処理です。キャプチャ、分類、安定化、オーバーレイ表示、ログ保存、サマリ生成を担当します。
- `templates/`
  Gemini が不安定なときのフォールバック判定で使うテンプレート画像です。
- `out/`
  実行結果（ログ・サマリ・最新ROI画像など）の保存先です。
- Gemini API キー
  `GOOGLE_API_KEY` または `GEMINI_API_KEY` を環境変数で設定します。
- Python 実行環境
  `opencv-python`, `mss`, `numpy`, `pillow`, `google-genai` などが必要です。

# 使い方
1. ROI を設定する
   - `python .\pick_roi.py`
   - 画面上で監視対象領域を選択し、`out/region.json` を作成します。
2. API キーを設定する（PowerShell）
   - `$env:GOOGLE_API_KEY="<YOUR_API_KEY>"`
3. リアルタイム実行する
   - `python .\realtime_summary.py`
4. 実行中の操作
   - `q`: 終了
   - `v`: ROIプレビュー表示切替
   - `1-9`: 手動イベント記録

# 生成される結果（データ）
- `out/run_<timestamp>.json`
  セッション本体ログ。主な項目は以下です。
  - `start_ts`, `end_ts`, `elapsed_sec`
  - `region`
  - `state_events`（状態遷移の時系列）
  - `ops_events`（手動イベント）
  - `durations`（状態別滞在時間）
  - `last_gemini`（最終推論メタ情報）
- `out/summary_<timestamp>.md`
  セッションの時系列サマリ（日本語見出し）。
- `out/last_roi.jpg`
  最新 ROI 画像（設定時/必要時）。
- `out/gemini_debug.log`
  デバッグ有効時の Gemini 詳細ログ。

# Computer Visionとは
Computer Vision は、画像や映像から意味を読み取る技術です。
このプロジェクトにおける CV は、主に次を指します。

- 画面の ROI を安定して取得する
- 入力異常（空画像・単色近傍など）を検知する
- 画面状態（`vm_list`, `vm_create`, `user_add` など）を分類する
- 単発誤判定に引きずられないように状態を安定化する

# Geminiの役割
Gemini は「画像の意味理解」と「画面状態の分類」を担当します。

- ROI画像を受け取り、定義済みラベルへ分類する
- JSONスキーマ制約で出力を安定化する
- 応答不調時はテンプレート判定へフォールバックして `unknown` を減らす
- （任意）ログを要約する補助情報を生成する

# どのようなことに役立つか
- デモ中に現在フェーズをリアルタイム表示できる
- 操作履歴を時系列で残し、後から検証しやすくなる
- 手順説明や教育用途で「何をいつ実施したか」を共有しやすい
- PoC段階で、画面認識パイプラインの精度改善を回しやすい
- 将来的に運用監視・分析基盤へ拡張しやすい
