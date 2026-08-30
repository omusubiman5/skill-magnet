# API従量課金なしDesktop handoff 実装報告

## 結論

Skill MagnetのCodex製品経路を、外部APIを呼ぶ実行器ではなく、検証済みskill packと実依頼を既存のCodex Desktop新規taskへ渡すhandoffへ一本化した。OpenAI/AnthropicのAPI key、従量課金API、追加支払いは要求しない。Claudeも既存のWeb新規conversationへ渡す。

## 実装内容

- packを選択単位とし、固定commitから検証した`INDEX.md`と全`SKILL.md`をcontract専用領域へmaterializeする。
- Desktop promptに、全ファイルの全文読了、INDEX関係とtrigger/boundaryの遵守、最低1つの適用、実依頼の完了を必須条件として含める。
- skillの説明、一覧、準備確認、実行可否の説明だけで終了することを明示的に禁止する。
- `depends-on`、`composes-with`、`contrasts-with`を一つの実行方法へ統合するよう要求する。
- Codexの起動先は`codex://threads/new`の新規Desktop taskとし、Codex CLI/TUIやOpenAI APIへfallbackしない。
- completion receipt、callback command、`activation-complete`、Desktop output schemaを製品コードとCLIから削除した。
- handoff結果は`desktop_handoff_ready`、`handoff_completed: true`、`answer_completion_claimed: false`として記録し、Desktop回答をSkill Magnetが取得・検証したとは表示しない。
- Python package/MSIXの版を0.4.0へ更新した。

## テスト契約

自動テストは次を直接検査する。

- promptがINDEXと全SKILL.mdを参照する。
- promptが最低1つのskill適用と実依頼完了を必須にする。
- 説明・一覧・準備確認だけでの終了を禁止する。
- API key、従量課金API、追加支払いを要求しない。
- receipt、callback、Desktop output schemaを生成しない。
- handoff成功を回答完了へ昇格しない。
- Windows ExplorerとmacOS Finderで同じpack/runtime/handoff意味論を保つ。

## 検証状況

- `python -m unittest discover -s tests -v`: 129 tests PASS、環境依存1件skip。旧receiptテストを削除し、新しいhandoff契約テストへ置換した。
- product policy、README、MVP設計文書の規範principle一致テスト: PASS。
- Python/MSIX version同期テスト: PASS。
- 0.4.0 wheelを独立した2 directoryでbuildし、論理payload SHA-256 `c9a0ffe8f542fd475144ac8fecd284175a46863d69d1d44ec5be78ed901ba38f`が一致した。

Windows実install/update/rollback/uninstallとWindows/macOS CIについては、この報告書の最終更新時に実測結果を追記する。
