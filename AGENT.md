# AGENT.md

## 目的

このリポジトリで動くエージェントに対し、案件固有差分と共通 SoT の関係を明示する。

## 共通前提

- 共通方針は `work-env` を優先する
- `work-env` は `C:\dev\work-env` にある前提で扱う
- 操作 UI は VS Code
- 実行オーケストレーターは Codex
- Claude は設計 / レビュー sidecar
- xAI は調査 sidecar
- `x-search` は blog / slide 向けの X 調査に使う

## 案件固有差分

- この repo は ESXi Host Client の ROI キャプチャと Gemini 分類を扱う
- 主な実装対象は `pick_roi.py`, `realtime_summary.py`, `templates/`
- デモ / 検証用途を前提にする

## 禁止

- 共通方針をこの repo 単独で上書きしない
- secrets をコミットしない
- `x-search` を画像分類や実行制御の代替に使わない
