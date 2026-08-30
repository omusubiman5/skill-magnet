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
- 0.4.0実機handoffで、裸のWindows pathに含まれる`\.`がMarkdown escapeとして解釈され、`C:\Users\HOMEA\.skill-magnet`が`C:\Users\HOMEA.skill-magnet`へ破損する不具合を確認した。全project/INDEX/SKILL pathをinline code内のforward-slash絶対pathへ変更し、0.4.1へ更新した。

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

- `python -m unittest discover -s tests -v`: 130 testsを実行するsuiteへ更新した。`.skill-magnet`を含むWindows pathのseparator保持テストを追加した。
- product policy、README、MVP設計文書の規範principle一致テスト: PASS。
- Python/MSIX version同期テスト: PASS。
- 0.4.0 wheelを独立した2 directoryでbuildし、論理payload SHA-256 `c9a0ffe8f542fd475144ac8fecd284175a46863d69d1d44ec5be78ed901ba38f`が一致した。
- 上記wheelをWindowsへ`--force-reinstall`し、既存0.3.6から0.4.0へ更新した。
- Windows modern context menuを再登録し、package `SkillMagnet.ContextMenu_0.4.0.0_x64__byy1sc3mfzfz4`、1 package leaf、Directory/Background、署名済みcommand target、`menu_contract_matches_config: true`、`usable_installed_state: true`を確認した。
- GitHub Actions run [`33310087151`](https://github.com/omusubiman5/skill-magnet/actions/runs/33310087151): Windows/macOSともgreen。両OS129 test、standalone wheel payload gate、macOS Finder Quick Action lifecycle、Windows certificate/native build/MSIX install・update・rollback・uninstall lifecycleがPASSした。

既存利用中installを破壊するlifecycle testはローカルでは強制せず、clean CI runnerで完遂した。Skill Magnetが保証するのは検証済みpromptの新規task handoffまでであり、Desktopモデルの回答完了・品質を機械検証したとは主張しない。
