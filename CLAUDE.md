# CLAUDE.md

## 目的

このリポジトリで Claude を呼ぶときの案件固有レビュー観点を定義する。

## 期待役割

- ROI キャプチャと分類フローの整理
- Gemini 利用境界のレビュー
- architecture と実装差分の確認

## 調査系との境界

- X 上の反応調査は `x-search` を優先する
- Claude は調査結果を構成や設計レビューへ接続する

## 確認すべきこと

- `work-env` との整合性
- API key や output artifact の扱い
- デモ / 検証用途としての前提が保たれているか
