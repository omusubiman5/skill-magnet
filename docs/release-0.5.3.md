# Skill Magnet 0.5.3 リリース報告

日付: 2026-09-03

## 判定

ローカル導入版0.5.3はリリースゲート合格。Library ManagerのPR merge待ちと障害復旧を状態機械として分離した。

## 修正

1. PR OPENをエラーではなく`waiting_for_merge`として保持する。
2. CLOSED未merge、MERGED、未知状態、merge後digest不一致を別々に扱う。
3. remote副作用後のlocal-only破棄をGUIとCLI domain APIで禁止する。
4. 同一library／remoteの非終端transactionを再利用し、重複PRを防ぐ。
5. PRを開く操作とmerge確認を分離し、その時点で可能な操作だけを1ボタンに表示する。
6. 差分0件ではcommit、push、PRを作らない。
7. 操作中はボタンを無効化し、二重実行を防ぐ。

## 実データ復旧

- 重複PR #2: CLOSED
- 継続対象PR #3: MERGED
- 継続transaction: `8dc76704a259400e9b0a2259612155ce`
- 実コード確認: `OPEN -> waiting_for_merge -> MERGED -> verified -> active`
- 有効化: `codex-cli`と`conflict-clarity`を追加し、Windows menuは3 package leaves＋Library Manager

## 検証と成果物

- release code: `3000d3c89284992faadd3f053631530c4f29b1c4`
- full test suite: 163 PASS、環境依存1件skip
- 独立wheel build: 2回
- logical payload SHA-256: `f300d219367c1deda0e7d6b9281bfc04e04eafbaa0c8f930133abc6b1bdd8651`（2 build一致）
- wheel file SHA-256: `8a5e1f9cae91cc2384ef0be8af9699e8d217c66007902fc271425dcca5ef8cb0`（保存したbuild）
- artifact: `dist/skill_magnet-0.5.3-py3-none-any.whl`
