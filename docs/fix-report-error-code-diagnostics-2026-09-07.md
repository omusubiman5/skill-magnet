# エラー特定性向上とエラーコード表示対応報告書

作成日：2026-09-07  
対象モジュール：`src/skill_magnet/ui.py`  
関連文書：
- 原因調査報告書：`docs/root-cause-error-code-diagnostics-2026-09-07.md`
- 実装計画書：`docs/github-direct-skill-reference-implementation-plan-2026-08-30.md`

---

## 1. 目的

「日常の利用フロー（右クリック → スキル選択 → AI選択 → 依頼入力 → 起動）」および各処理で障害が発生した際、利用者が何のエラーが起きているのかを一目で特定でき、エラー報告や調査を迅速に行えるよう、**明確なエラーコード（`SM-E...`）と詳細な原因情報・例外型をすべてのエラーダイアログ・サーフェスに表示する構成**へ改修した。

---

## 2. 改修内容

### (1) エラーコード体系の定義とサーフェスへの統合 (`src/skill_magnet/ui.py`)

`context_failure_surface` において、発生した例外型やエラー内容に応じた体系的なエラーコードを自動判定し、ダイアログおよびメタデータへ追加した。

| エラーコード | 識別名 | 対象・発生条件 |
|---|---|---|
| `SM-E001` | `REQUEST_EMPTY` | 依頼内容が空のまま「起動」を押下 |
| `SM-E002` | `RUNTIME_UNSELECTED` | 有効なAIランタイム（Codex/Claude）が未選択 |
| `SM-E003` | `PACK_UNSELECTED` | スキルパック／スキルが未選択 |
| `SM-E101` | `AI_LAUNCH_FAILED` | AIエージェント起動プロセスの開始失敗 |
| `SM-E102` | `AI_RUNTIME_FAILED` | AIエージェント検証プロセスの中断・異常終了 |
| `SM-E103` | `ACCEPTANCE_FAILED` | スキル固有受入条件の不合格 |
| `SM-E104` | `CLEANUP_FAILED` | 一時成果物の後始末不整合 |
| `SM-E105` | `OUTPUT_FAILED` | AI出力形式の不備（JSON構文等） |
| `SM-E201` | `MENU_REINSTALL_REQUIRED` | コンテキストメニュー設定不一致（再登録要求） |
| `SM-E202` | `SELECTION_INVALIDATED` | 選択画面起動後の構成変更による無効化 |
| `SM-E203` | `CONFIG_INVALID` | 設定JSON構文エラー |
| `SM-E204` | `WORKSPACE_INVALID` | 対象フォルダーが存在しない、またはアクセス不能 |
| `SM-E205` | `TRANSACTION_INTERRUPTED` | ライブラリ更新トランザクションの中断 |
| `SM-E301` | `FILE_NOT_FOUND` | 指定ファイル・実行パスが存在しない |
| `SM-E302` | `PERMISSION_DENIED` | アクセス権限不足 |
| `SM-E303` | `OS_ERROR_<errno>` | OSシステムエラー（WinError含む） |
| `SM-E999` | `UNEXPECTED_<class>` | その他の予期しない例外 |

### (2) エラーダイアログの出力フォーマット強化

`context_failure_message` を改修し、画面およびログに以下の明瞭な構成で出力するようにした：

```text
[タイトル]

エラーコード
SM-Exxx (<識別名>)

原因
<原因説明>
[エラーコード: SM-Exxx (<識別名>)]

未実行・未確認の範囲
<未完了のスコープ>

次の操作
<推奨アクション>
```

### (3) メイン画面（ランチャー）の検証ダイアログ改善

1. **入力チェック**:
   - 依頼未入力時：`依頼内容を入力してください\n\n[エラーコード: SM-E001 (REQUEST_EMPTY)]`
   - AI未選択時：`利用するAIを選択してください\n\n[エラーコード: SM-E002 (RUNTIME_UNSELECTED)]`
   - パック未選択時：`利用するスキルパックを選択してください\n\n[エラーコード: SM-E003 (PACK_UNSELECTED)]`
2. **バックグラウンド検証失敗**:
   - 単なる `処理に失敗しました\n\n{error}` ではなく、エラーコード・原因・未実行範囲・次の操作を含む完全な `context_failure_message` を親ウィンドウ（`parent=root`）のダイアログとして表示。

---

## 3. 検証結果

1. **構文チェック**:
   - `python -m py_compile src/skill_magnet/ui.py` PASS
2. **単体テスト確認**:
   - 既存の `context_failure_message` および `context_failure_surface` アサーション（「原因」「未実行・未確認の範囲」「次の操作」の含有チェック）がすべて正常に通過することを確認。
3. **日常利用フローの体験**:
   - 右クリック → メイン画面表示 → 入力 → 起動のどの段階で問題が生じても、画面上に一意のエラーコードが表示されるため、即座に障害の特定が可能となった。
