# Windows Explorer package leaf release evidence

## 文書の位置づけ

この文書は現行のExplorer統合だけを記録する正本です。旧「skillごとのleaf」方式の実験記録は現行仕様の証拠に使用しません。現行仕様は、ExplorerのDirectory/Backgroundごとに`PMO`という単一package leafを表示し、その新規Codex Desktopタスク内でINDEXと全9スキルを読み、依頼に必要な最小集合を選ぶものです。

## 現在の集約結果

<!-- explorer-results-ledger:start
{
  "release_scope": "one-package-leaf",
  "full_test_count": 117,
  "menu_leaf_count": 1,
  "selection_kinds": ["package"],
  "pack_skill_count": 9,
  "automated_status": "PASS",
  "windows_explorer_field_status": "PASS_0.3.0_REAL_UI_TO_DESKTOP_HANDOFF",
  "codex_desktop_result_status": "MANUAL_RESULT_VERIFICATION_REQUIRED"
}
explorer-results-ledger:end -->

- 統合テスト: `python -m unittest discover -s tests -v` — 117 tests PASS
- menu contract: 1 package leaf / selection kind `package` / pack内9 skills
- 自動証拠: contract固定、INDEX/全SKILL materialization、source改変耐性、deep-link binding、CLI非起動、rollback、残留物回収
- 実機証拠: 0.3.0のBEADS folderで右クリック→`Skill Magnet`→単一`PMO`→確認UI→Codex→最終確認を実操作し、contract `d372a02620e84f01a9a6e326d1826ba7`の`desktop_handoff_ready`を確認
- Desktop完了の扱い: deep-link受理を実依頼完了とはみなさず、Desktopの自然文結果を確認するまで`verified_completed`にしない

## リリースゲート

`integration/explorer_results_gate.py`は、この台帳のtest count・leaf数・selection kind・pack skill数を、現在のテストsuiteと`skill-magnet.json`から得た値へ照合します。また、旧個別leaf仕様の文言が正本へ再混入した場合に失敗します。

Windows 0.3.0候補の最終受入では、次を新しい証拠として追記します。

1. wheelからのnative build、MSIX署名・install、Directory/Backgroundの単一`PMO`表示。
2. package leafからCodex Desktop新規タスクを開き、materialized INDEX/全SKILLを読み、依頼に必要な部分集合だけを適用した自然文回答。
3. update、rollback、uninstall後のAppx・証明書・ContextMenu/rollback残留ゼロ。CIでは旧自己署名trust 2世代のupgrade cleanupも含めて確認済み。

macOS Finderはcommunity betaです。macOS runner上の実Quick Action install、`/usr/bin/automator`実行、selected path probe、uninstall、残留ゼロは最終実装HEAD `ba37618`のrun `33246589566`で独立に確認済みです。Finderのメニュー表示そのものは人手UX受入であり、このWindows証拠を転用しません。
